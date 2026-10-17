# ADR 0001: Financial records are append-only

**Status:** Accepted  
**Date:** 2026-10-17

## Decision

Posted journal entries, journal lines, stock movements, receipts, open items, and payment allocations are immutable. Corrections are represented by a new reversing or compensating entry.

## Why

Editing or deleting financial history makes audit trails unreliable and can silently alter past reports. Append-only records preserve the sequence of events and make each correction visible.

## Consequences

- Database triggers reject updates and deletes on posted financial records.
- Product cost changes affect future purchases only; historical COGS remains unchanged.
- User interfaces offer a reversal or credit-note workflow instead of an edit-history workflow.

