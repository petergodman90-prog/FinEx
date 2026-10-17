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

