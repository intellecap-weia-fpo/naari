import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "one.settings")

app = Celery("one")

# All Celery-related settings come from Django settings, keys prefixed with CELERY_.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Autodiscover tasks.py in every installed app.
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
