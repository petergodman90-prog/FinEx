# Ledger reports

Person A owns the authoritative report read models in
`backend/app/services/ledger_service.py`. Reports read the immutable journal and
stock movement history; they never recalculate balances from proposals, orders
or browser state. Person B may expose them through API routes, and Person C must
render the returned values without recomputing them.

## Date semantics

- `profit_and_loss(start, end)` includes both calendar dates.
- `balance_sheet(as_of)` includes every journal entry effective on or before
  `as_of`.
- Journal dates are compared in the database session's timezone (UTC for the
  application runtime).
- Reversal entries are appended, not substituted. Both the original entry and
  its reversal remain in history, so their amounts net to zero in the combined
  period while each period shows its own effect. Do not filter out entries with
  `status = 'reversed'`.

## Profit and loss

For each account, normal balance is:

- revenue: `credit - debit`
- cost of goods sold and expenses: `debit - credit`

The report returns:

```text
revenue_total            = sum(revenue account normal balances)
cogs_total               = sum(cogs account normal balances)
gross_profit             = revenue_total - cogs_total
operating_expense_total  = sum(expense account normal balances)
net_profit               = gross_profit - operating_expense_total
```

Each section also returns account-level `ReportLine` rows with debit, credit and
normal-balance amounts. Negative amounts are valid: they represent reversals,
refunds or contra entries.

## Balance sheet

The balance sheet includes all journal activity up to and including `as_of`:

- assets: `debit - credit`
- liabilities and equity: `credit - debit`
- current earnings: `revenue - cogs - operating expenses` for all periods
  included in the report

`total_liabilities_and_equity` is liabilities + equity + current earnings.
`is_balanced` is true only when total assets equal that figure. A false value
means the underlying journal or account classification needs investigation; the
report does not hide the difference.

## Stock valuation

`stock_valuation` reads `ledger.v_inventory_on_hand`, which is built from the
immutable stock movement log and the unit cost snapshot on each movement. It
returns quantity, book value and average unit cost per product, plus:

- `inventory_account_balance`: the GL balance of account `1200` (Inventory);
- `is_reconciled`: whether total stock book value equals that GL balance.

A non-reconciled report must be treated as an error, not as a display rounding
issue.

## Receipt read models

Receipts are immutable documents created by postings. Person A exposes them
only through read models:

- `list_receipts(org_id, receipt_type=..., customer_id=..., limit=..., offset=...)`
  returns documents newest-first by receipt number plus the filtered total.
- `get_receipt(org_id, receipt_id)` returns one document or `None`.
- `get_receipt_lines(org_id, receipt_id)` joins the parent receipt to enforce
  organisation scope, because receipt lines do not carry their own `org_id`.
- `receipt_credit_note_total(org_id, receipt_id)` sums credit notes linked to
  the original receipt.
- `get_posting_documents(org_id, journal_entry_ids)` batches receipt and
  open-item links for proposal read models.

`receipt_type` is `receipt` for cash sales and settlement receipts, or
`credit_note` for refunds. A credit note points at the original receipt through
`credit_note_of_id`; posted documents are never edited or voided.

## Debt aging

`debt_aging(item_type)` reads `ledger.v_open_item_balances` for either
`customer_receivable` or `supplier_payable`. Only items with a non-zero
outstanding amount are counted. Buckets follow the database view:

| Bucket | Meaning |
| --- | --- |
| `0-30` | not due yet, or up to 30 days overdue |
| `31-60` | 31–60 days overdue |
| `61-90` | 61–90 days overdue |
| `90+` | more than 90 days overdue |

All four buckets are always returned, including empty ones, so consumers get a
stable shape. Aging is calculated by the database against `current_date`.

## Tenant isolation

Every report query filters on `org_id` and runs under the request's RLS-bound
session. A report for one organisation cannot include another organisation's
accounts, journal lines, stock or open items. Tests cover cross-organisation
isolation in `backend/tests/test_posting_service.py`.
