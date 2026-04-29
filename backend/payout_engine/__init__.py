"""
payout_engine/__init__.py

Import Celery app here so it's loaded when Django starts.
This is required for the @shared_task decorator to work in tasks.py.
"""

from .celery import app as celery_app

__all__ = ('celery_app',)
