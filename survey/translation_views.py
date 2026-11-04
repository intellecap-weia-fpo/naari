from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.conf import settings
import json
from .models import Survey, Question, QuestionOption, QuestionTranslation, QuestionOptionTranslation, SurveyConsentTranslation, QuestionHintTranslation, SurveyPage, SurveyPageTranslation, OptionWarningTranslation
from .translation_service import TranslationService


@login_required
def question_translations(request, question_pk):
    """Manage translations for a question"""
    question = get_object_or_404(Question, pk=question_pk)

    # Check permission
    if not request.user.has_perm("survey.change_survey", question.survey):
        return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

    if request.method == "GET":
        # Return all translations for this question
        translations = {}
        for translation in question.translations.all():
            translations[translation.language] = translation.text

        return JsonResponse(
            {
                "status": "success",
                "translations": translations,
                "default_language": question.survey.default_language,
                "default_text": question.text,
            }
        )

    elif request.method == "POST":
        try:
            data = json.loads(request.body)
            language = data.get("language")
            text = data.get("text", "").strip()

            if not language or language == question.survey.default_language:
                return JsonResponse({"status": "error", "message": "Invalid language"}, status=400)

            if text:
                # Create or update translation
                translation, created = QuestionTranslation.objects.get_or_create(
                    question=question, language=language, defaults={"text": text}
                )
                if not created:
                    translation.text = text
                    translation.save()

                return JsonResponse(
                    {
                        "status": "success",
                        "message": "Translation saved",
                        "translation": {"language": translation.language, "text": translation.text},
                    }
                )
            else:
                # Delete translation if text is empty
                QuestionTranslation.objects.filter(question=question, language=language).delete()

                return JsonResponse({"status": "success", "message": "Translation deleted"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def hint_translations(request, question_pk):
    """Manage translations for a question hint"""
    question = get_object_or_404(Question, pk=question_pk)

    # Check permission
    if not request.user.has_perm("survey.change_survey", question.survey):
        return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

    if request.method == "GET":
        # Return all translations for this question's hint
        translations = {}
        for translation in question.hint_translations.all():
            translations[translation.language] = translation.hint_text

        return JsonResponse(
            {
                "status": "success",
                "translations": translations,
                "default_language": question.survey.default_language,
                "default_text": question.hint or "",
            }
        )

    elif request.method == "POST":
        try:
            data = json.loads(request.body)
            language = data.get("language")
            hint_text = data.get("hint_text", "").strip()

            if not language or language == question.survey.default_language:
                return JsonResponse({"status": "error", "message": "Invalid language"}, status=400)

            if hint_text:
                # Create or update translation
                translation, created = QuestionHintTranslation.objects.get_or_create(
                    question=question, language=language, defaults={"hint_text": hint_text}
                )
                if not created:
                    translation.hint_text = hint_text
                    translation.save()

                return JsonResponse(
                    {
                        "status": "success",
                        "message": "Hint translation saved",
                        "translation": {"language": translation.language, "hint_text": translation.hint_text},
                    }
                )
            else:
                # Delete translation if hint text is empty
                QuestionHintTranslation.objects.filter(question=question, language=language).delete()
                return JsonResponse({"status": "success", "message": "Hint translation deleted"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def option_translations(request, option_pk):
    """Manage translations for a question option"""
    option = get_object_or_404(QuestionOption, pk=option_pk)

    # Check permission
    if not request.user.has_perm("survey.change_survey", option.question.survey):
        return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

    if request.method == "GET":
        # Return all translations for this option
        translations = {}
        for translation in option.translations.all():
            translations[translation.language] = translation.text

        return JsonResponse(
            {
                "status": "success",
                "translations": translations,
                "default_language": option.question.survey.default_language,
                "default_text": option.text,
            }
        )

    elif request.method == "POST":
        try:
            data = json.loads(request.body)
            language = data.get("language")
            text = data.get("text", "").strip()

            if not language or language == option.question.survey.default_language:
                return JsonResponse({"status": "error", "message": "Invalid language"}, status=400)

            if text:
                # Create or update translation
                translation, created = QuestionOptionTranslation.objects.get_or_create(
                    option=option, language=language, defaults={"text": text}
                )
                if not created:
                    translation.text = text
                    translation.save()

                return JsonResponse(
                    {
                        "status": "success",
                        "message": "Translation saved",
                        "translation": {"language": translation.language, "text": translation.text},
                    }
                )
            else:
                # Delete translation if text is empty
                QuestionOptionTranslation.objects.filter(option=option, language=language).delete()

                return JsonResponse({"status": "success", "message": "Translation deleted"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def survey_languages(request, survey_pk):
    """Get available languages for a survey and manage language addition"""
    from .models import Survey

    survey = get_object_or_404(Survey, pk=survey_pk)

    # Check permission for viewing
    if not request.user.has_perm("survey.view_survey", survey):
        return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

    if request.method == "GET":
        try:
            supported_languages = survey.get_supported_languages()
            language_choices = dict(settings.LANGUAGES)

            languages = [
                {"code": lang, "name": language_choices.get(lang, lang), "is_default": lang == survey.default_language}
                for lang in supported_languages
            ]

            # Add available languages not yet used
            available_languages = [
                {"code": code, "name": name, "is_default": False, "available": True}
                for code, name in settings.LANGUAGES
                if code not in supported_languages
            ]

            return JsonResponse(
                {
                    "status": "success",
                    "current_languages": languages,
                    "available_languages": available_languages,
                    "default_language": survey.default_language,
                }
            )

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)

    elif request.method == "POST":
        # Check permission for editing
        if not request.user.has_perm("survey.change_survey", survey):
            return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

        try:
            data = json.loads(request.body)
            language_code = data.get("language")

            if not language_code:
                return JsonResponse({"status": "error", "message": "Language code is required"}, status=400)

            # Validate language code
            valid_languages = dict(settings.LANGUAGES)
            if language_code not in valid_languages:
                return JsonResponse({"status": "error", "message": "Invalid language code"}, status=400)

            # Check if language is already supported
            if language_code in survey.get_supported_languages():
                return JsonResponse({"status": "error", "message": "Language already added"}, status=400)

            # Add language by creating a placeholder translation for the first question
            # This ensures the language appears in get_supported_languages()
            first_question = survey.questions.first()
            if first_question:
                # Create placeholder translation (empty text)
                QuestionTranslation.objects.get_or_create(
                    question=first_question, language=language_code, defaults={"text": ""}
                )

            return JsonResponse(
                {
                    "status": "success",
                    "message": "Language added successfully",
                    "language": {"code": language_code, "name": valid_languages[language_code], "is_default": False},
                }
            )

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)

    elif request.method == "DELETE":
        # Check permission for editing
        if not request.user.has_perm("survey.change_survey", survey):
            return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

        try:
            data = json.loads(request.body)
            language_code = data.get("language")

            if not language_code:
                return JsonResponse({"status": "error", "message": "Language code is required"}, status=400)

            # Don't allow removing default language
            if language_code == survey.default_language:
                return JsonResponse({"status": "error", "message": "Cannot remove default language"}, status=400)

            # Remove all translations for this language associated with this survey
            QuestionTranslation.objects.filter(question__survey=survey, language=language_code).delete()
            QuestionOptionTranslation.objects.filter(option__question__survey=survey, language=language_code).delete()
            SurveyConsentTranslation.objects.filter(survey=survey, language=language_code).delete()
            QuestionHintTranslation.objects.filter(question__survey=survey, language=language_code).delete()
            SurveyPageTranslation.objects.filter(page__survey=survey, language=language_code).delete()
            OptionWarningTranslation.objects.filter(option__question__survey=survey, language=language_code).delete()

            return JsonResponse(
                {"status": "success", "message": "Language removed successfully", "language_code": language_code}
            )

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def auto_translate(request):
    """Auto-translate text using OpenAI API"""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        text = data.get("text", "").strip()
        target_languages = data.get("target_languages", [])
        source_language = data.get("source_language", "en")
        item_type = data.get("type")  # 'question' or 'option'
        item_id = data.get("id")

        if not text:
            return JsonResponse({"status": "error", "message": "Text is required"}, status=400)

        if not target_languages:
            return JsonResponse({"status": "error", "message": "Target languages are required"}, status=400)

        # Verify permissions based on item type
        if item_type == "question" and item_id:
            question = get_object_or_404(Question, pk=item_id)
            if not request.user.has_perm("survey.change_survey", question.survey):
                return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)
        elif item_type == "option" and item_id:
            option = get_object_or_404(QuestionOption, pk=item_id)
            if not request.user.has_perm("survey.change_survey", option.question.survey):
                return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)
        elif item_type == "consent" and item_id:
            survey = get_object_or_404(Survey, pk=item_id)
            if not request.user.has_perm("survey.change_survey", survey):
                return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

        # Initialize translation service
        translation_service = TranslationService()

        # Perform batch translation
        translations = translation_service.translate_batch(text, target_languages, source_language)

        if not translations:
            return JsonResponse({"status": "error", "message": "Translation failed. Please try again."}, status=500)

        return JsonResponse({"status": "success", "translations": translations})

    except ValueError as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "message": f"An error occurred: {str(e)}"}, status=500)


@login_required
def auto_translate_survey(request):
    """Auto-translate entire survey using OpenAI API"""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        survey_id = data.get("survey_id")
        target_language = data.get("target_language")
        source_language = data.get("source_language", "en")

        if not survey_id:
            return JsonResponse({"status": "error", "message": "Survey ID is required"}, status=400)

        if not target_language:
            return JsonResponse({"status": "error", "message": "Target language is required"}, status=400)

        # Get survey and check permissions
        survey = get_object_or_404(Survey, pk=survey_id)
        if not request.user.has_perm("survey.change_survey", survey):
            return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

        # Don't allow translating to the default language
        if target_language == survey.default_language:
            return JsonResponse({"status": "error", "message": "Cannot translate to the default language"}, status=400)

        # Initialize translation service
        translation_service = TranslationService()

        # Get all questions and options in the survey
        questions = survey.questions.all().prefetch_related('options')
        total_items = sum(1 + question.options.count() for question in questions)
        translated_count = 0

        # Calculate total items for progress
        response_data = {
            "status": "in_progress",
            "total_items": total_items,
            "completed_items": 0,
            "current_item": ""
        }

        for question in questions:
            # Update current item for progress
            response_data["current_item"] = f"Question: {question.text[:50]}..."
            
            # Translate question text
            translated_text = translation_service.translate_text(
                question.text, target_language, source_language
            )
            
            if translated_text:
                # Create or update question translation
                translation, created = QuestionTranslation.objects.get_or_create(
                    question=question, language=target_language, defaults={"text": translated_text}
                )
                if not created:
                    translation.text = translated_text
                    translation.save()
                translated_count += 1
                response_data["completed_items"] = translated_count

            # Translate question options
            for option in question.options.all():
                response_data["current_item"] = f"Option: {option.text[:50]}..."
                
                translated_option_text = translation_service.translate_text(
                    option.text, target_language, source_language
                )
                
                if translated_option_text:
                    # Create or update option translation
                    option_translation, created = QuestionOptionTranslation.objects.get_or_create(
                        option=option, language=target_language, defaults={"text": translated_option_text}
                    )
                    if not created:
                        option_translation.text = translated_option_text
                        option_translation.save()
                    translated_count += 1
                    response_data["completed_items"] = translated_count

        # Final response
        response_data.update({
            "status": "success",
            "message": f"Successfully translated {translated_count} items to {target_language}",
            "translated_count": translated_count,
            "current_item": ""
        })

        return JsonResponse(response_data)

    except ValueError as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "message": f"An error occurred: {str(e)}"}, status=500)

@login_required
def survey_consent_translations(request, survey_pk):
    """Manage translations for survey consent text"""
    survey = get_object_or_404(Survey, pk=survey_pk)

    # Check permission
    if not request.user.has_perm("survey.change_survey", survey):
        return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

    if request.method == "GET":
        # Return all translations for this survey's consent text
        translations = {}
        for translation in survey.consent_translations.all():
            translations[translation.language] = translation.consent_text

        return JsonResponse(
            {
                "status": "success",
                "translations": translations,
                "default_language": survey.default_language,
                "default_text": survey.consent_text or "",
            }
        )

    elif request.method == "POST":
        try:
            data = json.loads(request.body)
            language = data.get("language")
            text = data.get("text", "").strip()

            if not language or language == survey.default_language:
                return JsonResponse({"status": "error", "message": "Invalid language"}, status=400)

            if text:
                # Create or update translation
                translation, created = SurveyConsentTranslation.objects.get_or_create(
                    survey=survey, language=language, defaults={"consent_text": text}
                )
                if not created:
                    translation.consent_text = text
                    translation.save()

                return JsonResponse(
                    {
                        "status": "success",
                        "message": "Consent translation saved",
                        "translation": {"language": translation.language, "text": translation.consent_text},
                    }
                )
            else:
                # Delete translation if text is empty
                SurveyConsentTranslation.objects.filter(survey=survey, language=language).delete()
                return JsonResponse({"status": "success", "message": "Consent translation deleted"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def page_translations(request, page_pk):
    """Manage translations for a survey page"""
    page = get_object_or_404(SurveyPage, pk=page_pk)

    # Check permission
    if not request.user.has_perm("survey.change_survey", page.survey):
        return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

    if request.method == "GET":
        # Return all translations for this page
        translations = {}
        for translation in page.translations.all():
            translations[translation.language] = {
                "title": translation.title,
                "description": translation.description
            }

        return JsonResponse(
            {
                "status": "success",
                "translations": translations,
                "default_language": page.survey.default_language,
                "default_title": page.title or "",
                "default_description": page.description or "",
            }
        )

    elif request.method == "POST":
        try:
            data = json.loads(request.body)
            language = data.get("language")
            title = data.get("title", "").strip() or None
            description = data.get("description", "").strip() or None

            if not language or language == page.survey.default_language:
                return JsonResponse({"status": "error", "message": "Invalid language"}, status=400)

            if title or description:
                # Create or update translation
                translation, created = SurveyPageTranslation.objects.get_or_create(
                    page=page, language=language, defaults={"title": title, "description": description}
                )
                if not created:
                    if title is not None:
                        translation.title = title
                    if description is not None:
                        translation.description = description
                    translation.save()

                return JsonResponse(
                    {
                        "status": "success",
                        "message": "Page translation saved",
                        "translation": {
                            "language": translation.language,
                            "title": translation.title,
                            "description": translation.description
                        },
                    }
                )
            else:
                # Delete translation if both title and description are empty
                SurveyPageTranslation.objects.filter(page=page, language=language).delete()
                return JsonResponse({"status": "success", "message": "Page translation deleted"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def option_warning_translations(request, option_pk):
    """Manage translations for option warning messages"""
    option = get_object_or_404(QuestionOption, pk=option_pk)

    # Check permission
    if not request.user.has_perm("survey.change_survey", option.question.survey):
        return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

    if request.method == "GET":
        # Return all translations for this option's warnings
        translations = {}
        for translation in option.warning_translations.all():
            if translation.warning_type not in translations:
                translations[translation.warning_type] = {}
            translations[translation.warning_type][translation.language] = translation.message

        # Get default warning messages
        default_warnings = {}
        if option.option_warning:
            for warning_type in ['selected', 'not_selected']:
                if warning_type in option.option_warning:
                    default_warnings[warning_type] = option.option_warning[warning_type].get('message', '')

        return JsonResponse(
            {
                "status": "success",
                "translations": translations,
                "default_warnings": default_warnings,
                "default_language": option.question.survey.default_language,
            }
        )

    elif request.method == "POST":
        try:
            data = json.loads(request.body)
            language = data.get("language")
            warning_type = data.get("warning_type")  # 'selected' or 'not_selected'
            message = data.get("message", "").strip()

            if not language or language == option.question.survey.default_language:
                return JsonResponse({"status": "error", "message": "Invalid language"}, status=400)

            if not warning_type or warning_type not in ['selected', 'not_selected']:
                return JsonResponse({"status": "error", "message": "Invalid warning type"}, status=400)

            # Verify that the option has a warning configured for this type
            if not option.option_warning or warning_type not in option.option_warning:
                return JsonResponse(
                    {"status": "error", "message": f"Option does not have a warning configured for '{warning_type}'"},
                    status=400
                )

            if message:
                # Create or update translation
                translation, created = OptionWarningTranslation.objects.get_or_create(
                    option=option,
                    language=language,
                    warning_type=warning_type,
                    defaults={"message": message}
                )
                if not created:
                    translation.message = message
                    translation.save()

                return JsonResponse(
                    {
                        "status": "success",
                        "message": "Warning translation saved",
                        "translation": {
                            "language": translation.language,
                            "warning_type": translation.warning_type,
                            "message": translation.message
                        },
                    }
                )
            else:
                # Delete translation if message is empty
                OptionWarningTranslation.objects.filter(
                    option=option,
                    language=language,
                    warning_type=warning_type
                ).delete()

                return JsonResponse({"status": "success", "message": "Warning translation deleted"})

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
