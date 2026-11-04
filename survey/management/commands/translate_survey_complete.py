from django.core.management.base import BaseCommand
from django.conf import settings
from survey.models import (
    Survey,
    Question,
    QuestionOption,
    SurveyPage,
    QuestionTranslation,
    QuestionOptionTranslation,
    SurveyPageTranslation,
    QuestionHintTranslation,
    SurveyConsentTranslation,
)
from survey.translation_service import TranslationService
from django.db import transaction
import argparse


class Command(BaseCommand):
    help = "Translate an entire survey including pages, questions, options, hints, and consent text using OpenAI API"

    def add_arguments(self, parser):
        parser.add_argument("survey_id", type=int, help="ID of the survey to translate")
        parser.add_argument("target_language", type=str, help="Target language code (e.g., hi, es, fr)")
        parser.add_argument("--source-language", type=str, default="en", help="Source language code (default: en)")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be translated without saving")
        parser.add_argument("--skip-pages", action="store_true", help="Skip translating page titles and descriptions")
        parser.add_argument("--skip-questions", action="store_true", help="Skip translating questions")
        parser.add_argument("--skip-options", action="store_true", help="Skip translating question options")
        parser.add_argument("--skip-hints", action="store_true", help="Skip translating question hints")
        parser.add_argument("--skip-consent", action="store_true", help="Skip translating survey consent text")
        parser.add_argument(
            "--batch-size", type=int, default=5, help="Number of items to process before committing (default: 5)"
        )
        parser.add_argument("--overwrite", action="store_true", help="Overwrite existing translations")

    def handle(self, *args, **options):
        survey_id = options["survey_id"]
        target_language = options["target_language"]
        source_language = options["source_language"]
        dry_run = options["dry_run"]
        skip_pages = options["skip_pages"]
        skip_questions = options["skip_questions"]
        skip_options = options["skip_options"]
        skip_hints = options["skip_hints"]
        skip_consent = options["skip_consent"]
        batch_size = options["batch_size"]
        overwrite = options["overwrite"]

        # Validate languages
        valid_languages = dict(settings.LANGUAGES)
        if target_language not in valid_languages:
            self.stderr.write(
                self.style.ERROR(
                    f"Invalid target language: {target_language}. "
                    f"Valid languages: {', '.join(valid_languages.keys())}"
                )
            )
            return

        if source_language not in valid_languages:
            self.stderr.write(
                self.style.ERROR(
                    f"Invalid source language: {source_language}. "
                    f"Valid languages: {', '.join(valid_languages.keys())}"
                )
            )
            return

        # Get the survey
        try:
            survey = Survey.objects.get(pk=survey_id)
        except Survey.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Survey with ID {survey_id} does not exist"))
            return

        self.stdout.write(self.style.SUCCESS(f"Translating survey: '{survey.title}' (ID: {survey_id})"))
        self.stdout.write(f"From: {valid_languages.get(source_language, source_language)}")
        self.stdout.write(f"To: {valid_languages.get(target_language, target_language)}")
        self.stdout.write(f"Dry run: {dry_run}")
        self.stdout.write(f"Overwrite existing: {overwrite}")
        self.stdout.write("")

        # Initialize translation service using the same OpenAI client as FPO processor
        translation_service = TranslationService()

        # Translation counters
        total_translated = 0
        total_errors = 0
        translation_stats = {
            "pages": {"translated": 0, "errors": 0, "skipped": 0},
            "questions": {"translated": 0, "errors": 0, "skipped": 0},
            "options": {"translated": 0, "errors": 0, "skipped": 0},
            "hints": {"translated": 0, "errors": 0, "skipped": 0},
            "consent": {"translated": 0, "errors": 0, "skipped": 0},
        }

        # 1. Translate Survey Consent Text
        if not skip_consent and survey.consent_text:
            self.stdout.write(self.style.SUCCESS("=== Translating Survey Consent Text ==="))
            result = self._translate_consent_text(
                survey, target_language, source_language, translation_service, dry_run, overwrite
            )
            translation_stats["consent"]["translated"] += result["translated"]
            translation_stats["consent"]["errors"] += result["errors"]
            translation_stats["consent"]["skipped"] += result["skipped"]
            total_translated += result["translated"]
            total_errors += result["errors"]

        # 2. Translate Survey Pages
        if not skip_pages:
            self.stdout.write(self.style.SUCCESS("=== Translating Survey Pages ==="))
            pages = survey.pages.all()
            for i, page in enumerate(pages):
                result = self._translate_page(
                    page, target_language, source_language, translation_service, dry_run, overwrite
                )
                translation_stats["pages"]["translated"] += result["translated"]
                translation_stats["pages"]["errors"] += result["errors"]
                translation_stats["pages"]["skipped"] += result["skipped"]
                total_translated += result["translated"]
                total_errors += result["errors"]

        # 3. Translate Questions and related content
        if not skip_questions:
            self.stdout.write(self.style.SUCCESS("=== Translating Survey Questions ==="))
            questions = survey.questions.all().prefetch_related("options")

            for i, question in enumerate(questions):
                # Translate question text
                result = self._translate_question(
                    question, target_language, source_language, translation_service, dry_run, overwrite
                )
                translation_stats["questions"]["translated"] += result["translated"]
                translation_stats["questions"]["errors"] += result["errors"]
                translation_stats["questions"]["skipped"] += result["skipped"]
                total_translated += result["translated"]
                total_errors += result["errors"]

                # Translate question hint if present and not skipped
                if not skip_hints and question.hint:
                    result = self._translate_question_hint(
                        question, target_language, source_language, translation_service, dry_run, overwrite
                    )
                    translation_stats["hints"]["translated"] += result["translated"]
                    translation_stats["hints"]["errors"] += result["errors"]
                    translation_stats["hints"]["skipped"] += result["skipped"]
                    total_translated += result["translated"]
                    total_errors += result["errors"]

                # Translate question options if not skipped
                if not skip_options and question.requires_options():
                    for j, option in enumerate(question.options.all()):
                        result = self._translate_question_option(
                            option, target_language, source_language, translation_service, dry_run, overwrite
                        )
                        translation_stats["options"]["translated"] += result["translated"]
                        translation_stats["options"]["errors"] += result["errors"]
                        translation_stats["options"]["skipped"] += result["skipped"]
                        total_translated += result["translated"]
                        total_errors += result["errors"]

                # Progress indicator
                if (i + 1) % batch_size == 0:
                    self.stdout.write(f"Processed batch of {batch_size} questions")

        # Final Summary
        self._print_summary(translation_stats, total_translated, total_errors, dry_run)

    def _translate_consent_text(
        self, survey, target_language, source_language, translation_service, dry_run, overwrite
    ):
        """Translate survey consent text"""
        result = {"translated": 0, "errors": 0, "skipped": 0}

        try:
            # Check if translation already exists
            existing_translation = SurveyConsentTranslation.objects.filter(
                survey=survey, language=target_language
            ).first()

            if existing_translation and existing_translation.consent_text.strip() and not overwrite:
                self.stdout.write(f"Consent text: Already translated - skipping")
                result["skipped"] += 1
                return result

            self.stdout.write(f"Translating consent text...")

            translated_text = translation_service.translate_text(survey.consent_text, target_language, source_language)

            if translated_text:
                if not dry_run:
                    consent_translation, created = SurveyConsentTranslation.objects.get_or_create(
                        survey=survey, language=target_language, defaults={"consent_text": translated_text}
                    )

                    if not created:
                        consent_translation.consent_text = translated_text
                        consent_translation.save()

                    action = "Created" if created else "Updated"
                    self.stdout.write(self.style.SUCCESS(f"  ✓ {action} consent translation"))
                else:
                    self.stdout.write(self.style.WARNING(f"  ✓ Would translate consent text"))

                result["translated"] += 1
            else:
                self.stderr.write(self.style.ERROR("  ✗ Translation failed for consent text"))
                result["errors"] += 1

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"  ✗ Error translating consent text: {str(e)}"))
            result["errors"] += 1

        return result

    def _translate_page(self, page, target_language, source_language, translation_service, dry_run, overwrite):
        """Translate survey page title and description"""
        result = {"translated": 0, "errors": 0, "skipped": 0}

        try:
            # Check if translation already exists
            existing_translation = SurveyPageTranslation.objects.filter(page=page, language=target_language).first()

            if existing_translation and not overwrite:
                # Check if both title and description are translated
                has_title = existing_translation.title and existing_translation.title.strip()
                has_description = not page.description or (
                    existing_translation.description and existing_translation.description.strip()
                )

                if has_title and has_description:
                    self.stdout.write(f"Page {page.order + 1}: Already translated - skipping")
                    result["skipped"] += 1
                    return result

            self.stdout.write(f"Translating Page {page.order + 1}...")

            # Translate page title
            translated_title = None
            if page.title:
                translated_title = translation_service.translate_text(page.title, target_language, source_language)
                if translated_title:
                    self.stdout.write(self.style.SUCCESS(f"  ✓ Translated page title"))
                else:
                    self.stderr.write(self.style.ERROR("  ✗ Failed to translate page title"))
                    result["errors"] += 1

            # Translate page description
            translated_description = None
            if page.description:
                translated_description = translation_service.translate_text(
                    page.description, target_language, source_language
                )
                if translated_description:
                    self.stdout.write(self.style.SUCCESS(f"  ✓ Translated page description"))
                else:
                    self.stderr.write(self.style.ERROR("  ✗ Failed to translate page description"))
                    result["errors"] += 1

            # Save translations
            if not dry_run and (translated_title or translated_description):
                page_translation, created = SurveyPageTranslation.objects.get_or_create(
                    page=page,
                    language=target_language,
                    defaults={
                        "title": translated_title or page.title,
                        "description": translated_description or page.description,
                    },
                )

                if not created:
                    if translated_title:
                        page_translation.title = translated_title
                    if translated_description:
                        page_translation.description = translated_description
                    page_translation.save()

                action = "Created" if created else "Updated"
                self.stdout.write(self.style.SUCCESS(f"  ✓ {action} page translation"))

                if translated_title or translated_description:
                    result["translated"] += 1
            elif dry_run and (translated_title or translated_description):
                self.stdout.write(self.style.WARNING(f"  ✓ Would translate page"))
                result["translated"] += 1

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"  ✗ Error translating page: {str(e)}"))
            result["errors"] += 1

        return result

    def _translate_question(self, question, target_language, source_language, translation_service, dry_run, overwrite):
        """Translate question text"""
        result = {"translated": 0, "errors": 0, "skipped": 0}

        try:
            # Check if translation already exists
            existing_translation = QuestionTranslation.objects.filter(
                question=question, language=target_language
            ).first()

            if existing_translation and existing_translation.text.strip() and not overwrite:
                self.stdout.write(f"Question '{question.text[:50]}...': Already translated - skipping")
                result["skipped"] += 1
                return result

            self.stdout.write(f"Translating question: '{question.text[:50]}...'")

            translated_text = translation_service.translate_text(question.text, target_language, source_language)

            if translated_text:
                if not dry_run:
                    question_translation, created = QuestionTranslation.objects.get_or_create(
                        question=question, language=target_language, defaults={"text": translated_text}
                    )

                    if not created:
                        question_translation.text = translated_text
                        question_translation.save()

                    action = "Created" if created else "Updated"
                    self.stdout.write(self.style.SUCCESS(f"  ✓ {action} question translation"))
                else:
                    self.stdout.write(self.style.WARNING(f"  ✓ Would translate question"))

                result["translated"] += 1
            else:
                self.stderr.write(self.style.ERROR("  ✗ Translation failed for question"))
                result["errors"] += 1

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"  ✗ Error translating question: {str(e)}"))
            result["errors"] += 1

        return result

    def _translate_question_hint(
        self, question, target_language, source_language, translation_service, dry_run, overwrite
    ):
        """Translate question hint text"""
        result = {"translated": 0, "errors": 0, "skipped": 0}

        try:
            # Check if translation already exists
            existing_translation = QuestionHintTranslation.objects.filter(
                question=question, language=target_language
            ).first()

            if existing_translation and existing_translation.hint_text.strip() and not overwrite:
                self.stdout.write(f"  Hint: Already translated - skipping")
                result["skipped"] += 1
                return result

            self.stdout.write(f"  Translating hint...")

            translated_hint = translation_service.translate_text(question.hint, target_language, source_language)

            if translated_hint:
                if not dry_run:
                    hint_translation, created = QuestionHintTranslation.objects.get_or_create(
                        question=question, language=target_language, defaults={"hint_text": translated_hint}
                    )

                    if not created:
                        hint_translation.hint_text = translated_hint
                        hint_translation.save()

                    action = "Created" if created else "Updated"
                    self.stdout.write(self.style.SUCCESS(f"    ✓ {action} hint translation"))
                else:
                    self.stdout.write(self.style.WARNING(f"    ✓ Would translate hint"))

                result["translated"] += 1
            else:
                self.stderr.write(self.style.ERROR("    ✗ Translation failed for hint"))
                result["errors"] += 1

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"    ✗ Error translating hint: {str(e)}"))
            result["errors"] += 1

        return result

    def _translate_question_option(
        self, option, target_language, source_language, translation_service, dry_run, overwrite
    ):
        """Translate question option text"""
        result = {"translated": 0, "errors": 0, "skipped": 0}

        try:
            # Check if translation already exists
            existing_translation = QuestionOptionTranslation.objects.filter(
                option=option, language=target_language
            ).first()

            if existing_translation and existing_translation.text.strip() and not overwrite:
                self.stdout.write(f"    Option '{option.text[:30]}...': Already translated - skipping")
                result["skipped"] += 1
                return result

            self.stdout.write(f"    Translating option: '{option.text[:30]}...'")

            translated_text = translation_service.translate_text(option.text, target_language, source_language)

            if translated_text:
                if not dry_run:
                    option_translation, created = QuestionOptionTranslation.objects.get_or_create(
                        option=option, language=target_language, defaults={"text": translated_text}
                    )

                    if not created:
                        option_translation.text = translated_text
                        option_translation.save()

                    action = "Created" if created else "Updated"
                    self.stdout.write(self.style.SUCCESS(f"      ✓ {action} option translation"))
                else:
                    self.stdout.write(self.style.WARNING(f"      ✓ Would translate option"))

                result["translated"] += 1
            else:
                self.stderr.write(self.style.ERROR("      ✗ Translation failed for option"))
                result["errors"] += 1

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"      ✗ Error translating option: {str(e)}"))
            result["errors"] += 1

        return result

    def _print_summary(self, translation_stats, total_translated, total_errors, dry_run):
        """Print translation summary"""
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== TRANSLATION SUMMARY ==="))

        for category, stats in translation_stats.items():
            if stats["translated"] > 0 or stats["errors"] > 0 or stats["skipped"] > 0:
                self.stdout.write(
                    f"{category.title()}: {stats['translated']} translated, "
                    f"{stats['errors']} errors, {stats['skipped']} skipped"
                )

        self.stdout.write("")
        self.stdout.write(f"Total items translated: {total_translated}")
        self.stdout.write(f"Total errors: {total_errors}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were saved"))
        else:
            self.stdout.write(self.style.SUCCESS("All translations completed and saved"))
