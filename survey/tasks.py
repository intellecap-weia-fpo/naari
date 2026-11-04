"""Celery tasks for the survey app. Discovered automatically by
`one.celery.autodiscover_tasks()`."""

import logging
import os
import tempfile
from functools import wraps

from celery import chord, shared_task
from django.conf import settings
from django.core.files import File
from django.db import connection
from django.db.models import F
from django.utils import timezone
from django_tenants.utils import schema_context

from .insights_cache import build_filtered_insight_submissions
from .insights_guidance import find_guidance
from .insights_metrics import compute_indicator_empowerment_rates, compute_question_poor_rates
from .insights_service import InsightsService
from .models import (
    Indicator,
    ReportExportJob,
    Submission,
    SubmissionExportJob,
    Survey,
    SurveyInsightCache,
    SurveyInsightCacheItem,
)
from .report_exports import workbook_spec_for_report
from .selectors import build_submissions_queryset

logger = logging.getLogger(__name__)

# How often we flush processed_rows to the DB so the UI progress bar updates without hammering writes.
PROGRESS_UPDATE_EVERY = 100
# Chunk size used when iterating large querysets to keep memory bounded.
DB_CHUNK_SIZE = 500


def tenant_schema_task(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        schema_name = kwargs.pop("schema_name", None)
        if schema_name:
            with schema_context(schema_name):
                return func(*args, **kwargs)
        return func(*args, **kwargs)

    return wrapper


def _resolve_question_text_for_language(
    question,
    language: str,
    cache: dict[tuple[int, str], str],
) -> str:
    """Return question text in `language` for storing on a cached insight item.

    Order: existing QuestionTranslation row → live TranslationService call →
    original question text. Results are memoized inside one task run so we
    don't re-translate the same question across multiple insight rows.
    """
    original = question.text or ""
    code = (language or "").strip().lower()
    if not code or code == "en" or not original:
        return original

    cache_key = (question.id, code)
    if cache_key in cache:
        return cache[cache_key]

    translation = question.translations.filter(language=code).first()
    if translation and (translation.text or "").strip():
        cache[cache_key] = translation.text
        return translation.text

    try:
        from .translation_service import TranslationService
        translated = TranslationService().translate_text(original, code)
    except Exception:
        logger.exception(
            "Question text translation failed question_id=%s lang=%s", question.id, code
        )
        translated = None

    resolved = translated or original
    cache[cache_key] = resolved
    return resolved


def _collect_columns(questions, questions_with_other):
    """Build the (question, option/flag) tuples that become spreadsheet columns.
    Mirrors the logic in the existing synchronous download view."""
    columns = []
    for q in questions:
        if q.question_type == "radio":
            columns.append((q, "radio_single"))
            if q.id in questions_with_other:
                columns.append((q, "other"))
        elif q.question_type in ("checkbox", "dropdown"):
            options = list(q.options.all().order_by("order", "id"))
            for opt in options:
                columns.append((q, opt))
            if q.id in questions_with_other:
                columns.append((q, "other"))
        else:
            columns.append((q, None))
    return columns


def _build_header(columns, indicators):
    headers = ["Submission ID", "Submitted At (IST)", "Submitter", "Status", "App Version"]
    for q, opt in columns:
        if opt == "radio_single":
            headers.append(q.text)
        elif opt == "other":
            headers.append(f"{q.text}::Other")
        elif opt:
            headers.append(f"{q.text}::{opt.text}")
        else:
            headers.append(q.text)
    for ind in indicators:
        headers.append(ind.name)
    return headers


def _row_for(submission, columns, indicators, display_mode, ist_tz):
    """Build one spreadsheet row for a submission."""
    row = [
        submission.pk,
        submission.submitted_at.astimezone(ist_tz).strftime("%b %d, %Y %I:%M %p"),
        submission.get_submitter_display(),
        submission.get_status_display(),
        submission.app_version or "",
    ]

    answers_dict = {a.question_id: a for a in submission.answers.all()}

    for q, opt in columns:
        answer = answers_dict.get(q.id)
        if opt == "other":
            row.append(answer.other_text if answer and answer.other_text else "")
        elif opt == "radio_single":
            if answer:
                selected = list(answer.selected_options.all())
                if selected:
                    if display_mode == "values":
                        o = selected[0]
                        row.append(o.value if o.value else str(o.order + 1))
                    else:
                        row.append(selected[0].text)
                else:
                    row.append("")
            else:
                row.append("")
        elif opt:
            if answer:
                sel_ids = {o.id for o in answer.selected_options.all()}
                row.append("1" if opt.id in sel_ids else "0")
            else:
                row.append("0")
        else:
            row.append(answer.text_answer if answer and answer.text_answer else "")

    results_dict = {r.indicator_id: r for r in submission.indicator_results.all()}
    for ind in indicators:
        res = results_dict.get(ind.id)
        row.append(str(res.result) if res and res.success else "")

    return row


@shared_task(name="survey.refresh_survey_insights")
@tenant_schema_task
def refresh_survey_insights(survey_id: int, force: bool = True):
    """Run the insights generator for one survey from a Celery worker so the
    dashboard's "Refresh Insights" button doesn't block the web request.

    Each question triggers an external LLM call with built-in pacing + retries,
    so a full refresh for a large survey can take tens of seconds to a few
    minutes — well past typical browser/proxy timeouts.
    """
    from django.core.management import call_command

    args = [f"--survey-id={survey_id}"]
    if force:
        args.append("--force")
    logger.info("refresh_survey_insights starting survey_id=%s force=%s", survey_id, force)
    call_command("generate_survey_insights", *args)
    logger.info("refresh_survey_insights finished survey_id=%s", survey_id)


def _build_indicator_payloads(survey, submission_ids: list[int], submission_count: int) -> list[dict]:
    """Build one JSON-serializable payload per active indicator for a Celery
    child task.

    **Headline metric source:** ``compute_indicator_empowerment_rates`` — the
    same ``IndicatorResult`` data that powers the dashboard "Percentage of
    Empowered Women" card and the empowerment-by-geography map. ``poor_pct``
    is the disempowered share so the UI's ``Performance = 100 - poor_pct``
    equals the dashboard's empowerment %.

    **Supplementary context:** per-question option distributions from
    ``compute_question_poor_rates`` are kept as ``question_rows`` so the LLM
    can describe *why* respondents are disempowered (which options dominate),
    but they no longer drive the headline number.

    The payload carries plain strings/dicts (no Django model instances) so it
    survives the broker round-trip. Indicators without IndicatorResult records
    still get a payload with empty context plus guidance, so the child can fall
    back to the playbook.
    """
    # --- Primary: IndicatorResult-based empowerment (dashboard/map source) ---
    empowerment_rows = compute_indicator_empowerment_rates(
        survey, submission_ids=submission_ids
    )
    empowerment_by_indicator = {row["indicator"].id: row for row in empowerment_rows}

    # --- Supplementary: per-question option distributions for LLM context ---
    question_rows_by_indicator: dict[int, list[dict]] = {}
    all_rows = compute_question_poor_rates(
        survey,
        min_poor_pct=0,
        submission_ids=submission_ids,
    )
    for row in all_rows:
        indicator = row["indicator"]
        question = row["question"]
        question_rows_by_indicator.setdefault(indicator.id, []).append(
            {
                "question_text": question.text,
                "poor_pct": row["poor_pct"],
                "respondents_total": row.get("respondents_total"),
                "respondents_poor": row.get("respondents_poor"),
                "data_context": row.get("data_context"),
                "guidance": find_guidance(indicator.name, question.text),
            }
        )

    payloads: list[dict] = []
    active_indicators = Indicator.objects.filter(survey=survey, is_active=True).select_related("script")
    for indicator in active_indicators:
        emp = empowerment_by_indicator.get(indicator.id)
        question_rows = question_rows_by_indicator.get(indicator.id, [])

        if emp:
            poor_pct = emp["poor_pct"]
            respondents_total = emp["respondents_total"]
            respondents_poor = emp["respondents_poor"]
            empowerment_context = emp["data_context"]
        else:
            # No IndicatorResult for this indicator — skip the data-free LLM
            # call; the child task falls back to the playbook guidance.
            poor_pct = 0.0
            respondents_total = 0
            respondents_poor = 0
            empowerment_context = None

        guidance = next((r["guidance"] for r in question_rows if r.get("guidance")), None)
        if guidance is None:
            guidance = find_guidance(indicator.name, "")

        payloads.append(
            {
                "indicator_id": indicator.id,
                "indicator_name": indicator.name,
                "indicator_description": indicator.description or "",
                "poor_pct": poor_pct,
                "respondents_total": respondents_total,
                "respondents_poor": respondents_poor,
                "empowerment_context": empowerment_context,
                "question_rows": question_rows,
                "guidance": guidance,
            }
        )
    return payloads


@shared_task(bind=True, name="survey.generate_filtered_survey_insights")
@tenant_schema_task
def generate_filtered_survey_insights(self, cache_entry_id: int):
    """Orchestrate filter-aware insights for one cache entry.

    Rather than generating every indicator inline (a single long-running task
    that a mid-run Anthropic failure/rate-limit could blank out wholesale), this
    fans out one :func:`generate_indicator_insight` child task per active
    indicator and stitches them back together with a
    :func:`finalize_filtered_survey_insights` chord callback. Each indicator now
    succeeds, retries, or falls back to the playbook independently of the others.
    """
    try:
        cache_entry = SurveyInsightCache.objects.select_related("survey").get(pk=cache_entry_id)
    except SurveyInsightCache.DoesNotExist:
        logger.warning("SurveyInsightCache %s not found", cache_entry_id)
        return

    survey = cache_entry.survey
    schema_name = connection.schema_name
    cache_entry.status = SurveyInsightCache.STATUS_RUNNING
    cache_entry.started_at = timezone.now()
    cache_entry.error_message = ""
    cache_entry.result_count = 0
    cache_entry.save(
        update_fields=["status", "started_at", "error_message", "result_count", "updated_at"]
    )

    try:
        filtered_qs = build_filtered_insight_submissions(survey, cache_entry.filters or {})
        submission_ids = list(filtered_qs.values_list("id", flat=True))
        submission_count = len(submission_ids)
        target_language = (cache_entry.filters or {}).get("language") or ""

        payloads = _build_indicator_payloads(survey, submission_ids, submission_count)

        # Start from a clean slate; each child (re)creates its own indicator card.
        SurveyInsightCacheItem.objects.filter(cache_entry=cache_entry).delete()
        cache_entry.submission_count = submission_count
        cache_entry.save(update_fields=["submission_count", "updated_at"])

        if not payloads:
            _mark_cache_done(cache_entry, submission_count, result_count=0, statuses=[])
            return

        # Stagger child dispatch so the per-minute Anthropic token budget isn't
        # hit by every indicator firing at once; each child still runs, retries,
        # and falls back on its own.
        insight_delay = getattr(settings, "ANTHROPIC_INSIGHT_DELAY_SECS", 22)
        header = [
            generate_indicator_insight.s(
                cache_entry_id, payload, target_language, schema_name=schema_name
            ).set(countdown=index * insight_delay)
            for index, payload in enumerate(payloads)
        ]
        callback = finalize_filtered_survey_insights.s(
            cache_entry_id, submission_count, schema_name=schema_name
        )
        chord(header)(callback)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_filtered_survey_insights failed cache_entry_id=%s", cache_entry_id)
        cache_entry.status = SurveyInsightCache.STATUS_FAILED
        cache_entry.error_message = str(exc)
        cache_entry.completed_at = timezone.now()
        cache_entry.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
        raise


def _mark_cache_done(cache_entry, submission_count, result_count, statuses):
    """Flip a cache entry to DONE and summarize any indicators that could not
    produce AI narrative (e.g. the Anthropic usage limit was reached)."""
    empty = [s for s in (statuses or []) if s and not s.get("has_content")]
    message = ""
    if empty:
        message = (
            f"{len(empty)} of {len(statuses)} indicators returned no AI narrative and fell "
            "back to the playbook — the Anthropic API usage limit may have been reached."
        )
    cache_entry.status = SurveyInsightCache.STATUS_DONE
    cache_entry.submission_count = submission_count
    cache_entry.result_count = result_count
    cache_entry.error_message = message
    cache_entry.completed_at = timezone.now()
    cache_entry.save(
        update_fields=[
            "status",
            "submission_count",
            "result_count",
            "error_message",
            "completed_at",
            "updated_at",
        ]
    )


def _has_insight_content(result: dict | None) -> bool:
    """True if *result* carries at least one non-empty narrative field."""
    if not result:
        return False
    return bool(
        result.get("symptoms")
        or result.get("likely_causes")
        or result.get("action_fpo")
        or result.get("action_programme")
        or result.get("action_donor")
        or result.get("high_level_action")
    )


def _get_last_cached_insight(indicator) -> dict | None:
    """Return the most recent SurveyInsightCacheItem with narrative content
    for *indicator*, drawn from any cache entry.

    Used as the fallback when the LLM call fails so the card keeps its
    previous AI-generated text instead of going blank.
    """
    item = (
        SurveyInsightCacheItem.objects.filter(
            indicator=indicator,
            question__isnull=True,
        )
        .exclude(symptoms="")
        .order_by("-id")
        .first()
    )
    if not item:
        return None
    return {
        "symptoms": item.symptoms,
        "likely_causes": item.likely_causes,
        "high_level_action": item.high_level_action,
        "action_fpo": item.action_fpo,
        "action_programme": item.action_programme,
        "action_donor": item.action_donor,
    }


@shared_task(bind=True, name="survey.generate_indicator_insight", acks_late=True)
@tenant_schema_task
def generate_indicator_insight(self, cache_entry_id: int, payload: dict, language: str = ""):
    """Generate (and persist) the insight card for ONE indicator.

    Always writes a card and always returns a small status dict — it never
    raises, so a single indicator's failure can't abort the chord that finalizes
    the whole cache entry.

    Fallback strategy when the LLM call fails (rate limit, API error, non-JSON
    response, no IndicatorResult):
      1. Reuse the last successfully generated insight for this indicator from
         any cache entry (keeps the previous AI narrative instead of blanking
         the card).
      2. If no previous insight exists, the card is written with the correct
         empowerment metrics but empty text fields — the UI shows a
         "Regenerate Insights" message.
    """
    indicator_id = payload.get("indicator_id")
    status = {"indicator_id": indicator_id, "has_content": False}

    try:
        cache_entry = SurveyInsightCache.objects.get(pk=cache_entry_id)
    except SurveyInsightCache.DoesNotExist:
        logger.warning("SurveyInsightCache %s vanished before indicator %s", cache_entry_id, indicator_id)
        return status

    indicator = Indicator.objects.filter(pk=indicator_id).first()
    if indicator is None:
        logger.warning("Indicator %s vanished before its insight ran", indicator_id)
        return status

    question_rows = payload.get("question_rows") or []
    empowerment_context = payload.get("empowerment_context")

    result = None
    try:
        if empowerment_context:
            result = InsightsService().generate_indicator_overview(
                indicator_name=payload.get("indicator_name") or indicator.name,
                indicator_description=payload.get("indicator_description") or "",
                rows=question_rows,
                empowerment_context=empowerment_context,
                language=language or "",
            )
    except Exception:  # noqa: BLE001 — keep the chord alive.
        logger.exception("Indicator insight failed indicator_id=%s", indicator_id)
        result = None

    # LLM failed or returned nothing — reuse the last cached insight for this
    # indicator so the card keeps its previous AI narrative instead of going
    # blank or showing static playbook text.
    if not _has_insight_content(result):
        result = _get_last_cached_insight(indicator)

    result = result or {}
    status["has_content"] = _has_insight_content(result)

    SurveyInsightCacheItem.objects.update_or_create(
        cache_entry=cache_entry,
        indicator=indicator,
        question=None,
        defaults={
            "question_text_snapshot": f"{indicator.name} overview",
            "poor_performance_pct": payload.get("poor_pct") or 0.0,
            "respondents_total": payload.get("respondents_total") or 0,
            "respondents_poor": payload.get("respondents_poor") or 0,
            "symptoms": result.get("symptoms", ""),
            "likely_causes": result.get("likely_causes", ""),
            "high_level_action": result.get("high_level_action", ""),
            "action_fpo": result.get("action_fpo", ""),
            "action_programme": result.get("action_programme", ""),
            "action_donor": result.get("action_donor", ""),
        },
    )
    # Live progress: bump the count so the UI advances as each indicator lands.
    SurveyInsightCache.objects.filter(pk=cache_entry_id).update(
        result_count=F("result_count") + 1, updated_at=timezone.now()
    )
    return status


@shared_task(bind=True, name="survey.finalize_filtered_survey_insights")
@tenant_schema_task
def finalize_filtered_survey_insights(self, results, cache_entry_id: int, submission_count: int):
    """Chord callback: flip the cache entry to DONE once every indicator's child
    task has written its card."""
    try:
        cache_entry = SurveyInsightCache.objects.get(pk=cache_entry_id)
    except SurveyInsightCache.DoesNotExist:
        logger.warning("SurveyInsightCache %s gone at finalize", cache_entry_id)
        return

    result_count = SurveyInsightCacheItem.objects.filter(cache_entry=cache_entry).count()
    _mark_cache_done(cache_entry, submission_count, result_count, results or [])


@shared_task(name="survey.cleanup_old_export_jobs")
def cleanup_old_export_jobs(max_age_days: int = 7):
    from customers.models import Client

    total_removed = 0
    for tenant in Client.objects.filter(is_active=True).only("schema_name"):
        with schema_context(tenant.schema_name):
            total_removed += _cleanup_old_export_jobs_for_current_schema(max_age_days)
    logger.info("cleanup_old_export_jobs removed %s jobs across tenant schemas", total_removed)
    return total_removed


def _cleanup_old_export_jobs_for_current_schema(max_age_days: int = 7):
    """Periodic task (Celery Beat) that removes export job rows and their XLSX files
    older than `max_age_days`. Skips jobs still running/pending so we don't yank a file
    that's actively being built."""
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(days=max_age_days)
    qs = SubmissionExportJob.objects.filter(
        created_at__lt=cutoff,
        status__in=[
            SubmissionExportJob.STATUS_DONE,
            SubmissionExportJob.STATUS_FAILED,
        ],
    )
    removed = 0
    for job in qs:
        if job.file:
            try:
                job.file.delete(save=False)
            except Exception:  # noqa: BLE001 — best effort; don't block the purge
                logger.exception("Failed to delete export file for job %s", job.pk)
        job.delete()
        removed += 1

    report_qs = ReportExportJob.objects.filter(
        created_at__lt=cutoff,
        status__in=[
            ReportExportJob.STATUS_DONE,
            ReportExportJob.STATUS_FAILED,
        ],
    )
    for job in report_qs:
        if job.file:
            try:
                job.file.delete(save=False)
            except Exception:  # noqa: BLE001 — best effort; don't block the purge
                logger.exception("Failed to delete report export file for job %s", job.pk)
        job.delete()
        removed += 1
    logger.info("cleanup_old_export_jobs removed %s jobs older than %s days", removed, max_age_days)
    return removed


def _write_rows(ws, rows):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = rows or [["No data"]]
    max_cols = max((len(row) for row in rows), default=1)
    column_widths = [12] * max_cols

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1565C0", end_color="1565C0", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for row_idx, row in enumerate(rows, start=1):
        ws.append(list(row))
        for idx, value in enumerate(row):
            width = min(max(len(str(value or "")) + 2, 12), 60)
            if width > column_widths[idx]:
                column_widths[idx] = width
        if row_idx == 1:
            for col_idx in range(1, len(row) + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align

    for idx, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    ws.row_dimensions[1].height = 28
    if len(rows) > 1:
        ws.freeze_panes = "A2"


@shared_task(bind=True, name="survey.generate_report_export")
@tenant_schema_task
def generate_report_export(self, job_id: str):
    """Generate an XLSX workbook for the survey report dashboard in a Celery worker."""
    from openpyxl import Workbook

    try:
        job = ReportExportJob.objects.get(pk=job_id)
    except ReportExportJob.DoesNotExist:
        logger.warning("Report export job %s not found", job_id)
        return

    if job.status == ReportExportJob.STATUS_DONE and job.file:
        logger.info("Report export job %s already done, skipping re-run", job_id)
        return

    try:
        job.status = ReportExportJob.STATUS_RUNNING
        job.started_at = timezone.now()
        job.error_message = ""
        job.save(update_fields=["status", "started_at", "error_message"])

        spec = workbook_spec_for_report(job.user, job.report_type, job.filters or {})
        total_steps = max(spec.get("total_steps") or len(spec.get("sheets", [])) or 1, 1)
        job.total_steps = total_steps
        job.completed_steps = 0
        job.save(update_fields=["total_steps", "completed_steps"])

        wb = Workbook()
        first_sheet = True
        for index, (sheet_name, rows) in enumerate(spec["sheets"], start=1):
            ws = wb.active if first_sheet else wb.create_sheet()
            first_sheet = False
            ws.title = sheet_name.replace("\\", " ").replace("/", " ").replace("?", " ").replace("*", " ").replace("[", " ").replace("]", " ").replace(":", " ")[:31] or f"Sheet {index}"
            _write_rows(ws, rows)
            completed = min(index, total_steps)
            ReportExportJob.objects.filter(pk=job.pk).update(completed_steps=completed)

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            wb.save(tmp_path)
            with open(tmp_path, "rb") as fh:
                filename = f"{spec['filename']}_{timezone.localdate().isoformat()}.xlsx"
                job.file.save(filename, File(fh), save=False)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        job.status = ReportExportJob.STATUS_DONE
        job.completed_at = timezone.now()
        job.completed_steps = total_steps
        job.save(update_fields=["status", "completed_at", "completed_steps", "file"])

    except Exception as exc:  # noqa: BLE001
        logger.exception("Report export job %s failed", job_id)
        ReportExportJob.objects.filter(pk=job_id).update(
            status=ReportExportJob.STATUS_FAILED,
            error_message=str(exc)[:2000],
            completed_at=timezone.now(),
        )
        raise


@shared_task(bind=True, name="survey.generate_submissions_export")
@tenant_schema_task
def generate_submissions_export(self, job_id: str):
    """Generate an XLSX file for a SubmissionExportJob.

    Designed to run inside a Celery worker against arbitrary-sized result sets:
    - Uses openpyxl's write_only workbook which streams rows to disk instead of
      buffering in memory.
    - Iterates the queryset with chunk_size=DB_CHUNK_SIZE.
    - Updates processed_rows every PROGRESS_UPDATE_EVERY rows so the UI can show a
      live progress bar without flooding the database with writes.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    try:
        job = SubmissionExportJob.objects.get(pk=job_id)
    except SubmissionExportJob.DoesNotExist:
        logger.warning("Export job %s not found", job_id)
        return

    # Idempotency: if this task was re-delivered after acks_late and the previous run
    # already produced a file, don't re-run and overwrite.
    if job.status == SubmissionExportJob.STATUS_DONE and job.file:
        logger.info("Export job %s already done, skipping re-run", job_id)
        return

    try:
        job.status = SubmissionExportJob.STATUS_RUNNING
        job.started_at = timezone.now()
        job.error_message = ""
        job.save(update_fields=["status", "started_at", "error_message"])

        filters = job.filters or {}
        display_mode = (filters.get("display") or "labels").lower()

        survey = job.survey
        qs = build_submissions_queryset(job.user, filters, survey=survey).prefetch_related(
            "answers",
            "answers__question",
            "answers__selected_options",
            "indicator_results",
            "indicator_results__indicator",
        ).select_related("survey", "user")

        total = qs.count()
        job.total_rows = total
        job.processed_rows = 0
        job.save(update_fields=["total_rows", "processed_rows"])

        # Without a survey filter (cross-survey export) we only include submissions
        # whose survey is consistent with the caller's access; question columns still
        # need a concrete survey, so we use the survey attached to the job or
        # refuse to run with a generic format.
        if survey is None:
            # Cross-survey export: just include submission metadata + status.
            # Per-question columns are survey-specific so aren't sensible here.
            questions = []
            indicators = []
        else:
            questions = list(
                survey.questions.select_related("page")
                .prefetch_related("options")
                .order_by("page__order", "order", "id")
            )
            indicators = list(survey.indicators.filter(is_active=True))

        questions_with_other = set()
        for q in questions:
            if q.question_type in ("radio", "checkbox", "dropdown"):
                if any(opt.text.strip().lower() == "other" for opt in q.options.all()):
                    questions_with_other.add(q.id)

        columns = _collect_columns(questions, questions_with_other)
        headers = _build_header(columns, indicators)

        # Streaming workbook (write_only) — rows are flushed to disk; memory stays flat.
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title="Submissions")

        # Title banner row — must visually span the entire row like the synchronous download does.
        # write_only mode can't use ws.merge_cells(), so instead every cell gets the same green/blue
        # background and only the first carries the "Data in Labels"/"Data in Values" label.
        display_label = "Data in Labels" if display_mode == "labels" else "Data in Values"
        banner_fill_hex = "2E7D32" if display_mode == "labels" else "1565C0"
        from openpyxl.cell import WriteOnlyCell

        banner_font = Font(bold=True, size=14, color="FFFFFF")
        banner_fill = PatternFill(start_color=banner_fill_hex, end_color=banner_fill_hex, fill_type="solid")
        banner_align = Alignment(horizontal="center", vertical="center")

        banner_row = []
        total_cols = max(len(headers), 1)
        for i in range(total_cols):
            cell = WriteOnlyCell(ws, value=display_label if i == 0 else "")
            cell.font = banner_font
            cell.fill = banner_fill
            cell.alignment = banner_align
            banner_row.append(cell)
        ws.append(banner_row)
        # Match the synchronous download's 30px banner height.
        ws.row_dimensions[1].height = 30

        # Header row (row 2) — bold on light blue, same as synchronous download.
        header_cells = []
        for header_text in headers:
            c = WriteOnlyCell(ws, value=header_text)
            c.font = Font(bold=True)
            c.fill = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
            c.alignment = Alignment(horizontal="center", wrap_text=True)
            header_cells.append(c)
        ws.append(header_cells)
        ws.row_dimensions[2].height = 40

        ist_tz = timezone.get_fixed_timezone(330)

        processed = 0
        for submission in qs.iterator(chunk_size=DB_CHUNK_SIZE):
            if survey is None:
                ws.append([
                    submission.pk,
                    submission.submitted_at.astimezone(ist_tz).strftime("%b %d, %Y %I:%M %p"),
                    submission.get_submitter_display(),
                    submission.get_status_display(),
                    submission.app_version or "",
                ])
            else:
                ws.append(_row_for(submission, columns, indicators, display_mode, ist_tz))
            processed += 1
            if processed % PROGRESS_UPDATE_EVERY == 0:
                SubmissionExportJob.objects.filter(pk=job.pk).update(processed_rows=processed)

        # Flush the final processed count so the UI can reach 100% before the file link appears.
        SubmissionExportJob.objects.filter(pk=job.pk).update(processed_rows=processed)

        # Save workbook to a temp file, then move contents into the job's FileField.
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            wb.save(tmp_path)
            with open(tmp_path, "rb") as fh:
                filename_survey = (survey.title if survey else "submissions").replace(" ", "_")
                filename = f"{filename_survey}_submissions_{display_mode}.xlsx"
                job.file.save(filename, File(fh), save=False)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        job.status = SubmissionExportJob.STATUS_DONE
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "file"])

    except Exception as exc:  # noqa: BLE001 — we want to surface any failure to the UI
        logger.exception("Export job %s failed", job_id)
        SubmissionExportJob.objects.filter(pk=job_id).update(
            status=SubmissionExportJob.STATUS_FAILED,
            error_message=str(exc)[:2000],
            completed_at=timezone.now(),
        )
        raise
