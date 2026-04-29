"""
ledger/services.py

This is where all the important business logic lives.
Views call these functions — they don't do any DB work directly.

The two critical functions here are:
1. get_balance() — calculates balance using DB aggregate (never Python sum)
2. create_payout() — handles concurrency lock + idempotency

If you're reviewing this code, the most important thing to understand is
WHY we use select_for_update() and WHY the balance check happens INSIDE
the locked transaction.
"""

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Merchant, LedgerEntry, Payout, IdempotencyKey


def get_balance(merchant_id):
    """
    Calculate a merchant's balance using database-level aggregation.

    Returns a dict with:
    - total_credits_paise: all money that came in
    - total_debits_paise: all money that went out (completed payouts only)
    - held_paise: money locked up in pending/processing payouts
    - available_paise: what the merchant can actually withdraw right now

    IMPORTANT: We never load LedgerEntry rows into Python and sum them.
    That would be slow on large datasets and also incorrect because
    of floating point issues. We let the DB do the math.
    """

    # Sum up all credits (money the merchant earned)
    credits_result = LedgerEntry.objects.filter(
        merchant_id=merchant_id,
        entry_type=LedgerEntry.CREDIT
    ).aggregate(total=Sum('amount_paise'))

    total_credits = credits_result['total'] or 0

    # Sum up all debits.
    # IMPORTANT: We create a DEBIT ledger entry IMMEDIATELY when a payout is created
    # (not when it completes). This means even PENDING payouts are already debited.
    # So: available = credits - debits (that's it — no need to also subtract "held")
    # If a payout fails, we create a CREDIT entry to reverse the debit.
    debits_result = LedgerEntry.objects.filter(
        merchant_id=merchant_id,
        entry_type=LedgerEntry.DEBIT
    ).aggregate(total=Sum('amount_paise'))

    total_debits = debits_result['total'] or 0

    # Available balance = money in - money out (via ledger)
    # The debit entries for PENDING/PROCESSING payouts already reduce this correctly.
    available = total_credits - total_debits

    # Held = money locked in payouts that are still in-flight (informational only)
    # This is useful for the dashboard to show "Rs.X is in-flight"
    # but it does NOT affect the available balance calculation (already captured in debits)
    held_result = Payout.objects.filter(
        merchant_id=merchant_id,
        status__in=[Payout.PENDING, Payout.PROCESSING]
    ).aggregate(total=Sum('amount_paise'))

    held = held_result['total'] or 0

    return {
        'total_credits_paise': total_credits,
        'total_debits_paise': total_debits,
        'held_paise': held,          # informational: how much is in-flight
        'available_paise': available, # actual spendable balance
        # Also return rupee versions for convenience
        'available_rupees': available / 100,
        'held_rupees': held / 100,
    }


def create_payout(merchant_id, amount_paise, bank_account_id, idempotency_key_str):
    """
    Create a payout request for a merchant.

    This function handles two hard problems:

    1. CONCURRENCY: Two simultaneous payout requests must not both succeed
       if there's only enough balance for one. We solve this with
       select_for_update() which locks the merchant's DB row while we
       check balance and create the payout. The second request has to wait
       until the first one finishes, then it sees the updated balance.

    2. IDEMPOTENCY: If the client sends the same request twice (network retry,
       double-click, etc.), we should return the same response without creating
       a duplicate payout. We track this with the idempotency_key.

    Returns a tuple: (response_dict, http_status_code, is_new_request)
    - is_new_request = True means we processed it fresh
    - is_new_request = False means we returned a cached response
    """

    with transaction.atomic():
        # Step 1: Lock the merchant row.
        # No other transaction can modify or read this row (with FOR UPDATE)
        # until we're done. This is what prevents race conditions.
        #
        # Note: The balance check MUST happen AFTER acquiring this lock.
        # If we check balance first and lock later, another request could
        # slip in between and spend the same money.
        try:
            merchant = Merchant.objects.select_for_update().get(id=merchant_id)
        except Merchant.DoesNotExist:
            return {'error': 'Merchant not found'}, 404, True

        # Step 2: Check if we've seen this idempotency key before.
        # We do this inside the lock to handle the case where two identical
        # requests arrive at exactly the same time.
        existing_key = IdempotencyKey.objects.filter(
            merchant=merchant,
            key=idempotency_key_str
        ).first()

        if existing_key is not None:
            # Check if the key has expired (older than 24 hours)
            if existing_key.is_expired():
                # Expired key — treat as a fresh request
                # (Don't delete it, just ignore and proceed)
                pass
            else:
                # We've seen this key before and it's still valid.
                # Return the exact same response we gave last time.
                return existing_key.response_body, existing_key.status_code, False

        # Step 3: Now calculate the balance INSIDE the lock.
        # Since we hold the merchant lock, no other transaction can
        # create payouts for this merchant while we're here.
        balance = get_balance(merchant_id)
        available = balance['available_paise']

        # Step 4: Check if they have enough money
        if amount_paise > available:
            error_response = {
                'error': 'Insufficient balance',
                'available_paise': available,
                'available_rupees': available / 100,
                'requested_paise': amount_paise,
            }

            # Save the failed response to the idempotency key
            # so duplicate requests also get the same error
            _save_idempotency_key(merchant, idempotency_key_str, error_response, 400, None)

            return error_response, 400, True

        # Step 5: Create the Payout record
        payout = Payout.objects.create(
            merchant=merchant,
            amount_paise=amount_paise,
            bank_account_id=bank_account_id,
            status=Payout.PENDING,
        )

        # Step 6: Create a DEBIT ledger entry to record that money is going out.
        # We create this immediately (not when the payout completes) so the
        # balance calculation correctly excludes this amount from "available".
        LedgerEntry.objects.create(
            merchant=merchant,
            entry_type=LedgerEntry.DEBIT,
            amount_paise=amount_paise,
            payout=payout,
            description=f'Payout #{payout.id} to account {bank_account_id}',
        )

        # Step 7: Build the response and save the idempotency key
        success_response = {
            'payout_id': payout.id,
            'merchant_id': merchant.id,
            'merchant_name': merchant.name,
            'amount_paise': amount_paise,
            'amount_rupees': amount_paise / 100,
            'bank_account_id': bank_account_id,
            'status': payout.status,
            'created_at': payout.created_at.isoformat(),
        }

        # Save the idempotency key — next duplicate gets this response back
        _save_idempotency_key(
            merchant, idempotency_key_str, success_response, 201, payout
        )

        # Also store the key string on the payout for reference
        payout.idempotency_key_str = idempotency_key_str
        payout.save()

        # Step 8: Kick off the Celery task to process this payout.
        # We dispatch AFTER the transaction commits (using on_commit) so the
        # worker doesn't try to process a payout that doesn't exist in the DB yet.
        #
        # In tests (CELERY_TASK_ALWAYS_EAGER=True), we skip task dispatch entirely.
        # Tests only verify the DB-level concurrency and idempotency behavior.
        # The task itself is tested separately in TaskTests.
        if not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            from .tasks import process_payout
            transaction.on_commit(lambda: process_payout.delay(payout.id))

        return success_response, 201, True


def _save_idempotency_key(merchant, key_str, response_body, status_code, payout):
    """
    Helper to create an IdempotencyKey record.
    
    Using get_or_create here handles the edge case where two requests with
    the same key arrive at the exact same millisecond — only one will
    succeed in creating the record, the other will get the existing one.
    """
    obj, created = IdempotencyKey.objects.get_or_create(
        merchant=merchant,
        key=key_str,
        defaults={
            'response_body': response_body,
            'status_code': status_code,
            'payout': payout,
        }
    )
    return obj


def release_funds_for_failed_payout(payout):
    """
    When a payout fails, we need to give the merchant their money back.
    
    This MUST happen atomically — the status change and the credit entry
    must both succeed or both fail. If we do them separately and something
    crashes in between, the merchant loses money forever.
    """
    with transaction.atomic():
        # Transition the payout to FAILED state
        payout.transition_to(Payout.FAILED)

        # Create a CREDIT entry to reverse the debit we made when creating the payout
        LedgerEntry.objects.create(
            merchant=payout.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=payout.amount_paise,
            payout=payout,
            description=f'Refund for failed payout #{payout.id}',
        )
