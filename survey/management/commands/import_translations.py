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
    SurveyConsentTranslation
)
import pandas as pd
from django.db import transaction
import os


class Command(BaseCommand):
    help = 'Import translations from XLSX file to update survey translations'

    def add_arguments(self, parser):
        parser.add_argument('survey_id', type=int, help='ID of the survey to import translations for')
        parser.add_argument('input_file', type=str, help='Input XLSX file path')
        parser.add_argument('--dry-run', action='store_true', 
                          help='Show what would be imported without saving')
        parser.add_argument('--overwrite', action='store_true',
                          help='Overwrite existing translations')
        parser.add_argument('--language', type=str, default='hi',
                          help='Language code to import translations for (default: hi)')

    def handle(self, *args, **options):
        survey_id = options['survey_id']
        input_file = options['input_file']
        dry_run = options['dry_run']
        overwrite = options['overwrite']
        target_language = options['language']

        # Validate input file
        if not os.path.exists(input_file):
            self.stderr.write(self.style.ERROR(f"Input file does not exist: {input_file}"))
            return

        # Get the survey
        try:
            survey = Survey.objects.get(pk=survey_id)
        except Survey.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Survey with ID {survey_id} does not exist"))
            return

        self.stdout.write(
            self.style.SUCCESS(f"Importing translations for survey: '{survey.title}' (ID: {survey_id})")
        )
        self.stdout.write(f"Input file: {input_file}")
        self.stdout.write(f"Language: {target_language}")
        self.stdout.write(f"Dry run: {dry_run}")
        self.stdout.write(f"Overwrite: {overwrite}")
        self.stdout.write("")

        # Import counters
        import_stats = {
            'questions': {'imported': 0, 'errors': 0, 'skipped': 0},
            'hints': {'imported': 0, 'errors': 0, 'skipped': 0},
            'options': {'imported': 0, 'errors': 0, 'skipped': 0},
            'pages': {'imported': 0, 'errors': 0, 'skipped': 0},
            'consent': {'imported': 0, 'errors': 0, 'skipped': 0},
        }

        with transaction.atomic():
            # Import each type of translation from separate sheets
            try:
                self._import_question_translations(survey, input_file, target_language, 
                                                 dry_run, overwrite, import_stats)
                self._import_hint_translations(survey, input_file, target_language, 
                                             dry_run, overwrite, import_stats)
                self._import_option_translations(survey, input_file, target_language, 
                                               dry_run, overwrite, import_stats)
                self._import_page_translations(survey, input_file, target_language, 
                                             dry_run, overwrite, import_stats)
                self._import_consent_translations(survey, input_file, target_language, 
                                                dry_run, overwrite, import_stats)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Error reading XLSX file: {str(e)}"))
                return

        # Final Summary
        self._print_summary(import_stats, dry_run)

    def _import_question_translations(self, survey, input_file, target_language, 
                                    dry_run, overwrite, import_stats):
        """Import question translations from XLSX"""
        self.stdout.write("Importing question translations...")
        
        try:
            df = pd.read_excel(input_file, sheet_name='Question_Translations')
        except ValueError:
            self.stdout.write("  ⚠ No Question_Translations sheet found")
            return
        
        for index, row in df.iterrows():
            try:
                question_id = int(row['question_id'])
                translated_text = str(row['translated_text']).strip()
                
                if not translated_text:
                    continue
                
                # Get the question
                try:
                    question = Question.objects.get(id=question_id, survey=survey)
                except Question.DoesNotExist:
                    self.stderr.write(f"  ✗ Question ID {question_id} not found")
                    import_stats['questions']['errors'] += 1
                    continue
                
                # Check if translation already exists
                existing_translation = QuestionTranslation.objects.filter(
                    question=question, language=target_language
                ).first()
                
                if existing_translation and not overwrite:
                    self.stdout.write(f"  ⚠ Question {question_id}: Translation exists - skipping")
                    import_stats['questions']['skipped'] += 1
                    continue
                
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
                    self.stdout.write(f"  ✓ Question {question_id}: {action} translation")
                else:
                    self.stdout.write(f"  ✓ Question {question_id}: Would import translation")
                
                import_stats['questions']['imported'] += 1
                
            except Exception as e:
                self.stderr.write(f"  ✗ Error importing question {row.get('question_id', 'unknown')}: {str(e)}")
                import_stats['questions']['errors'] += 1

    def _import_hint_translations(self, survey, input_file, target_language, 
                                dry_run, overwrite, import_stats):
        """Import question hint translations from XLSX"""
        self.stdout.write("Importing hint translations...")
        
        try:
            df = pd.read_excel(input_file, sheet_name='Hint_Translations')
        except ValueError:
            self.stdout.write("  ⚠ No Hint_Translations sheet found")
            return
        
        for index, row in df.iterrows():
            try:
                question_id = int(row['question_id'])
                translated_hint = str(row['translated_hint']).strip()
                
                if not translated_hint:
                    continue
                
                # Get the question
                try:
                    question = Question.objects.get(id=question_id, survey=survey)
                except Question.DoesNotExist:
                    self.stderr.write(f"  ✗ Question ID {question_id} not found")
                    import_stats['hints']['errors'] += 1
                    continue
                
                # Check if translation already exists
                existing_translation = QuestionHintTranslation.objects.filter(
                    question=question, language=target_language
                ).first()
                
                if existing_translation and not overwrite:
                    self.stdout.write(f"  ⚠ Hint for question {question_id}: Translation exists - skipping")
                    import_stats['hints']['skipped'] += 1
                    continue
                
                if not dry_run:
                    translation, created = QuestionHintTranslation.objects.get_or_create(
                        question=question,
                        language=target_language,
                        defaults={"hint_text": translated_hint}
                    )
                    
                    if not created:
                        translation.hint_text = translated_hint
                        translation.save()
                    
                    action = "Created" if created else "Updated"
                    self.stdout.write(f"  ✓ Hint for question {question_id}: {action} translation")
                else:
                    self.stdout.write(f"  ✓ Hint for question {question_id}: Would import translation")
                
                import_stats['hints']['imported'] += 1
                
            except Exception as e:
                self.stderr.write(f"  ✗ Error importing hint for question {row.get('question_id', 'unknown')}: {str(e)}")
                import_stats['hints']['errors'] += 1

    def _import_option_translations(self, survey, input_file, target_language, 
                                  dry_run, overwrite, import_stats):
        """Import question option translations from XLSX"""
        self.stdout.write("Importing option translations...")
        
        try:
            df = pd.read_excel(input_file, sheet_name='Option_Translations')
        except ValueError:
            self.stdout.write("  ⚠ No Option_Translations sheet found")
            return
        
        for index, row in df.iterrows():
            try:
                option_id = int(row['option_id'])
                translated_text = str(row['translated_option_text']).strip()
                
                if not translated_text:
                    continue
                
                # Get the option
                try:
                    option = QuestionOption.objects.get(id=option_id, question__survey=survey)
                except QuestionOption.DoesNotExist:
                    self.stderr.write(f"  ✗ Option ID {option_id} not found")
                    import_stats['options']['errors'] += 1
                    continue
                
                # Check if translation already exists
                existing_translation = QuestionOptionTranslation.objects.filter(
                    option=option, language=target_language
                ).first()
                
                if existing_translation and not overwrite:
                    self.stdout.write(f"  ⚠ Option {option_id}: Translation exists - skipping")
                    import_stats['options']['skipped'] += 1
                    continue
                
                if not dry_run:
                    translation, created = QuestionOptionTranslation.objects.get_or_create(
                        option=option,
                        language=target_language,
                        defaults={"text": translated_text}
                    )
                    
                    if not created:
                        translation.text = translated_text
                        translation.save()
                    
                    action = "Created" if created else "Updated"
                    self.stdout.write(f"  ✓ Option {option_id}: {action} translation")
                else:
                    self.stdout.write(f"  ✓ Option {option_id}: Would import translation")
                
                import_stats['options']['imported'] += 1
                
            except Exception as e:
                self.stderr.write(f"  ✗ Error importing option {row.get('option_id', 'unknown')}: {str(e)}")
                import_stats['options']['errors'] += 1

    def _import_page_translations(self, survey, input_file, target_language, 
                                dry_run, overwrite, import_stats):
        """Import survey page translations from XLSX"""
        self.stdout.write("Importing page translations...")
        
        try:
            df = pd.read_excel(input_file, sheet_name='Page_Translations')
        except ValueError:
            self.stdout.write("  ⚠ No Page_Translations sheet found")
            return
        
        for index, row in df.iterrows():
            try:
                page_id = int(row['page_id'])
                translated_title = str(row['translated_title']).strip()
                translated_description = str(row['translated_description']).strip()
                
                if not translated_title and not translated_description:
                    continue
                
                # Get the page
                try:
                    page = SurveyPage.objects.get(id=page_id, survey=survey)
                except SurveyPage.DoesNotExist:
                    self.stderr.write(f"  ✗ Page ID {page_id} not found")
                    import_stats['pages']['errors'] += 1
                    continue
                
                # Check if translation already exists
                existing_translation = SurveyPageTranslation.objects.filter(
                    page=page, language=target_language
                ).first()
                
                if existing_translation and not overwrite:
                    self.stdout.write(f"  ⚠ Page {page_id}: Translation exists - skipping")
                    import_stats['pages']['skipped'] += 1
                    continue
                
                if not dry_run:
                    translation, created = SurveyPageTranslation.objects.get_or_create(
                        page=page,
                        language=target_language,
                        defaults={
                            "title": translated_title or page.title,
                            "description": translated_description or page.description
                        }
                    )
                    
                    if not created:
                        if translated_title:
                            translation.title = translated_title
                        if translated_description:
                            translation.description = translated_description
                        translation.save()
                    
                    action = "Created" if created else "Updated"
                    self.stdout.write(f"  ✓ Page {page_id}: {action} translation")
                else:
                    self.stdout.write(f"  ✓ Page {page_id}: Would import translation")
                
                import_stats['pages']['imported'] += 1
                
            except Exception as e:
                self.stderr.write(f"  ✗ Error importing page {row.get('page_id', 'unknown')}: {str(e)}")
                import_stats['pages']['errors'] += 1

    def _import_consent_translations(self, survey, input_file, target_language, 
                                   dry_run, overwrite, import_stats):
        """Import survey consent translations from XLSX"""
        self.stdout.write("Importing consent translations...")
        
        try:
            df = pd.read_excel(input_file, sheet_name='Consent_Translations')
        except ValueError:
            self.stdout.write("  ⚠ No Consent_Translations sheet found")
            return
        
        for index, row in df.iterrows():
            try:
                translated_consent = str(row['translated_consent']).strip()
                
                if not translated_consent:
                    continue
                
                # Check if translation already exists
                existing_translation = SurveyConsentTranslation.objects.filter(
                    survey=survey, language=target_language
                ).first()
                
                if existing_translation and not overwrite:
                    self.stdout.write(f"  ⚠ Consent: Translation exists - skipping")
                    import_stats['consent']['skipped'] += 1
                    continue
                
                if not dry_run:
                    translation, created = SurveyConsentTranslation.objects.get_or_create(
                        survey=survey,
                        language=target_language,
                        defaults={"consent_text": translated_consent}
                    )
                    
                    if not created:
                        translation.consent_text = translated_consent
                        translation.save()
                    
                    action = "Created" if created else "Updated"
                    self.stdout.write(f"  ✓ Consent: {action} translation")
                else:
                    self.stdout.write(f"  ✓ Consent: Would import translation")
                
                import_stats['consent']['imported'] += 1
                
            except Exception as e:
                self.stderr.write(f"  ✗ Error importing consent: {str(e)}")
                import_stats['consent']['errors'] += 1

    def _print_summary(self, import_stats, dry_run):
        """Print import summary"""
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== IMPORT SUMMARY ==="))
        
        for category, stats in import_stats.items():
            if stats['imported'] > 0 or stats['errors'] > 0 or stats['skipped'] > 0:
                self.stdout.write(
                    f"{category.title()}: {stats['imported']} imported, "
                    f"{stats['errors']} errors, {stats['skipped']} skipped"
                )
        
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were saved"))
        else:
            self.stdout.write(self.style.SUCCESS("All translations imported successfully"))