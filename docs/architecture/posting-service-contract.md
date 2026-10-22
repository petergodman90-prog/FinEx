# Financial posting-service contract

The posting service is the only route that creates financial or inventory
history. The implementation lives in
`backend/app/services/posting_service.py` (Person A). Approvals, HTTP routes and
UI forms are consumers; they never write ledger tables directly.

## Required inputs

Every posting request must include:

- `org_id`: the organisation that owns the event;
- `source_type` and `source_id`: where the event originated;
- `idempotency_key`: a unique key that prevents duplicate processing;
- `requested_by`: the authenticated user or system actor (a UUID);
- `effective_at`: when the business event occurred (ISO-8601 with an offset).

Approved proposals always use:

```text
source_type     = 'proposal'
source_id       = public.proposals.id
idempotency_key = public.proposals.id
requested_by    = the approving user's UUID
```

The posting service compares the session's `app.current_org_id` with `org_id`
before calling any privileged function. A caller bound to another organisation
is rejected with `cross_org`.

## Guarantees

The service must guarantee all of the following before returning success:

- journal debits equal journal credits;
- each journal line has a debit or a credit, never both;
- accounts, customers, products and suppliers belong to the same organisation;
- a sale posts revenue and matching COGS together when inventory is credited;
- stock cannot become negative;
- repeated requests with the same idempotency key return the original result
  rather than post twice (including after the period is closed or the entry is
  reversed);
- related writes either all succeed or all roll back (a savepoint keeps the
  caller able to record a failure);
- posted records are immutable and corrections are reversals.

## Payloads

The payload is stored on the proposal in `proposals.payload` and is validated
with `extra="forbid"` by the posting service. Money values are decimal strings
or JSON numbers; the service quantises them to four decimal places.

### Sale

```json
{
  "customer_id": "0f0b1d4e-7a2c-4f3e-9a1b-9c9f0a7d2e11",
  "currency": "NGN",
  "effective_at": "2026-10-22T10:30:00+01:00",
  "memo": "Counter sale",
  "lines": [
    {
      "product_id": "8a6d2c1f-3f4e-4b5a-8c7d-2e1f0a9b8c7d",
      "quantity": "2",
      "unit_price": "150.00",
      "unit_cost": "100.00",
      "description": "Blue shirt"
    }
  ],
  "payment": {
    "method": "cash",
    "cash_account_code": "1000",
    "issue_receipt": true,
    "receipt_notes": "Paid at the counter"
  }
}
```

Sale rules:

- `lines` must contain at least one line. Each product may appear only once;
  combine quantities instead of duplicating a product.
- `unit_cost` is the historical cost snapshot used for COGS and the stock
  movement. It may be zero, but it is required so a later product-cost change
  cannot rewrite history.
- `payment.method` is `"cash"` (any immediately settling account, selected by
  `cash_account_code`, default `1000`) or `"receivable"` (debit `1100` and
  create a customer receivable).
- For `"receivable"`, `payment.due_on` (a date) is optional. A receipt cannot be
  issued for a receivable sale.
- For `"cash"`, `issue_receipt` defaults to `true`. When true the service
  creates the receipt document linked to the journal entry. It does not create a
  second journal entry.
- The sale journal is: debit cash/receivables for revenue, credit revenue
  `4000`; when cost is positive, debit COGS `5000` and credit inventory `1200`.
- If `proposals.amount` is set it must equal the sale line total. If
  `proposals.currency` is set it must equal `currency`.

### Expense

```json
{
  "amount": "250.00",
  "currency": "NGN",
  "effective_at": "2026-10-22T09:00:00+01:00",
  "expense_account_code": "6100",
  "category": "rent",
  "vendor": "Lagos Property Ltd",
  "description": "October rent",
  "payment": {
    "method": "cash",
    "cash_account_code": "1000"
  }
}
```

A payable expense uses a supplier from the ledger setup API:

```json
{
  "amount": "250.00",
  "currency": "NGN",
  "effective_at": "2026-10-22T09:00:00+01:00",
  "category": "utilities",
  "vendor": "Power Co",
  "payment": {
    "method": "payable",
    "supplier_id": "5b7c9d11-2e3f-4a5b-8c9d-0e1f2a3b4c5d",
    "due_on": "2026-11-21"
  }
}
```

Expense rules:

- Exactly one of `expense_account_code` or `category` is required. A code wins.
  Categories map to the seeded accounts: `rent`/`utilities` → `6100`,
  `salaries`/`payroll`/`wages` → `6200`, `marketing`/`advertising` → `6300`,
  `bad_debt` → `6400`, `bank_charges` → `6500`, `inventory_variance` → `6600`,
  and `other`/`operating` → `6000`.
- `"cash"` credits the selected cash account. `"payable"` requires
  `payment.supplier_id`, credits accounts payable `2000`, and creates a supplier
  payable open item.
- `amount` must be positive and must equal `proposals.amount` and
  `proposals.currency` when those fields are set.

### Opening stock

Opening stock is how the first sale gets stock. It can be posted from an
`opening_stock` proposal, or called directly during setup with an explicit
idempotency key.

```json
{
  "effective_at": "2026-10-01T00:00:00+01:00",
  "memo": "Opening stock count",
  "lines": [
    {
      "product_id": "8a6d2c1f-3f4e-4b5a-8c7d-2e1f0a9b8c7d",
      "quantity": "20",
      "unit_cost": "100.00",
      "description": "Blue shirt"
    }
  ]
}
```

Opening stock debits inventory `1200` and credits opening equity `3000`, and
posts one `opening_balance` stock movement per product. The total cost must be
positive. Each product may appear only once.

### Manual proposals

**Manual proposals are informational only and are never posted.** They have no
financial payload. `post_proposal` rejects `proposal_type = "manual"` with
`manual_not_postable`. The UI must show them as pending/approved records, not
as posted transactions, and must not include them in ledger, stock or financial
totals.

## Finance setup

The posting service exposes setup operations that Person B can wrap in APIs:

| Service function | Purpose |
| --- | --- |
| `ensure_default_chart` | Idempotently seed the standard chart and return it by code |
| `create_account` / `update_account` | Add, rename, activate or mark a cash account |
| `create_product` / `update_product` | Product master, default cost, reorder point |
| `create_supplier` | Minimal payable counterparty (no PII) |
| `post_opening_stock` | Opening inventory and its journal entry |

The seeded chart contains 19 accounts:

| Code | Account | Type | Control | Cash |
| --- | --- | --- | --- | --- |
| 1000 | Cash on Hand | asset | yes | yes |
| 1010 | Bank Account | asset | yes | yes |
| 1100 | Accounts Receivable | asset | yes | no |
| 1200 | Inventory | asset | yes | no |
| 1300 | Prepaid Expenses | asset | no | no |
| 2000 | Accounts Payable | liability | yes | no |
| 2100 | Credit Notes Issued | liability | yes | no |
| 3000 | Owner Equity | equity | no | no |
| 3100 | Retained Earnings | equity | no | no |
| 4000 | Sales Revenue | revenue | no | no |
| 4100 | Other Income | revenue | no | no |
| 5000 | Cost of Goods Sold | cogs | no | no |
| 6000 | Operating Expenses | expense | no | no |
| 6100 | Rent and Utilities | expense | no | no |
| 6200 | Salaries and Wages | expense | no | no |
| 6300 | Marketing | expense | no | no |
| 6400 | Bad Debt Expense | expense | no | no |
| 6500 | Bank Charges | expense | no | no |
| 6600 | Inventory Variance | expense | no | no |

`ensure_default_chart` is safe to call on every posting and on workspace
creation. Existing accounts are never modified.

## Service API for Person B

```python
result = await posting_service.post_proposal(
    session,
    org_id=actor.org_id,
    proposal_id=proposal.id,
    proposal_type=proposal.proposal_type,
    payload=proposal.payload,
    amount=proposal.amount,
    currency=proposal.currency,
    requested_by=uuid.UUID(actor.sub),
    role=actor.role,
)
result.journal_entry_id
```

The service never commits. Person B's request transaction owns the commit. On
success, store `result.journal_entry_id` on the proposal and set it to
`applied`/posted. On failure, catch `PostingError`, keep the proposal approved,
store `error.code`, `str(error)` and `error.retryable`, and expose retry.

## Approval-to-posting behaviour (MVP)

Posting is synchronous:

1. approval succeeds and the proposal is `approved`;
2. posting is attempted immediately in the same request;
3. success records the journal-entry id and moves the proposal to `applied`;
4. failure leaves the proposal `approved` with a posting error and a retry
   action.

A retry is safe and idempotent; it either returns the original journal entry or
performs the posting exactly once. The UI must only show **Posted** when a
journal-entry id is present. Until then it shows **Approved — posting pending**
or **Posting failed — retry**.

## Failure codes

| Code | Retryable | Meaning |
| --- | --- | --- |
| `invalid_payload` | no | Payload does not match the frozen shape |
| `amount_mismatch` / `currency_mismatch` | no | Proposal header disagrees with payload |
| `customer_not_found` / `supplier_not_found` / `product_not_found` | no | Reference is missing, inactive or in another organisation |
| `account_not_found` | no | Required chart account is missing |
| `cross_org` | no | Session organisation differs from posting organisation |
| `unbalanced_entry` | no | Journal debits do not equal credits |
| `insufficient_stock` | yes | Stock would go negative; restock and retry |
| `closed_period` | yes | Effective date is in a closed accounting period |
| `already_reversed` | no | A different reversal already exists |
| `manual_not_postable` | no | Manual proposals are informational only |
| `posting_failed` | yes | Transient database failure; retry is safe |

## Concurrency and reversal

- A transaction-scoped advisory lock keyed by `(org_id, idempotency_key)`
  serialises concurrent approvals. The posting-event unique key is the final
  database guard.
- Reversal posts the opposite journal entry with `reversal_of_id` set and posts
  the opposite stock movements (`sale_reversal` for sales). Repeating a reversal
  with the same idempotency key returns the original reversal entry.
- This MVP reverses the journal and inventory effect only. Receipt credit notes
  and settlement reversals are separate postings.

## Tenant context (enforced in the database)

Every posting call runs in a transaction where `app.current_org_id` is bound to
the authenticated organisation. Ledger write guards reject any row whose
`org_id` does not match that session value, even if a privileged
`SECURITY DEFINER` function is invoked with a foreign id. The posting service
binds the same `org_id` it passes to the function and validates cross-domain
references (customer, product, supplier) in that organisation first.

## Cross-domain identifiers

- `public.customers.id` is the canonical customer UUID. `ledger.receipts.customer_id`
  and `ledger.open_items.counterparty_id` use the same UUID. There is deliberately
  no cross-schema foreign key: the ledger domain must remain usable independently,
  and tenant deletion is handled explicitly.
- `public.proposals.id` is the approval idempotency key. Re-approving or retrying
  therefore returns the original journal entry instead of posting twice.
- `requested_by` is the authenticated user's UUID (or a system actor UUID), never
  a display name.

## Related contracts

- [Ownership and boundaries](ownership-and-boundaries.md)
- [ADR 0001: financial records are append-only](../adr/0001-financial-records-are-append-only.md)
- `db/migrations/0001_person_a_financial_core.sql` — ledger schema and posting functions
- `db/migrations/0002_person_a_posting_service.sql` — setup functions and sale lines
- `db/migrations/0003_person_a_posting_service_hardening.sql` — replay guards
