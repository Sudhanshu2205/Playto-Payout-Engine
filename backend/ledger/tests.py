"""
ledger/tests.py

Two critical test cases that are explicitly graded:

1. Concurrency test:
   Two simultaneous payout requests, each for 600 rupees, against a balance of 1000 rupees.
   Exactly one should succeed and one should fail. Final balance should be 400 rupees.

2. Idempotency test:
   Same idempotency key sent twice. Should return same payout_id both times.
   Only ONE Payout record should exist in the DB.

We use PostgreSQL for these tests (required — select_for_update doesn't work on SQLite).
"""

import threading
import uuid

from django.test import TestCase, TransactionTestCase

from .models import Merchant, LedgerEntry, Payout
from .services import get_balance


class ConcurrencyTest(TransactionTestCase):
    """
    Test that two simultaneous payout requests don't both succeed
    when there's only enough balance for one.

    We use TransactionTestCase (not TestCase) because:
    - TestCase wraps everything in a transaction that never commits
    - select_for_update() needs real transactions to work
    - TransactionTestCase actually commits transactions so the lock works correctly
    """

    def setUp(self):
        # Create a merchant with exactly 1000 rupees (100,000 paise)
        self.merchant = Merchant.objects.create(
            name='Test Merchant',
            email='test@merchant.com'
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=100000,  # 1000 rupees
            description='Initial credit for concurrency test',
        )

    def test_two_concurrent_payouts_only_one_succeeds(self):
        """
        Fire two payout requests simultaneously for 600 rupees each.
        Only one should succeed (we only have 1000 rupees).
        The other should fail with insufficient balance.
        Final balance should be 400 rupees (1000 - 600).
        """

        results = []  # We'll store (status_code, response_data) tuples here
        errors = []   # Store any unexpected exceptions

        def make_payout_request():
            """Function that each thread will run."""
            try:
                from .services import create_payout
                response_data, http_status, _ = create_payout(
                    merchant_id=self.merchant.id,
                    amount_paise=60000,  # 600 rupees
                    bank_account_id='TEST-BANK-001',
                    idempotency_key_str=str(uuid.uuid4()),  # unique key per request
                )
                results.append((http_status, response_data))
            except Exception as e:
                errors.append(str(e))

        # Launch two threads at almost the same time
        thread1 = threading.Thread(target=make_payout_request)
        thread2 = threading.Thread(target=make_payout_request)

        thread1.start()
        thread2.start()

        # Wait for both to finish
        thread1.join()
        thread2.join()

        # No unexpected errors should have occurred
        self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")

        # Both threads should have returned a result
        self.assertEqual(len(results), 2)

        # Extract status codes
        status_codes = [r[0] for r in results]
        status_codes.sort()  # sort so we can assert [201, 400] or [201, 409]

        # Exactly ONE should have succeeded (201 Created)
        success_count = status_codes.count(201)
        fail_count = len([s for s in status_codes if s in [400, 409]])

        self.assertEqual(success_count, 1, f"Expected exactly 1 success, got {success_count}. Status codes: {status_codes}")
        self.assertEqual(fail_count, 1, f"Expected exactly 1 failure, got {fail_count}. Status codes: {status_codes}")

        # Check final balance — should be 400 rupees (40,000 paise)
        balance = get_balance(self.merchant.id)
        self.assertEqual(
            balance['available_paise'], 40000,
            f"Expected 40,000 paise (400 rupees) remaining. Got {balance['available_paise']} paise."
        )

        # Only ONE payout should have been created
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 1, f"Expected exactly 1 payout, found {payout_count}")

        print("\nPASS: Concurrency test passed!")
        print("  Status codes: " + str(status_codes))
        print("  Final balance: Rs." + str(balance['available_rupees']))


class IdempotencyTest(TransactionTestCase):
    """
    Test that sending the same request twice with the same idempotency key
    returns the same response and does NOT create a duplicate payout.
    """

    def setUp(self):
        self.merchant = Merchant.objects.create(
            name='Idempotency Test Merchant',
            email='idempotency@test.com'
        )
        # Give them 5000 rupees to work with
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=500000,  # 5000 rupees
            description='Initial credit for idempotency test',
        )

    def test_same_key_twice_returns_same_payout(self):
        """
        POST with the same idempotency key twice.
        Second call should return the same payout_id.
        Only ONE Payout row should exist.
        Balance should be deducted only once.
        """
        from .services import create_payout

        idempotency_key = 'test-idempotency-key-' + str(uuid.uuid4())
        amount_paise = 100000  # 1000 rupees

        # First request
        response1, status1, is_new1 = create_payout(
            merchant_id=self.merchant.id,
            amount_paise=amount_paise,
            bank_account_id='HDFC-001',
            idempotency_key_str=idempotency_key,
        )

        # Second request — same key, same everything
        response2, status2, is_new2 = create_payout(
            merchant_id=self.merchant.id,
            amount_paise=amount_paise,
            bank_account_id='HDFC-001',
            idempotency_key_str=idempotency_key,
        )

        # Both should return 201
        self.assertEqual(status1, 201, f"First request should succeed. Got: {response1}")
        self.assertEqual(status2, 201, f"Second request should also return 201 (cached). Got: {response2}")

        # Both should return the SAME payout_id
        self.assertEqual(
            response1['payout_id'], response2['payout_id'],
            f"Both responses should have the same payout_id. "
            f"Got {response1['payout_id']} and {response2['payout_id']}"
        )

        # The first one should be new, the second should be cached
        self.assertTrue(is_new1, "First request should be a new request")
        self.assertFalse(is_new2, "Second request should be a cached response")

        # Only ONE payout should exist in the DB
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 1, f"Expected exactly 1 payout. Found {payout_count}")

        # Balance should be deducted only once (once 1000 from 5000 = 4000 remaining)
        balance = get_balance(self.merchant.id)
        # The payout is PENDING, so it shows up in 'held' not as a debit yet
        # available = credits - debits - held = 500000 - 100000 - 0 = 400000
        # (The debit ledger entry was created when the payout was created)
        self.assertEqual(
            balance['available_paise'], 400000,
            f"Balance should be 400,000 paise (4000 rupees). Got {balance['available_paise']}"
        )

        print("\nPASS: Idempotency test passed!")
        print("  Payout ID: " + str(response1['payout_id']) + " (same for both requests)")
        print("  Payout count in DB: " + str(payout_count))
        print("  Final balance: Rs." + str(balance['available_rupees']))

    def test_different_keys_create_different_payouts(self):
        """
        Sanity check: two requests with DIFFERENT keys should create two payouts.
        """
        from .services import create_payout

        key1 = 'unique-key-1-' + str(uuid.uuid4())
        key2 = 'unique-key-2-' + str(uuid.uuid4())

        response1, status1, _ = create_payout(
            merchant_id=self.merchant.id,
            amount_paise=10000,  # 100 rupees
            bank_account_id='HDFC-001',
            idempotency_key_str=key1,
        )
        response2, status2, _ = create_payout(
            merchant_id=self.merchant.id,
            amount_paise=10000,  # 100 rupees
            bank_account_id='HDFC-001',
            idempotency_key_str=key2,
        )

        self.assertEqual(status1, 201)
        self.assertEqual(status2, 201)

        # Should be different payout IDs
        self.assertNotEqual(response1['payout_id'], response2['payout_id'])

        # Two payouts should exist
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 2)

        print("\nPASS: Different keys create different payouts: " + str(response1['payout_id']) + " and " + str(response2['payout_id']))


class BalanceCalculationTest(TransactionTestCase):
    """Test that balance calculations are correct."""

    def test_balance_calculates_correctly(self):
        merchant = Merchant.objects.create(name='Balance Test', email='balance@test.com')

        # Add some credits
        LedgerEntry.objects.create(merchant=merchant, entry_type=LedgerEntry.CREDIT, amount_paise=100000)
        LedgerEntry.objects.create(merchant=merchant, entry_type=LedgerEntry.CREDIT, amount_paise=50000)

        # Add a debit (completed payout)
        payout = Payout.objects.create(
            merchant=merchant, amount_paise=30000,
            bank_account_id='TEST', status=Payout.COMPLETED
        )
        LedgerEntry.objects.create(
            merchant=merchant, entry_type=LedgerEntry.DEBIT,
            amount_paise=30000, payout=payout
        )

        balance = get_balance(merchant.id)

        # 100000 + 50000 credits - 30000 debit = 120000 available
        self.assertEqual(balance['available_paise'], 120000)
        self.assertEqual(balance['total_credits_paise'], 150000)
        self.assertEqual(balance['total_debits_paise'], 30000)
        self.assertEqual(balance['held_paise'], 0)

        print("\nPASS: Balance calculation correct: Rs." + str(balance['available_rupees']))


class StateMachineTest(TestCase):
    """Test that the payout state machine blocks invalid transitions."""

    def test_invalid_transition_raises_error(self):
        merchant = Merchant.objects.create(name='State Test', email='state@test.com')
        payout = Payout.objects.create(
            merchant=merchant,
            amount_paise=10000,
            bank_account_id='TEST',
            status=Payout.PENDING
        )

        # PENDING → COMPLETED is invalid (must go through PROCESSING)
        with self.assertRaises(ValueError):
            payout.transition_to(Payout.COMPLETED)

        # PENDING → FAILED is also invalid
        with self.assertRaises(ValueError):
            payout.transition_to(Payout.FAILED)

        # PENDING → PROCESSING is valid
        payout.transition_to(Payout.PROCESSING)
        self.assertEqual(payout.status, Payout.PROCESSING)

        # PROCESSING → COMPLETED is valid
        payout.transition_to(Payout.COMPLETED)
        self.assertEqual(payout.status, Payout.COMPLETED)

        # COMPLETED → anything is invalid (terminal state)
        with self.assertRaises(ValueError):
            payout.transition_to(Payout.FAILED)

        print("\nPASS: State machine correctly blocks invalid transitions")
