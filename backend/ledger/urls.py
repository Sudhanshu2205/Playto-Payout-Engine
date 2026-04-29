from django.urls import path
from .views import (
    MerchantListView,
    MerchantBalanceView,
    MerchantLedgerView,
    PayoutListCreateView,
    PayoutDetailView,
)

urlpatterns = [
    # Merchants
    path('merchants/', MerchantListView.as_view(), name='merchant-list'),
    path('merchants/<int:merchant_id>/balance/', MerchantBalanceView.as_view(), name='merchant-balance'),
    path('merchants/<int:merchant_id>/ledger/', MerchantLedgerView.as_view(), name='merchant-ledger'),

    # Payouts
    path('payouts/', PayoutListCreateView.as_view(), name='payout-list-create'),
    path('payouts/<int:payout_id>/', PayoutDetailView.as_view(), name='payout-detail'),
]
