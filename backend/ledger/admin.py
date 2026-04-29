from django.contrib import admin
from .models import Merchant, LedgerEntry, Payout, IdempotencyKey


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'email', 'created_at']
    search_fields = ['name', 'email']


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ['id', 'merchant', 'entry_type', 'amount_paise', 'payout_id', 'created_at']
    list_filter = ['entry_type', 'merchant']
    search_fields = ['merchant__name']


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ['id', 'merchant', 'amount_paise', 'status', 'attempt_count', 'idempotency_key_str', 'created_at']
    list_filter = ['status', 'merchant']
    search_fields = ['merchant__name', 'bank_account_id']


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ['id', 'merchant', 'key', 'status_code', 'created_at']
    list_filter = ['merchant']
