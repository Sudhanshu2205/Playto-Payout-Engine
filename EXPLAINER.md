# EXPLAINER.md — Playto Payout Engine

This document explains the five most important technical decisions in this project.

---

## 1. Balance Calculation — Why we use DB aggregation, not Python sum

**The wrong way (what most AI tools suggest first):**

```python
# WRONG — loads every single row into Python memory and sums them
entries = LedgerEntry.objects.filter(merchant=merchant)
balance = sum(
    e.amount_paise if e.entry_type == 'CREDIT' else -e.amount_paise
    for e in entries
)
```

**Why this is wrong:**
- Loads potentially thousands of rows into Python memory
- Slow on large datasets (a real merchant could have millions of entries)
- No locking — another transaction could write between the fetch and the sum
- Conceptually breaks the pattern: the DB should do the math, not Python

**The correct way (what we actually use):**

```python
# CORRECT — single SQL query, DB does the aggregation
from django.db.models import Sum

credits = LedgerEntry.objects.filter(
    merchant_id=merchant_id,
    entry_type=LedgerEntry.CREDIT
).aggregate(total=Sum('amount_paise'))['total'] or 0

debits = LedgerEntry.objects.filter(
    merchant_id=merchant_id,
    entry_type=LedgerEntry.DEBIT
).aggregate(total=Sum('amount_paise'))['total'] or 0

available = credits - debits - held
```

This translates to a single `SELECT SUM(amount_paise) FROM ledger_entry WHERE ...` which is fast, accurate, and consistent.

---

## 2. Concurrency — SELECT FOR UPDATE and why it must wrap the check AND the write

**The race condition (classic bug):**

```python
# WRONG — race condition between check and write
balance = get_balance(merchant_id)           # Thread 1 reads: 1000 rupees
if balance['available_paise'] >= amount:     # Thread 1: 600 <= 1000 ✓
    # --- Thread 2 ALSO reads 1000 here ---
    # --- Thread 2: 600 <= 1000 ✓ also passes ---
    create_payout(...)  # Thread 1 creates payout for 600
    # Thread 2 ALSO creates payout for 600 → total 1200 but only 1000 available!
```

**What we actually do:**

```python
# CORRECT — lock wraps both the check and the write
with transaction.atomic():
    # This line locks the merchant's DB row until our transaction commits.
    # Thread 2 will block here waiting for Thread 1 to finish.
    merchant = Merchant.objects.select_for_update().get(id=merchant_id)
    
    # Now compute balance INSIDE the lock.
    # Thread 2 can't touch this merchant until we're done.
    balance = get_balance(merchant_id)
    
    if balance['available_paise'] < amount_paise:
        return {'error': 'Insufficient balance'}, 400, True
    
    # Create payout + debit entry atomically
    payout = Payout.objects.create(...)
    LedgerEntry.objects.create(entry_type=DEBIT, ...)
    # Transaction commits → Thread 2 unblocks, sees updated balance, fails correctly
```

The key insight: `select_for_update()` acquires a row-level lock at the DB layer. The second thread has to **wait** until the first transaction commits. By then, the balance has already been updated, so the second thread's check will correctly fail.

---

## 3. Idempotency — Using DB unique constraint to handle the concurrent duplicate key edge case

**The wrong way (AI's first suggestion):**

```python
# WRONG — in-memory dictionary, not safe with multiple workers/servers
_seen_keys = {}

def create_payout(merchant_id, amount, key):
    if key in _seen_keys:
        return _seen_keys[key]  # fails with multiple processes/pods
    result = _do_create(...)
    _seen_keys[key] = result
    return result
```

**Why this fails:**
- Doesn't work with multiple Celery workers or Django processes
- Lost on restart
- Not thread-safe even within a single process
- No 24h expiry logic

**What we actually use:**

```python
# CORRECT — DB-level unique constraint + select_for_update inside same transaction

# models.py — unique constraint at DB level
class IdempotencyKey(models.Model):
    merchant = models.ForeignKey(Merchant, ...)
    key = models.CharField(max_length=255)
    
    class Meta:
        unique_together = [('merchant', 'key')]  # DB enforces this

# services.py — check inside the same lock as the payout creation
with transaction.atomic():
    merchant = Merchant.objects.select_for_update().get(id=merchant_id)
    
    existing = IdempotencyKey.objects.filter(merchant=merchant, key=key).first()
    if existing and not existing.is_expired():
        return existing.response_body, existing.status_code, False  # return cached
    
    # ... create payout ...
    IdempotencyKey.objects.create(merchant=merchant, key=key, response_body=response, ...)
```

The `unique_together` constraint means if two requests arrive at the exact same millisecond, only one will succeed in creating the row — the other gets a DB integrity error. This makes idempotency safe even under high concurrency.

---

## 4. State Machine Guard — Blocking invalid transitions in the model layer

**The wrong approach (only validating in the API layer):**

```python
# WRONG — validation only in the view/serializer
class PayoutView(APIView):
    def patch(self, request, payout_id):
        if request.data['status'] == 'COMPLETED' and payout.status == 'FAILED':
            return Response({'error': 'Invalid'}, 400)  # only blocks API calls
        # But direct DB updates, admin panel, Celery tasks — can still skip this!
```

**The correct approach (in the model itself):**

```python
# CORRECT — state machine lives in the model, always enforced

class Payout(models.Model):
    VALID_TRANSITIONS = {
        'PENDING': ['PROCESSING'],
        'PROCESSING': ['COMPLETED', 'FAILED'],
        # COMPLETED and FAILED have no outgoing transitions — terminal states
    }

    def transition_to(self, new_status):
        allowed = self.VALID_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Cannot go from {self.status} to {new_status}. Allowed: {allowed}"
            )
        self.status = new_status
        if new_status == 'PROCESSING':
            self.processing_started_at = timezone.now()
            self.attempt_count += 1
        self.save()
```

This means the state machine guard is enforced **everywhere** — Celery tasks, admin actions, API calls — because all of them call `payout.transition_to()`. You can't accidentally do `payout.status = 'COMPLETED'; payout.save()` without bypassing an explicit design decision.

---

## 5. Atomic Fund Release on Failed Payout

**What happens if a payout fails and we don't handle it atomically:**

```python
# WRONG — two separate operations, crash between them = merchant loses money forever
payout.status = 'FAILED'
payout.save()
# <<< CRASH HERE (server restart, OOM, power outage) >>>
LedgerEntry.objects.create(type=CREDIT, ...)  # Never runs! Merchant lost funds!
```

**What we actually do:**

```python
# CORRECT — both operations in one transaction, either both commit or both rollback

def release_funds_for_failed_payout(payout):
    with transaction.atomic():
        payout.transition_to(Payout.FAILED)       # status change
        LedgerEntry.objects.create(                # credit refund
            merchant=payout.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=payout.amount_paise,
            payout=payout,
            description=f'Refund for failed payout #{payout.id}',
        )
    # If anything goes wrong inside transaction.atomic(), BOTH operations roll back.
    # The merchant's balance stays correct no matter what.
```

The `transaction.atomic()` context manager wraps both writes in a single DB transaction. If the process crashes between the two writes, PostgreSQL will automatically roll back both — the merchant's money is safe.

---

## AI Audit — What the AI got wrong and what I fixed

When I first asked an AI assistant to help with this project, here's what it gave me for the balance calculation:

**AI-generated code (wrong):**
```python
def get_balance(merchant_id):
    entries = LedgerEntry.objects.filter(merchant_id=merchant_id)
    balance = 0.0  # ← FloatField! Wrong for money.
    for entry in entries:  # ← Loading all rows into Python. Very wrong.
        if entry.entry_type == 'CREDIT':
            balance += float(entry.amount)  # ← float() on money. Wrong.
        else:
            balance -= float(entry.amount)
    return balance
```

**Problems I identified:**
1. Used `float` — floating point arithmetic is not precise for money (0.1 + 0.2 ≠ 0.3 in Python)
2. Loaded every LedgerEntry row into Python — O(n) memory, slow on large datasets
3. No paise/rupee distinction — the AI mixed up the units
4. No "held" balance concept — pending payouts weren't subtracted

**What I replaced it with:**
- `BigIntegerField` storing paise (1 rupee = 100 paise), never floats
- `aggregate(Sum('amount_paise'))` — DB does the math in a single SQL query
- Separate calculation for held funds (PENDING + PROCESSING payout amounts)

The AI also initially suggested checking balance before acquiring the `select_for_update()` lock — exactly the race condition described in the spec. I moved the balance check to happen inside the locked transaction.
