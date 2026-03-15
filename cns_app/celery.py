import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cns_app.settings")

app = Celery("cns_app")

# Pull Celery config from Django settings under the CELERY_ namespace.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every INSTALLED_APP.
app.autodiscover_tasks()
