from django.apps import AppConfig


class SurveyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "survey"

    def ready(self):
        import survey.signals  # noqa: F401
        
        # Connect m2m_changed signal for SubmissionAnswer.selected_options
        from django.db.models.signals import m2m_changed
        from survey.models import SubmissionAnswer
        from survey.signals import submission_answer_options_changed
        
        m2m_changed.connect(
            submission_answer_options_changed,
            sender=SubmissionAnswer.selected_options.through
        )
