from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.conf import settings
from django.core.validators import MinLengthValidator, MaxLengthValidator
import re
from django.db.models import Q
from django.template import Template, Context


class Survey(models.Model):
    DISPLAY_MODE_CHOICES = [
        ("page_wise", "Page-wise (Show all questions in a page at once)"),
        ("question_per_page", "Question per page (One question at a time)"),
    ]
    
    VISIBILITY_CHOICES = [
        ("private", "Private (Only invited users can access)"),
        ("public", "Public (Anyone with the link can access)"),
    ]
    
    title = models.CharField(
        max_length=255,
        validators=[MinLengthValidator(3)],
        help_text="Survey title must be at least 3 characters long"
    )
    description = models.TextField(
        blank=True, null=True,
        validators=[MaxLengthValidator(2000)],
        help_text="Description cannot exceed 2000 characters"
    )
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="surveys")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    end_date = models.DateField(blank=True, null=True, help_text="Optional end date for the survey")
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Soft-delete flag used to hide surveys from normal project lists without removing data.",
    )
    allow_multiple_submissions = models.BooleanField(default=False)
    display_mode = models.CharField(max_length=20, choices=DISPLAY_MODE_CHOICES, default="page_wise")
    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default="private")
    default_language = models.CharField(
        max_length=10,
        default="en",
        choices=settings.LANGUAGES,
    )
    consent_text = models.TextField(
        blank=True,
        null=True,
        validators=[MaxLengthValidator(5000)],
        help_text="Consent text displayed to respondents before they start the survey (max 5000 characters)"
    )
    render_in_response = models.BooleanField(
        default=True,
        help_text="Whether to render this survey in response output"
    )
    render_field = models.ForeignKey(
        'Question',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='surveys_as_render_field',
        help_text="Question whose answer will be used as the title for submissions"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def clean(self):
        if self.title and len(self.title.strip()) < 3:
            raise ValidationError({'title': 'Survey title must be at least 3 characters long (excluding whitespace).'})
        
        if self.description and len(self.description) > 2000:
            raise ValidationError({'description': 'Description cannot exceed 2000 characters.'})
        
        # Validate that survey has at least one page when marked as active
        if self.is_active and self.pk and not self.pages.exists():
            raise ValidationError({'is_active': 'Cannot activate survey without at least one page.'})

    def get_supported_languages(self):
        """Get all languages that have translations for this survey"""
        languages = set([self.default_language])
        
        # Get all question translation languages in a single query
        question_languages = QuestionTranslation.objects.filter(
            question__survey=self
        ).values_list('language', flat=True).distinct()
        languages.update(question_languages)
        
        # Get all page translation languages in a single query
        page_languages = SurveyPageTranslation.objects.filter(
            page__survey=self
        ).values_list('language', flat=True).distinct()
        languages.update(page_languages)
        
        return list(languages)
    
    def is_accessible_by_user(self, user, include_public=True):
        """Check if a user can access this survey.

        include_public=False is for management pages: a public survey is
        answerable by anyone, but it must not appear in the admin listings or
        open its detail page to an account restricted to assigned projects.
        """
        if not user.is_authenticated:
            return include_public and self.visibility == 'public'

        # Restricted accounts never get the role-based bypass, even when they
        # are staff, a Manager or a superuser. For partner accounts this is
        # automatic once an admin has assigned them projects.
        from .selectors import (
            get_partner_admin_survey_id,
            is_survey_access_restricted,
        )

        # Partner admins are hardcoded to one survey, regardless of grants.
        partner_admin_survey_id = get_partner_admin_survey_id(user)
        if partner_admin_survey_id is not None:
            if self.pk == partner_admin_survey_id:
                return True
            return include_public and self.visibility == 'public'

        if is_survey_access_restricted(user):
            if self.created_by == user:
                return True
            if self.invitations.filter(user=user, is_active=True).exists():
                return True
            return include_public and self.visibility == 'public'

        # Managers, staff, and superusers have access to all surveys
        if user.is_staff or user.is_superuser or user.groups.filter(name='Manager').exists():
            return True

        # Survey creator always has access
        if self.created_by == user:
            return True

        # Check if user has explicit permissions
        if user.has_perm('survey.view_survey', self):
            return True

        # Check if user is invited to this survey
        if self.invitations.filter(user=user, is_active=True).exists():
            return True

        # Public surveys are accessible to anyone
        return self.visibility == 'public'
    
    def get_invited_users(self):
        """Get all users who are invited to this survey"""
        return User.objects.filter(
            survey_invitations__survey=self,
            survey_invitations__is_active=True
        ).distinct()

    def get_consent_text(self, language=None):
        """Get consent text in specified language, fallback to default"""
        if language and language != self.default_language:
            translation = self.consent_translations.filter(language=language).first()
            if translation and translation.consent_text:
                return translation.consent_text
        return self.consent_text
    
    def invite_user(self, user, invited_by):
        """Invite a user to participate in this survey"""
        invitation, created = SurveyInvitation.objects.get_or_create(
            survey=self,
            user=user,
            defaults={'invited_by': invited_by}
        )
        if not created and not invitation.is_active:
            invitation.is_active = True
            invitation.invited_by = invited_by
            invitation.save()
        return invitation

    def duplicate(self, new_title, created_by):
        """
        Create a complete deep copy of this survey with all related objects.
        
        Args:
            new_title: Title for the new survey
            created_by: User who will own the cloned survey
        
        Returns:
            The newly created Survey instance
        """
        from django.db import transaction
        import json
        
        with transaction.atomic():
            # Step 1: Clone the survey itself
            new_survey = Survey.objects.create(
                title=new_title,
                description=self.description,
                created_by=created_by,
                end_date=self.end_date,
                is_active=False,  # Start as inactive for safety
                allow_multiple_submissions=self.allow_multiple_submissions,
                display_mode=self.display_mode,
                visibility=self.visibility,
                default_language=self.default_language,
                consent_text=self.consent_text,
                render_in_response=self.render_in_response,
                render_field=None,  # Will be updated later if applicable
            )
            
            # Mappings from old IDs to new instances
            page_map = {}  # old_page_id -> new_page
            question_map = {}  # old_question_id -> new_question
            option_map = {}  # old_option_id -> new_option
            
            # Step 2: Clone pages
            for old_page in self.pages.all().order_by('order'):
                new_page = SurveyPage.objects.create(
                    survey=new_survey,
                    title=old_page.title,
                    description=old_page.description,
                    order=old_page.order,
                )
                page_map[old_page.id] = new_page
                
                # Clone page translations
                for translation in old_page.translations.all():
                    SurveyPageTranslation.objects.create(
                        page=new_page,
                        language=translation.language,
                        title=translation.title,
                        description=translation.description,
                    )
            
            # Step 3: Clone questions (first pass - basic fields)
            for old_question in self.questions.all().order_by('page__order', 'order'):
                new_page = page_map.get(old_question.page_id) if old_question.page_id else None
                
                new_question = Question.objects.create(
                    survey=new_survey,
                    page=new_page,
                    key=old_question.key,
                    text=old_question.text,
                    question_type=old_question.question_type,
                    is_required=old_question.is_required,
                    order=old_question.order,
                    is_conditional=old_question.is_conditional,
                    filter_options_question=None,  # Will be updated in second pass
                    min_length=old_question.min_length,
                    max_length=old_question.max_length,
                    min_value=old_question.min_value,
                    max_value=old_question.max_value,
                    validation_pattern=old_question.validation_pattern,
                    validation_message=old_question.validation_message,
                    hint=old_question.hint,
                    evaluator_condition=old_question.evaluator_condition,
                    group=old_question.group,
                )
                question_map[old_question.id] = new_question
                
                # Clone question translations
                for translation in old_question.translations.all():
                    QuestionTranslation.objects.create(
                        question=new_question,
                        language=translation.language,
                        text=translation.text,
                    )
                
                # Clone hint translations
                for hint_trans in old_question.hint_translations.all():
                    QuestionHintTranslation.objects.create(
                        question=new_question,
                        language=hint_trans.language,
                        hint_text=hint_trans.hint_text,
                    )
            
            # Step 4: Clone question options
            for old_question in self.questions.all():
                new_question = question_map[old_question.id]
                
                for old_option in old_question.options.all().order_by('order'):
                    # Deep copy the option_warning JSON and update target_question_id references
                    new_option_warning = None
                    if old_option.option_warning:
                        new_option_warning = json.loads(json.dumps(old_option.option_warning))
                        # Update target_question_id in warning configs
                        for key in ['selected', 'not_selected']:
                            if key in new_option_warning and 'target_question_id' in new_option_warning[key]:
                                old_target_id = new_option_warning[key]['target_question_id']
                                if old_target_id in question_map:
                                    new_option_warning[key]['target_question_id'] = question_map[old_target_id].id
                    
                    new_option = QuestionOption.objects.create(
                        question=new_question,
                        text=old_option.text,
                        value=old_option.value,
                        order=old_option.order,
                        is_conditional=old_option.is_conditional,
                        option_warning=new_option_warning,
                        option_warning_text=old_option.option_warning_text,
                    )
                    option_map[old_option.id] = new_option
                    
                    # Clone option translations
                    for translation in old_option.translations.all():
                        QuestionOptionTranslation.objects.create(
                            option=new_option,
                            language=translation.language,
                            text=translation.text,
                        )
                    
                    # Clone option warning translations
                    for warning_trans in old_option.warning_translations.all():
                        OptionWarningTranslation.objects.create(
                            option=new_option,
                            language=warning_trans.language,
                            warning_type=warning_trans.warning_type,
                            message=warning_trans.message,
                        )
            
            # Step 5: Update self-references in questions (filter_options_question)
            for old_question in self.questions.all():
                if old_question.filter_options_question_id:
                    new_question = question_map[old_question.id]
                    new_filter_question = question_map.get(old_question.filter_options_question_id)
                    if new_filter_question:
                        new_question.filter_options_question = new_filter_question
                        new_question.save(update_fields=['filter_options_question'])
            
            # Step 6: Clone question conditions
            for old_question in self.questions.all():
                new_question = question_map[old_question.id]
                
                for old_condition in old_question.conditions.all():
                    new_target_question = question_map.get(old_condition.target_question_id)
                    if new_target_question:
                        QuestionCondition.objects.create(
                            question=new_question,
                            target_question=new_target_question,
                            condition_type=old_condition.condition_type,
                            value=old_condition.value,
                            logic_operator=old_condition.logic_operator,
                            order=old_condition.order,
                        )
            
            # Step 7: Clone option conditions
            for old_question in self.questions.all():
                for old_option in old_question.options.all():
                    new_option = option_map[old_option.id]
                    
                    for old_condition in old_option.conditions.all():
                        new_target_question = question_map.get(old_condition.target_question_id)
                        if new_target_question:
                            OptionCondition.objects.create(
                                option=new_option,
                                target_question=new_target_question,
                                condition_type=old_condition.condition_type,
                                value=old_condition.value,
                                logic_operator=old_condition.logic_operator,
                                order=old_condition.order,
                            )
            
            # Step 8: Clone sub_questions many-to-many relationships
            for old_question in self.questions.all():
                for old_option in old_question.options.all():
                    new_option = option_map[old_option.id]
                    
                    for old_sub_question in old_option.sub_questions.all():
                        new_sub_question = question_map.get(old_sub_question.id)
                        if new_sub_question:
                            new_option.sub_questions.add(new_sub_question)
            
            # Step 9: Clone consent translations
            for translation in self.consent_translations.all():
                SurveyConsentTranslation.objects.create(
                    survey=new_survey,
                    language=translation.language,
                    consent_text=translation.consent_text,
                )
            
            # Step 10: Update render_field reference
            if self.render_field_id:
                new_render_field = question_map.get(self.render_field_id)
                if new_render_field:
                    new_survey.render_field = new_render_field
                    new_survey.save(update_fields=['render_field'])
            
            return new_survey


class SurveyPage(models.Model):
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="pages")
    title = models.CharField(
        max_length=255, 
        blank=True, 
        null=True,
        validators=[MinLengthValidator(2)],
        help_text="Page title must be at least 2 characters if provided"
    )
    description = models.TextField(
        blank=True, 
        null=True,
        validators=[MaxLengthValidator(1000)],
        help_text="Description cannot exceed 1000 characters"
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.survey.title} - Page {self.order + 1}"

    def clean(self):
        if self.title and len(self.title.strip()) < 2:
            raise ValidationError({'title': 'Page title must be at least 2 characters long (excluding whitespace).'})
        
        if self.description and len(self.description) > 1000:
            raise ValidationError({'description': 'Page description cannot exceed 1000 characters.'})
        
        if self.order < 0:
            raise ValidationError({'order': 'Page order cannot be negative.'})

    def get_title(self, language=None):
        """Get page title in specified language, fallback to default"""
        if language and language != self.survey.default_language:
            translation = self.translations.filter(language=language).first()
            if translation and translation.title:
                return translation.title
        return self.title

    def get_description(self, language=None):
        """Get page description in specified language, fallback to default"""
        if language and language != self.survey.default_language:
            translation = self.translations.filter(language=language).first()
            if translation and translation.description:
                return translation.description
        return self.description


class Question(models.Model):
    QUESTION_TYPES = [
        ("text", "Text"),
        ("textarea", "Textarea"),
        ("radio", "Radio Button"),
        ("checkbox", "Checkbox"),
        ("dropdown", "Dropdown"),
        ("number", "Number"),
        ("email", "Email"),
        ("date", "Date"),
        ("time", "Time"),
        ("datetime", "Date & Time"),
        ("note", "Note/Information"),
        ("consent", "Consent Page"),
    ]
    
    # Conditional logic field
    is_conditional = models.BooleanField(
        default=False,
        help_text="Whether this question has conditional logic"
    )

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="questions")
    page = models.ForeignKey(SurveyPage, on_delete=models.CASCADE, related_name="questions", null=True, blank=True)
    key = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Unique identifier for this question within the survey (used in template variables)"
    )
    text = models.TextField(
        validators=[MinLengthValidator(5)],
        help_text="Question text must be at least 5 characters long"
    )
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPES, default="text")
    is_required = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    
    # Filter options from another question (for option-based questions)
    filter_options_question = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='options_filtered_dependents',
        help_text="If set, this question's options are filtered/mapped from the selected options of the referenced question"
    )
    
    # Validation fields
    min_length = models.PositiveIntegerField(null=True, blank=True, help_text="Minimum length for text inputs")
    max_length = models.PositiveIntegerField(null=True, blank=True, help_text="Maximum length for text inputs")
    min_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, help_text="Minimum value for number inputs")
    max_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, help_text="Maximum value for number inputs")
    validation_pattern = models.CharField(max_length=500, null=True, blank=True, help_text="Regex pattern for custom validation")
    validation_message = models.CharField(max_length=255, null=True, blank=True, help_text="Custom error message for validation")
    hint = models.TextField(
        null=True,
        blank=True,
        max_length=1000,
        help_text="Optional hint text to help respondents answer the question (max 1000 characters)"
    )
    # Evaluator condition (applicable mainly to consent questions)
    evaluator_condition = models.TextField(
        null=True,
        blank=True,
        help_text="Optional expression/flag used by evaluators (primarily for consent questions)"
    )
    group = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Optional group name for organizing related questions"
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.survey.title} - {self.text[:50]}"

    def clean(self):
        # Validate question text
        if self.text and len(self.text) > 1000:
            raise ValidationError({'text': 'Question text cannot exceed 1000 characters.'})
        
        # Validate question text contains actual content (not just special characters)
        if self.text and not re.search(r'[a-zA-Z0-9]', self.text.strip()):
            raise ValidationError({'text': 'Question text must contain at least one alphanumeric character.'})
        
        # Validate order
        if self.order < 0:
            raise ValidationError({'order': 'Question order cannot be negative.'})
        
        # Skip option validation during creation (when pk is None)
        if self.pk and self.question_type in ["radio", "checkbox", "dropdown"] and not self.options.exists():
            raise ValidationError("Questions with radio, checkbox, or dropdown type must have at least one option.")
        
        # Validate email question type specific rules
        if self.question_type == 'email' and self.is_required:
            # Additional validation can be added here if needed
            pass
        
        # Validate number question type
        if self.question_type == 'number':
            if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
                raise ValidationError({'min_value': 'Minimum value cannot be greater than maximum value.'})
        
        # Validate text length constraints for text-based questions
        if self.question_type in ['text', 'textarea', 'email']:
            if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
                raise ValidationError({'min_length': 'Minimum length cannot be greater than maximum length.'})
            if self.min_length is not None and self.min_length < 0:
                raise ValidationError({'min_length': 'Minimum length cannot be negative.'})
            if self.max_length is not None and self.max_length < 1:
                raise ValidationError({'max_length': 'Maximum length must be at least 1.'})
        
        # Note and consent types cannot be required
        if self.question_type in ['note', 'consent'] and self.is_required:
            raise ValidationError({'is_required': 'Note and consent question types cannot be marked as required.'})
        
        # Note and consent types should not have validation rules
        if self.question_type in ['note', 'consent']:
            if self.min_length is not None or self.max_length is not None or self.min_value is not None or self.max_value is not None or self.validation_pattern:
                raise ValidationError({'question_type': 'Note and consent question types cannot have validation rules.'})
        
        # Validate filter options configuration
        if self.filter_options_question:
            # Must belong to the same survey
            if self.filter_options_question.survey_id != self.survey_id:
                raise ValidationError({'filter_options_question': 'Filter options question must belong to the same survey.'})
            # This question must be option-based
            if self.question_type not in ["radio", "checkbox", "dropdown"]:
                raise ValidationError({'filter_options_question': 'Only option-based questions can use filter options question.'})
            # The referenced question must also be option-based
            if self.filter_options_question.question_type not in ["radio", "checkbox", "dropdown"]:
                raise ValidationError({'filter_options_question': 'The referenced question must be radio/checkbox/dropdown.'})
        
        # Validate key uniqueness within survey
        if self.key and self.survey_id:
            existing_questions = Question.objects.filter(
                survey=self.survey,
                key=self.key
            )
            if self.pk:
                existing_questions = existing_questions.exclude(pk=self.pk)
            
            if existing_questions.exists():
                raise ValidationError({'key': 'Question key must be unique within this survey.'})
        
        # Validate regex pattern
        if self.validation_pattern:
            try:
                re.compile(self.validation_pattern)
            except re.error:
                raise ValidationError({'validation_pattern': 'Invalid regular expression pattern.'})

    def requires_options(self):
        return self.question_type in ["radio", "checkbox", "dropdown"]
    
    def is_display_only(self):
        """Check if this question is for display only (note/consent) and doesn't collect answers"""
        return self.question_type in ["note", "consent"]
    
    def get_processed_text(self, context_data=None):
        """Get question text with template variables processed"""
        if context_data is None:
            context_data = {}
        
        # Only process templates for note and consent types
        if self.question_type in ["note", "consent"]:
            try:
                template = Template(self.text)
                context = Context(context_data)
                return template.render(context)
            except Exception:
                # If template processing fails, return original text
                return self.text
        return self.text
    
    def get_validation_attrs(self):
        """Get HTML validation attributes for this question"""
        attrs = {}
        
        if self.is_required:
            attrs['required'] = True
        
        if self.question_type in ['text', 'textarea', 'email']:
            if self.min_length:
                attrs['minlength'] = self.min_length
            if self.max_length:
                attrs['maxlength'] = self.max_length
        
        if self.question_type == 'number':
            if self.min_value is not None:
                attrs['min'] = str(self.min_value)
            if self.max_value is not None:
                attrs['max'] = str(self.max_value)
            attrs['step'] = '0.01'
        
        if self.validation_pattern:
            attrs['pattern'] = self.validation_pattern
        
        if self.validation_message:
            attrs['title'] = self.validation_message
        
        return attrs

    def get_text(self, language=None):
        """Get question text in specified language, fallback to default"""
        if language and language != self.survey.default_language:
            translation = self.translations.filter(language=language).first()
            if translation and translation.text:
                return translation.text
        return self.text

    def get_hint(self, language=None):
        """Get hint text in specified language, fallback to default"""
        if language and language != self.survey.default_language:
            translation = self.hint_translations.filter(language=language).first()
            if translation and translation.hint_text:
                return translation.hint_text
        return self.hint

    def get_available_languages(self):
        """Get all languages this question has translations for"""
        languages = set([self.survey.default_language])
        # Get languages in a single query
        translation_languages = self.translations.values_list('language', flat=True)
        languages.update(translation_languages)
        return list(languages)
    
    def should_show_conditionally(self, submission_data):
        """Check if this question should be shown based on conditional logic"""
        conditions = self.conditions.all()
        if not conditions:
            return True
        
        # Evaluate all conditions with the specified logic operator
        results = []
        for condition in conditions:
            results.append(condition.evaluate(submission_data))
        
        if self.conditions.filter(logic_operator='OR').exists():
            # If any condition uses OR, use OR logic
            return any(results)
        else:
            # Default to AND logic
            return all(results)


class QuestionCondition(models.Model):
    """Model for conditional logic - when to show/hide questions based on answers"""

    CONDITION_TYPES = [
        ('equals', 'Equals'),
        ('not_equals', 'Not Equals'),
        ('contains', 'Contains'),
        ('not_contains', 'Not Contains'),
        ('greater_than', 'Greater Than'),
        ('less_than', 'Less Than'),
        ('in', 'In (any of)'),
        ('not_in', 'Not In (none of)'),
        ('is_empty', 'Is Empty'),
        ('is_not_empty', 'Is Not Empty'),
    ]
    
    LOGIC_OPERATORS = [
        ('AND', 'AND'),
        ('OR', 'OR'),
    ]
    
    question = models.ForeignKey(
        'Question',
        on_delete=models.CASCADE,
        related_name="conditions",
        help_text="Question that this condition applies to"
    )
    target_question = models.ForeignKey(
        'Question',
        on_delete=models.CASCADE,
        related_name="+",
        help_text="Question whose answer will be evaluated"
    )
    condition_type = models.CharField(
        max_length=20,
        choices=CONDITION_TYPES,
        help_text="Type of condition to evaluate"
    )
    value = models.TextField(
        blank=True,
        null=True,
        help_text="Value to compare against (not needed for empty/not_empty conditions)"
    )
    logic_operator = models.CharField(
        max_length=3,
        choices=LOGIC_OPERATORS,
        default='AND',
        help_text="Logical operator to combine multiple conditions"
    )
    order = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ["order", "id"]
    
    def __str__(self):
        return f"If {self.target_question.text[:30]} {self.get_condition_type_display()} {self.value or ''}"
    
    def clean(self):
        # Validate that target_question and question belong to the same survey
        if self.target_question and self.question:
            if self.target_question.survey != self.question.survey:
                raise ValidationError("Target question and condition question must belong to the same survey.")

        # Validate that value is provided for conditions that require it
        if self.condition_type not in ['is_empty', 'is_not_empty'] and not self.value:
            raise ValidationError({'value': 'Value is required for this condition type.'})

        # Validate that empty/not_empty conditions don't have a value
        if self.condition_type in ['is_empty', 'is_not_empty'] and self.value:
            raise ValidationError({'value': 'Value should not be provided for empty/not_empty conditions.'})

        # For 'in' and 'not_in' conditions, validate that value is a list or can be parsed as one
        if self.condition_type in ['in', 'not_in', 'is_in', 'is in', 'is-in', 'not in', 'not-in']:
            import json
            if not isinstance(self.value, (list, str)):
                raise ValidationError({'value': 'Value must be a list or comma-separated string for in/not_in conditions.'})
            if isinstance(self.value, str):
                try:
                    parsed = json.loads(self.value)
                    if not isinstance(parsed, list):
                        # Try parsing as comma-separated
                        if not self.value.strip():
                            raise ValidationError({'value': 'Value is required for in/not_in conditions.'})
                except (json.JSONDecodeError, ValueError):
                    # Allow comma-separated values as fallback
                    if not self.value.strip():
                        raise ValidationError({'value': 'Value is required for in/not_in conditions.'})

        if self.order < 0:
            raise ValidationError({'order': 'Condition order cannot be negative.'})
    
    def evaluate(self, submission_data):
        """Evaluate this condition against submission data"""
        target_answer = submission_data.get(str(self.target_question.id))

        if not target_answer:
            # If no answer, check if we should show based on condition type
            if self.condition_type in ['is_empty', 'not_equals', 'not_contains', 'not_in']:
                return True
            return False

        # Handle different condition types
        if self.condition_type == 'equals':
            return str(target_answer) == str(self.value)
        elif self.condition_type == 'not_equals':
            return str(target_answer) != str(self.value)
        elif self.condition_type == 'contains':
            return str(self.value) in str(target_answer)
        elif self.condition_type == 'not_contains':
            return str(self.value) not in str(target_answer)
        elif self.condition_type == 'greater_than':
            try:
                return float(target_answer) > float(self.value)
            except (ValueError, TypeError):
                return False
        elif self.condition_type == 'less_than':
            try:
                return float(target_answer) < float(self.value)
            except (ValueError, TypeError):
                return False
        elif self.condition_type in ['in', 'is_in', 'is in', 'is-in']:
            # Handle list values - value can be a list or comma-separated string
            import json
            if isinstance(self.value, list):
                value_list = self.value
            elif isinstance(self.value, str):
                try:
                    value_list = json.loads(self.value)
                except (json.JSONDecodeError, ValueError):
                    # Fall back to comma-separated values
                    value_list = [v.strip() for v in self.value.split(',')]
            else:
                value_list = []
            return str(target_answer) in [str(v) for v in value_list]
        elif self.condition_type in ['not_in', 'not in', 'not-in']:
            # Handle list values - value can be a list or comma-separated string
            import json
            if isinstance(self.value, list):
                value_list = self.value
            elif isinstance(self.value, str):
                try:
                    value_list = json.loads(self.value)
                except (json.JSONDecodeError, ValueError):
                    # Fall back to comma-separated values
                    value_list = [v.strip() for v in self.value.split(',')]
            else:
                value_list = []
            return str(target_answer) not in [str(v) for v in value_list]
        elif self.condition_type == 'is_empty':
            return not bool(target_answer)
        elif self.condition_type == 'is_not_empty':
            return bool(target_answer)

        return False


class OptionCondition(models.Model):
    """Conditional logic for showing/hiding a specific option based on other answers"""

    CONDITION_TYPES = [
        ('equals', 'Equals'),
        ('not_equals', 'Not Equals'),
        ('contains', 'Contains'),
        ('not_contains', 'Not Contains'),
        ('greater_than', 'Greater Than'),
        ('less_than', 'Less Than'),
        ('in', 'In (any of)'),
        ('not_in', 'Not In (none of)'),
        ('is_empty', 'Is Empty'),
        ('is_not_empty', 'Is Not Empty'),
    ]

    LOGIC_OPERATORS = [
        ('AND', 'AND'),
        ('OR', 'OR'),
    ]

    option = models.ForeignKey(
        'QuestionOption',
        on_delete=models.CASCADE,
        related_name="conditions",
        help_text="Option that this condition applies to"
    )
    target_question = models.ForeignKey(
        'Question',
        on_delete=models.CASCADE,
        related_name="+",
        help_text="Question whose answer will be evaluated"
    )
    condition_type = models.CharField(
        max_length=20,
        choices=CONDITION_TYPES,
        help_text="Type of condition to evaluate"
    )
    value = models.TextField(
        blank=True,
        null=True,
        help_text="Value to compare against (not needed for empty/not_empty conditions)"
    )
    logic_operator = models.CharField(
        max_length=3,
        choices=LOGIC_OPERATORS,
        default='AND',
        help_text="Logical operator to combine multiple conditions"
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"If {self.target_question.text[:30]} {self.get_condition_type_display()} {self.value or ''}"

    def clean(self):
        # Validate that target question and option belong to the same survey
        if self.target_question and self.option and self.option.question:
            if self.target_question.survey != self.option.question.survey:
                raise ValidationError("Target question and option must belong to the same survey.")
        # Validate value presence rules
        if self.condition_type not in ['is_empty', 'is_not_empty'] and not self.value:
            raise ValidationError({'value': 'Value is required for this condition type.'})
        if self.condition_type in ['is_empty', 'is_not_empty'] and self.value:
            raise ValidationError({'value': 'Value should not be provided for empty/not_empty conditions.'})

        # For 'in' and 'not_in' conditions, validate that value is a list or can be parsed as one
        if self.condition_type in ['in', 'not_in', 'is_in', 'is in', 'is-in', 'not in', 'not-in']:
            import json
            if not isinstance(self.value, (list, str)):
                raise ValidationError({'value': 'Value must be a list or comma-separated string for in/not_in conditions.'})
            if isinstance(self.value, str):
                try:
                    parsed = json.loads(self.value)
                    if not isinstance(parsed, list):
                        # Try parsing as comma-separated
                        if not self.value.strip():
                            raise ValidationError({'value': 'Value is required for in/not_in conditions.'})
                except (json.JSONDecodeError, ValueError):
                    # Allow comma-separated values as fallback
                    if not self.value.strip():
                        raise ValidationError({'value': 'Value is required for in/not_in conditions.'})

        if self.order < 0:
            raise ValidationError({'order': 'Condition order cannot be negative.'})

    def evaluate(self, submission_data):
        """Evaluate this condition against submission data"""
        target_answer = submission_data.get(str(self.target_question.id))

        if not target_answer:
            if self.condition_type in ['is_empty', 'not_equals', 'not_contains', 'not_in']:
                return True
            return False

        if self.condition_type == 'equals':
            return str(target_answer) == str(self.value)
        elif self.condition_type == 'not_equals':
            return str(target_answer) != str(self.value)
        elif self.condition_type == 'contains':
            return str(self.value) in str(target_answer)
        elif self.condition_type == 'not_contains':
            return str(self.value) not in str(target_answer)
        elif self.condition_type == 'greater_than':
            try:
                return float(target_answer) > float(self.value)
            except (ValueError, TypeError):
                return False
        elif self.condition_type == 'less_than':
            try:
                return float(target_answer) < float(self.value)
            except (ValueError, TypeError):
                return False
        elif self.condition_type in ['in', 'is_in', 'is in', 'is-in']:
            # Handle list values - value can be a list or comma-separated string
            import json
            if isinstance(self.value, list):
                value_list = self.value
            elif isinstance(self.value, str):
                try:
                    value_list = json.loads(self.value)
                except (json.JSONDecodeError, ValueError):
                    # Fall back to comma-separated values
                    value_list = [v.strip() for v in self.value.split(',')]
            else:
                value_list = []
            return str(target_answer) in [str(v) for v in value_list]
        elif self.condition_type in ['not_in', 'not in', 'not-in']:
            # Handle list values - value can be a list or comma-separated string
            import json
            if isinstance(self.value, list):
                value_list = self.value
            elif isinstance(self.value, str):
                try:
                    value_list = json.loads(self.value)
                except (json.JSONDecodeError, ValueError):
                    # Fall back to comma-separated values
                    value_list = [v.strip() for v in self.value.split(',')]
            else:
                value_list = []
            return str(target_answer) not in [str(v) for v in value_list]
        elif self.condition_type == 'is_empty':
            return not bool(target_answer)
        elif self.condition_type == 'is_not_empty':
            return bool(target_answer)

        return False

class QuestionOption(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    text = models.CharField(
        max_length=1000,
        validators=[MinLengthValidator(1)],
        help_text="Option text must be at least 1 character long"
    )
    value = models.CharField(
        max_length=1000,
        null=True,
        blank=True,
        help_text="Optional value/code associated with this option (e.g., score or key)"
    )
    order = models.PositiveIntegerField(default=0)
    # Conditional logic toggle for option
    is_conditional = models.BooleanField(
        default=False,
        help_text="Whether this option has conditional logic"
    )
    option_warning = models.JSONField(
        blank=True,
        null=True,
        help_text="Optional warning condition configuration for this option"
    )
    option_warning_text = models.TextField(
        blank=True,
        null=True,
        help_text="Warning message shown when the warning condition is met"
    )
    sub_questions = models.ManyToManyField(
        'Question',
        blank=True,
        related_name="parent_options",
        help_text="Optional sub-questions that appear when this option is selected"
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        base_str = f"{self.question.text[:30]} - {self.text}"
        if self.sub_questions.exists():
            return f"{base_str} (has {self.sub_questions.count()} sub-question(s))"
        return base_str

    def clean(self):
        if not self.text or len(self.text.strip()) == 0:
            raise ValidationError({'text': 'Option text cannot be empty.'})
        
        if len(self.text) > 255:
            raise ValidationError({'text': 'Option text cannot exceed 255 characters.'})
        
        if self.order < 0:
            raise ValidationError({'order': 'Option order cannot be negative.'})
        
        # Check for duplicate options within the same question
        if self.question_id:
            existing_options = QuestionOption.objects.filter(
                question=self.question,
                text__iexact=self.text.strip()
            )
            if self.pk:
                existing_options = existing_options.exclude(pk=self.pk)
            
            if existing_options.exists():
                raise ValidationError({'text': 'This option already exists for this question.'})

        if self.option_warning in ({}, []):
            self.option_warning = None

        if self.option_warning:
            if not isinstance(self.option_warning, dict):
                raise ValidationError({'option_warning': 'Warning configuration must be a mapping.'})

            raw_warnings = self.option_warning or {}
            if 'selected' not in raw_warnings and 'not_selected' not in raw_warnings:
                # Backward compatibility with legacy single warning structure
                raw_warnings = {'selected': raw_warnings}

            valid_condition_types = [choice[0] for choice in OptionCondition.CONDITION_TYPES]
            normalized_warnings = {}
            collected_messages = []

            for key in ['selected', 'not_selected']:
                entry = raw_warnings.get(key)
                if not entry:
                    continue
                if not isinstance(entry, dict):
                    raise ValidationError({'option_warning': 'Warning entry must be a mapping.'})

                target_question_id = entry.get('target_question_id')
                condition_type = entry.get('condition_type')
                value = entry.get('value', '')
                message = entry.get('message') or entry.get('text') or self.option_warning_text or ''

                if not target_question_id:
                    raise ValidationError({'option_warning': 'Warning target question is required.'})

                target_question = Question.objects.filter(pk=target_question_id).first()
                if not target_question:
                    raise ValidationError({'option_warning': 'Target question for warning does not exist.'})
                if self.question and target_question.survey_id != self.question.survey_id:
                    raise ValidationError({'option_warning': 'Warning target question must belong to the same survey.'})

                if condition_type not in valid_condition_types:
                    raise ValidationError({'option_warning': 'Invalid condition type provided.'})

                requires_value = condition_type not in ['is_empty', 'is_not_empty']
                normalized_value = ''
                if requires_value:
                    if not str(value).strip():
                        raise ValidationError({'option_warning': 'Warning value is required for this condition type.'})
                    normalized_value = str(value).strip()

                normalized_message = message.strip()
                if not normalized_message:
                    raise ValidationError({'option_warning': 'Warning message is required when a warning is configured.'})

                normalized_warnings[key] = {
                    'target_question_id': target_question.id,
                    'condition_type': condition_type,
                    'value': normalized_value,
                    'message': normalized_message,
                }
                collected_messages.append(normalized_message)

            self.option_warning = normalized_warnings or None
            primary_message = collected_messages[0] if collected_messages else ''
            self.option_warning_text = primary_message

        if self.option_warning_text and len(self.option_warning_text.strip()) > 1000:
            raise ValidationError({'option_warning_text': 'Warning text cannot exceed 1000 characters.'})

        if not self.option_warning:
            self.option_warning_text = ''

    def get_text(self, language=None):
        """Get option text in specified language, fallback to default"""
        if language and language != self.question.survey.default_language:
            translation = self.translations.filter(language=language).first()
            if translation and translation.text:
                return translation.text
        return self.text

    def get_warning_message(self, warning_type, language=None):
        """Get warning message in specified language, fallback to default"""
        if not self.option_warning or warning_type not in self.option_warning:
            return None

        default_message = self.option_warning[warning_type].get('message', '')

        if language and language != self.question.survey.default_language:
            translation = self.warning_translations.filter(
                language=language,
                warning_type=warning_type
            ).first()
            if translation and translation.message:
                return translation.message

        return default_message

    def should_show_conditionally(self, submission_data):
        """Check if this option should be shown based on its own conditional logic"""
        conditions = getattr(self, 'conditions', None)
        if conditions is None:
            # Fallback if related name not yet available (during migrations)
            return True
        conditions_qs = self.conditions.all()
        if not conditions_qs:
            return True
        results = [cond.evaluate(submission_data) for cond in conditions_qs]
        if self.conditions.filter(logic_operator='OR').exists():
            return any(results)
        return all(results)


class QuestionTranslation(models.Model):
    """Translation model for questions"""

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=10, choices=settings.LANGUAGES)
    text = models.TextField(
        validators=[MinLengthValidator(5)],
        help_text="Translation text must be at least 5 characters long"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["question", "language"]
        ordering = ["language"]

    def __str__(self):
        return f"{self.question.text[:30]} - {self.get_language_display()}"

    def clean(self):
        if not self.text or len(self.text.strip()) < 5:
            raise ValidationError({'text': 'Translation text must be at least 5 characters long (excluding whitespace).'})
        
        if len(self.text) > 1000:
            raise ValidationError({'text': 'Translation text cannot exceed 1000 characters.'})
        
        # Validate translation text contains actual content
        if not re.search(r'[a-zA-Z0-9]', self.text.strip()):
            raise ValidationError({'text': 'Translation text must contain at least one alphanumeric character.'})
        
        # Don't allow translation in the same language as the survey default
        if self.question and self.language == self.question.survey.default_language:
            raise ValidationError({'language': 'Cannot create translation in the same language as survey default language.'})


class QuestionOptionTranslation(models.Model):
    """Translation model for question options"""

    option = models.ForeignKey(QuestionOption, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=10, choices=settings.LANGUAGES)
    text = models.TextField(
        validators=[MinLengthValidator(1)],
        help_text="Translation text must be at least 1 character long"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["option", "language"]
        ordering = ["language"]

    def __str__(self):
        return f"{self.option.text[:30]} - {self.get_language_display()}"

    def clean(self):
        if not self.text or len(self.text.strip()) == 0:
            raise ValidationError({'text': 'Translation text cannot be empty.'})



class OptionWarningTranslation(models.Model):
    """Translation model for option warning messages"""

    option = models.ForeignKey(QuestionOption, on_delete=models.CASCADE, related_name="warning_translations")
    language = models.CharField(max_length=10, choices=settings.LANGUAGES)
    warning_type = models.CharField(
        max_length=20,
        choices=[('selected', 'When Selected'), ('not_selected', 'When Not Selected')],
        help_text="Which warning condition this translation is for"
    )
    message = models.TextField(
        validators=[MinLengthValidator(1)],
        help_text="Translated warning message"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["option", "language", "warning_type"]
        ordering = ["language", "warning_type"]

    def __str__(self):
        return f"{self.option.text[:30]} - {self.get_warning_type_display()} - {self.get_language_display()}"

    def clean(self):
        if not self.message or len(self.message.strip()) == 0:
            raise ValidationError({'message': 'Warning message translation cannot be empty.'})

        if len(self.message) > 1000:
            raise ValidationError({'message': 'Warning message translation cannot exceed 1000 characters.'})

        # Verify that the option actually has a warning configured for this type
        if self.option and self.option.option_warning:
            warnings = self.option.option_warning
            if self.warning_type not in warnings:
                raise ValidationError({'warning_type': f'Option does not have a warning configured for "{self.warning_type}".'})

        # Prevent translation in the survey's default language
        if self.option and self.language == self.option.question.survey.default_language:
            raise ValidationError({'language': 'Cannot create translation in the same language as survey default language.'})


class SurveyConsentTranslation(models.Model):
    """Translation model for survey consent text"""
    
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="consent_translations")
    language = models.CharField(max_length=10, choices=settings.LANGUAGES)
    consent_text = models.TextField(
        validators=[MaxLengthValidator(5000)],
        help_text="Consent text translation (max 5000 characters)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["survey", "language"]
        ordering = ["language"]

    def __str__(self):
        return f"{self.survey.title[:30]} - {self.get_language_display()}"

    def clean(self):
        if not self.consent_text or len(self.consent_text.strip()) < 5:
            raise ValidationError({'consent_text': 'Consent text translation must be at least 5 characters long (excluding whitespace).'})
        
        if len(self.consent_text) > 5000:
            raise ValidationError({'consent_text': 'Consent text translation cannot exceed 5000 characters.'})
        
        # Don't allow translation in the same language as the survey default
        if self.survey and self.language == self.survey.default_language:
            raise ValidationError({'language': 'Cannot create translation in the same language as survey default language.'})


class QuestionHintTranslation(models.Model):
    """Translation model for question hints"""
    
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="hint_translations")
    language = models.CharField(max_length=10, choices=settings.LANGUAGES)
    hint_text = models.TextField(
        max_length=1000,
        blank=True,
        null=True,
        help_text="Hint translation text (max 1000 characters)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["question", "language"]
        ordering = ["language"]

    def __str__(self):
        return f"{self.question.text[:30]} Hint - {self.get_language_display()}"

    def clean(self):
        if self.hint_text and len(self.hint_text) > 1000:
            raise ValidationError({'hint_text': 'Hint translation text cannot exceed 1000 characters.'})
        
        # Don't allow translation in the same language as the survey default
        if self.question and self.language == self.question.survey.default_language:
            raise ValidationError({'language': 'Cannot create translation in the same language as survey default language.'})


class SurveyPageTranslation(models.Model):
    """Translation model for survey page titles"""
    
    page = models.ForeignKey(SurveyPage, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=10, choices=settings.LANGUAGES)
    title = models.TextField(
        blank=True,
        null=True,
        validators=[MinLengthValidator(2)],
        help_text="Page title translation (must be at least 2 characters if provided)"
    )
    description = models.TextField(
        blank=True,
        null=True,
        validators=[MaxLengthValidator(1000)],
        help_text="Page description translation (max 1000 characters)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["page", "language"]
        ordering = ["language"]

    def __str__(self):
        return f"{self.page.survey.title[:30]} Page {self.page.order + 1} - {self.get_language_display()}"

    def clean(self):
        if self.title and len(self.title.strip()) < 2:
            raise ValidationError({'title': 'Page title translation must be at least 2 characters long (excluding whitespace).'})
        
        if self.description and len(self.description) > 1000:
            raise ValidationError({'description': 'Page description translation cannot exceed 1000 characters.'})
        
        # Don't allow translation in the same language as the survey default
        if self.page and self.language == self.page.survey.default_language:
            raise ValidationError({'language': 'Cannot create translation in the same language as survey default language.'})


class Submission(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("pending", "Pending"),
        ("completed", "Completed"),
    ]
    
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="submissions")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="survey_submissions", null=True, blank=True)
    submitted_by_username = models.CharField(max_length=150, null=True, blank=True)
    submitted_by_full_name = models.CharField(max_length=300, null=True, blank=True)
    session_key = models.CharField(max_length=40, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed")
    submitted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    signature_image = models.ImageField(
        upload_to='survey_submissions/signatures/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text="Upload signature image (if required)"
    )
    photo_image = models.ImageField(
        upload_to='survey_submissions/photos/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text="Upload photo image (if required)"
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Geographic latitude coordinate of the submission"
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Geographic longitude coordinate of the submission"
    )
    language = models.CharField(
        max_length=10,
        choices=settings.LANGUAGES,
        null=True,
        blank=True,
        help_text="Language the survey was submitted in"
    )
    app_version = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Version of the mobile app (APK) that submitted this survey"
    )
    local_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Client-generated UUID for offline sync"
    )
    is_archived = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this submission has been archived"
    )
    computed_title = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        db_index=True,
        help_text="Pre-computed title for duplicate detection and filtering"
    )
    
    class Meta:
        ordering = ["-submitted_at"]
        
    def __str__(self):
        submitter = self.get_submitter_display()
        return f"{self.survey.title} - {submitter} - {self.submitted_at.strftime('%Y-%m-%d %H:%M')}"

    def clean(self):
        # Validate that survey allows multiple submissions if user has already submitted
        if self.user and self.survey_id:
            existing_submissions = Submission.objects.filter(
                survey=self.survey,
                user=self.user
            )
            if self.pk:
                existing_submissions = existing_submissions.exclude(pk=self.pk)
            
            if existing_submissions.exists() and not self.survey.allow_multiple_submissions:
                raise ValidationError('This survey does not allow multiple submissions per user.')
        
        # Validate that survey is active
        if self.survey and not self.survey.is_active:
            raise ValidationError('Cannot submit to an inactive survey.')
        
        # Validate image file sizes
        if self.signature_image:
            if self.signature_image.size > 5 * 1024 * 1024:  # 5MB
                raise ValidationError({'signature_image': 'Signature image must be less than 5MB.'})
        
        if self.photo_image:
            if self.photo_image.size > 10 * 1024 * 1024:  # 10MB
                raise ValidationError({'photo_image': 'Photo image must be less than 10MB.'})
    
    def save(self, *args, **kwargs):
        if self.user_id and self.user:
            self.submitted_by_username = self.user.username
            full_name = self.user.get_full_name().strip()
            self.submitted_by_full_name = full_name or None
        super().save(*args, **kwargs)

    def get_submitter_display(self):
        if self.user:
            return self.user.username
        if self.submitted_by_username:
            return self.submitted_by_username
        return "Anonymous"

    def get_submitter_full_name(self):
        if self.user:
            return self.user.get_full_name() or self.user.username
        return self.submitted_by_full_name or self.submitted_by_username or "Anonymous"
    
    def get_title(self):
        """Calculate submission title using the same logic as GraphQL resolver"""
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
    
    @property
    def title(self):
        """Property to get submission title, matching GraphQL resolver behavior"""
        return self.get_title()


class SubmissionAnswer(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    text_answer = models.TextField(null=True, blank=True)
    selected_options = models.ManyToManyField(QuestionOption, blank=True)
    other_text = models.TextField(
        null=True,
        blank=True,
        help_text="Text entered when user selects 'Other' option"
    )
    
    class Meta:
        unique_together = ["submission", "question"]
        
    def __str__(self):
        return f"{self.submission} - {self.question.text[:50]}"

    def clean(self):
        # Validate that required questions have answers
        if self.question and self.question.is_required:
            has_text_answer = self.text_answer and self.text_answer.strip()
            has_selected_options = self.selected_options.exists() if self.pk else False
            
            if not has_text_answer and not has_selected_options:
                raise ValidationError('This question is required and must have an answer.')
        
        # Validate email format for email questions
        if self.question and self.question.question_type == 'email' and self.text_answer:
            email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_regex, self.text_answer.strip()):
                raise ValidationError({'text_answer': 'Please enter a valid email address.'})
        
        # Validate number format for number questions
        if self.question and self.question.question_type == 'number' and self.text_answer:
            try:
                float(self.text_answer.strip())
            except ValueError:
                raise ValidationError({'text_answer': 'Please enter a valid number.'})
        
        # Validate that answers match question type
        if self.question:
            if self.question.question_type in ['radio', 'checkbox', 'dropdown']:
                # These should have selected_options, not text_answer
                if self.text_answer and self.text_answer.strip():
                    raise ValidationError('This question type should use selected options, not text answer.')
            else:
                # Text-based questions should not have selected_options
                if self.pk and self.selected_options.exists():
                    raise ValidationError('This question type should use text answer, not selected options.')
        
        # Validate radio button questions have only one option selected
        if self.question and self.question.question_type == 'radio' and self.pk:
            if self.selected_options.count() > 1:
                raise ValidationError('Radio button questions can only have one option selected.')
    
    def get_answer_display(self):
        if self.selected_options.exists():
            answer_parts = [option.text for option in self.selected_options.all()]
            if self.other_text:
                answer_parts.append(f"Other: {self.other_text}")
            return ", ".join(answer_parts)
        return self.text_answer or ""

    def get_answer_display_value(self):
        """Return option values (codes) instead of labels for option-based questions."""
        if self.selected_options.exists():
            answer_parts = [opt.value if opt.value else str(opt.order + 1) for opt in self.selected_options.all()]
            if self.other_text:
                answer_parts.append(f"Other: {self.other_text}")
            return ", ".join(answer_parts)
        return self.text_answer or ""


class SurveyInvitation(models.Model):
    """Model to track user access to private surveys - users get direct access without needing to accept"""
    
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="invitations")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="survey_invitations")
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_survey_invitations")
    invited_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ["survey", "user"]
        ordering = ["-invited_at"]
    
    def __str__(self):
        return f"{self.survey.title} - {self.user.username}"
    
    def clean(self):
        # Prevent self-access
        if self.user == self.invited_by:
            raise ValidationError("Cannot give yourself access to a survey.")
        
        # Prevent giving access to survey creator (they already have access)
        if self.user == self.survey.created_by:
            raise ValidationError("Survey creator already has access.")
    
    def mark_as_responded(self):
        """Mark when user completes the survey"""
        if not self.responded_at:
            from django.utils import timezone
            self.responded_at = timezone.now()
            self.save(update_fields=['responded_at'])
    
    def has_responded(self):
        """Check if the user has completed this survey"""
        return self.responded_at is not None or Submission.objects.filter(
            survey=self.survey,
            user=self.user
        ).exists()


class IndicatorScript(models.Model):
    """Model to store Python scripts for indicator calculations"""
    
    name = models.CharField(max_length=100, help_text="Name of the script")
    description = models.TextField(blank=True, help_text="Description of what this script calculates")
    code = models.TextField(help_text="Python code for the indicator calculation")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ["name"]
    
    def __str__(self):
        return self.name
    
    def clean(self):
        """Validate the Python code syntax"""
        from survey.indicator_sandbox import UnsafeScriptError, validate

        try:
            validate(self.code)
        except SyntaxError as e:
            raise ValidationError({'code': f'Syntax error in Python code: {e}'})
        except UnsafeScriptError as e:
            raise ValidationError({'code': str(e)})


class Indicator(models.Model):
    """Model for survey indicators that calculate scores from submissions"""
    
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="indicators")
    name = models.CharField(max_length=100, help_text="Name of the indicator")
    description = models.TextField(blank=True, help_text="Description of what this indicator measures")
    script = models.ForeignKey(IndicatorScript, on_delete=models.CASCADE, related_name="indicators")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ["name"]
    
    def __str__(self):
        return f"{self.survey.title} - {self.name}"
    
    def execute(self, submission):
        """Execute the indicator script on a submission and return the result"""
        from survey.indicator_sandbox import run_isolated, snapshot_submission

        try:
            # The script sets a 'result' variable
            result, _output = run_isolated(self.script.code, snapshot_submission(submission))
            return {
                'success': True,
                'result': result,
                'error': None
            }
            
        except Exception as e:
            return {
                'success': False,
                'result': None,
                'error': str(e)
            }


class IndicatorResult(models.Model):
    """Model to store results of indicator calculations for submissions"""
    
    indicator = models.ForeignKey(Indicator, on_delete=models.CASCADE, related_name="results")
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="indicator_results")
    result = models.JSONField(help_text="Result of the indicator calculation")
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True, null=True, help_text="Error message if calculation failed")
    calculated_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ["indicator", "submission"]
        ordering = ["-calculated_at"]

    def __str__(self):
        return f"{self.indicator.name} - {self.submission}"


class SurveyInsight(models.Model):
    """LLM-generated insight rows for questions where ≥50% of respondents performed poorly.

    Refreshed at most once per day per survey by the generate_survey_insights
    management command; the dashboard reads rows with generated_on = today
    (or the most recent day if today's run hasn't happened yet).
    """

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="insights")
    indicator = models.ForeignKey(
        Indicator, on_delete=models.SET_NULL, null=True, blank=True, related_name="insights"
    )
    question = models.ForeignKey(
        Question, on_delete=models.SET_NULL, null=True, blank=True, related_name="insights"
    )
    question_text_snapshot = models.TextField(blank=True)
    poor_performance_pct = models.FloatField(default=0.0)
    respondents_total = models.IntegerField(default=0)
    respondents_poor = models.IntegerField(default=0)
    symptoms = models.TextField(blank=True)
    likely_causes = models.TextField(blank=True)
    high_level_action = models.TextField(blank=True)  # legacy single recommendation
    # Persona-targeted recommended actions (supersede high_level_action).
    action_fpo = models.TextField(blank=True)
    action_programme = models.TextField(blank=True)
    action_donor = models.TextField(blank=True)
    generated_on = models.DateField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("survey", "indicator", "question", "generated_on")]
        ordering = ["-generated_on", "indicator_id", "question_id"]

    def __str__(self):
        q = self.question_text_snapshot[:40] if self.question_text_snapshot else "(no question)"
        return f"{self.survey_id}/{self.indicator_id} - {q} @ {self.generated_on}"


import uuid


class SurveyInsightCache(models.Model):
    """Tracks filter-aware insights generation and cache state for the reports page."""

    STATUS_MISSING = "missing"
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_MISSING, "Missing"),
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="insight_caches")
    cache_key = models.CharField(max_length=64, unique=True, db_index=True)
    filters = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    submission_count = models.IntegerField(default=0)
    result_count = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["survey", "status"]),
            models.Index(fields=["survey", "completed_at"]),
        ]

    def __str__(self):
        return f"SurveyInsightCache({self.survey_id}, {self.cache_key}, {self.status})"


class SurveyInsightCacheItem(models.Model):
    """One generated insight card belonging to a filter-aware cache entry."""

    cache_entry = models.ForeignKey(
        SurveyInsightCache,
        on_delete=models.CASCADE,
        related_name="items",
    )
    indicator = models.ForeignKey(
        Indicator, on_delete=models.SET_NULL, null=True, blank=True, related_name="insight_cache_items"
    )
    question = models.ForeignKey(
        Question, on_delete=models.SET_NULL, null=True, blank=True, related_name="insight_cache_items"
    )
    question_text_snapshot = models.TextField(blank=True)
    poor_performance_pct = models.FloatField(default=0.0)
    respondents_total = models.IntegerField(default=0)
    respondents_poor = models.IntegerField(default=0)
    symptoms = models.TextField(blank=True)
    likely_causes = models.TextField(blank=True)
    high_level_action = models.TextField(blank=True)  # legacy single recommendation
    # Persona-targeted recommended actions (supersede high_level_action).
    action_fpo = models.TextField(blank=True)
    action_programme = models.TextField(blank=True)
    action_donor = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("cache_entry", "indicator", "question")]
        ordering = ["indicator_id", "-poor_performance_pct", "question_id"]

    def __str__(self):
        q = self.question_text_snapshot[:40] if self.question_text_snapshot else "(no question)"
        return f"SurveyInsightCacheItem({self.cache_entry_id}, {q})"


def _export_file_upload_to(instance, filename):
    return f"exports/submissions/{instance.id}/{filename}"


class SubmissionExportJob(models.Model):
    """Tracks a background export of submissions → XLSX handled by Celery.
    Stores a snapshot of the filters used so the worker rebuilds the same queryset,
    and persists the generated file so users can re-download without re-running the task.
    """

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submission_export_jobs",
    )
    survey = models.ForeignKey(
        Survey,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submission_export_jobs",
        help_text="Null = cross-survey submissions list export",
    )
    filters = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    total_rows = models.IntegerField(default=0)
    processed_rows = models.IntegerField(default=0)
    file = models.FileField(upload_to=_export_file_upload_to, null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"ExportJob({self.id}, {self.status}, user={self.user_id})"

    @property
    def progress_percent(self) -> int:
        if not self.total_rows:
            return 0 if self.status in (self.STATUS_PENDING, self.STATUS_RUNNING) else 100
        return min(100, int(round((self.processed_rows / self.total_rows) * 100)))


def _report_export_file_upload_to(instance, filename):
    return f"exports/reports/{instance.id}/{filename}"


class ReportExportJob(models.Model):
    """Tracks a background XLSX export for the survey reports dashboard."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="report_export_jobs",
    )
    report_type = models.CharField(max_length=32, db_index=True)
    filters = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    total_steps = models.IntegerField(default=0)
    completed_steps = models.IntegerField(default=0)
    file = models.FileField(upload_to=_report_export_file_upload_to, null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "report_type", "status"]),
        ]

    def __str__(self):
        return f"ReportExportJob({self.id}, {self.report_type}, {self.status}, user={self.user_id})"

    @property
    def progress_percent(self) -> int:
        if not self.total_steps:
            return 0 if self.status in (self.STATUS_PENDING, self.STATUS_RUNNING) else 100
        return min(100, int(round((self.completed_steps / self.total_steps) * 100)))
