from django.core.management.base import BaseCommand
from django.conf import settings
from survey.models import Survey, Question, QuestionOption, QuestionTranslation, QuestionOptionTranslation
from survey.translation_service import TranslationService
import argparse
from django.db import transaction

class Command(BaseCommand):
    help = 'Translate an entire survey to a target language using OpenAI API'

    def add_arguments(self, parser):
        parser.add_argument('survey_id', type=int, help='ID of the survey to translate')
        parser.add_argument('target_language', type=str, help='Target language code (e.g., hi, es, fr)')
        parser.add_argument('--source-language', type=str, default='en', 
                          help='Source language code (default: en)')
        parser.add_argument('--dry-run', action='store_true', 
                          help='Show what would be translated without saving')
        parser.add_argument('--skip-options', action='store_true',
                          help='Skip translating question options')
        parser.add_argument('--batch-size', type=int, default=10,
                          help='Number of items to process before committing (default: 10)')

    def handle(self, *args, **options):
        survey_id = options['survey_id']
        target_language = options['target_language']
        source_language = options['source_language']
        dry_run = options['dry_run']
        skip_options = options['skip_options']
        batch_size = options['batch_size']

        # Validate languages
        valid_languages = dict(settings.LANGUAGES)
        if target_language not in valid_languages:
            self.stderr.write(
                self.style.ERROR(f"Invalid target language: {target_language}. "
                               f"Valid languages: {', '.join(valid_languages.keys())}")
            )
            return

        if source_language not in valid_languages:
            self.stderr.write(
                self.style.ERROR(f"Invalid source language: {source_language}. "
                               f"Valid languages: {', '.join(valid_languages.keys())}")
            )
            return

        # Get the survey
        try:
            survey = Survey.objects.get(pk=survey_id)
        except Survey.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Survey with ID {survey_id} does not exist"))
            return

        self.stdout.write(
            self.style.SUCCESS(f"Translating survey: '{survey.title}' (ID: {survey_id})")
        )
        self.stdout.write(f"From: {valid_languages.get(source_language, source_language)}")
        self.stdout.write(f"To: {valid_languages.get(target_language, target_language)}")
        self.stdout.write(f"Dry run: {dry_run}")
        self.stdout.write(f"Skip options: {skip_options}")
        self.stdout.write("")

        # Initialize translation service
        translation_service = TranslationService()

        # Get all questions with options
        questions = survey.questions.all().prefetch_related('options')
        total_items = sum(1 + (0 if skip_options else question.options.count()) for question in questions)
        
        self.stdout.write(f"Found {len(questions)} questions with {total_items} total items to translate")
        self.stdout.write("")

        translated_count = 0
        error_count = 0

        with transaction.atomic():
            for i, question in enumerate(questions):
                # Check if question already has a translation for this language
                existing_translation = QuestionTranslation.objects.filter(
                    question=question, language=target_language
                ).first()
                
                if existing_translation and existing_translation.text.strip():
                    self.stdout.write(f"Question #{i+1}: Already translated - skipping")
                    translated_count += 1
                    continue
                
                # Translate question
                try:
                    self.stdout.write(f"Translating question #{i+1}: '{question.text[:50]}...'")
                    
                    translated_text = translation_service.translate_text(
                        question.text, target_language, source_language
                    )
                    
                    if translated_text:
                        if not dry_run:
                            translation, created = QuestionTranslation.objects.get_or_create(
                                question=question,
                                language=target_language,
                                defaults={"text": translated_text}
                            )
                            
                            if not created:
                                translation.text = translated_text
                                translation.save()
                            
                            action = "Created" if created else "Updated"
                            self.stdout.write(
                                self.style.SUCCESS(f"  ✓ {action} question translation")
                            )
                        else:
                            self.stdout.write(
                                self.style.WARNING(f"  ✓ Would translate to: '{translated_text[:50]}...'")
                            )
                        
                        translated_count += 1
                    else:
                        self.stderr.write(
                            self.style.ERROR("  ✗ Translation failed for question")
                        )
                        error_count += 1

                except Exception as e:
                    self.stderr.write(
                        self.style.ERROR(f"  ✗ Error translating question: {str(e)}")
                    )
                    error_count += 1

                # Translate options if not skipped
                if not skip_options:
                    for j, option in enumerate(question.options.all()):
                        # Check if option already has a translation for this language
                        existing_option_translation = QuestionOptionTranslation.objects.filter(
                            option=option, language=target_language
                        ).first()
                        
                        if existing_option_translation and existing_option_translation.text.strip():
                            self.stdout.write(f"  Option #{j+1}: Already translated - skipping")
                            translated_count += 1
                            continue
                        
                        try:
                            self.stdout.write(f"  Translating option #{j+1}: '{option.text[:30]}...'")
                            
                            translated_option_text = translation_service.translate_text(
                                option.text, target_language, source_language
                            )
                            
                            if translated_option_text:
                                if not dry_run:
                                    option_translation, created = QuestionOptionTranslation.objects.get_or_create(
                                        option=option,
                                        language=target_language,
                                        defaults={"text": translated_option_text}
                                    )
                                    
                                    if not created:
                                        option_translation.text = translated_option_text
                                        option_translation.save()
                                    
                                    action = "Created" if created else "Updated"
                                    self.stdout.write(
                                        self.style.SUCCESS(f"    ✓ {action} option translation")
                                    )
                                else:
                                    self.stdout.write(
                                        self.style.WARNING(f"    ✓ Would translate to: '{translated_option_text[:30]}...'")
                                    )
                                
                                translated_count += 1
                            else:
                                self.stderr.write(
                                    self.style.ERROR("    ✗ Translation failed for option")
                                )
                                error_count += 1

                        except Exception as e:
                            self.stderr.write(
                                self.style.ERROR(f"    ✗ Error translating option: {str(e)}")
                            )
                            error_count += 1

                # Save progress for batch processing (commit happens automatically at end of atomic block)
                if not dry_run and (i + 1) % batch_size == 0:
                    self.stdout.write(f"Processed batch of {batch_size} questions")

                self.stdout.write("")

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Translation Summary:"))
        self.stdout.write(f"Total items processed: {total_items}")
        self.stdout.write(f"Successfully translated: {translated_count}")
        self.stdout.write(f"Errors: {error_count}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were saved"))
        else:
            self.stdout.write(self.style.SUCCESS("All translations completed and saved"))