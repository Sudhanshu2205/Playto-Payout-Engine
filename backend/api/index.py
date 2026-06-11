"""
api/index.py — Vercel Python serverless entry point for the Django backend.

Vercel's Python runtime looks for a file named `handler` (WSGI callable).
This file wires the Django WSGI application so that all requests routed to
/_/backend/* are served by Django.
"""

import os
import sys

# Make sure the backend root (one level above this api/ dir) is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "payout_engine.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

# Vercel Python runtime calls this as the WSGI handler.
handler = get_wsgi_application()
