"""
management/commands/seed_data.py

Run this to populate the database with test data.

Usage:
    python manage.py seed_data

Creates:
- 3 merchants
- 5-8 credit entries per merchant
- 1-2 completed payouts per merchant

This is needed for the live demo.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from ledger.models import Merchant, LedgerEntry, Payout


class Command(BaseCommand):
    help = 'Seeds the database with test merchants, credits, and payouts'

    def handle(self, *args, **options):
        self.stdout.write('Seeding database...')

        with transaction.atomic():
            # Clear existing data so we can re-run safely
            self.stdout.write('Clearing existing data...')
            Payout.objects.all().delete()
            LedgerEntry.objects.all().delete()
            Merchant.objects.all().delete()

            # Create merchants
            merchant1 = Merchant.objects.create(
                name='Rajesh Electronics',
                email='rajesh@electronics.in'
            )
            merchant2 = Merchant.objects.create(
                name='Priya Textiles',
                email='priya@textiles.in'
            )
            merchant3 = Merchant.objects.create(
                name='Akash Grocery Store',
                email='akash@grocery.in'
            )

            self.stdout.write(f'Created 3 merchants')

            # Seed credit history for Rajesh Electronics
            # Total credits: 50,000 + 25,000 + 15,000 + 8,000 + 12,000 = 1,10,000 paise = 1100 rupees
            credits_rajesh = [
                (5000000, 'Customer payment - Order #1001'),   # 50000 rupees
                (2500000, 'Customer payment - Order #1002'),   # 25000 rupees
                (1500000, 'Customer payment - Order #1003'),   # 15000 rupees
                (800000,  'Customer payment - Order #1004'),   # 8000 rupees
                (1200000, 'Customer payment - Order #1005'),   # 12000 rupees
            ]
            for amount, desc in credits_rajesh:
                LedgerEntry.objects.create(
                    merchant=merchant1,
                    entry_type=LedgerEntry.CREDIT,
                    amount_paise=amount,
                    description=desc,
                )

            self.stdout.write(f'Added {len(credits_rajesh)} credit entries for {merchant1.name}')

            # Seed credit history for Priya Textiles
            credits_priya = [
                (3000000, 'Wholesale order - Batch #201'),   # 30000 rupees
                (7500000, 'Wholesale order - Batch #202'),   # 75000 rupees
                (2200000, 'Retail payment - Invoice #301'),  # 22000 rupees
                (1800000, 'Retail payment - Invoice #302'),  # 18000 rupees
                (4500000, 'Online sale - Order #501'),       # 45000 rupees
                (900000,  'Online sale - Order #502'),       # 9000 rupees
            ]
            for amount, desc in credits_priya:
                LedgerEntry.objects.create(
                    merchant=merchant2,
                    entry_type=LedgerEntry.CREDIT,
                    amount_paise=amount,
                    description=desc,
                )

            self.stdout.write(f'Added {len(credits_priya)} credit entries for {merchant2.name}')

            # Seed credit history for Akash Grocery Store
            credits_akash = [
                (500000,  'Daily sales - Day 1'),   # 5000 rupees
                (750000,  'Daily sales - Day 2'),   # 7500 rupees
                (620000,  'Daily sales - Day 3'),   # 6200 rupees
                (890000,  'Daily sales - Day 4'),   # 8900 rupees
                (1100000, 'Daily sales - Day 5'),   # 11000 rupees
                (430000,  'Daily sales - Day 6'),   # 4300 rupees
                (680000,  'Daily sales - Day 7'),   # 6800 rupees
                (920000,  'Weekly bulk sale'),       # 9200 rupees
            ]
            for amount, desc in credits_akash:
                LedgerEntry.objects.create(
                    merchant=merchant3,
                    entry_type=LedgerEntry.CREDIT,
                    amount_paise=amount,
                    description=desc,
                )

            self.stdout.write(f'Added {len(credits_akash)} credit entries for {merchant3.name}')

            # Add a completed payout for Rajesh (so there's some history to show)
            payout1 = Payout.objects.create(
                merchant=merchant1,
                amount_paise=1000000,  # 10000 rupees
                bank_account_id='HDFC-RAJESH-001',
                status=Payout.COMPLETED,
                attempt_count=1,
            )
            # Record the debit for this completed payout
            LedgerEntry.objects.create(
                merchant=merchant1,
                entry_type=LedgerEntry.DEBIT,
                amount_paise=1000000,
                payout=payout1,
                description=f'Payout #{payout1.id} to HDFC-RAJESH-001',
            )

            # Add a completed payout for Priya
            payout2 = Payout.objects.create(
                merchant=merchant2,
                amount_paise=2000000,  # 20000 rupees
                bank_account_id='SBI-PRIYA-001',
                status=Payout.COMPLETED,
                attempt_count=1,
            )
            LedgerEntry.objects.create(
                merchant=merchant2,
                entry_type=LedgerEntry.DEBIT,
                amount_paise=2000000,
                payout=payout2,
                description=f'Payout #{payout2.id} to SBI-PRIYA-001',
            )

            # Add a failed payout + refund for Akash (to demonstrate the refund flow)
            payout3 = Payout.objects.create(
                merchant=merchant3,
                amount_paise=100000,  # 1000 rupees
                bank_account_id='ICICI-AKASH-001',
                status=Payout.FAILED,
                attempt_count=3,
            )
            # Debit when it was created
            LedgerEntry.objects.create(
                merchant=merchant3,
                entry_type=LedgerEntry.DEBIT,
                amount_paise=100000,
                payout=payout3,
                description=f'Payout #{payout3.id} to ICICI-AKASH-001',
            )
            # Credit when it failed (refund)
            LedgerEntry.objects.create(
                merchant=merchant3,
                entry_type=LedgerEntry.CREDIT,
                amount_paise=100000,
                payout=payout3,
                description=f'Refund for failed payout #{payout3.id}',
            )

            self.stdout.write(f'Added 3 sample payouts (2 completed, 1 failed with refund)')

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== Seed complete! ==='))
        self.stdout.write('')

        for merchant in Merchant.objects.all():
            from ledger.services import get_balance
            bal = get_balance(merchant.id)
            self.stdout.write(
                f'{merchant.name}: available = Rs.{bal["available_rupees"]:.2f}, '
                f'held = Rs.{bal["held_rupees"]:.2f}'
            )
