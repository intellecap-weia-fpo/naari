"""Permanent, self-service deletion of an account in the current workspace."""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from graphql_jwt.refresh_token.utils import get_refresh_token_model

from survey.models import ReportExportJob, Submission, SubmissionExportJob, SurveyInsight, SurveyInsightCache

logger = logging.getLogger(__name__)


def delete_account(user, password):
    files = []
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=user.pk)
        if not user.check_password(password):
            return False

        # Django does not remove uploaded files when their database rows are deleted.
        profile = getattr(user, "profile", None)
        if profile:
            files.extend([profile.profile_image, profile.cover_image])
        submissions = Submission.objects.filter(user=user)
        survey_ids = list(submissions.values_list("survey_id", flat=True).distinct())
        for submission in submissions.iterator():
            files.extend([submission.signature_image, submission.photo_image])
        for model in (SubmissionExportJob, ReportExportJob):
            files.extend(job.file for job in model.objects.filter(user=user))

        # Explicitly remove responses: Submission.user uses SET_NULL for other
        # administrative deletion flows, which is insufficient for self-deletion.
        submissions.delete()
        # Derived insights can quote deleted responses; regenerate them from
        # the remaining responses instead of continuing to serve cached text.
        SurveyInsight.objects.filter(survey_id__in=survey_ids).delete()
        SurveyInsightCache.objects.filter(survey_id__in=survey_ids).delete()
        get_refresh_token_model().objects.filter(user=user).delete()
        user.delete()

        def remove_files():
            for field in files:
                if field and field.name:
                    try:
                        field.storage.delete(field.name)
                    except Exception:
                        logger.exception("Account deletion: could not remove uploaded file")

        transaction.on_commit(remove_files)
    return True
