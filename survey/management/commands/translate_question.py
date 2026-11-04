from django.core.management.base import BaseCommand
from django.conf import settings
from survey.models import Question, QuestionTranslation
from survey.translation_service import TranslationService
import argparse

class Command(BaseCommand):
    help = 'Translate a specific question to a target language using OpenAI API'

    def add_arguments(self, parser):
        parser.add_argument('question_id', type=int, help='ID of the question to translate')
        parser.add_argument('target_language', type=str, help='Target language code (e.g., hi, es, fr)')
        parser.add_argument('--source-language', type=str, default='en', 
                          help='Source language code (default: en)')
        parser.add_argument('--dry-run', action='store_true', 
                          help='Show what would be translated without saving')

    def handle(self, *args, **options):
        question_id = options['question_id']
        target_language = options['target_language']
        source_language = options['source_language']
        dry_run = options['dry_run']

        # Validate target language
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

        # Get the question
        try:
            question = Question.objects.get(pk=question_id)
        except Question.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Question with ID {question_id} does not exist"))
            return

        # Check if question already has a translation for this language
        existing_translation = QuestionTranslation.objects.filter(
            question=question, language=target_language
        ).first()
        
        if existing_translation and existing_translation.text.strip():
            self.stdout.write(
                self.style.SUCCESS(f"Question #{question_id} already translated to {target_language}")
            )
            self.stdout.write(f"Existing translation: '{existing_translation.text}'")
            if not dry_run:
                self.stdout.write(self.style.WARNING("Skipping - translation already exists"))
            else:
                self.stdout.write(self.style.WARNING("Dry run - would skip existing translation"))
            return

        self.stdout.write(
            self.style.SUCCESS(f"Translating question #{question_id}: '{question.text}'")
        )
        self.stdout.write(f"From: {valid_languages.get(source_language, source_language)}")
        self.stdout.write(f"To: {valid_languages.get(target_language, target_language)}")

        # Initialize translation service
        translation_service = TranslationService()

        # Translate the question
        try:
            translated_text = translation_service.translate_text(
                question.text, target_language, source_language
            )
            
            if translated_text:
                self.stdout.write(
                    self.style.SUCCESS(f"Translated text: '{translated_text}'")
                )

                if not dry_run:
                    # Save the translation
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
                        self.style.SUCCESS(f"{action} translation for language '{target_language}'")
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING("Dry run - translation not saved")
                    )
            else:
                self.stderr.write(
                    self.style.ERROR("Translation failed - no text returned from translation service")
                )

        except Exception as e:
            self.stderr.write(
                self.style.ERROR(f"Translation error: {str(e)}")
            )