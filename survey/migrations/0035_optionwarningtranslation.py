# Generated manually for option warning translations

from django.conf import settings
from django.core.validators import MinLengthValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('survey', '0034_questionoption_option_warning_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='OptionWarningTranslation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('language', models.CharField(choices=settings.LANGUAGES, max_length=10)),
                ('warning_type', models.CharField(
                    choices=[('selected', 'When Selected'), ('not_selected', 'When Not Selected')],
                    help_text='Which warning condition this translation is for',
                    max_length=20
                )),
                ('message', models.TextField(
                    help_text='Translated warning message',
                    validators=[MinLengthValidator(1)]
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('option', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='warning_translations',
                    to='survey.questionoption'
                )),
            ],
            options={
                'ordering': ['language', 'warning_type'],
                'unique_together': {('option', 'language', 'warning_type')},
            },
        ),
    ]
