"""
ledger/models.py

These are the core models for the payout engine.

Important design decisions:
1. Merchant has NO balance column. Balance is always calculated fresh from
   LedgerEntry records. This prevents stale/incorrect cached balances.

2. All amounts are stored in PAISE (1 rupee = 100 paise) as BigIntegerField.
   Never use FloatField or DecimalField for money — floating point math is
   not precise enough for financial calculations.

3. LedgerEntry is the source of truth. Every money movement (credit or debit)
   gets its own row here. This gives us a full audit trail.
"""

from django.db import models
from django.utils import timezone


class Merchant(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class LedgerEntry(models.Model):
    """
    Every money movement is recorded here.
    CREDIT = money coming IN (e.g. customer payment received)
    DEBIT = money going OUT (e.g. payout to merchant's bank)

    We store positive values for both — the 'type' field tells us direction.
    """

    CREDIT = 'CREDIT'
    DEBIT = 'DEBIT'
    ENTRY_TYPES = [
        (CREDIT, 'Credit'),
        (DEBIT, 'Debit'),
    ]

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='ledger_entries')
    entry_type = models.CharField(max_length=6, choices=ENTRY_TYPES)
    amount_paise = models.BigIntegerField()  # Always positive, direction comes from entry_type
    description = models.CharField(max_length=500, blank=True)

    # Which payout triggered this debit (null for credits)
    payout = models.ForeignKey('Payout', on_delete=models.SET_NULL, null=True, blank=True, related_name='ledger_entries')

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        direction = '+' if self.entry_type == self.CREDIT else '-'
        return f"{self.merchant.name}: {direction}{self.amount_paise} paise"

    class Meta:
        ordering = ['-created_at']


class Payout(models.Model):
    """
    Tracks payout requests and their lifecycle.

    State machine:
        PENDING → PROCESSING → COMPLETED
                             → FAILED

    Invalid transitions (e.g. FAILED → COMPLETED) are blocked
    in the transition_to() method below.
    """

    PENDING = 'PENDING'
    PROCESSING = 'PROCESSING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (PROCESSING, 'Processing'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
    ]

    # Which transitions are allowed from each state
    VALID_TRANSITIONS = {
        PENDING: [PROCESSING],
        PROCESSING: [COMPLETED, FAILED],
        # COMPLETED and FAILED are terminal — no transitions out
    }

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='payouts')
    amount_paise = models.BigIntegerField()
    bank_account_id = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    attempt_count = models.IntegerField(default=0)

    # Store the idempotency key string directly to avoid circular FK issues
    # (IdempotencyKey already has a FK back to Payout, so we avoid the circular reference)
    idempotency_key_str = models.CharField(max_length=255, blank=True, default='')

    # When the worker picked this up — used to detect stuck payouts (> 30 seconds in PROCESSING)
    processing_started_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def transition_to(self, new_status):
        """
        Move payout to a new status.

        This is the state machine guard — it prevents invalid transitions
        like FAILED → COMPLETED or PENDING → COMPLETED (skipping PROCESSING).

        Always call this instead of doing payout.status = 'COMPLETED' directly.
        """
        allowed = self.VALID_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition payout {self.id} from {self.status} to {new_status}. "
                f"Allowed transitions from {self.status}: {allowed}"
            )

        self.status = new_status

        if new_status == self.PROCESSING:
            self.processing_started_at = timezone.now()
            self.attempt_count += 1

        self.save()

    def __str__(self):
        return f"Payout #{self.id} — {self.merchant.name} — {self.status}"

    class Meta:
        ordering = ['-created_at']


class IdempotencyKey(models.Model):
    """
    Prevents duplicate payouts when the same request is sent multiple times.

    How it works:
    - Client sends a unique key in the 'Idempotency-Key' header
    - First time we see this key: process the request, store the response here
    - Second time we see the same key: return the stored response without processing again

    Scope: key is unique PER merchant (not globally)
    Expiry: keys expire after 24 hours
    """

    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='idempotency_keys')
    key = models.CharField(max_length=255)

    # We store the full response so we can return it exactly on duplicate requests
    response_body = models.JSONField()
    status_code = models.IntegerField()

    # Which payout was created (nullable because failed requests may not have a payout)
    payout = models.ForeignKey(
        'Payout',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='idempotency_records'  # avoids reverse name clash
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # This unique constraint means the DB will reject duplicate (merchant, key) pairs
        unique_together = [('merchant', 'key')]
        ordering = ['-created_at']

    def is_expired(self):
        """Check if this idempotency key is older than 24 hours."""
        age = timezone.now() - self.created_at
        return age.total_seconds() > 24 * 60 * 60  # 24 hours in seconds

    def __str__(self):
        return f"IdempotencyKey for {self.merchant.name}: {self.key}"
