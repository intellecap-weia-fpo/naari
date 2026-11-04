from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from django.utils import timezone


def touch_survey(survey):
    """Update the survey's updated_at timestamp without triggering signals recursively."""
    if survey:
        from survey.models import Survey
        Survey.objects.filter(pk=survey.pk).update(updated_at=timezone.now())


def update_submission_computed_title(submission):
    """Update the computed_title field on a submission."""
    if submission and submission.pk:
        from survey.models import Submission
        title = submission.get_title()
        Submission.objects.filter(pk=submission.pk).update(computed_title=title)


# ── Submission ───────────────────────────────────────────────────────────────

@receiver(post_save, sender='survey.Submission')
def submission_saved(sender, instance, created, **kwargs):
    """Update computed_title when a submission is saved."""
    update_submission_computed_title(instance)


# ── SubmissionAnswer ─────────────────────────────────────────────────────────

@receiver(post_save, sender='survey.SubmissionAnswer')
def submission_answer_saved(sender, instance, **kwargs):
    """Update computed_title when a submission answer is saved."""
    update_submission_computed_title(instance.submission)


def submission_answer_options_changed(sender, instance, **kwargs):
    """Update computed_title when a submission answer's selected options change."""
    update_submission_computed_title(instance.submission)


# ── SurveyPage ───────────────────────────────────────────────────────────────

@receiver(post_save, sender='survey.SurveyPage')
@receiver(post_delete, sender='survey.SurveyPage')
def survey_page_changed(sender, instance, **kwargs):
    touch_survey(instance.survey)


# ── Question ─────────────────────────────────────────────────────────────────

@receiver(post_save, sender='survey.Question')
@receiver(post_delete, sender='survey.Question')
def question_changed(sender, instance, **kwargs):
    touch_survey(instance.survey)


# ── QuestionOption ───────────────────────────────────────────────────────────

@receiver(post_save, sender='survey.QuestionOption')
@receiver(post_delete, sender='survey.QuestionOption')
def question_option_changed(sender, instance, **kwargs):
    touch_survey(instance.question.survey)


# ── QuestionCondition ────────────────────────────────────────────────────────

@receiver(post_save, sender='survey.QuestionCondition')
@receiver(post_delete, sender='survey.QuestionCondition')
def question_condition_changed(sender, instance, **kwargs):
    touch_survey(instance.question.survey)


# ── OptionCondition ──────────────────────────────────────────────────────────

@receiver(post_save, sender='survey.OptionCondition')
@receiver(post_delete, sender='survey.OptionCondition')
def option_condition_changed(sender, instance, **kwargs):
    touch_survey(instance.option.question.survey)


# ── QuestionTranslation ─────────────────────────────────────────────────────

@receiver(post_save, sender='survey.QuestionTranslation')
@receiver(post_delete, sender='survey.QuestionTranslation')
def question_translation_changed(sender, instance, **kwargs):
    touch_survey(instance.question.survey)


# ── QuestionOptionTranslation ───────────────────────────────────────────────

@receiver(post_save, sender='survey.QuestionOptionTranslation')
@receiver(post_delete, sender='survey.QuestionOptionTranslation')
def question_option_translation_changed(sender, instance, **kwargs):
    touch_survey(instance.option.question.survey)


# ── OptionWarningTranslation ────────────────────────────────────────────────

@receiver(post_save, sender='survey.OptionWarningTranslation')
@receiver(post_delete, sender='survey.OptionWarningTranslation')
def option_warning_translation_changed(sender, instance, **kwargs):
    touch_survey(instance.option.question.survey)


# ── QuestionHintTranslation ─────────────────────────────────────────────────

@receiver(post_save, sender='survey.QuestionHintTranslation')
@receiver(post_delete, sender='survey.QuestionHintTranslation')
def question_hint_translation_changed(sender, instance, **kwargs):
    touch_survey(instance.question.survey)


# ── SurveyPageTranslation ───────────────────────────────────────────────────

@receiver(post_save, sender='survey.SurveyPageTranslation')
@receiver(post_delete, sender='survey.SurveyPageTranslation')
def survey_page_translation_changed(sender, instance, **kwargs):
    touch_survey(instance.page.survey)
