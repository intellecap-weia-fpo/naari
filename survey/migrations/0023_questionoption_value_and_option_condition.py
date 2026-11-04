from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("survey", "0022_question_filter_options_question"),
    ]

    operations = [
        migrations.AddField(
            model_name="questionoption",
            name="value",
            field=models.CharField(
                max_length=255,
                null=True,
                blank=True,
                help_text="Optional value/code associated with this option (e.g., score or key)",
            ),
        ),
        migrations.AddField(
            model_name="questionoption",
            name="is_conditional",
            field=models.BooleanField(
                default=False,
                help_text="Whether this option has conditional logic",
            ),
        ),
        migrations.CreateModel(
            name="OptionCondition",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("condition_type", models.CharField(choices=[('equals', 'Equals'), ('not_equals', 'Not Equals'), ('contains', 'Contains'), ('not_contains', 'Not Contains'), ('greater_than', 'Greater Than'), ('less_than', 'Less Than'), ('is_empty', 'Is Empty'), ('is_not_empty', 'Is Not Empty')], max_length=20, help_text="Type of condition to evaluate")),
                ("value", models.TextField(blank=True, null=True, help_text="Value to compare against (not needed for empty/not_empty conditions)")),
                ("logic_operator", models.CharField(choices=[('AND', 'AND'), ('OR', 'OR')], default='AND', max_length=3, help_text="Logical operator to combine multiple conditions")),
                ("order", models.PositiveIntegerField(default=0)),
                ("option", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conditions", to="survey.questionoption", help_text="Option that this condition applies to")),
                ("target_question", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="survey.question", help_text="Question whose answer will be evaluated")),
            ],
            options={
                "ordering": ["order", "id"],
            },
        ),
    ]

