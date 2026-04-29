"""
ledger/tasks.py

Celery tasks for processing payouts asynchronously.

Two tasks here:
1. process_payout — handles a single payout (simulates 70% success, 20% fail, 10% hang)
2. retry_stuck_payouts — periodic task (runs every 10s) to find payouts stuck in PROCESSING

The simulation percentages are hardcoded to match the spec, but in production
this would call an actual payment gateway like Razorpay or Cashfree.
"""

import random
import time
import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from .models import Payout
from .services import release_funds_for_failed_payout

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,  # retry after 30 seconds
    autoretry_for=(Exception,),
    retry_backoff=True,       # exponential backoff: 30s, 60s, 120s
)
def process_payout(self, payout_id):
    """
    Process a single payout.

    This task simulates what would happen when calling a real payment gateway:
    - 70% of the time: success
    - 20% of the time: failure (gateway rejected it)
    - 10% of the time: hang (simulates a network timeout)

    For retries: exponential backoff up to 3 attempts.
    If all attempts fail, the payout is marked FAILED and funds are returned.
    """
    logger.info(f"Processing payout {payout_id} (attempt {self.request.retries + 1}/3)")

    try:
        # Get the payout — if it doesn't exist, something went wrong
        payout = Payout.objects.get(id=payout_id)
    except Payout.DoesNotExist:
        logger.error(f"Payout {payout_id} not found! Cannot process.")
        return

    # If the payout is already in a terminal state, don't process it again
    if payout.status in [Payout.COMPLETED, Payout.FAILED]:
        logger.info(f"Payout {payout_id} is already {payout.status}, skipping.")
        return

    # Move to PROCESSING state
    # If this fails (e.g. invalid transition), the exception will bubble up
    payout.transition_to(Payout.PROCESSING)

    # Simulate the payment gateway call
    outcome = _simulate_gateway_call()

    if outcome == 'success':
        # Mark it as completed
        payout.transition_to(Payout.COMPLETED)
        logger.info(f"Payout {payout_id} completed successfully!")

    elif outcome == 'fail':
        # Gateway rejected the payout — return funds to merchant
        logger.warning(f"Payout {payout_id} failed at gateway. Releasing funds.")
        release_funds_for_failed_payout(payout)

    elif outcome == 'hang':
        # Simulate a network hang — the task will timeout
        # The retry_stuck_payouts task will pick this up and re-queue it
        logger.warning(f"Payout {payout_id} gateway is hanging... (simulating timeout)")

        # In tests, use a short sleep so tests don't take 2 minutes
        from django.conf import settings
        hang_duration = 2 if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False) else 120
        time.sleep(hang_duration)


def _simulate_gateway_call():
    """
    Simulate a payment gateway response.

    Returns one of: 'success', 'fail', 'hang'

    70% success, 20% fail, 10% hang — as per the spec.
    """
    roll = random.random()  # returns a float between 0.0 and 1.0

    if roll < 0.70:
        return 'success'
    elif roll < 0.90:  # 70% to 90% = 20% fail
        return 'fail'
    else:              # 90% to 100% = 10% hang
        return 'hang'


@shared_task
def retry_stuck_payouts():
    """
    Periodic task that runs every 10 seconds (configured in settings.py CELERY_BEAT_SCHEDULE).

    Finds payouts that have been stuck in PROCESSING state for more than 30 seconds.
    This handles the 10% "hang" case from process_payout.

    For each stuck payout:
    - If attempt_count < 3: re-queue it for processing
    - If attempt_count >= 3: mark as FAILED and return funds to merchant
    """
    # Find payouts that have been stuck in PROCESSING for more than 30 seconds
    stuck_cutoff = timezone.now() - timedelta(seconds=30)

    stuck_payouts = Payout.objects.filter(
        status=Payout.PROCESSING,
        processing_started_at__lt=stuck_cutoff  # started more than 30 seconds ago
    )

    if not stuck_payouts.exists():
        return  # Nothing to do

    logger.info(f"Found {stuck_payouts.count()} stuck payout(s). Processing...")

    for payout in stuck_payouts:
        logger.warning(
            f"Payout {payout.id} has been in PROCESSING since {payout.processing_started_at} "
            f"(attempt {payout.attempt_count}/3). Handling..."
        )

        if payout.attempt_count < 3:
            # Still have retries left — reset to PENDING and re-queue
            with transaction.atomic():
                payout.status = Payout.PENDING
                payout.processing_started_at = None
                payout.save()

            # Re-queue the task
            process_payout.delay(payout.id)
            logger.info(f"Payout {payout.id} re-queued (attempt {payout.attempt_count + 1}/3)")

        else:
            # Out of retries — give up and refund the merchant
            logger.error(
                f"Payout {payout.id} has failed 3 times. Marking as FAILED and refunding."
            )
            release_funds_for_failed_payout(payout)
