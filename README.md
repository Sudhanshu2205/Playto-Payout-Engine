# Playto Payout Engine

A ledger-based merchant payout system with concurrency safety, idempotency, and async processing.

**Stack:** Django + DRF · PostgreSQL · Celery + Redis · React (Vite)

---

## Quick Start (Local)

### Prerequisites
- Python 3.11+
- PostgreSQL 17 (install via `winget install -e --id PostgreSQL.PostgreSQL.17` on Windows)
- Redis (optional for local dev — Celery tasks are skipped in test mode)
- Node.js 18+

### 1. Set up the backend

```powershell
cd backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy env file and configure
copy .env.example .env
# Edit .env with your DB credentials (default password is 'postgres')

# Create the PostgreSQL database (run in a separate terminal)
# $env:PGPASSWORD = "postgres"; psql -U postgres -h localhost -c "CREATE DATABASE payout_engine;"

# Run migrations
.\venv\Scripts\python.exe manage.py makemigrations ledger
.\venv\Scripts\python.exe manage.py migrate

# Seed the database with test data
.\venv\Scripts\python.exe manage.py seed_data
```

### 2. Start the Django server

```powershell
.\venv\Scripts\python.exe manage.py runserver
```

### 3. Start Celery worker (new PowerShell terminal)

```powershell
cd backend
venv\Scripts\activate
.\venv\Scripts\python.exe -m celery -A payout_engine worker --loglevel=info
```

### 4. Start Celery beat scheduler (new PowerShell terminal)

```powershell
cd backend
venv\Scripts\activate
.\venv\Scripts\python.exe -m celery -A payout_engine beat --loglevel=info
```

### 5. Start the React frontend (new PowerShell terminal)

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

---

## Run Tests

```bash
cd backend
python manage.py test ledger
```

Tests run:
- `ConcurrencyTest` — two simultaneous 600-rupee payouts against a 1000-rupee balance. Asserts exactly 1 succeeds.
- `IdempotencyTest` — same idempotency key sent twice. Asserts same payout_id, only 1 Payout row in DB.
- `BalanceCalculationTest` — verifies DB aggregate balance logic
- `StateMachineTest` — verifies invalid state transitions are blocked

---

## Docker (easiest way to run everything)

```bash
# Copy the env file first
cp backend/.env.example backend/.env

# Start everything
docker compose up --build

# In another terminal, run migrations and seed
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_data
```

Services:
- Django API: http://localhost:8000
- React dashboard: http://localhost:5173
- Django admin: http://localhost:8000/admin

---

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/api/v1/merchants/` | List all merchants |
| GET | `/api/v1/merchants/{id}/balance/` | Merchant balance (available + held) |
| GET | `/api/v1/merchants/{id}/ledger/` | Paginated ledger history |
| POST | `/api/v1/payouts/` | Create a payout request |
| GET | `/api/v1/payouts/` | List payouts (filter: `?merchant_id=X`) |
| GET | `/api/v1/payouts/{id}/` | Single payout status |

### Creating a payout

```bash
curl -X POST http://localhost:8000/api/v1/payouts/ \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
    "merchant_id": 1,
    "amount": 500.00,
    "bank_account_id": "HDFC-12345"
  }'
```

---

## Architecture Decisions

### Why no balance column on Merchant?
Balance is always derived fresh from LedgerEntry records via DB aggregation. A cached balance column would become stale (race conditions, crashes mid-transaction, etc.). The ledger is the source of truth.

### Why paise (BigIntegerField)?
Floating point math is imprecise. `0.1 + 0.2 != 0.3` in Python. All amounts are stored in paise (₹1 = 100 paise) as integers. Converting to rupees only happens at display time.

### Why select_for_update()?
The balance check and debit write must be atomic. We lock the merchant row at the start of the transaction so no two payout requests can check balance simultaneously for the same merchant. The second request blocks until the first commits, then sees the updated balance.

### Why is idempotency checked inside the lock?
If two requests with the same idempotency key arrive at exactly the same millisecond, a Python-level dictionary check would fail. By checking inside the `select_for_update` transaction, only one can proceed — the other waits, then finds the existing key and returns the cached response.

---

## Project Structure

```
backend/
├── payout_engine/      # Django project (settings, celery, urls)
├── ledger/             # Main app
│   ├── models.py       # Merchant, LedgerEntry, Payout, IdempotencyKey
│   ├── services.py     # get_balance(), create_payout() — all business logic
│   ├── tasks.py        # Celery: process_payout, retry_stuck_payouts
│   ├── views.py        # DRF API views
│   ├── serializers.py
│   ├── tests.py        # Concurrency + idempotency tests
│   └── management/commands/seed_data.py
frontend/
└── src/App.jsx         # Dashboard with live polling
```
