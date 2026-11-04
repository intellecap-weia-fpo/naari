"""Load the open sample questionnaire shipped in samples/ into the current tenant."""

import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from survey.models import Question, QuestionOption, Survey, SurveyPage

DEFAULT_PATH = Path(settings.BASE_DIR) / "samples" / "sample_questionnaire.json"


class Command(BaseCommand):
    help = "Create a survey from the open sample questionnaire (or another file of the same shape)."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Username that will own the survey")
        parser.add_argument("--file", default=str(DEFAULT_PATH), help="Questionnaire JSON file")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            owner = User.objects.get(username=options["user"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user named {options['user']}") from exc

        data = json.loads(Path(options["file"]).read_text(encoding="utf-8"))
        if Survey.objects.filter(title=data["title"], is_deleted=False).exists():
            raise CommandError(f"A survey titled {data['title']!r} already exists")

        survey = Survey.objects.create(
            title=data["title"],
            description=data.get("description", ""),
            default_language=data.get("default_language", "en"),
            consent_text=data.get("consent_text", ""),
            created_by=owner,
        )
        order = 0
        for page_order, page_data in enumerate(data["pages"]):
            page = SurveyPage.objects.create(survey=survey, title=page_data["title"], order=page_order)
            for question_data in page_data["questions"]:
                question = Question.objects.create(
                    survey=survey,
                    page=page,
                    key=question_data.get("key"),
                    text=question_data["text"],
                    question_type=question_data["type"],
                    is_required=question_data.get("required", False),
                    order=order,
                )
                order += 1
                for option_order, text in enumerate(question_data.get("options", [])):
                    QuestionOption.objects.create(
                        question=question, text=text, value=str(option_order + 1), order=option_order
                    )
        self.stdout.write(self.style.SUCCESS(f"Created survey {survey.pk}: {survey.title} ({order} questions)"))
