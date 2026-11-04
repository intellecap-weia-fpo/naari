"""Compute per-question "poor performance" rates for a survey.

A respondent is considered to have performed poorly on a question when:
- Choice question (radio / dropdown / checkbox): they selected an option in the
  bottom tier. We treat the lowest-ordered option (or two, if there are ≥5
  options) as "poor". This maps cleanly to Likert-style questions used across
  the WEAI survey (e.g. "Not at all" / "Small extent").
- Number question: answered below the median of all respondents' numeric
  answers for that question. Empty answers are excluded from both numerator
  and denominator.
- Other types: skipped (text answers aren't scoreable).

Only questions that are actually referenced by the indicator's script (via
`answers[<question_id>]` or `answers.get(<question_id>)`) are scored — this
prevents irrelevant demographic questions from being flagged.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import Iterable

from django.db.models import Count

from .models import (
    Indicator,
    IndicatorResult,
    Question,
    QuestionOption,
    Submission,
    SubmissionAnswer,
)
from .insights_overrides import classify_question, poor_option_ids as override_poor_ids


# Geographic question IDs in the NAARI survey — used to break poor responders
# down by state / district so insights can name where the issue concentrates
# instead of staying abstract.
STATE_QUESTION_ID = 709
DISTRICT_QUESTION_ID = 710
# Minimum responders in a region before we surface it as a "hotspot" — avoids
# top-listing a district with 1 respondent at 100% poor.
_GEO_MIN_SAMPLE = 5
_GEO_TOP_N = 3
_OPTION_TOP_N = 6


_ANSWER_ID_RE = re.compile(r"answers\s*(?:\[|\.get\()\s*(\d+)")
# Fallback: any integer literal in the script (filtered against real Question ids)
_INT_LITERAL_RE = re.compile(r"\b(\d{2,6})\b")


def _question_ids_in_script(code: str, survey=None) -> list[int]:
    """Extract question IDs referenced inside an indicator script.

    First pass looks for `answers[123]` / `answers.get(123)`. If none match
    (the NAARI scripts use dict literals like `'decision_borrow_qid': 1199`
    and `for qid, answer in answers.items(): if qid == 984`), we fall back
    to collecting every integer literal in the script and — when a survey is
    provided — intersecting with the survey's real question IDs so we do not
    treat arbitrary constants (e.g. 960-min time-poverty thresholds) as
    question references.
    """
    if not code:
        return []
    direct = [int(m) for m in _ANSWER_ID_RE.findall(code)]
    if direct:
        return list(dict.fromkeys(direct))

    ints = {int(m) for m in _INT_LITERAL_RE.findall(code)}
    if not ints:
        return []
    if survey is None:
        return sorted(ints)
    real = set(
        Question.objects.filter(survey=survey, id__in=ints).values_list("id", flat=True)
    )
    return sorted(real)


def _poor_option_ids(question: Question) -> list[int]:
    """Return the IDs of the options that count as "poor" for a choice question.

    Prefers the explicit rule in `insights_overrides`. If no override matches,
    returns []  — signalling the caller to skip this question rather than
    risk publishing an inverted count (e.g. flagging "Self" as disempowered
    on a "Who decides" question).
    """
    rule = classify_question(question)
    if rule is None:
        return []
    if rule.get("kind") == "skip":
        return []
    return sorted(override_poor_ids(question, rule))


def _compute_choice_poor_rate(
    question: Question, submission_ids: Iterable[int]
) -> tuple[int, int, set[int], set[int], dict[int, int]]:
    """Return (poor_count, total_count, respondent_submission_ids,
    poor_submission_ids, option_counts).

    `respondent_submission_ids` is the set of submissions that actually
    answered this question — used as the denominator when slicing by region.

    `option_counts` maps QuestionOption.id -> number of respondents who
    selected it (for the option-distribution context the LLM uses).

    Returns empty results when no override rule exists so the caller skips
    the question — preventing cards with incorrect "poor" semantics.
    """
    poor_option_ids = _poor_option_ids(question)
    if not poor_option_ids:
        return 0, 0, set(), set(), {}

    answers = (
        SubmissionAnswer.objects.filter(
            question=question, submission_id__in=submission_ids
        )
        .annotate(opt_count=Count("selected_options"))
        .filter(opt_count__gt=0)
    )

    total = 0
    poor = 0
    poor_set = set(poor_option_ids)
    poor_submission_ids: set[int] = set()
    respondent_submission_ids: set[int] = set()
    option_counts: dict[int, int] = defaultdict(int)
    for ans in answers.prefetch_related("selected_options"):
        selected = {o.id for o in ans.selected_options.all()}
        if not selected:
            continue
        total += 1
        respondent_submission_ids.add(ans.submission_id)
        for oid in selected:
            option_counts[oid] += 1
        if selected & poor_set:
            poor += 1
            poor_submission_ids.add(ans.submission_id)
    return poor, total, respondent_submission_ids, poor_submission_ids, dict(option_counts)


def _compute_number_poor_rate(
    question: Question, submission_ids: Iterable[int]
) -> tuple[int, int, set[int], set[int], dict[str, float]]:
    """Return (poor_count, total_count, respondent_submission_ids,
    poor_submission_ids, value_stats).

    `value_stats` carries median/min/max/p25/p75 so the LLM can describe the
    spread instead of just the headline poor share.
    """
    answers = (
        SubmissionAnswer.objects.filter(
            question=question, submission_id__in=submission_ids
        )
        .exclude(text_answer__isnull=True)
        .exclude(text_answer__exact="")
    )

    pairs: list[tuple[int, float]] = []
    for ans in answers:
        try:
            pairs.append((ans.submission_id, float(ans.text_answer.strip())))
        except (TypeError, ValueError):
            continue

    if not pairs:
        return 0, 0, set(), set(), {}
    values = [v for _, v in pairs]
    median = statistics.median(values)
    respondent_submission_ids = {sid for sid, _ in pairs}
    poor_submission_ids = {sid for sid, v in pairs if v < median}
    sorted_values = sorted(values)
    n = len(sorted_values)

    def _pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return sorted_values[idx]

    stats = {
        "min": min(values),
        "max": max(values),
        "median": median,
        "p25": _pct(0.25),
        "p75": _pct(0.75),
    }
    return len(poor_submission_ids), n, respondent_submission_ids, poor_submission_ids, stats


def _build_option_distribution(
    question: Question, option_counts: dict[int, int], total: int
) -> list[dict]:
    """Return a sorted list of `{text, count, pct}` for each option that was
    chosen at least once. Capped at `_OPTION_TOP_N` items so the prompt stays
    readable. The "poor" options are flagged so the LLM can anchor causes to
    the dominant low-empowerment response rather than guessing."""
    if total <= 0 or not option_counts:
        return []
    poor_set = set(_poor_option_ids(question))
    options = list(question.options.all().order_by("order", "id"))
    rows = []
    for opt in options:
        c = option_counts.get(opt.id, 0)
        if c == 0:
            continue
        rows.append(
            {
                "text": opt.text,
                "count": c,
                "pct": round((c / total) * 100.0, 1),
                "is_poor": opt.id in poor_set,
            }
        )
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows[:_OPTION_TOP_N]


def _build_geo_breakdown(
    respondent_submission_ids: Iterable[int],
    poor_submission_ids: set[int],
    geo_question_id: int,
) -> list[dict]:
    """For each region (state or district), return its poor share among the
    submissions that actually answered the target question. Filters to
    regions with at least `_GEO_MIN_SAMPLE` respondents, sorts by poor pct
    descending, returns top `_GEO_TOP_N`.
    """
    respondent_ids = list(respondent_submission_ids)
    if not respondent_ids or not poor_submission_ids:
        return []

    answers = SubmissionAnswer.objects.filter(
        question_id=geo_question_id, submission_id__in=respondent_ids
    ).prefetch_related("selected_options")

    region_total: dict[str, int] = defaultdict(int)
    region_poor: dict[str, int] = defaultdict(int)
    for ans in answers:
        labels = [o.text for o in ans.selected_options.all()]
        if not labels and ans.text_answer:
            labels = [ans.text_answer.strip()]
        for label in labels:
            if not label:
                continue
            region_total[label] += 1
            if ans.submission_id in poor_submission_ids:
                region_poor[label] += 1

    rows = []
    for label, total in region_total.items():
        if total < _GEO_MIN_SAMPLE:
            continue
        poor = region_poor.get(label, 0)
        rows.append(
            {
                "name": label,
                "poor": poor,
                "total": total,
                "pct": round((poor / total) * 100.0, 1),
            }
        )
    rows.sort(key=lambda r: (r["pct"], r["poor"]), reverse=True)
    return rows[:_GEO_TOP_N]


def _build_question_data_context(
    question: Question,
    respondent_submission_ids: Iterable[int],
    poor_submission_ids: set[int],
    option_counts: dict[int, int] | None,
    value_stats: dict[str, float] | None,
    total: int,
) -> dict:
    """Bundle everything we want the LLM to see beyond the headline pct:
    option distribution, geographic hotspots, numeric spread. Each piece is
    optional — the prompt is built defensively from whatever is present.
    """
    ctx: dict = {}
    if option_counts:
        dist = _build_option_distribution(question, option_counts, total)
        if dist:
            ctx["option_distribution"] = dist
    if value_stats:
        ctx["value_stats"] = value_stats
    states = _build_geo_breakdown(respondent_submission_ids, poor_submission_ids, STATE_QUESTION_ID)
    if states:
        ctx["top_states"] = states
    districts = _build_geo_breakdown(respondent_submission_ids, poor_submission_ids, DISTRICT_QUESTION_ID)
    if districts:
        ctx["top_districts"] = districts
    return ctx


def compute_question_poor_rates(
    survey,
    min_poor_pct: float = 50.0,
    uncovered_log: list | None = None,
    submission_ids: Iterable[int] | None = None,
) -> list[dict]:
    """For each active indicator on the survey, return one row per question
    where `poor_pct >= min_poor_pct`.

    When `uncovered_log` is passed, questions skipped because they have no
    override rule are appended to it as `(question_id, question_text)` tuples
    so callers can surface a warning. This makes coverage gaps visible rather
    than silent.
    """
    if submission_ids is None:
        submission_ids = list(
            Submission.objects.filter(survey=survey, status="completed").values_list("id", flat=True)
        )
    else:
        submission_ids = list(submission_ids)
    if not submission_ids:
        return []

    rows: list[dict] = []
    indicators = Indicator.objects.filter(survey=survey, is_active=True).select_related("script")
    for indicator in indicators:
        question_ids = _question_ids_in_script(indicator.script.code, survey=survey)
        if not question_ids:
            continue
        questions = Question.objects.filter(id__in=question_ids, survey=survey).prefetch_related("options")
        for question in questions:
            option_counts: dict[int, int] | None = None
            value_stats: dict[str, float] | None = None
            if question.question_type in ("radio", "dropdown", "checkbox"):
                rule = classify_question(question)
                if rule is None:
                    if uncovered_log is not None:
                        uncovered_log.append((question.id, question.text[:100]))
                    continue
                if rule.get("kind") == "skip":
                    continue
                poor, total, respondent_sids, poor_sids, option_counts = (
                    _compute_choice_poor_rate(question, submission_ids)
                )
            elif question.question_type == "number":
                poor, total, respondent_sids, poor_sids, value_stats = (
                    _compute_number_poor_rate(question, submission_ids)
                )
            else:
                continue
            if total == 0:
                continue
            pct = (poor / total) * 100.0
            if pct < min_poor_pct:
                continue
            data_context = _build_question_data_context(
                question,
                respondent_sids,
                poor_sids,
                option_counts,
                value_stats,
                total,
            )
            rows.append(
                {
                    "indicator": indicator,
                    "question": question,
                    "poor_pct": pct,
                    "respondents_total": total,
                    "respondents_poor": poor,
                    "data_context": data_context,
                }
            )
    return rows

# ----------------------------------------------------------------------
# IndicatorResult-based empowerment rates (the same source the dashboard
# cards and map use). Replaces override-rule-based "poor rates" as the
# headline metric for insight cards.
# ----------------------------------------------------------------------

# Keys checked (in priority order) when an IndicatorResult.result is a dict.
_EMPOWERMENT_KEYS = ["empowered", "is_empowered", "empowered_status", "status"]


def classify_empowered(result) -> bool:
    """Determine whether an ``IndicatorResult.result`` value means 'empowered'.

    Single source of truth — the dashboard cards
    (``get_optimized_indicator_results``), the map
    (``get_optimized_state_map_data``), and the insight pipeline all share
    this heuristic so their numbers always agree.

    Handles bool, numeric, string, and dict results.  Dict results check for
    an explicit empowerment key first, then fall back to a score/value
    threshold (50 when >1, 0.5 otherwise).
    """
    if isinstance(result, bool):
        return result
    if isinstance(result, (int, float)):
        threshold = 50 if result > 1 else 0.5
        return result > threshold
    if isinstance(result, str):
        return result.lower() in ("empowered", "true", "yes", "1", "enabled", "active")
    if isinstance(result, dict):
        for key in _EMPOWERMENT_KEYS:
            if key in result:
                val = result[key]
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    return val.lower() in ("true", "empowered", "yes", "1")
                if isinstance(val, (int, float)):
                    return val > 0.5
        # No explicit key — fall back to score/value threshold.
        value = None
        if "value" in result:
            value = float(result["value"])
        elif "score" in result:
            value = float(result["score"])
        if value is not None:
            threshold = 50 if value > 1 else 0.5
            return value > threshold
    return False


def _build_empowerment_geo_breakdown(
    submission_ids: Iterable[int],
    empowered_sids: set[int],
    geo_question_id: int,
) -> list[dict]:
    """For each region (state or district), return its empowerment rate among
    submissions that have a successful IndicatorResult for this indicator.

    Mirrors the map's per-region empowerment computation but surfaces the
    LOWEST-empowerment regions first — the LLM needs to name where the problem
    concentrates, not where it's already solved.
    """
    respondent_ids = list(submission_ids)
    if not respondent_ids:
        return []

    answers = SubmissionAnswer.objects.filter(
        question_id=geo_question_id, submission_id__in=respondent_ids
    ).prefetch_related("selected_options")

    region_total: dict[str, int] = defaultdict(int)
    region_empowered: dict[str, int] = defaultdict(int)
    for ans in answers:
        labels = [o.text for o in ans.selected_options.all()]
        if not labels and ans.text_answer:
            labels = [ans.text_answer.strip()]
        for label in labels:
            if not label:
                continue
            region_total[label] += 1
            if ans.submission_id in empowered_sids:
                region_empowered[label] += 1

    rows = []
    for label, total in region_total.items():
        if total < _GEO_MIN_SAMPLE:
            continue
        empowered = region_empowered.get(label, 0)
        emp_pct = round((empowered / total) * 100.0, 1)
        rows.append(
            {"name": label, "empowered_pct": emp_pct, "empowered": empowered, "total": total}
        )
    rows.sort(key=lambda r: (r["empowered_pct"], r["total"]))  # lowest first
    return rows[:_GEO_TOP_N]


def compute_indicator_empowerment_rates(
    survey,
    submission_ids: Iterable[int] | None = None,
) -> list[dict]:
    """For each active indicator, compute empowerment rates from
    ``IndicatorResult`` — the SAME source as the dashboard "Percentage of
    Empowered Women" card and the map's geographic empowerment breakdown.

    This replaces the override-rule-based poor-rate as the *headline metric*
    for insight cards (``poor_pct`` below = disempowered share, so the UI's
    ``Performance = 100 - poor_pct`` equals the dashboard's empowerment %).

    Returns one row per indicator:
        indicator, poor_pct, respondents_total, respondents_poor,
        data_context (empowerment summary + geo breakdown from
        IndicatorResult).
    """
    if submission_ids is None:
        submission_ids = list(
            Submission.objects.filter(survey=survey, status="completed").values_list(
                "id", flat=True
            )
        )
    else:
        submission_ids = list(submission_ids)
    if not submission_ids:
        return []

    indicators = list(Indicator.objects.filter(survey=survey, is_active=True))
    if not indicators:
        return []
    indicator_ids = [ind.id for ind in indicators]

    # Bulk-fetch all IndicatorResults for these indicators + submissions.
    results_qs = IndicatorResult.objects.filter(
        indicator_id__in=indicator_ids,
        submission_id__in=submission_ids,
        success=True,
    ).values("indicator_id", "submission_id", "result")

    # Group by indicator: {ind_id: {submission_id: result}}
    by_indicator: dict[int, dict[int, object]] = defaultdict(dict)
    for row in results_qs:
        by_indicator[row["indicator_id"]][row["submission_id"]] = row["result"]

    rows: list[dict] = []
    for indicator in indicators:
        sub_results = by_indicator.get(indicator.id, {})
        if not sub_results:
            continue

        empowered_sids: set[int] = set()
        disempowered_sids: set[int] = set()
        for sid, result in sub_results.items():
            if classify_empowered(result):
                empowered_sids.add(sid)
            else:
                disempowered_sids.add(sid)

        total = len(sub_results)
        empowered_count = len(empowered_sids)
        disempowered_count = len(disempowered_sids)
        disempowered_pct = (disempowered_count / total * 100.0) if total else 0.0

        respondent_sids = empowered_sids | disempowered_sids
        top_states = _build_empowerment_geo_breakdown(
            respondent_sids, empowered_sids, STATE_QUESTION_ID
        )
        top_districts = _build_empowerment_geo_breakdown(
            respondent_sids, empowered_sids, DISTRICT_QUESTION_ID
        )

        data_context: dict = {
            "empowerment_pct": round(
                (empowered_count / total * 100.0) if total else 0, 1
            ),
            "empowered_count": empowered_count,
            "disempowered_count": disempowered_count,
            "total_count": total,
        }
        if top_states:
            data_context["top_states"] = top_states
        if top_districts:
            data_context["top_districts"] = top_districts

        rows.append(
            {
                "indicator": indicator,
                "poor_pct": disempowered_pct,
                "respondents_total": total,
                "respondents_poor": disempowered_count,
                "data_context": data_context,
            }
        )

    return rows
