from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('survey', '0037_add_language_to_submission'),
    ]

    operations = [
        migrations.AddField(
            model_name='submission',
            name='local_id',
            field=models.CharField(
                blank=True,
                help_text='Client-generated UUID for offline sync',
                max_length=100,
                null=True,
                unique=True,
            ),
        ),
    ]
