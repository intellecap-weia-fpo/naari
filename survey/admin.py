from django.contrib import admin
from guardian.admin import GuardedModelAdmin
from .models import (
    Survey,
    SurveyPage,
    Question,
    QuestionOption,
    Submission,
    SubmissionAnswer,
    Indicator,
    IndicatorScript,
    IndicatorResult,
    OptionWarningTranslation,
)


class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    extra = 1
    ordering = ["order"]
    fields = ("text", "value", "order", "is_conditional")
    readonly_fields = ()


class QuestionInline(admin.StackedInline):
    model = Question
    extra = 1
    ordering = ["order"]
    show_change_link = True


class SurveyPageInline(admin.StackedInline):
    model = SurveyPage
    extra = 1
    ordering = ["order"]
    show_change_link = True


@admin.register(Survey)
class SurveyAdmin(GuardedModelAdmin):
    list_display = ["title", "created_by", "created_at", "is_active", "render_in_response"]
    list_filter = ["is_active", "created_at", "created_by", "render_in_response"]
    search_fields = ["title", "description"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [SurveyPageInline]
    fields = [
        "title",
        "description",
        "consent_text",
        "end_date",
        "is_active",
        "allow_multiple_submissions",
        "display_mode",
        "visibility",
        "render_field",
        "render_in_response",
        "created_at",
        "updated_at",
    ]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(SurveyPage)
class SurveyPageAdmin(admin.ModelAdmin):
    list_display = ["title", "survey", "order"]
    list_filter = ["survey"]
    search_fields = ["title", "survey__title"]
    list_editable = ["order"]
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["text_preview", "survey", "page", "question_type", "is_required", "order"]
    list_filter = ["question_type", "is_required", "survey", "page"]
    search_fields = ["text", "survey__title", "hint"]
    list_editable = ["order", "is_required"]
    inlines = [QuestionOptionInline]
    fieldsets = [
        (
            None,
            {
                "fields": [
                    "survey",
                    "page",
                    "key",
                    "text",
                    "hint",
                    "evaluator_condition",
                    "question_type",
                    "is_required",
                    "order",
                    "filter_options_question",
                ]
            },
        ),
        (
            "Validation Rules",
            {
                "fields": [
                    "min_length",
                    "max_length",
                    "min_value",
                    "max_value",
                    "validation_pattern",
                    "validation_message",
                ],
                "classes": ["collapse"],
            },
        ),
        ("Conditional Logic", {"fields": ["is_conditional"], "classes": ["collapse"]}),
    ]

    def text_preview(self, obj):
        return obj.text[:50] + "..." if len(obj.text) > 50 else obj.text

    text_preview.short_description = "Question Text"


@admin.register(QuestionOption)
class QuestionOptionAdmin(admin.ModelAdmin):
    list_display = ["text", "value", "is_conditional", "question_preview", "order"]
    list_filter = ["question__survey", "question__question_type"]
    search_fields = ["text", "value", "question__text"]
    list_editable = ["order"]
    fields = ("question", "text", "value", "order", "is_conditional", "sub_questions")

    def question_preview(self, obj):
        return obj.question.text[:30] + "..." if len(obj.question.text) > 30 else obj.question.text

    question_preview.short_description = "Question"


class SubmissionAnswerInline(admin.TabularInline):
    model = SubmissionAnswer
    extra = 0
    readonly_fields = ["question"]
    fields = ["question", "text_answer", "selected_options"]

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "selected_options":
            # Filter options based on the submission's survey questions
            if request.resolver_match.kwargs.get("object_id"):
                try:
                    submission = Submission.objects.get(pk=request.resolver_match.kwargs["object_id"])
                    kwargs["queryset"] = QuestionOption.objects.filter(question__survey=submission.survey)
                except Submission.DoesNotExist:
                    pass
        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ["survey_title", "user", "status", "language", "submitted_at", "get_submitter_display"]
    list_filter = ["status", "language", "submitted_at", "survey", "survey__created_by"]
    search_fields = ["survey__title", "user__username", "user__email", "ip_address"]
    readonly_fields = ["submitted_at", "ip_address", "user_agent"]
    date_hierarchy = "submitted_at"
    inlines = [SubmissionAnswerInline]

    def survey_title(self, obj):
        return obj.survey.title

    survey_title.short_description = "Survey"
    survey_title.admin_order_field = "survey__title"


@admin.register(SubmissionAnswer)
class SubmissionAnswerAdmin(admin.ModelAdmin):
    list_display = ["submission_info", "question_preview", "text_answer_preview", "selected_options_preview"]
    list_filter = ["submission__survey", "submission__status", "question__question_type", "submission__submitted_at"]
    search_fields = ["submission__survey__title", "question__text", "text_answer"]
    readonly_fields = ["submission", "question"]

    def submission_info(self, obj):
        return f"{obj.submission.survey.title} - {obj.submission.get_submitter_display()}"

    submission_info.short_description = "Submission"

    def question_preview(self, obj):
        return obj.question.text[:50] + "..." if len(obj.question.text) > 50 else obj.question.text

    question_preview.short_description = "Question"

    def text_answer_preview(self, obj):
        if obj.text_answer:
            return obj.text_answer[:100] + "..." if len(obj.text_answer) > 100 else obj.text_answer
        return "-"

    text_answer_preview.short_description = "Text Answer"

    def selected_options_preview(self, obj):
        options = obj.selected_options.all()
        if options.exists():
            return ", ".join([opt.text for opt in options[:3]]) + ("..." if options.count() > 3 else "")
        return "-"

    selected_options_preview.short_description = "Selected Options"


class IndicatorResultInline(admin.TabularInline):
    model = IndicatorResult
    extra = 0
    readonly_fields = ["indicator", "submission", "calculated_at"]
    can_delete = False


@admin.register(IndicatorScript)
class IndicatorScriptAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at", "updated_at"]
    list_filter = ["created_at"]
    search_fields = ["name", "description"]
    readonly_fields = ["created_at", "updated_at"]
    fields = ["name", "description", "code", "created_at", "updated_at"]


@admin.register(Indicator)
class IndicatorAdmin(admin.ModelAdmin):
    list_display = ["name", "survey", "is_active", "created_at"]
    list_filter = ["is_active", "survey", "created_at"]
    search_fields = ["name", "description", "survey__title"]
    readonly_fields = ["created_at", "updated_at"]
    fields = ["survey", "name", "description", "script", "is_active", "created_at", "updated_at"]
    inlines = [IndicatorResultInline]


@admin.register(IndicatorResult)
class IndicatorResultAdmin(admin.ModelAdmin):
    list_display = ["indicator", "submission", "success", "calculated_at"]
    list_filter = ["success", "calculated_at", "indicator__survey"]
    search_fields = ["indicator__name", "submission__survey__title"]
    readonly_fields = ["calculated_at"]


@admin.register(OptionWarningTranslation)
class OptionWarningTranslationAdmin(admin.ModelAdmin):
    list_display = ["option", "warning_type", "language", "message_preview", "created_at"]
    list_filter = ["language", "warning_type", "created_at"]
    search_fields = ["option__text", "message"]
    readonly_fields = ["created_at", "updated_at"]
    fields = ["option", "language", "warning_type", "message", "created_at", "updated_at"]

    def message_preview(self, obj):
        return obj.message[:50] + "..." if len(obj.message) > 50 else obj.message
    message_preview.short_description = "Message Preview"
