"""
ledger/views.py

API views for the payout engine.

All the heavy lifting (balance calculation, concurrency, idempotency)
is done in services.py — views just parse input and format output.

Endpoints:
    POST   /api/v1/payouts/                        — Create a payout
    GET    /api/v1/payouts/                        — List all payouts (filterable by merchant)
    GET    /api/v1/payouts/{id}/                   — Get single payout status
    GET    /api/v1/merchants/                      — List all merchants
    GET    /api/v1/merchants/{id}/balance/         — Get merchant balance
    GET    /api/v1/merchants/{id}/ledger/          — Get merchant ledger history
"""

import uuid
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Merchant, LedgerEntry, Payout
from .serializers import (
    MerchantSerializer,
    LedgerEntrySerializer,
    PayoutSerializer,
    CreatePayoutSerializer,
)
from .services import get_balance, create_payout


class MerchantListView(APIView):
    """GET /api/v1/merchants/ — list all merchants"""

    def get(self, request):
        merchants = Merchant.objects.all()
        serializer = MerchantSerializer(merchants, many=True)
        return Response(serializer.data)


class MerchantBalanceView(APIView):
    """GET /api/v1/merchants/{merchant_id}/balance/ — get merchant's current balance"""

    def get(self, request, merchant_id):
        try:
            merchant = Merchant.objects.get(id=merchant_id)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=status.HTTP_404_NOT_FOUND)

        balance = get_balance(merchant_id)

        return Response({
            'merchant_id': merchant.id,
            'merchant_name': merchant.name,
            **balance,  # spread in all the balance fields
        })


class MerchantLedgerView(APIView):
    """GET /api/v1/merchants/{merchant_id}/ledger/ — paginated ledger history"""

    def get(self, request, merchant_id):
        try:
            merchant = Merchant.objects.get(id=merchant_id)
        except Merchant.DoesNotExist:
            return Response({'error': 'Merchant not found'}, status=status.HTTP_404_NOT_FOUND)

        # Simple manual pagination — page size of 20
        page = int(request.query_params.get('page', 1))
        page_size = 20
        offset = (page - 1) * page_size

        entries = LedgerEntry.objects.filter(merchant=merchant)[offset:offset + page_size]
        total_count = LedgerEntry.objects.filter(merchant=merchant).count()

        serializer = LedgerEntrySerializer(entries, many=True)

        return Response({
            'merchant_id': merchant.id,
            'merchant_name': merchant.name,
            'page': page,
            'page_size': page_size,
            'total_count': total_count,
            'entries': serializer.data,
        })


class PayoutListCreateView(APIView):
    """
    GET  /api/v1/payouts/ — list payouts (filterable by ?merchant_id=X)
    POST /api/v1/payouts/ — create a new payout
    """

    def get(self, request):
        payouts = Payout.objects.select_related('merchant').all()

        # Optional filter by merchant
        merchant_id = request.query_params.get('merchant_id')
        if merchant_id:
            payouts = payouts.filter(merchant_id=merchant_id)

        serializer = PayoutSerializer(payouts, many=True)
        return Response(serializer.data)

    def post(self, request):
        # Step 1: Validate the input data
        serializer = CreatePayoutSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Step 2: Get the idempotency key from the request header
        # If no key is provided, generate one (so every request without a key
        # is treated as unique — no idempotency protection, but it won't crash)
        idempotency_key = request.headers.get('Idempotency-Key', str(uuid.uuid4()))

        merchant_id = serializer.validated_data['merchant_id']
        amount_paise = serializer.get_amount_paise()
        bank_account_id = serializer.validated_data['bank_account_id']

        # Step 3: Call the service — all the hard work happens there
        response_data, http_status, is_new = create_payout(
            merchant_id=merchant_id,
            amount_paise=amount_paise,
            bank_account_id=bank_account_id,
            idempotency_key_str=idempotency_key,
        )

        # Step 4: Return the response with appropriate headers
        response = Response(response_data, status=http_status)

        # Tell the client whether this was a fresh response or a cached one
        if not is_new:
            response['X-Idempotency-Replay'] = 'true'

        return response


class PayoutDetailView(APIView):
    """GET /api/v1/payouts/{payout_id}/ — get a single payout's current status"""

    def get(self, request, payout_id):
        try:
            payout = Payout.objects.select_related('merchant').get(id=payout_id)
        except Payout.DoesNotExist:
            return Response({'error': 'Payout not found'}, status=status.HTTP_404_NOT_FOUND)

        serializer = PayoutSerializer(payout)
        return Response(serializer.data)
