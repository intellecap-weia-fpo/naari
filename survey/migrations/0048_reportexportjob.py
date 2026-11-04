from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import survey.models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("survey", "0046_submissionexportjob"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReportExportJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("report_type", models.CharField(db_index=True, max_length=32)),
                ("filters", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")], db_index=True, default="pending", max_length=16)),
                ("total_steps", models.IntegerField(default=0)),
                ("completed_steps", models.IntegerField(default=0)),
                ("file", models.FileField(blank=True, null=True, upload_to=survey.models._report_export_file_upload_to)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="report_export_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="reportexportjob",
            index=models.Index(fields=["user", "status"], name="survey_repo_user_id_4c0ded_idx"),
        ),
        migrations.AddIndex(
            model_name="reportexportjob",
            index=models.Index(fields=["user", "report_type", "status"], name="survey_repo_user_id_e5ce35_idx"),
        ),
    ]
