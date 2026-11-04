from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("survey", "0024_alter_optioncondition_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="evaluator_condition",
            field=models.CharField(
                max_length=255,
                null=True,
                blank=True,
                help_text="Optional expression/flag used by evaluators (primarily for consent questions)",
            ),
        ),
    ]
