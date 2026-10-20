# Financial posting-service contract

The posting service is the only route that creates financial or inventory history.

## Required inputs

Every posting request must include:

- `org_id`: the organisation that owns the event;
- `source_type` and `source_id`: where the event originated, such as an order or supplier purchase;
- `idempotency_key`: a unique key that prevents duplicate processing;
- `requested_by`: the authenticated user or system actor;
- `effective_at`: when the business event occurred.

## Guarantees

The service must guarantee all of the following before returning success:

- journal debits equal journal credits;
- each journal line has a debit or a credit, never both;
- accounts and referenced records belong to the same organisation;
- a sale posts revenue and matching COGS together;
- stock cannot become negative;
- repeated requests with the same idempotency key return the original result rather than post twice;
- related writes either all succeed or all roll back;
- posted records are immutable and corrections are reversals.

## Consumers

The UI, importer, AI assistant, WhatsApp integration, and future APIs are consumers of this contract. They must not write financial or stock tables directly.

## Cross-domain identifiers

- `public.customers.id` is the canonical customer UUID. `ledger.receipts.customer_id`
  and `ledger.open_items.counterparty_id` use the same UUID. There is deliberately
  no cross-schema foreign key: the ledger domain must remain usable independently,
  and tenant deletion is handled explicitly. The posting service validates that the
  customer belongs to `org_id` before writing.
- An approved proposal posts with `source_type = 'proposal'`,
  `source_id = public.proposals.id`, and `idempotency_key = public.proposals.id`.
  Re-approving or retrying therefore returns the original journal entry instead of
  posting twice.
- `requested_by` is the authenticated user's UUID (or a system actor UUID), never
  a display name.

## Tenant context (enforced in the database)

Every posting call runs in a transaction where `app.current_org_id` is bound to the
authenticated organisation. Ledger write guards reject any row whose `org_id` does
not match that session value, even if a privileged `SECURITY DEFINER` function is
invoked with a foreign id. The posting service must bind the same `org_id` it passes
to the function.

## Approval state vs. posting state

An approved proposal is an authorisation, not a financial posting. Until the posting
service returns a journal-entry id, the UI must not label the proposal as posted or
show it in ledger/stock figures. The backend approval endpoint records the decision;
the financial commit is a separate, idempotent call owned by Person A.

