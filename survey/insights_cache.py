from __future__ import annotations

import hashlib
import json

from django.db.models import Q, QuerySet

from .models import Question, Submission, Survey, SurveyInsightCache, SurveyInsightCacheItem
from .selectors import get_visibility_start_date, parse_submissions_date_filter

INSIGHTS_SURVEY_ID = 6
STATE_QUESTION_ID = 709
DISTRICT_QUESTION_ID = 710
BLOCK_QUESTION_ID = 711
# "min_date" is the partner visibility cutoff baked into filters by the view
# layer; it lives in the cache key so each restricted partner gets its own
# insights slice.
# "language" lives in the cache key so each website language gets its own
# insights slice — the Celery worker generates the LLM output directly in that
# language, and we don't want one language overwriting another's cached cards.
INSIGHT_FILTER_FIELDS = (
    "state",
    "district",
    "block",
    "year",
    "date_from",
    "date_to",
    "min_date",
    "language",
)


def normalize_insight_filters(filters: dict | None) -> dict:
    normalized = {}
    source = filters or {}
    for key in INSIGHT_FILTER_FIELDS:
        value = source.get(key, "")
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if value is None:
            value = ""
        value = str(value).strip()
        if value:
            normalized[key] = value
    return normalized


def _find_filter_option(question: Question | None, raw_value: str):
    if not question or not raw_value:
        return None

    option_filter = Q(text__iexact=raw_value) | Q(value__iexact=raw_value)
    if raw_value.isdigit():
        option_filter |= Q(id=int(raw_value))
    return question.options.filter(option_filter).first()


def _question_by_id(survey: Survey, question_id: int) -> Question | None:
    return Question.objects.filter(id=question_id, survey=survey).first()


def normalize_insight_filters_for_survey(survey: Survey, filters: dict | None) -> dict:
    """Normalize filter values into a canonical cache-key shape for one survey.

    Report selects can submit option IDs while bookmarked/API calls may submit
    labels. Canonicalizing option-backed geography filters to labels keeps both
    forms on the same cache entry.
    """
    normalized = normalize_insight_filters(filters)
    question_ids = {
        "state": STATE_QUESTION_ID,
        "district": DISTRICT_QUESTION_ID,
        "block": BLOCK_QUESTION_ID,
    }
    for key, question_id in question_ids.items():
        raw_value = normalized.get(key, "")
        if not raw_value:
            continue
        option = _find_filter_option(_question_by_id(survey, question_id), raw_value)
        if option:
            normalized[key] = option.text.strip()
    return normalized


def apply_visibility_insight_filters(filters: dict | None, user) -> dict:
    """Inject the user's partner visibility cutoff into insight filters.

    Why: The cache key is derived from these filters, so adding `min_date` here
    gives restricted users their own cache slice automatically — and the Celery
    insights task picks it up via cache_entry.filters with no extra plumbing.
    """
    merged = dict(filters or {})
    cutoff = get_visibility_start_date(user)
    if cutoff is None:
        return merged
    merged["min_date"] = cutoff.isoformat()
    return merged


def build_insight_cache_key(survey_id: int, filters: dict | None) -> str:
    payload = {
        "survey_id": survey_id,
        "filters": normalize_insight_filters(filters),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _filter_submission_ids_by_answer(
    queryset: QuerySet,
    question: Question | None,
    raw_value: str,
    base_ids: set[int] | None = None,
) -> set[int] | None:
    if not raw_value or question is None:
        return base_ids
    raw_value = str(raw_value).strip()
    if not raw_value:
        return base_ids

    scoped = queryset
    if base_ids is not None:
        scoped = scoped.filter(id__in=base_ids)

    aliases = {raw_value}
    option = _find_filter_option(question, raw_value)
    if option:
        aliases.add(str(option.id))
        aliases.add(option.text)
        if option.value:
            aliases.add(option.value)
    aliases = {alias.strip() for alias in aliases if alias and alias.strip()}

    text_match = Q()
    option_text_match = Q()
    option_value_match = Q()
    option_ids = []
    for alias in aliases:
        text_match |= Q(answers__text_answer__iexact=alias)
        option_text_match |= Q(answers__selected_options__text__iexact=alias)
        option_value_match |= Q(answers__selected_options__value__iexact=alias)
        if alias.isdigit():
            option_ids.append(int(alias))

    text_ids = set(
        scoped.filter(
            answers__question=question,
        )
        .filter(text_match)
        .values_list("id", flat=True)
    )
    option_text_ids = set(
        scoped.filter(
            answers__question=question,
        )
        .filter(option_text_match)
        .values_list("id", flat=True)
    )
    option_value_match_ids = set(
        scoped.filter(
            answers__question=question,
        )
        .filter(option_value_match)
        .values_list("id", flat=True)
    )
    option_id_match_ids = set()
    if option_ids:
        option_id_match_ids = set(
            scoped.filter(
                answers__question=question,
                answers__selected_options__id__in=option_ids,
            ).values_list("id", flat=True)
        )
    return text_ids | option_text_ids | option_value_match_ids | option_id_match_ids


def build_filtered_insight_submissions(survey: Survey, filters: dict | None) -> QuerySet:
    normalized = normalize_insight_filters_for_survey(survey, filters)
    submissions = Submission.objects.filter(
        survey=survey,
        status="completed",
        is_archived=False,
    ).distinct()

    year = normalized.get("year")
    if year:
        submissions = submissions.filter(submitted_at__year=year)

    dt_from = parse_submissions_date_filter(normalized.get("date_from", ""))
    if dt_from:
        submissions = submissions.filter(submitted_at__gte=dt_from)

    dt_to = parse_submissions_date_filter(normalized.get("date_to", ""), is_end=True)
    if dt_to:
        submissions = submissions.filter(submitted_at__lte=dt_to)

    min_date = parse_submissions_date_filter(normalized.get("min_date", ""))
    if min_date:
        submissions = submissions.filter(submitted_at__gte=min_date)

    state = normalized.get("state", "")
    district = normalized.get("district", "")
    block = normalized.get("block", "")
    if state or district or block:
        state_question = Question.objects.filter(id=STATE_QUESTION_ID, survey=survey).first()
        district_question = Question.objects.filter(id=DISTRICT_QUESTION_ID, survey=survey).first()
        block_question = Question.objects.filter(id=BLOCK_QUESTION_ID, survey=survey).first()

        filtered_ids = _filter_submission_ids_by_answer(submissions, state_question, state)
        filtered_ids = _filter_submission_ids_by_answer(submissions, district_question, district, filtered_ids)
        filtered_ids = _filter_submission_ids_by_answer(submissions, block_question, block, filtered_ids)
        if filtered_ids is not None:
            submissions = submissions.filter(id__in=filtered_ids)

    return submissions.distinct()


def get_or_create_insight_cache(survey: Survey, filters: dict | None) -> tuple[SurveyInsightCache, bool]:
    normalized = normalize_insight_filters_for_survey(survey, filters)
    cache_key = build_insight_cache_key(survey.id, normalized)
    return SurveyInsightCache.objects.get_or_create(
        cache_key=cache_key,
        defaults={
            "survey": survey,
            "filters": normalized,
            "status": SurveyInsightCache.STATUS_PENDING,
        },
    )


def serialize_insight_groups(cache_entry: SurveyInsightCache) -> list[dict]:
    grouped = {}
    items = list(
        SurveyInsightCacheItem.objects.filter(cache_entry=cache_entry)
        .select_related("indicator", "question")
        .order_by("indicator__name", "-poor_performance_pct", "question_id")
    )
    for item in items:
        key = item.indicator_id or 0
        bucket = grouped.setdefault(
            key,
            {
                "indicator_name": item.indicator.name if item.indicator else "(ungrouped)",
                "indicator_description": item.indicator.description if item.indicator else "",
                "items": [],
            },
        )
        bucket["items"].append(_serialize_insight_item(item))
    for bucket in grouped.values():
        overview = _merge_indicator_items(bucket["indicator_name"], bucket["items"])
        bucket["overview"] = overview
        bucket.update(overview)
        bucket["items"] = [overview]
    result = list(grouped.values())
    result.sort(key=lambda b: b.get("poor_performance_pct", 0), reverse=True)
    return result


def _serialize_insight_item(item: SurveyInsightCacheItem) -> dict:
    return {
        "question_text_snapshot": item.question_text_snapshot,
        "poor_performance_pct": item.poor_performance_pct,
        "respondents_total": item.respondents_total,
        "respondents_poor": item.respondents_poor,
        "symptoms": item.symptoms,
        "likely_causes": item.likely_causes,
        "high_level_action": item.high_level_action,
        "action_fpo": item.action_fpo,
        "action_programme": item.action_programme,
        "action_donor": item.action_donor,
    }


def _merge_indicator_items(indicator_name: str, items: list[dict]) -> dict:
    if not items:
        return {
            "question_text_snapshot": f"{indicator_name} overview",
            "poor_performance_pct": 0,
            "respondents_total": 0,
            "respondents_poor": 0,
            "symptoms": "",
            "likely_causes": "",
            "high_level_action": "",
            "action_fpo": "",
            "action_programme": "",
            "action_donor": "",
        }
    if len(items) == 1:
        item = dict(items[0])
        item["question_text_snapshot"] = f"{indicator_name} overview"
        return item

    respondents_total = sum(item.get("respondents_total") or 0 for item in items)
    respondents_poor = sum(item.get("respondents_poor") or 0 for item in items)
    poor_pct = (respondents_poor / respondents_total * 100) if respondents_total else max(
        item.get("poor_performance_pct") or 0 for item in items
    )
    return {
        "question_text_snapshot": f"{indicator_name} overview",
        "poor_performance_pct": poor_pct,
        "respondents_total": respondents_total,
        "respondents_poor": respondents_poor,
        "symptoms": _join_unique(item.get("symptoms", "") for item in items),
        "likely_causes": _join_unique(item.get("likely_causes", "") for item in items),
        "high_level_action": _join_unique(item.get("high_level_action", "") for item in items),
        "action_fpo": _join_unique(item.get("action_fpo", "") for item in items),
        "action_programme": _join_unique(item.get("action_programme", "") for item in items),
        "action_donor": _join_unique(item.get("action_donor", "") for item in items),
    }


def _join_unique(values) -> str:
    seen = set()
    chunks = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        chunks.append(text)
    return " ".join(chunks)
