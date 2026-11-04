"""Generate LLM-backed insights for surveys.

Run nightly via cron:
    0 2 * * * cd /app && uv run python manage.py generate_survey_insights

Idempotent: a same-day re-run without --force is a no-op for surveys that
already have today's rows.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from survey.insights_guidance import find_guidance
from survey.insights_metrics import compute_question_poor_rates
from survey.insights_service import InsightsService
from survey.models import Survey, SurveyInsight

# Insights are currently only meaningful for the NAARI (Original) survey —
# it is the only one with full indicator scripts and a statistically-useful
# number of completed submissions. Keep this list explicit; widen only when
# another survey has matching indicator coverage.
INSIGHT_SURVEY_IDS = [6]


class Command(BaseCommand):
    help = "Generate Symptoms / Likely causes / High-level actions for under-performing survey questions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--survey-id",
            type=int,
            default=None,
            help=f"Override the default survey scope (allowed: {INSIGHT_SURVEY_IDS}).",
        )
        parser.add_argument("--force", action="store_true", help="Regenerate even if today's rows already exist.")
        parser.add_argument(
            "--min-poor-pct",
            type=float,
            default=50.0,
            help="Flag questions at/above this percent of poor respondents (default 50).",
        )

    def handle(self, *args, **opts):
        survey_id = opts.get("survey_id")
        force = opts.get("force")
        min_pct = opts.get("min_poor_pct")
        today = timezone.now().date()

        allowed_ids = [survey_id] if survey_id else INSIGHT_SURVEY_IDS
        surveys = Survey.objects.filter(id__in=allowed_ids)

        service = InsightsService()
        total_generated = 0
        total_skipped = 0

        for survey in surveys:
            already = SurveyInsight.objects.filter(survey=survey, generated_on=today).exists()
            if already and not force:
                self.stdout.write(f"[skip] survey {survey.id} ({survey.title!r}) — insights already generated today")
                total_skipped += 1
                continue

            uncovered: list = []
            rows = compute_question_poor_rates(
                survey, min_poor_pct=min_pct, uncovered_log=uncovered
            )
            if uncovered:
                self.stdout.write(self.style.WARNING(
                    f"[warn] survey {survey.id}: {len(uncovered)} questions have no override rule "
                    f"and were skipped — add them to insights_overrides.py:"
                ))
                for qid, qtext in uncovered:
                    self.stdout.write(f"        q={qid}  {qtext!r}")
            if not rows:
                self.stdout.write(f"[ok]   survey {survey.id} ({survey.title!r}) — no flagged questions")
                continue

            if force:
                SurveyInsight.objects.filter(survey=survey, generated_on=today).delete()

            for row in rows:
                indicator = row["indicator"]
                question = row["question"]
                guidance = find_guidance(indicator.name, question.text)
                result = service.generate_insight(
                    indicator_name=indicator.name,
                    indicator_description=indicator.description or "",
                    question_text=question.text,
                    poor_pct=row["poor_pct"],
                    guidance_snippet=guidance,
                    data_context=row.get("data_context"),
                    respondents_total=row.get("respondents_total"),
                    respondents_poor=row.get("respondents_poor"),
                )
                if not result:
                    self.stdout.write(self.style.WARNING(
                        f"  ! no result for indicator={indicator.id} q={question.id}, skipping"
                    ))
                    continue

                SurveyInsight.objects.update_or_create(
                    survey=survey,
                    indicator=indicator,
                    question=question,
                    generated_on=today,
                    defaults={
                        "question_text_snapshot": question.text,
                        "poor_performance_pct": row["poor_pct"],
                        "respondents_total": row["respondents_total"],
                        "respondents_poor": row["respondents_poor"],
                        "symptoms": result.get("symptoms", ""),
                        "likely_causes": result.get("likely_causes", ""),
                        "high_level_action": result.get("high_level_action", ""),
                        "action_fpo": result.get("action_fpo", ""),
                        "action_programme": result.get("action_programme", ""),
                        "action_donor": result.get("action_donor", ""),
                    },
                )
                total_generated += 1
                self.stdout.write(
                    f"  + survey={survey.id} indicator={indicator.name!r} "
                    f"q={question.id} pct={row['poor_pct']:.1f}%"
                )
                # Space out requests so the free-tier rate limiter doesn't 429.
                time.sleep(1.0)

        self.stdout.write(self.style.SUCCESS(
            f"Done. generated={total_generated} skipped_surveys={total_skipped}"
        ))
