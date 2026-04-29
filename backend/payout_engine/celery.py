"""
Celery app setup for payout_engine.
"""

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'payout_engine.settings')

app = Celery('payout_engine')

# Read config from Django settings, all keys starting with CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all installed apps
app.autodiscover_tasks()
