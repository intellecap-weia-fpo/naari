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
from django.db.models import Prefetch
import os
from datetime import datetime


class Command(BaseCommand):
    help = 'Export all survey translations to XLSX file with multiple sheets'

    def add_arguments(self, parser):
        parser.add_argument('survey_id', type=int, help='ID of the survey to export translations for')
        parser.add_argument('--output-file', type=str, 
                          help='Output XLSX file path (default: translations_<survey_id>_<timestamp>.xlsx)')
        parser.add_argument('--language', type=str, default='hi',
                          help='Language code to export translations for (default: hi)')
        parser.add_argument('--include-all-languages', action='store_true',
                          help='Include all languages instead of just one')

    def handle(self, *args, **options):
        survey_id = options['survey_id']
        output_file = options['output_file']
        target_language = options['language']
        include_all_languages = options['include_all_languages']

        # Get the survey
        try:
            survey = Survey.objects.get(pk=survey_id)
        except Survey.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Survey with ID {survey_id} does not exist"))
            return

        # Set default output file name
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"translations_survey_{survey_id}_{timestamp}.xlsx"

        self.stdout.write(
            self.style.SUCCESS(f"Exporting translations for survey: '{survey.title}' (ID: {survey_id})")
        )
        self.stdout.write(f"Output file: {output_file}")
        self.stdout.write(f"Language: {target_language}")
        self.stdout.write(f"Include all languages: {include_all_languages}")
        self.stdout.write("")

        # Create Excel writer
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            # Export each type of translation to separate sheets
            self._export_question_translations(survey, target_language, include_all_languages, writer)
            self._export_question_hint_translations(survey, target_language, include_all_languages, writer)
            self._export_option_translations(survey, target_language, include_all_languages, writer)
            self._export_page_translations(survey, target_language, include_all_languages, writer)
            self._export_consent_translations(survey, target_language, include_all_languages, writer)

        self.stdout.write(self.style.SUCCESS(f"Translations exported successfully to {output_file}"))

    def _export_question_translations(self, survey, target_language, include_all_languages, writer):
        """Export question translations"""
        self.stdout.write("Exporting question translations...")
        
        questions = survey.questions.all().prefetch_related(
            Prefetch('translations', queryset=QuestionTranslation.objects.all())
        )
        
        data = []
        for question in questions:
            if include_all_languages:
                # Include all available translations
                for translation in question.translations.all():
                    data.append({
                        'question_id': question.id,
                        'original_text': question.text,
                        'language': translation.language,
                        'translated_text': translation.text,
                        'question_type': question.question_type,
                        'order': question.order
                    })
            else:
                # Include only the target language
                translation = question.translations.filter(language=target_language).first()
                data.append({
                    'question_id': question.id,
                    'original_text': question.text,
                    'language': target_language,
                    'translated_text': translation.text if translation else '',
                    'question_type': question.question_type,
                    'order': question.order
                })
        
        if data:
            df = pd.DataFrame(data)
            df = df[['question_id', 'order', 'question_type', 'language', 'original_text', 'translated_text']]
            df.to_excel(writer, sheet_name='Question_Translations', index=False)
            self.stdout.write(f"  ✓ Exported {len(data)} question translations")
        else:
            self.stdout.write("  ⚠ No question translations found")

    def _export_question_hint_translations(self, survey, target_language, include_all_languages, writer):
        """Export question hint translations"""
        self.stdout.write("Exporting question hint translations...")
        
        questions = survey.questions.filter(hint__isnull=False).prefetch_related(
            Prefetch('hint_translations', queryset=QuestionHintTranslation.objects.all())
        )
        
        data = []
        for question in questions:
            if include_all_languages:
                # Include all available translations
                for translation in question.hint_translations.all():
                    data.append({
                        'question_id': question.id,
                        'question_text': question.text[:100] + '...' if len(question.text) > 100 else question.text,
                        'original_hint': question.hint,
                        'language': translation.language,
                        'translated_hint': translation.hint_text,
                        'question_type': question.question_type
                    })
            else:
                # Include only the target language
                translation = question.hint_translations.filter(language=target_language).first()
                data.append({
                    'question_id': question.id,
                    'question_text': question.text[:100] + '...' if len(question.text) > 100 else question.text,
                    'original_hint': question.hint,
                    'language': target_language,
                    'translated_hint': translation.hint_text if translation else '',
                    'question_type': question.question_type
                })
        
        if data:
            df = pd.DataFrame(data)
            df = df[['question_id', 'question_type', 'language', 'question_text', 'original_hint', 'translated_hint']]
            df.to_excel(writer, sheet_name='Hint_Translations', index=False)
            self.stdout.write(f"  ✓ Exported {len(data)} hint translations")
        else:
            self.stdout.write("  ⚠ No hint translations found")

    def _export_option_translations(self, survey, target_language, include_all_languages, writer):
        """Export question option translations"""
        self.stdout.write("Exporting option translations...")
        
        questions = survey.questions.filter(options__isnull=False).prefetch_related(
            'options__translations'
        ).distinct()
        
        data = []
        for question in questions:
            for option in question.options.all():
                if include_all_languages:
                    # Include all available translations
                    for translation in option.translations.all():
                        data.append({
                            'question_id': question.id,
                            'option_id': option.id,
                            'question_text': question.text[:100] + '...' if len(question.text) > 100 else question.text,
                            'original_option_text': option.text,
                            'language': translation.language,
                            'translated_option_text': translation.text,
                            'option_order': option.order
                        })
                else:
                    # Include only the target language
                    translation = option.translations.filter(language=target_language).first()
                    data.append({
                        'question_id': question.id,
                        'option_id': option.id,
                        'question_text': question.text[:100] + '...' if len(question.text) > 100 else question.text,
                        'original_option_text': option.text,
                        'language': target_language,
                        'translated_option_text': translation.text if translation else '',
                        'option_order': option.order
                    })
        
        if data:
            df = pd.DataFrame(data)
            df = df[['question_id', 'option_id', 'option_order', 'language', 'question_text', 
                    'original_option_text', 'translated_option_text']]
            df.to_excel(writer, sheet_name='Option_Translations', index=False)
            self.stdout.write(f"  ✓ Exported {len(data)} option translations")
        else:
            self.stdout.write("  ⚠ No option translations found")

    def _export_page_translations(self, survey, target_language, include_all_languages, writer):
        """Export survey page translations"""
        self.stdout.write("Exporting page translations...")
        
        pages = survey.pages.all().prefetch_related(
            Prefetch('translations', queryset=SurveyPageTranslation.objects.all())
        )
        
        data = []
        for page in pages:
            if include_all_languages:
                # Include all available translations
                for translation in page.translations.all():
                    data.append({
                        'page_id': page.id,
                        'page_order': page.order,
                        'original_title': page.title,
                        'original_description': page.description,
                        'language': translation.language,
                        'translated_title': translation.title,
                        'translated_description': translation.description
                    })
            else:
                # Include only the target language
                translation = page.translations.filter(language=target_language).first()
                data.append({
                    'page_id': page.id,
                    'page_order': page.order,
                    'original_title': page.title,
                    'original_description': page.description,
                    'language': target_language,
                    'translated_title': translation.title if translation else '',
                    'translated_description': translation.description if translation else ''
                })
        
        if data:
            df = pd.DataFrame(data)
            df = df[['page_id', 'page_order', 'language', 'original_title', 'translated_title', 
                    'original_description', 'translated_description']]
            df.to_excel(writer, sheet_name='Page_Translations', index=False)
            self.stdout.write(f"  ✓ Exported {len(data)} page translations")
        else:
            self.stdout.write("  ⚠ No page translations found")

    def _export_consent_translations(self, survey, target_language, include_all_languages, writer):
        """Export survey consent text translations"""
        self.stdout.write("Exporting consent translations...")
        
        if not survey.consent_text:
            self.stdout.write("  ⚠ No consent text found")
            return
        
        consent_translations = SurveyConsentTranslation.objects.filter(survey=survey)
        
        data = []
        if include_all_languages:
            # Include all available translations
            for translation in consent_translations:
                data.append({
                    'survey_id': survey.id,
                    'original_consent': survey.consent_text,
                    'language': translation.language,
                    'translated_consent': translation.consent_text
                })
        else:
            # Include only the target language
            translation = consent_translations.filter(language=target_language).first()
            data.append({
                'survey_id': survey.id,
                'original_consent': survey.consent_text,
                'language': target_language,
                'translated_consent': translation.consent_text if translation else ''
            })
        
        if data:
            df = pd.DataFrame(data)
            df = df[['survey_id', 'language', 'original_consent', 'translated_consent']]
            df.to_excel(writer, sheet_name='Consent_Translations', index=False)
            self.stdout.write(f"  ✓ Exported {len(data)} consent translations")
        else:
            self.stdout.write("  ⚠ No consent translations found")