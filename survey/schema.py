import graphene
from graphene import relay
from graphene_django import DjangoObjectType
from graphene_django.fields import DjangoConnectionField
from graphene_django.filter import DjangoFilterConnectionField
from graphql_jwt.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import models, transaction, IntegrityError
import re
import uuid

from .models import (
    Survey, SurveyPage, Question, QuestionOption, QuestionCondition, OptionCondition,
    Submission, SubmissionAnswer, SurveyInvitation,
    SurveyConsentTranslation, OptionWarningTranslation
)

User = get_user_model()


def is_manager_or_admin(user):
    """Check if user is a manager, staff, or superuser"""
    if user.is_staff or user.is_superuser:
        return True
    return user.groups.filter(name='Manager').exists()


def _get_prefetched_related(instance, relation_name):
    cache = getattr(instance, '_prefetched_objects_cache', None)
    if not cache:
        return None
    return cache.get(relation_name)


def _get_default_language(info, fallback=None):
    context_language = getattr(info.context, 'survey_default_language', None)
    if context_language:
        return context_language
    return fallback


def _get_survey_languages_from_prefetch(survey):
    languages = {survey.default_language}
    pages = _get_prefetched_related(survey, 'pages')
    if pages is None:
        return None

    for page in pages:
        page_translations = _get_prefetched_related(page, 'translations')
        if page_translations is not None:
            languages.update(t.language for t in page_translations)

        questions = _get_prefetched_related(page, 'questions')
        if questions is None:
            continue
        for question in questions:
            question_translations = _get_prefetched_related(question, 'translations')
            if question_translations is not None:
                languages.update(t.language for t in question_translations)

    return list(languages)


def _survey_with_form_prefetches(queryset, user):
    question_conditions = QuestionCondition.objects.select_related('target_question').order_by('order', 'id')
    option_conditions = OptionCondition.objects.select_related('target_question').order_by('order', 'id')
    sub_questions = Question.objects.select_related(
        'survey',
        'page',
        'filter_options_question',
    ).prefetch_related(
        'translations',
        'hint_translations',
        models.Prefetch('conditions', queryset=question_conditions),
        models.Prefetch(
            'options',
            queryset=QuestionOption.objects.select_related(
                'question',
                'question__survey',
            ).prefetch_related(
                'translations',
                'warning_translations',
                models.Prefetch('conditions', queryset=option_conditions),
            ).order_by('order', 'id'),
        ),
    ).order_by('order', 'id')
    options = QuestionOption.objects.select_related(
        'question',
        'question__survey',
    ).prefetch_related(
        'translations',
        'warning_translations',
        models.Prefetch('conditions', queryset=option_conditions),
        models.Prefetch('sub_questions', queryset=sub_questions),
    ).order_by('order', 'id')
    questions = Question.objects.select_related(
        'survey',
        'page',
        'filter_options_question',
    ).prefetch_related(
        'translations',
        'hint_translations',
        models.Prefetch('conditions', queryset=question_conditions),
        models.Prefetch('options', queryset=options),
    ).order_by('order', 'id')
    pages = SurveyPage.objects.select_related('survey').prefetch_related(
        'translations',
        models.Prefetch('questions', queryset=questions),
    ).order_by('order', 'id')

    return queryset.select_related(
        'created_by',
        'render_field',
    ).prefetch_related(
        'consent_translations',
        models.Prefetch('pages', queryset=pages),
    ).annotate(
        _has_submitted=models.Exists(
            Submission.objects.filter(survey_id=models.OuterRef('pk'), user=user)
        ),
        _submission_count=models.Count(
            'submissions',
            filter=models.Q(submissions__user=user),
        ),
    )


def _resolve_survey_pages(root, info, **kwargs):
    pages = _get_prefetched_related(root, 'pages')
    if pages is not None:
        return pages
    return root.pages.all().order_by('order', 'id')


def _resolve_page_questions(root, info, **kwargs):
    questions = _get_prefetched_related(root, 'questions')
    if questions is not None:
        return questions
    return root.questions.all().order_by('order', 'id')


def _resolve_question_options(root, info, **kwargs):
    options = _get_prefetched_related(root, 'options')
    if options is not None:
        return options
    return root.options.all().order_by('order', 'id')


def _resolve_survey_questions(root, info, **kwargs):
    questions = _get_prefetched_related(root, 'questions')
    if questions is not None:
        return questions
    return root.questions.select_related(
        'survey',
        'page',
        'filter_options_question',
    ).order_by('order', 'id')


def _resolve_survey_submissions(root, info, **kwargs):
    user = getattr(info.context, 'user', None)
    if not user or not user.is_authenticated:
        return Submission.objects.none()

    return _submission_with_detail_prefetches(
        root.submissions.filter(user=user).select_related('user').order_by('-submitted_at')
    )


def _resolve_question_answers(root, info, **kwargs):
    answers = _get_prefetched_related(root, 'answers')
    if answers is not None:
        return answers
    return root.answers.select_related(
        'submission',
        'question',
        'question__page',
        'question__page__survey',
        'question__survey',
    ).prefetch_related(
        models.Prefetch(
            'selected_options',
            queryset=QuestionOption.objects.select_related(
                'question',
                'question__survey',
            ).order_by('order', 'id'),
        ),
    ).order_by('id')


def _resolve_submission_answers(root, info, **kwargs):
    answers = _get_prefetched_related(root, 'answers')
    if answers is not None:
        return answers
    return root.answers.select_related(
        'question',
        'question__page',
        'question__page__survey',
        'question__survey',
    ).prefetch_related('selected_options').order_by('id')


def _resolve_answer_selected_options(root, info, **kwargs):
    selected_options = _get_prefetched_related(root, 'selected_options')
    if selected_options is not None:
        return selected_options
    return root.selected_options.select_related(
        'question',
        'question__survey',
    ).order_by('order', 'id')


def _submission_with_detail_prefetches(queryset):
    answers = SubmissionAnswer.objects.select_related(
        'question',
        'question__page',
        'question__page__survey',
        'question__survey',
    ).prefetch_related(
        'question__page__translations',
        models.Prefetch(
            'selected_options',
            queryset=QuestionOption.objects.select_related(
                'question',
                'question__survey',
            ).order_by('order', 'id'),
        ),
    ).order_by('id')

    return queryset.select_related(
        'survey',
        'survey__render_field',
        'user',
    ).prefetch_related(
        models.Prefetch('answers', queryset=answers),
    )


class SurveyNode(DjangoObjectType):
    available_languages = graphene.List(graphene.String)
    has_submitted = graphene.Boolean()
    submission_count = graphene.Int()
    consent_translations = graphene.List(lambda: SurveyConsentTranslationNode)
    localized_consent_text = graphene.String()
    render_in_response = graphene.Boolean()
    render_field = graphene.Field(lambda: QuestionNode)
    pages = DjangoConnectionField(
        lambda: SurveyPageNode,
        resolver=_resolve_survey_pages,
    )
    questions = DjangoConnectionField(
        lambda: QuestionNode,
        resolver=_resolve_survey_questions,
    )
    submissions = DjangoConnectionField(
        lambda: SubmissionNode,
        resolver=_resolve_survey_submissions,
    )

    class Meta:
        model = Survey
        exclude = ()
        filter_fields = {
            'title': ['exact', 'icontains', 'istartswith'],
            'is_active': ['exact'],
            'visibility': ['exact'],
            'created_by__username': ['exact', 'icontains'],
            'end_date': ['exact', 'lt', 'lte', 'gt', 'gte', 'isnull'],
        }
        interfaces = (relay.Node,)

    def resolve_available_languages(self, info):
        # Use cached result if available from annotation
        cached = getattr(self, '_available_languages', None)
        if cached is not None:
            return cached
        prefetched_languages = _get_survey_languages_from_prefetch(self)
        if prefetched_languages is not None:
            return prefetched_languages
        return self.get_supported_languages()

    def resolve_has_submitted(self, info):
        # Use annotated value if available
        if hasattr(self, '_has_submitted'):
            return self._has_submitted
        user = info.context.user
        if not user.is_authenticated:
            return False
        return Submission.objects.filter(survey=self, user=user).exists()

    def resolve_submission_count(self, info):
        # Use annotated value if available
        if hasattr(self, '_submission_count'):
            return self._submission_count
        user = info.context.user
        if not user.is_authenticated:
            return 0
        return Submission.objects.filter(survey=self, user=user).count()
    
    def resolve_consent_translations(self, info):
        translations = _get_prefetched_related(self, 'consent_translations')
        if translations is not None:
            return translations
        return self.consent_translations.all()

    def resolve_pages(self, info, **kwargs):
        return _resolve_survey_pages(self, info, **kwargs)
    
    def resolve_localized_consent_text(self, info, **kwargs):
        """Get consent text in specified language, fallback to default"""
        language = getattr(info.context, 'survey_language', None)
        if not language:
            return self.consent_text

        default_language = _get_default_language(info, self.default_language)
        if language == default_language:
            return self.consent_text

        translations = _get_prefetched_related(self, 'consent_translations')
        if translations is not None:
            for translation in translations:
                if translation.language == language and translation.consent_text:
                    return translation.consent_text
            return self.consent_text

        return self.get_consent_text(language)


class SurveyPageNode(DjangoObjectType):
    localized_title = graphene.String()
    questions = DjangoConnectionField(
        lambda: QuestionNode,
        resolver=_resolve_page_questions,
    )
    
    class Meta:
        model = SurveyPage
        exclude = ()
        filter_fields = {
            'title': ['exact', 'icontains'],
            'order': ['exact', 'lt', 'gt'],
        }
        interfaces = (relay.Node,)
    
    def resolve_questions(self, info):
        """Resolve questions with proper ordering"""
        return _resolve_page_questions(self, info)
    
    def resolve_localized_title(self, info, **kwargs):
        """Get page title in specified language, fallback to default"""
        language = getattr(info.context, 'survey_language', None)
        default_language = _get_default_language(info, self.survey.default_language)
        if not language or language == default_language:
            return self.title

        translations = _get_prefetched_related(self, 'translations')
        if translations is not None:
            for translation in translations:
                if translation.language == language and translation.title:
                    return translation.title
            return self.title

        return self.get_title(language)


class QuestionConditionNode(DjangoObjectType):
    """GraphQL node for question conditions"""
    
    class Meta:
        model = QuestionCondition
        exclude = ()
        interfaces = (relay.Node,)
    
    def resolve_target_question(self, info):
        """Resolve target question with proper ordering"""
        return self.target_question


class QuestionNode(DjangoObjectType):
    localized_text = graphene.String()
    localized_hint = graphene.String()
    available_languages = graphene.List(graphene.String)
    conditions = graphene.List(lambda: QuestionConditionNode)
    is_conditional = graphene.Boolean()
    options = DjangoConnectionField(
        lambda: QuestionOptionNode,
        resolver=_resolve_question_options,
    )
    answers = DjangoConnectionField(
        lambda: SubmissionAnswerNode,
        resolver=_resolve_question_answers,
    )
    
    class Meta:
        model = Question
        exclude = ()
        filter_fields = {
            'text': ['icontains'],
            'question_type': ['exact'],
            'is_required': ['exact'],
            'order': ['exact', 'lt', 'gt'],
            'filter_options_question': ['exact', 'isnull'],
        }
        interfaces = (relay.Node,)
    
    def resolve_localized_text(self, info, **kwargs):
        language = getattr(info.context, 'survey_language', None)
        default_language = _get_default_language(info, self.survey.default_language)
        if not language or language == default_language:
            return self.text

        translations = _get_prefetched_related(self, 'translations')
        if translations is not None:
            for translation in translations:
                if translation.language == language and translation.text:
                    return translation.text
            return self.text

        return self.get_text(language)
    
    def resolve_localized_hint(self, info, **kwargs):
        """Get hint text in specified language, fallback to default"""
        language = getattr(info.context, 'survey_language', None)
        default_language = _get_default_language(info, self.survey.default_language)
        if not language or language == default_language:
            return self.hint

        hint_translations = _get_prefetched_related(self, 'hint_translations')
        if hint_translations is not None:
            for translation in hint_translations:
                if translation.language == language and translation.hint_text:
                    return translation.hint_text
            return self.hint

        return self.get_hint(language)
    
    def resolve_available_languages(self, info):
        default_language = _get_default_language(info, self.survey.default_language)
        languages = {default_language}
        translations = _get_prefetched_related(self, 'translations')
        if translations is not None:
            languages.update([translation.language for translation in translations])
            return list(languages)
        return self.get_available_languages()
    
    def resolve_conditions(self, info):
        """Resolve conditions with proper ordering"""
        conditions = _get_prefetched_related(self, 'conditions')
        if conditions is not None:
            return conditions
        return self.conditions.all().order_by('order', 'id')
    
    def resolve_is_conditional(self, info):
        """Resolve is_conditional field"""
        conditions = _get_prefetched_related(self, 'conditions')
        if conditions is not None:
            return self.is_conditional or len(conditions) > 0
        return self.is_conditional or self.conditions.exists()

    def resolve_options(self, info, **kwargs):
        return _resolve_question_options(self, info, **kwargs)

    def resolve_answers(self, info, **kwargs):
        return _resolve_question_answers(self, info, **kwargs)


class QuestionOptionNode(DjangoObjectType):
    localized_text = graphene.String()
    has_sub_question = graphene.Boolean()
    sub_questions = graphene.List(lambda: QuestionNode)
    conditions = graphene.List(lambda: OptionConditionNode)
    is_conditional = graphene.Boolean()
    option_warning = graphene.JSONString()
    option_warning_text = graphene.String()
    localized_option_warning = graphene.JSONString()
    localized_option_warning_text = graphene.String()
    warning_translations = graphene.List(lambda: OptionWarningTranslationNode)

    class Meta:
        model = QuestionOption
        exclude = ()
        filter_fields = {
            'text': ['exact', 'icontains'],
            'order': ['exact', 'lt', 'gt'],
        }
        interfaces = (relay.Node,)

    def resolve_localized_text(self, info, **kwargs):
        language = getattr(info.context, 'survey_language', None)
        default_language = _get_default_language(info, self.question.survey.default_language)
        if not language or language == default_language:
            return self.text

        translations = _get_prefetched_related(self, 'translations')
        if translations is not None:
            for translation in translations:
                if translation.language == language and translation.text:
                    return translation.text
            return self.text

        return self.get_text(language)

    def resolve_has_sub_question(self, info, **kwargs):
        sub_questions = _get_prefetched_related(self, 'sub_questions')
        if sub_questions is not None:
            return len(sub_questions) > 0
        return self.sub_questions.count() > 0

    def resolve_sub_questions(self, info, **kwargs):
        sub_questions = _get_prefetched_related(self, 'sub_questions')
        if sub_questions is not None:
            return sub_questions
        return self.sub_questions.all().order_by('order', 'id')

    def resolve_conditions(self, info, **kwargs):
        conditions = _get_prefetched_related(self, 'conditions')
        if conditions is not None:
            return conditions
        return self.conditions.all().order_by('order', 'id')

    def resolve_is_conditional(self, info, **kwargs):
        conditions = _get_prefetched_related(self, 'conditions')
        if conditions is not None:
            return getattr(self, 'is_conditional', False) or len(conditions) > 0
        return getattr(self, 'is_conditional', False) or self.conditions.exists()

    def resolve_option_warning(self, info, **kwargs):
        return self.option_warning or None

    def resolve_option_warning_text(self, info, **kwargs):
        return self.option_warning_text or ""

    def resolve_localized_option_warning(self, info, **kwargs):
        """Get warning messages with translations applied"""
        if not self.option_warning:
            return None

        language = getattr(info.context, 'survey_language', None)
        default_language = _get_default_language(info, self.question.survey.default_language)

        if not language or language == default_language:
            return self.option_warning

        warning_translations = _get_prefetched_related(self, 'warning_translations')
        translated_by_type = {}
        if warning_translations is not None:
            translated_by_type = {
                translation.warning_type: translation.message
                for translation in warning_translations
                if translation.language == language and translation.message
            }

        localized_warning = {}
        for warning_type in ['selected', 'not_selected']:
            if warning_type in self.option_warning:
                warning_config = self.option_warning[warning_type].copy()
                translated_message = translated_by_type.get(warning_type)
                if translated_message is None:
                    translated_message = self.get_warning_message(warning_type, language)
                if translated_message:
                    warning_config['message'] = translated_message
                localized_warning[warning_type] = warning_config

        return localized_warning if localized_warning else None

    def resolve_localized_option_warning_text(self, info, **kwargs):
        """Get localized warning text (primary message from selected warning)"""
        if not self.option_warning:
            return ""

        language = getattr(info.context, 'survey_language', None)
        warning_translations = _get_prefetched_related(self, 'warning_translations')
        translated_by_type = {}
        if warning_translations is not None and language:
            translated_by_type = {
                translation.warning_type: translation.message
                for translation in warning_translations
                if translation.language == language and translation.message
            }

        for warning_type in ['selected', 'not_selected']:
            if warning_type in self.option_warning:
                message = translated_by_type.get(warning_type)
                if message is None:
                    message = self.get_warning_message(warning_type, language)
                if message:
                    return message

        return self.option_warning_text or ""

    def resolve_warning_translations(self, info, **kwargs):
        """Get all warning translations for this option"""
        warning_translations = _get_prefetched_related(self, 'warning_translations')
        if warning_translations is not None:
            return warning_translations
        return self.warning_translations.all()


class OptionConditionNode(DjangoObjectType):
    class Meta:
        model = OptionCondition
        exclude = ()
        interfaces = (relay.Node,)


class OptionWarningTranslationNode(DjangoObjectType):
    class Meta:
        model = OptionWarningTranslation
        exclude = ()
        interfaces = (relay.Node,)


class SurveyConsentTranslationNode(DjangoObjectType):
    language = graphene.String()

    class Meta:
        model = SurveyConsentTranslation
        exclude = ()
        interfaces = (relay.Node,)

    def resolve_language(self, info):
        return self.language.upper() if self.language else self.language


class SubmissionNode(DjangoObjectType):
    signature_image_url = graphene.String()
    photo_image_url = graphene.String()
    latitude = graphene.Float()
    longitude = graphene.Float()
    language = graphene.String()
    title = graphene.String()
    local_id = graphene.String()
    answers = DjangoConnectionField(
        lambda: SubmissionAnswerNode,
        resolver=_resolve_submission_answers,
    )
    
    class Meta:
        model = Submission
        exclude = ()
        filter_fields = {
            'submitted_at': ['exact', 'lt', 'gt', 'date'],
            'status': ['exact'],
            'language': ['exact'],
        }
        interfaces = (relay.Node,)
    
    def resolve_signature_image_url(self, info):
        if self.signature_image:
            return info.context.build_absolute_uri(self.signature_image.url)
        return None
    
    def resolve_photo_image_url(self, info):
        if self.photo_image:
            return info.context.build_absolute_uri(self.photo_image.url)
        return None
    
    def resolve_title(self, info):
        """Resolve title from render_field if set, otherwise fallback to existing logic"""
        answers = _get_prefetched_related(self, 'answers')
        if answers is not None:
            answers = list(answers)
            render_field = getattr(self.survey, 'render_field', None)
            if render_field:
                for answer in answers:
                    if answer.question_id == render_field.id:
                        if answer.text_answer:
                            return answer.text_answer
                        selected_options = _get_prefetched_related(answer, 'selected_options')
                        if selected_options is not None and selected_options:
                            return ", ".join([opt.text for opt in selected_options])

            title_question_keys = ["title", "name", "survey_title", "submission_title"]
            for answer in answers:
                if answer.question.key and answer.question.key.lower() in title_question_keys:
                    if answer.text_answer:
                        return answer.text_answer
                    selected_options = _get_prefetched_related(answer, 'selected_options')
                    if selected_options is not None and selected_options:
                        return ", ".join([opt.text for opt in selected_options])

            return self.survey.title

        # First check if survey has a render_field set
        if self.survey.render_field:
            # Look for an answer to the render_field question
            render_answer = self.answers.filter(question=self.survey.render_field).first()
            if render_answer:
                if render_answer.text_answer:
                    return render_answer.text_answer
                elif render_answer.selected_options.exists():
                    # If it's an option-based question, return the selected option text
                    return ", ".join([opt.text for opt in render_answer.selected_options.all()])
        
        # Fallback to existing logic if render_field is not set or no answer found
        title_question_keys = ["title", "name", "survey_title", "submission_title"]
        
        for answer in self.answers.all():
            # Check if this question has one of the title keys
            if answer.question.key and answer.question.key.lower() in title_question_keys:
                if answer.text_answer:
                    return answer.text_answer
                elif answer.selected_options.exists():
                    # If it's an option-based question, return the selected option text
                    return ", ".join([opt.text for opt in answer.selected_options.all()])
        
        # Final fallback: return the survey title
        return self.survey.title


class SubmissionAnswerNode(DjangoObjectType):
    selected_options = DjangoConnectionField(
        lambda: QuestionOptionNode,
        resolver=_resolve_answer_selected_options,
    )

    class Meta:
        model = SubmissionAnswer
        exclude = ()
        interfaces = (relay.Node,)


class SubmissionStatsType(graphene.ObjectType):
    draft_count = graphene.Int()
    pending_count = graphene.Int()
    completed_count = graphene.Int()
    total_count = graphene.Int()


class AnswerInput(graphene.InputObjectType):
    question_id = graphene.ID(required=True)
    text_answer = graphene.String()
    selected_option_ids = graphene.List(graphene.ID)
    signature_image = graphene.String(description="Base64 encoded signature image")
    photo_image = graphene.String(description="Base64 encoded photo image")
    other_text = graphene.String(description="Text entered when user selects 'Other' option")


def _validate_local_id(local_id):
    """Validate local_id format: must be a valid UUID string, max 100 chars."""
    if not local_id:
        return None
    local_id = str(local_id).strip()
    if len(local_id) > 100:
        return "local_id must be at most 100 characters"
    try:
        uuid.UUID(local_id)
    except ValueError:
        return "local_id must be a valid UUID format"
    return None

def _validate_coordinates(latitude, longitude):
    """Validate latitude/longitude ranges."""
    errors = []
    if latitude is not None and (latitude < -90 or latitude > 90):
        errors.append("Latitude must be between -90 and 90")
    if longitude is not None and (longitude < -180 or longitude > 180):
        errors.append("Longitude must be between -180 and 180")
    return errors


class SubmitSurveyMutation(graphene.Mutation):
    class Arguments:
        survey_id = graphene.ID(required=True)
        answers = graphene.List(AnswerInput, required=True)
        language = graphene.String()
        status = graphene.String()
        submission_id = graphene.ID(description="ID of existing draft submission to update")
        latitude = graphene.Float(description="Geographic latitude coordinate (optional)")
        longitude = graphene.Float(description="Geographic longitude coordinate (optional)")
        local_id = graphene.String(description="Client-generated local UUID for offline sync")
        app_version = graphene.String(description="Version of the mobile app (APK) submitting the survey")

    success = graphene.Boolean()
    submission = graphene.Field(SubmissionNode)
    errors = graphene.List(graphene.String)

    @login_required
    def mutate(self, info, survey_id, answers, language=None, status=None, submission_id=None, latitude=None, longitude=None, local_id=None, app_version=None):
        user = info.context.user
        errors = []
        
        # Set default status if not provided
        if not status:
            status = "completed"
        
        # Validate status
        valid_statuses = ["draft", "pending", "completed"]
        if status not in valid_statuses:
            errors.append(f"Invalid status. Must be of: {', '.join(valid_statuses)}")
            return SubmitSurveyMutation(success=False, errors=errors)
        
        # Validate local_id format
        if local_id:
            local_id_error = _validate_local_id(local_id)
            if local_id_error:
                return SubmitSurveyMutation(success=False, errors=[local_id_error])
        
        # Validate coordinates
        coord_errors = _validate_coordinates(latitude, longitude)
        if coord_errors:
            return SubmitSurveyMutation(success=False, errors=coord_errors)
        
        # For draft submissions, we skip most validations
        is_draft = status == "draft"
        
        try:
            # Handle Relay global ID for survey
            if isinstance(survey_id, str) and len(survey_id) > 10:
                try:
                    from graphql_relay import from_global_id
                    _, actual_survey_id = from_global_id(survey_id)
                except Exception:
                    actual_survey_id = survey_id
            else:
                actual_survey_id = survey_id
                
            # Get survey
            survey = Survey.objects.get(id=actual_survey_id)
            
            # Check if user can access this survey
            if not survey.is_accessible_by_user(user):
                errors.append("You don't have permission to access this survey")
                return SubmitSurveyMutation(success=False, errors=errors)
            
            # Skip survey active check for drafts
            if not is_draft and not survey.is_active:
                errors.append("This survey is not active")
                return SubmitSurveyMutation(success=False, errors=errors)
            
            # Skip multiple submissions check for drafts
            if not is_draft and not survey.allow_multiple_submissions:
                existing = Submission.objects.filter(survey=survey, user=user, status="completed").exists()
                if existing:
                    errors.append("You have already submitted this survey")
                    return SubmitSurveyMutation(success=False, errors=errors)
            
            # If submission_id is provided, update existing draft submission
            if submission_id:
                # Handle Relay global ID for submission
                if isinstance(submission_id, str) and len(submission_id) > 10:
                    try:
                        from graphql_relay import from_global_id
                        _, actual_submission_id = from_global_id(submission_id)
                    except Exception:
                        actual_submission_id = submission_id
                else:
                    actual_submission_id = submission_id
                
                try:
                    submission = Submission.objects.get(id=actual_submission_id, user=user, survey=survey)
                except Submission.DoesNotExist:
                    errors.append("Draft submission not found or you don't have permission to update it")
                    return SubmitSurveyMutation(success=False, errors=errors)
                
                if submission.status != "draft":
                    errors.append("Can only update draft submissions")
                    return SubmitSurveyMutation(success=False, errors=errors)
                
                # Clear existing answers and update submission within a transaction
                with transaction.atomic():
                    submission.answers.all().delete()
                    submission.status = status
                    submission.ip_address = info.context.META.get('REMOTE_ADDR')
                    submission.user_agent = info.context.META.get('HTTP_USER_AGENT', '')
                    if latitude is not None:
                        submission.latitude = latitude
                    if longitude is not None:
                        submission.longitude = longitude
                    if local_id:
                        submission.local_id = local_id
                    if language:
                        submission.language = language
                    if app_version:
                        submission.app_version = app_version
                    submission.save()
            else:
                # Create new submission with geolocation data if provided
                submission_data = {
                    'survey': survey,
                    'user': user,
                    'status': status,
                    'ip_address': info.context.META.get('REMOTE_ADDR'),
                    'user_agent': info.context.META.get('HTTP_USER_AGENT', '')
                }
                
                # Add geolocation data if provided
                if latitude is not None:
                    submission_data['latitude'] = latitude
                if longitude is not None:
                    submission_data['longitude'] = longitude
                if local_id:
                    submission_data['local_id'] = local_id

                # Add language if provided
                if language:
                    submission_data['language'] = language

                # Add app (APK) version if provided
                if app_version:
                    submission_data['app_version'] = app_version
                    
                try:
                    submission = Submission.objects.create(**submission_data)
                except IntegrityError:
                    # Duplicate local_id: return the existing submission
                    if local_id:
                        submission = Submission.objects.get(local_id=local_id, user=user)
                    else:
                        raise
            
            def _decode_relay_id(value):
                if isinstance(value, str) and len(value) > 10:
                    try:
                        from graphql_relay import from_global_id
                        _, decoded = from_global_id(value)
                        return decoded
                    except Exception:
                        return value
                return value

            # Process image uploads
            signature_image = None
            photo_image = None

            question_ids = [_decode_relay_id(answer_data.question_id) for answer_data in answers]
            question_map = Question.objects.filter(id__in=question_ids, survey=survey).in_bulk()
            answer_rows = []
            answer_option_ids = []

            for answer_data, actual_question_id in zip(answers, question_ids):
                question = question_map.get(int(actual_question_id)) if str(actual_question_id).isdigit() else question_map.get(actual_question_id)
                if not question:
                    errors.append(f"Question with ID {answer_data.question_id} not found")
                    continue

                try:
                    # Handle image uploads for signature and photo
                    if question.question_type == "signature" and answer_data.signature_image:
                        signature_image = self._handle_image_upload(
                            answer_data.signature_image,
                            f"signature_{submission.id}_{question.id}",
                            "signature"
                        )

                    if question.question_type == "photo" and answer_data.photo_image:
                        photo_image = self._handle_image_upload(
                            answer_data.photo_image,
                            f"photo_{submission.id}_{question.id}",
                            "photo"
                        )

                    if question.question_type not in ["signature", "photo"]:
                        answer_rows.append(SubmissionAnswer(
                            submission=submission,
                            question=question,
                            text_answer=answer_data.text_answer,
                            other_text=answer_data.other_text
                        ))
                        answer_option_ids.append([
                            _decode_relay_id(option_id)
                            for option_id in (answer_data.selected_option_ids or [])
                        ])

                except Exception as e:
                    errors.append(f"Error processing answer for question {answer_data.question_id}: {str(e)}")

            created_answers = SubmissionAnswer.objects.bulk_create(answer_rows)
            option_pairs = []
            requested_option_ids = {
                int(option_id)
                for option_ids in answer_option_ids
                for option_id in option_ids
                if str(option_id).isdigit()
            }
            valid_option_question = dict(
                QuestionOption.objects.filter(id__in=requested_option_ids).values_list('id', 'question_id')
            )

            for answer, option_ids in zip(created_answers, answer_option_ids):
                for option_id in option_ids:
                    if not str(option_id).isdigit():
                        continue
                    option_id = int(option_id)
                    if valid_option_question.get(option_id) == answer.question_id:
                        option_pairs.append(
                            SubmissionAnswer.selected_options.through(
                                submissionanswer_id=answer.id,
                                questionoption_id=option_id,
                            )
                        )

            if option_pairs:
                SubmissionAnswer.selected_options.through.objects.bulk_create(
                    option_pairs,
                    ignore_conflicts=True,
                )
            
            # Update submission with images if they were uploaded
            if signature_image or photo_image:
                if signature_image:
                    submission.signature_image = signature_image
                if photo_image:
                    submission.photo_image = photo_image
                submission.save()
            
            if errors:
                submission.delete()
                return SubmitSurveyMutation(success=False, errors=errors)

            from .signals import update_submission_computed_title
            update_submission_computed_title(submission)
            
            # Mark invitation as responded only for completed submissions
            if status == "completed":
                invitation = SurveyInvitation.objects.filter(survey=survey, user=user).first()
                if invitation:
                    invitation.mark_as_responded()
            
            return SubmitSurveyMutation(success=True, submission=submission, errors=[])
            
        except Survey.DoesNotExist:
            errors.append("Survey not found")
            return SubmitSurveyMutation(success=False, errors=errors)
        except Exception as e:
            errors.append(f"Unexpected error: {str(e)}")
            return SubmitSurveyMutation(success=False, errors=errors)
    
    def _handle_image_upload(self, base64_data, filename_prefix, image_type):
        """Handle base64 image upload and return the file object"""
        import base64
        from django.core.files.base import ContentFile
        
        try:
            # Extract the base64 data (remove data:image/...;base64, prefix if present)
            if ';base64,' in base64_data:
                format, imgstr = base64_data.split(';base64,')
                ext = format.split('/')[-1]
            else:
                imgstr = base64_data
                ext = 'png'  # default extension
            
            # Decode the base64 data
            data = base64.b64decode(imgstr)
            
            # Create a ContentFile
            filename = f"{filename_prefix}.{ext}"
            return ContentFile(data, name=filename)
            
        except Exception as e:
            raise ValueError(f"Invalid {image_type} image data: {str(e)}")


class CreateSubmissionMutation(graphene.Mutation):
    class Arguments:
        survey_id = graphene.ID(required=True)
        status = graphene.String()
        language = graphene.String(description="Language the survey is being submitted in (optional)")
        latitude = graphene.Float(description="Geographic latitude coordinate (optional)")
        longitude = graphene.Float(description="Geographic longitude coordinate (optional)")
        local_id = graphene.String(description="Client-generated local UUID for offline sync")

    success = graphene.Boolean()
    submission = graphene.Field(SubmissionNode)
    errors = graphene.List(graphene.String)

    @login_required
    def mutate(self, info, survey_id, status=None, language=None, latitude=None, longitude=None, local_id=None):
        user = info.context.user
        errors = []
        
        if not status:
            status = "draft"
        
        valid_statuses = ["draft", "pending", "completed"]
        if status not in valid_statuses:
            errors.append(f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
            return CreateSubmissionMutation(success=False, errors=errors)
        
        # Validate local_id format
        if local_id:
            local_id_error = _validate_local_id(local_id)
            if local_id_error:
                return CreateSubmissionMutation(success=False, errors=[local_id_error])
        
        # Validate coordinates
        coord_errors = _validate_coordinates(latitude, longitude)
        if coord_errors:
            return CreateSubmissionMutation(success=False, errors=coord_errors)
        
        try:
            if isinstance(survey_id, str) and len(survey_id) > 10:
                try:
                    from graphql_relay import from_global_id
                    _, actual_survey_id = from_global_id(survey_id)
                except Exception:
                    actual_survey_id = survey_id
            else:
                actual_survey_id = survey_id
                
            survey = Survey.objects.get(id=actual_survey_id)
            
            if not survey.is_accessible_by_user(user):
                errors.append("You don't have permission to access this survey")
                return CreateSubmissionMutation(success=False, errors=errors)
            
            if status != "draft" and not survey.is_active:
                errors.append("This survey is not active")
                return CreateSubmissionMutation(success=False, errors=errors)
            
            if status != "draft" and not survey.allow_multiple_submissions:
                existing = Submission.objects.filter(survey=survey, user=user, status="completed").exists()
                if existing:
                    errors.append("You have already submitted this survey")
                    return CreateSubmissionMutation(success=False, errors=errors)
            
            # Create submission with geolocation data if provided
            submission_data = {
                'survey': survey,
                'user': user,
                'status': status,
                'ip_address': info.context.META.get('REMOTE_ADDR'),
                'user_agent': info.context.META.get('HTTP_USER_AGENT', '')
            }
            
            # Add geolocation data if provided
            if latitude is not None:
                submission_data['latitude'] = latitude
            if longitude is not None:
                submission_data['longitude'] = longitude
            if local_id:
                submission_data['local_id'] = local_id
            
            # Add language if provided
            if language:
                submission_data['language'] = language
                
            try:
                submission = Submission.objects.create(**submission_data)
            except IntegrityError:
                # Duplicate local_id: return the existing submission
                if local_id:
                    submission = Submission.objects.get(local_id=local_id, user=user)
                else:
                    raise
            
            return CreateSubmissionMutation(success=True, submission=submission, errors=[])
            
        except Survey.DoesNotExist:
            errors.append("Survey not found")
            return CreateSubmissionMutation(success=False, errors=errors)
        except IntegrityError as e:
            errors.append(f"Data integrity error: {str(e)}")
            return CreateSubmissionMutation(success=False, errors=errors)
        except Exception as e:
            errors.append(f"Unexpected error: {str(e)}")
            return CreateSubmissionMutation(success=False, errors=errors)


class SaveAnswerMutation(graphene.Mutation):
    class Arguments:
        submission_id = graphene.ID(required=True)
        question_id = graphene.ID(required=True)
        text_answer = graphene.String()
        selected_option_ids = graphene.List(graphene.ID)
        other_text = graphene.String(description="Text entered when user selects 'Other' option")

    success = graphene.Boolean()
    answer = graphene.Field(SubmissionAnswerNode)
    errors = graphene.List(graphene.String)

    @login_required
    def mutate(self, info, submission_id, question_id, text_answer=None, selected_option_ids=None, other_text=None):
        user = info.context.user
        errors = []
        
        try:
            if isinstance(submission_id, str) and len(submission_id) > 10:
                try:
                    from graphql_relay import from_global_id
                    _, actual_submission_id = from_global_id(submission_id)
                except Exception:
                    actual_submission_id = submission_id
            else:
                actual_submission_id = submission_id
                
            if isinstance(question_id, str) and len(question_id) > 10:
                try:
                    from graphql_relay import from_global_id
                    _, actual_question_id = from_global_id(question_id)
                except Exception:
                    actual_question_id = question_id
            else:
                actual_question_id = question_id
            
            submission = Submission.objects.get(id=actual_submission_id, user=user)
            question = Question.objects.get(id=actual_question_id, survey=submission.survey)
            
            # Determine how to save the answer based on question type
            if question.question_type in ["radio", "checkbox", "dropdown"]:
                # Option-based questions: save in selected_options, clear text_answer
                answer, created = SubmissionAnswer.objects.get_or_create(
                    submission=submission,
                    question=question,
                    defaults={'text_answer': None, 'other_text': other_text}
                )
                
                if not created:
                    answer.text_answer = None
                    answer.selected_options.clear()
                    answer.other_text = other_text
                
                # Handle selected options
                if selected_option_ids:
                    actual_option_ids = []
                    for option_id in selected_option_ids:
                        if isinstance(option_id, str) and len(option_id) > 10:
                            try:
                                from graphql_relay import from_global_id
                                _, actual_option_id = from_global_id(option_id)
                                actual_option_ids.append(actual_option_id)
                            except Exception:
                                actual_option_ids.append(option_id)
                        else:
                            actual_option_ids.append(option_id)
                    
                    # Validate that options belong to this question
                    options = QuestionOption.objects.filter(
                        id__in=actual_option_ids,
                        question=question
                    )
                    
                    if len(options) != len(actual_option_ids):
                        errors.append("One or more selected options do not belong to this question")
                        return SaveAnswerMutation(success=False, errors=errors)
                    
                    # For radio buttons, ensure only one option is selected
                    if question.question_type == "radio" and len(options) > 1:
                        errors.append("Radio button questions can only have one option selected")
                        return SaveAnswerMutation(success=False, errors=errors)
                    
                    answer.selected_options.set(options)
                
                answer.save()
                
            else:
                # Text-based questions: save in text_answer, clear selected_options
                answer, created = SubmissionAnswer.objects.get_or_create(
                    submission=submission,
                    question=question,
                    defaults={'text_answer': text_answer, 'other_text': other_text}
                )
                
                if not created:
                    answer.text_answer = text_answer
                    answer.selected_options.clear()
                    answer.other_text = other_text
                    answer.save()
                
                # Validate that no options were provided for text-based questions
                if selected_option_ids:
                    errors.append(f"Question type '{question.question_type}' should not have selected options")
                    return SaveAnswerMutation(success=False, errors=errors)
            
            return SaveAnswerMutation(success=True, answer=answer, errors=[])
            
        except Submission.DoesNotExist:
            errors.append("Submission not found or you don't have permission to modify it")
            return SaveAnswerMutation(success=False, errors=errors)
        except Question.DoesNotExist:
            errors.append("Question not found")
            return SaveAnswerMutation(success=False, errors=errors)
        except Exception as e:
            errors.append(f"Unexpected error: {str(e)}")
            return SaveAnswerMutation(success=False, errors=errors)


class Query(graphene.ObjectType):
    # Survey queries
    survey = graphene.Field(SurveyNode, id=graphene.ID(required=True), language=graphene.String())
    all_surveys = DjangoFilterConnectionField(SurveyNode)
    my_surveys = DjangoFilterConnectionField(SurveyNode)
    accessible_surveys = DjangoFilterConnectionField(SurveyNode)
    
    # Survey components
    survey_page = relay.Node.Field(SurveyPageNode)
    question = relay.Node.Field(QuestionNode)
    question_option = relay.Node.Field(QuestionOptionNode)
    question_condition = relay.Node.Field(QuestionConditionNode)
    
    # Submissions
    submission = graphene.Field(SubmissionNode, id=graphene.ID(required=True))
    submission_by_local_id = graphene.Field(SubmissionNode, local_id=graphene.String(required=True))
    my_submissions = DjangoFilterConnectionField(SubmissionNode, survey_id=graphene.ID())
    survey_submissions = DjangoFilterConnectionField(SubmissionNode)
    submission_statistics = graphene.Field(SubmissionStatsType)

    @login_required
    def resolve_survey(self, info, id, language=None):
        user = info.context.user

        try:
            # Handle Relay global ID
            if isinstance(id, str) and len(id) > 10:
                try:
                    from graphql_relay import from_global_id
                    _, survey_id = from_global_id(id)
                except Exception:
                    survey_id = id
            else:
                survey_id = id

            # Fix 1: Auth check BEFORE expensive prefetch
            lightweight_survey = Survey.objects.select_related(
                'created_by',
            ).get(id=survey_id)

            if not lightweight_survey.is_accessible_by_user(user):
                return None

            survey = _survey_with_form_prefetches(
                Survey.objects.filter(id=survey_id),
                user,
            ).get()

            survey._available_languages = _get_survey_languages_from_prefetch(survey) or [survey.default_language]

            info.context.survey_default_language = survey.default_language
            if language:
                info.context.survey_language = language
            return survey
        except Survey.DoesNotExist:
            return None

    @login_required
    def resolve_all_surveys(self, info, **kwargs):
        user = info.context.user
        if is_manager_or_admin(user):
            return _survey_with_form_prefetches(Survey.objects.all(), user)
        return Survey.objects.none()

    @login_required
    def resolve_my_surveys(self, info, **kwargs):
        user = info.context.user
        return Survey.objects.filter(created_by=user).annotate(
            _submission_count=models.Count('submissions')
        )

    @login_required
    def resolve_accessible_surveys(self, info, **kwargs):
        from .selectors import get_accessible_surveys
        user = info.context.user

        # get_accessible_surveys applies the role-based bypass unless the
        # account is pinned to its assigned projects.
        return get_accessible_surveys(user).filter(is_active=True).distinct()

    @login_required
    def resolve_submission_by_local_id(self, info, local_id):
        from .selectors import is_submission_visible
        user = info.context.user
        try:
            submission = _submission_with_detail_prefetches(
                Submission.objects.filter(local_id=local_id, user=user)
            ).get()
        except Submission.DoesNotExist:
            return None
        if not is_submission_visible(user, submission):
            return None
        return submission

    def resolve_submission(self, info, id):
        try:
            if isinstance(id, str) and len(id) > 10:
                try:
                    from graphql_relay import from_global_id
                    _, submission_id = from_global_id(id)
                except Exception:
                    submission_id = id
            else:
                submission_id = id
            return _submission_with_detail_prefetches(
                Submission.objects.filter(id=submission_id)
            ).get()
        except Submission.DoesNotExist:
            return None

    @login_required
    def resolve_my_submissions(self, info, survey_id=None, **kwargs):
        from .selectors import apply_submission_visibility
        user = info.context.user
        submissions = apply_submission_visibility(
            _submission_with_detail_prefetches(Submission.objects.filter(user=user)),
            user,
        )

        if survey_id:
            if isinstance(survey_id, str) and len(survey_id) > 10:
                try:
                    from graphql_relay import from_global_id
                    _, actual_survey_id = from_global_id(survey_id)
                except Exception:
                    actual_survey_id = survey_id
            else:
                actual_survey_id = survey_id

            submissions = submissions.filter(survey_id=actual_survey_id)

        return submissions.order_by('-submitted_at')

    @login_required
    def resolve_survey_submissions(self, info, **kwargs):
        from .selectors import apply_submission_visibility
        user = info.context.user
        if is_manager_or_admin(user):
            qs = Submission.objects.all()
        else:
            qs = Submission.objects.filter(survey__created_by=user)
        qs = apply_submission_visibility(qs, user)
        return qs.select_related('survey', 'user').order_by('-submitted_at')

    @login_required
    def resolve_submission_statistics(self, info):
        from django.db.models import Count
        from .selectors import apply_submission_visibility
        user = info.context.user

        # Get submissions the user can access (managers see all)
        if is_manager_or_admin(user):
            submissions = Submission.objects.all()
        else:
            submissions = Submission.objects.filter(user=user)
        submissions = apply_submission_visibility(submissions, user)
        
        # Count by status
        stats = submissions.values('status').annotate(count=Count('id'))
        
        draft_count = 0
        pending_count = 0
        completed_count = 0
        
        for stat in stats:
            if stat['status'] == 'draft':
                draft_count = stat['count']
            elif stat['status'] == 'pending':
                pending_count = stat['count']
            elif stat['status'] == 'completed':
                completed_count = stat['count']
        
        total_count = draft_count + pending_count + completed_count
        
        return SubmissionStatsType(
            draft_count=draft_count,
            pending_count=pending_count,
            completed_count=completed_count,
            total_count=total_count
        )

    @login_required
    def resolve_sub_questions(self, info, option_id=None, **kwargs):
        if option_id:
            # Handle Relay global ID
            if isinstance(option_id, str) and len(option_id) > 10:
                try:
                    from graphql_relay import from_global_id
                    _, actual_option_id = from_global_id(option_id)
                except Exception:
                    actual_option_id = option_id
            else:
                actual_option_id = option_id
            
            try:
                option = QuestionOption.objects.get(id=actual_option_id)
                return option.sub_questions.all()
            except QuestionOption.DoesNotExist:
                return Question.objects.none()
        
        # If no option_id provided, return all questions that are sub_questions
        return Question.objects.filter(parent_options__isnull=False).distinct()


class Mutation(graphene.ObjectType):
    submit_survey = SubmitSurveyMutation.Field()
    create_submission = CreateSubmissionMutation.Field()
    save_answer = SaveAnswerMutation.Field()
