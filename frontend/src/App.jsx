/*
  App.jsx — Main dashboard component

  Shows:
  - Merchant selector (dropdown)
  - Balance cards (available + held)
  - Payout form
  - Payout history with status (auto-refreshes every 3 seconds)
  - Ledger table (credits and debits)
*/

import { useState, useEffect, useCallback } from 'react'

const API = '/api/v1'

// Helper: format paise as rupees with ₹ symbol
function formatRupees(paise) {
  if (paise === null || paise === undefined) return '₹0.00'
  return '₹' + (paise / 100).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

// Helper: format ISO date string to readable
function formatDate(isoString) {
  if (!isoString) return '-'
  return new Date(isoString).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true
  })
}

// Status badge component
function StatusBadge({ status }) {
  const classes = {
    PENDING: 'badge badge-pending',
    PROCESSING: 'badge badge-processing',
    COMPLETED: 'badge badge-completed',
    FAILED: 'badge badge-failed',
  }
  return <span className={classes[status] || 'badge'}>{status}</span>
}

export default function App() {
  const [merchants, setMerchants] = useState([])
  const [selectedMerchantId, setSelectedMerchantId] = useState(null)
  const [balance, setBalance] = useState(null)
  const [payouts, setPayouts] = useState([])
  const [ledger, setLedger] = useState([])
  const [loading, setLoading] = useState(false)

  // Payout form state
  const [formAmount, setFormAmount] = useState('')
  const [formBankAccount, setFormBankAccount] = useState('')
  const [formSubmitting, setFormSubmitting] = useState(false)
  const [formMessage, setFormMessage] = useState(null)  // { type: 'success'|'error', text: string }

  // Load all merchants on first render
  useEffect(() => {
    fetch(`${API}/merchants/`)
      .then(r => r.json())
      .then(data => {
        setMerchants(data)
        if (data.length > 0) {
          setSelectedMerchantId(data[0].id)
        }
      })
      .catch(err => console.error('Failed to load merchants:', err))
  }, [])

  // Fetch balance, payouts, and ledger for the selected merchant
  const fetchMerchantData = useCallback(() => {
    if (!selectedMerchantId) return

    // Balance
    fetch(`${API}/merchants/${selectedMerchantId}/balance/`)
      .then(r => r.json())
      .then(setBalance)
      .catch(console.error)

    // Payouts
    fetch(`${API}/payouts/?merchant_id=${selectedMerchantId}`)
      .then(r => r.json())
      .then(setPayouts)
      .catch(console.error)

    // Ledger
    fetch(`${API}/merchants/${selectedMerchantId}/ledger/`)
      .then(r => r.json())
      .then(data => setLedger(data.entries || []))
      .catch(console.error)
  }, [selectedMerchantId])

  // Fetch data when merchant changes
  useEffect(() => {
    fetchMerchantData()
  }, [fetchMerchantData])

  // Poll every 3 seconds to get live payout status updates
  useEffect(() => {
    if (!selectedMerchantId) return
    const interval = setInterval(fetchMerchantData, 3000)
    return () => clearInterval(interval)  // cleanup on unmount
  }, [selectedMerchantId, fetchMerchantData])

  // Handle payout form submission
  async function handlePayoutSubmit(e) {
    e.preventDefault()
    setFormSubmitting(true)
    setFormMessage(null)

    // Generate a random idempotency key for this request
    // In a real app, you'd store this in localStorage and use it on retry
    const idempotencyKey = crypto.randomUUID()

    try {
      const response = await fetch(`${API}/payouts/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          merchant_id: selectedMerchantId,
          amount: parseFloat(formAmount),
          bank_account_id: formBankAccount,
        }),
      })

      const data = await response.json()

      if (response.ok) {
        setFormMessage({
          type: 'success',
          text: `Payout #${data.payout_id} created for ${formatRupees(data.amount_paise)}. Status: ${data.status}`,
        })
        setFormAmount('')
        setFormBankAccount('')
        fetchMerchantData()  // refresh immediately
      } else {
        setFormMessage({
          type: 'error',
          text: data.error || 'Something went wrong. Please try again.',
        })
      }
    } catch (err) {
      setFormMessage({
        type: 'error',
        text: 'Network error. Is the backend running?',
      })
    } finally {
      setFormSubmitting(false)
    }
  }

  const selectedMerchant = merchants.find(m => m.id === selectedMerchantId)

  return (
    <div className="app">
      {/* Header */}
      <div className="app-header">
        <div>
          <h1 className="app-title">Playto Payout Engine</h1>
          <p className="app-subtitle">Merchant payout management dashboard</p>
        </div>
        <div className="poll-indicator">
          <div className="poll-dot" />
          <span>Live — updates every 3s</span>
        </div>
      </div>

      {/* Merchant selector */}
      <div className="merchant-selector">
        <label htmlFor="merchant-select">Viewing merchant:</label>
        <select
          id="merchant-select"
          value={selectedMerchantId || ''}
          onChange={e => setSelectedMerchantId(parseInt(e.target.value))}
        >
          {merchants.map(m => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </div>

      {/* Balance cards */}
      {balance && (
        <div className="balance-row">
          <div className="balance-card">
            <div className="balance-card-label">Available Balance</div>
            <div className="balance-card-amount success">
              {formatRupees(balance.available_paise)}
            </div>
            <div className="balance-card-sub">Ready to withdraw</div>
          </div>

          <div className="balance-card">
            <div className="balance-card-label">Held (In-Flight)</div>
            <div className="balance-card-amount warning">
              {formatRupees(balance.held_paise)}
            </div>
            <div className="balance-card-sub">Pending + Processing payouts</div>
          </div>

          <div className="balance-card">
            <div className="balance-card-label">Total Credits</div>
            <div className="balance-card-amount info">
              {formatRupees(balance.total_credits_paise)}
            </div>
            <div className="balance-card-sub">All time earnings</div>
          </div>

          <div className="balance-card">
            <div className="balance-card-label">Total Paid Out</div>
            <div className="balance-card-amount">
              {formatRupees(balance.total_debits_paise)}
            </div>
            <div className="balance-card-sub">Completed payouts</div>
          </div>
        </div>
      )}

      {/* Main grid: Payout form + Payout history */}
      <div className="main-grid">
        {/* Payout form */}
        <div className="panel">
          <div className="panel-title">
            <span>💸</span> Request Payout
          </div>
          <form onSubmit={handlePayoutSubmit}>
            <div className="form-group">
              <label className="form-label" htmlFor="amount">Amount (in ₹ Rupees)</label>
              <input
                id="amount"
                type="number"
                className="form-input"
                placeholder="e.g. 500.00"
                min="1"
                step="0.01"
                value={formAmount}
                onChange={e => setFormAmount(e.target.value)}
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="bank-account">Bank Account ID</label>
              <input
                id="bank-account"
                type="text"
                className="form-input"
                placeholder="e.g. HDFC-12345"
                value={formBankAccount}
                onChange={e => setFormBankAccount(e.target.value)}
                required
              />
            </div>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={formSubmitting || !formAmount || !formBankAccount}
              id="submit-payout-btn"
            >
              {formSubmitting ? (
                <>
                  <div className="spinner" />
                  Processing...
                </>
              ) : 'Submit Payout Request'}
            </button>
          </form>

          {formMessage && (
            <div className={`alert alert-${formMessage.type}`}>
              {formMessage.text}
            </div>
          )}
        </div>

        {/* Recent payouts */}
        <div className="panel">
          <div className="panel-title">
            <span>📋</span> Payout History
          </div>
          {payouts.length === 0 ? (
            <div className="empty-state">No payouts yet</div>
          ) : (
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th>Date</th>
                  </tr>
                </thead>
                <tbody>
                  {payouts.slice(0, 10).map(p => (
                    <tr key={p.id}>
                      <td style={{ color: '#64748b' }}>{p.id}</td>
                      <td>{formatRupees(p.amount_paise)}</td>
                      <td><StatusBadge status={p.status} /></td>
                      <td style={{ color: '#64748b', fontSize: '11px' }}>
                        {formatDate(p.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Ledger table */}
      <div className="panel-full">
        <div className="panel-title">
          <span>📒</span> Ledger History
        </div>
        {ledger.length === 0 ? (
          <div className="empty-state">No ledger entries yet</div>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Type</th>
                  <th>Amount</th>
                  <th>Description</th>
                  <th>Payout ID</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map(entry => (
                  <tr key={entry.id}>
                    <td style={{ color: '#64748b' }}>{entry.id}</td>
                    <td>
                      <span className={entry.entry_type === 'CREDIT' ? 'text-credit' : 'text-debit'}>
                        {entry.entry_type === 'CREDIT' ? '▲ Credit' : '▼ Debit'}
                      </span>
                    </td>
                    <td className={entry.entry_type === 'CREDIT' ? 'text-credit' : 'text-debit'}>
                      {entry.entry_type === 'CREDIT' ? '+' : '-'}{formatRupees(entry.amount_paise)}
                    </td>
                    <td style={{ color: '#94a3b8', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {entry.description || '-'}
                    </td>
                    <td style={{ color: '#64748b' }}>
                      {entry.payout_id ? `#${entry.payout_id}` : '-'}
                    </td>
                    <td style={{ color: '#64748b', fontSize: '11px' }}>
                      {formatDate(entry.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
