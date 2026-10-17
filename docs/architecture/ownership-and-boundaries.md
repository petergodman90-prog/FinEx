# Ownership and boundaries

This document prevents duplicate logic and unsafe cross-domain writes in FinEx.

## Primary ownership

| Area | Primary owner | Boundary |
| --- | --- | --- |
| Ledger, inventory, sales posting, expenses, debts, receipts, reports | Person A | The authoritative financial and stock record. |
| Tenant isolation, sessions, WhatsApp, AI assistant, customer and multi-business services | Person B | Organisation access, external integrations, and AI proposals. |
| Login interface, onboarding screens, dashboard, orders, staff, state display, approval flows | Person C | All visible interfaces and client-side interaction. |

## Non-negotiable rules

1. **Posting boundary:** Only the Person A posting service may write money, stock movements, receipts, open items, or journal records.
2. **Tenant boundary:** Every query and service call uses an `org_id`. Person B owns the organisation context and row-level security policy.
3. **Display boundary:** Person C displays values returned by the backend and does not recalculate stock, balances, profit, or totals in the browser.
4. **AI boundary:** AI creates a proposal only. Person C presents it for approval and Person A posts the approved action.

## A sale crosses three domains

1. Person C collects the order and approval from the user.
2. Person B confirms the active organisation and may supply an integration or AI proposal.
3. Person A validates stock and posts the sale, revenue, COGS, and stock movement in one transaction.
4. Person C receives the resulting read model and shows it to the user.

