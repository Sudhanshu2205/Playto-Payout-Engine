"""
ledger/serializers.py

DRF serializers for converting our model data to/from JSON.
Kept simple — no nested serializers, just flat output.
"""

from rest_framework import serializers
from .models import Merchant, LedgerEntry, Payout


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        fields = ['id', 'name', 'email', 'created_at']


class LedgerEntrySerializer(serializers.ModelSerializer):
    # Convert paise to rupees for display (still store in paise)
    amount_rupees = serializers.SerializerMethodField()

    class Meta:
        model = LedgerEntry
        fields = ['id', 'entry_type', 'amount_paise', 'amount_rupees', 'description', 'payout_id', 'created_at']

    def get_amount_rupees(self, obj):
        return obj.amount_paise / 100


class PayoutSerializer(serializers.ModelSerializer):
    amount_rupees = serializers.SerializerMethodField()
    merchant_name = serializers.SerializerMethodField()

    class Meta:
        model = Payout
        fields = [
            'id', 'merchant_id', 'merchant_name',
            'amount_paise', 'amount_rupees',
            'bank_account_id', 'status',
            'attempt_count', 'processing_started_at',
            'created_at', 'updated_at'
        ]

    def get_amount_rupees(self, obj):
        return obj.amount_paise / 100

    def get_merchant_name(self, obj):
        return obj.merchant.name


class CreatePayoutSerializer(serializers.Serializer):
    """
    Validates incoming payout creation requests.
    
    amount is in RUPEES from the client (friendlier for the frontend)
    but we convert to paise before storing.
    """
    merchant_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)  # in rupees
    bank_account_id = serializers.CharField(max_length=100)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than 0")
        return value

    def get_amount_paise(self):
        # Convert rupees to paise — multiply by 100 and round to int
        # We use int() here, not round(), to avoid floating point issues
        return int(self.validated_data['amount'] * 100)
