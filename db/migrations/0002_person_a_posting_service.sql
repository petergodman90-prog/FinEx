-- Person A — posting-service support layer
-- PostgreSQL 15+
--
-- Migration 0001 created the ledger skeleton: chart of accounts, journal,
-- stock movements, open items, receipts and the four posting functions.
-- It deliberately stopped short of the write paths the application needs.
--
-- This migration closes that gap so that *every* financial or stock write can
-- be expressed as a call to a SECURITY DEFINER function. The runtime role
-- (`finex_app`) holds SELECT on the tables and EXECUTE on the functions —
-- never INSERT/UPDATE/DELETE — so a stray write from the UI, an importer, the
-- AI assistant or the WhatsApp integration is impossible, not merely
-- discouraged.
--
-- Added here:
--   * products.current_cost          — the cost side of the product master.
--   * accounts.is_cash_account       — cash-flow classification.
--   * receipts.receipt_type / credit_note_of_id — credit notes.
--   * sale_lines                     — product-level sales detail.
--   * payment_allocations.reversal_of_id + signed amounts — a reversed
--     settlement can be recorded without mutating history.
--   * write functions for accounts, products, open items, settlements,
--     accounting periods and sale lines.
--
-- Cost policy note: the cost of goods sold for a sale is decided by the
-- application-side cost engine (a perpetual moving average over the
-- immutable movement log) and snapshotted into `stock_movements.unit_cost`.
-- Changing `products.current_cost` afterwards therefore cannot rewrite
-- historical COGS. There is deliberately no SQL valuation view, so that
-- exactly one cost policy exists.

CREATE TYPE ledger.receipt_type AS ENUM ('receipt', 'credit_note');

-- ── Product master: the cost side ───────────────────────────

ALTER TABLE ledger.products
    ADD COLUMN current_cost numeric(19,4) NOT NULL DEFAULT 0;

ALTER TABLE ledger.products
    ADD CONSTRAINT products_current_cost_non_negative CHECK (current_cost >= 0);

-- ── Chart of accounts: cash-flow classification ─────────────

ALTER TABLE ledger.accounts
    ADD COLUMN is_cash_account boolean NOT NULL DEFAULT false;

-- ── Receipts: credit notes ──────────────────────────────────

ALTER TABLE ledger.receipts
    ADD COLUMN receipt_type ledger.receipt_type NOT NULL DEFAULT 'receipt';

ALTER TABLE ledger.receipts
    ADD COLUMN credit_note_of_id uuid;

ALTER TABLE ledger.receipts
    ADD CONSTRAINT receipts_credit_note_of_fk
    FOREIGN KEY (org_id, credit_note_of_id)
    REFERENCES ledger.receipts (org_id, id) ON DELETE RESTRICT;

ALTER TABLE ledger.receipts
    ADD CONSTRAINT receipts_credit_note_not_self
    CHECK (credit_note_of_id IS NULL OR credit_note_of_id <> id);

ALTER TABLE ledger.receipts
    ADD CONSTRAINT receipts_credit_note_only_for_credit_notes
    CHECK (receipt_type = 'credit_note' OR credit_note_of_id IS NULL);

-- ── Settlements: a reversed settlement must be recordable ──
--
-- Allocations are immutable, so undoing a payment means appending a negative
-- allocation that points at the original. The outstanding balance stays
-- `original_amount - sum(allocations)`, which is correct for both directions.

ALTER TABLE ledger.payment_allocations
    ADD COLUMN reversal_of_id uuid;

ALTER TABLE ledger.payment_allocations
    DROP CONSTRAINT payment_allocations_amount_check;

ALTER TABLE ledger.payment_allocations
    ADD CONSTRAINT payment_allocations_amount_check CHECK (amount <> 0);

ALTER TABLE ledger.payment_allocations
    ADD CONSTRAINT payment_allocations_org_id_id_key UNIQUE (org_id, id);

ALTER TABLE ledger.payment_allocations
    ADD CONSTRAINT payment_allocations_reversal_of_fk
    FOREIGN KEY (org_id, reversal_of_id)
    REFERENCES ledger.payment_allocations (org_id, id) ON DELETE RESTRICT;

-- ── Suppliers: the payable counterparty ─────────────────────
--
-- Receivables point at `public.customers.id` (the CRM master, owned by
-- another domain). A payable needs a counterparty too, and a supplier is not
-- a customer, so the ledger keeps its own minimal supplier reference. There is
-- deliberately no contact data here: PII handling belongs to the CRM domain.

CREATE TABLE ledger.suppliers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    code text,
    name text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, id),
    CHECK (length(trim(name)) > 0)
);

CREATE UNIQUE INDEX suppliers_org_code_active_idx
    ON ledger.suppliers (org_id, lower(code))
    WHERE code IS NOT NULL;

-- ── Sale lines: product level sales detail ──────────────────
--
-- Journal lines record the accounting (revenue against receivables, COGS
-- against inventory) but not which products were sold. Sale lines carry that
-- detail so the sales summary reconciles with the profit and loss report.

CREATE TABLE ledger.sale_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    journal_entry_id uuid NOT NULL,
    line_number smallint NOT NULL,
    product_id uuid NOT NULL,
    quantity numeric(19,4) NOT NULL,
    unit_price numeric(19,4) NOT NULL,
    line_total numeric(19,4) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    cogs_amount numeric(19,4) NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (journal_entry_id, line_number),
    UNIQUE (org_id, id),
    FOREIGN KEY (org_id, journal_entry_id)
        REFERENCES ledger.journal_entries (org_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (org_id, product_id)
        REFERENCES ledger.products (org_id, id) ON DELETE RESTRICT,
    CHECK (line_number > 0),
    CHECK (quantity > 0),
    CHECK (unit_price >= 0),
    CHECK (cogs_amount >= 0)
);

CREATE INDEX sale_lines_org_product_idx
    ON ledger.sale_lines (org_id, product_id, created_at);

CREATE TRIGGER sale_lines_are_immutable
BEFORE UPDATE OR DELETE ON ledger.sale_lines
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

-- ── Aging view: measure from the due date, or from opening when ─
-- ── there is no due date, and keep the existing column order. ─

CREATE OR REPLACE VIEW ledger.v_open_item_balances AS
SELECT
    oi.org_id,
    oi.id AS open_item_id,
    oi.item_type,
    oi.counterparty_id,
    oi.original_amount,
    coalesce(sum(pa.amount), 0) AS allocated_amount,
    oi.original_amount - coalesce(sum(pa.amount), 0) AS outstanding_amount,
    oi.due_on,
    CASE
        WHEN current_date <= coalesce(oi.due_on, oi.opened_at::date) THEN '0-30'
        WHEN current_date - coalesce(oi.due_on, oi.opened_at::date) <= 30 THEN '31-60'
        WHEN current_date - coalesce(oi.due_on, oi.opened_at::date) <= 60 THEN '61-90'
        ELSE '90+'
    END AS aging_bucket,
    oi.opened_at
FROM ledger.open_items oi
LEFT JOIN ledger.payment_allocations pa ON pa.open_item_id = oi.id
GROUP BY oi.org_id, oi.id, oi.item_type, oi.counterparty_id,
         oi.original_amount, oi.due_on, oi.opened_at;

-- ══════════════════════════════════════════════════════════════
-- Write functions
-- ══════════════════════════════════════════════════════════════

-- Chart of accounts ───────────────────────────────────────────

CREATE OR REPLACE FUNCTION ledger.create_account(
    p_org_id uuid,
    p_code text,
    p_name text,
    p_account_type ledger.account_type,
    p_is_control boolean DEFAULT false,
    p_is_cash_account boolean DEFAULT false
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_org_id IS NULL
       OR coalesce(length(trim(p_code)), 0) = 0
       OR coalesce(length(trim(p_name)), 0) = 0 THEN
        RAISE EXCEPTION 'organisation, code and name are required';
    END IF;

    INSERT INTO ledger.accounts (
        org_id, code, name, account_type, is_control_account, is_cash_account
    ) VALUES (
        p_org_id, trim(p_code), trim(p_name), p_account_type,
        coalesce(p_is_control, false), coalesce(p_is_cash_account, false)
    ) RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- Idempotent seeding of the standard chart. Existing accounts are left
-- untouched; the returned map is keyed by account code.
CREATE OR REPLACE FUNCTION ledger.seed_default_chart_of_accounts(p_org_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_result jsonb := '{}'::jsonb;
    v_id uuid;
    v_row record;
BEGIN
    IF p_org_id IS NULL THEN
        RAISE EXCEPTION 'organisation is required';
    END IF;

    FOR v_row IN
        SELECT * FROM (VALUES
            ('1000', 'Cash on Hand',            'asset',     true,  true),
            ('1010', 'Bank Account',             'asset',     true,  true),
            ('1100', 'Accounts Receivable',      'asset',     true,  false),
            ('1200', 'Inventory',                'asset',     true,  false),
            ('1300', 'Prepaid Expenses',         'asset',     false, false),
            ('2000', 'Accounts Payable',         'liability', true,  false),
            ('2100', 'Credit Notes Issued',      'liability', true,  false),
            ('3000', 'Owner Equity',             'equity',    false, false),
            ('3100', 'Retained Earnings',        'equity',    false, false),
            ('4000', 'Sales Revenue',            'revenue',   false, false),
            ('4100', 'Other Income',             'revenue',   false, false),
            ('5000', 'Cost of Goods Sold',       'cogs',      false, false),
            ('6000', 'Operating Expenses',       'expense',   false, false),
            ('6100', 'Rent and Utilities',       'expense',   false, false),
            ('6200', 'Salaries and Wages',       'expense',   false, false),
            ('6300', 'Marketing',                'expense',   false, false),
            ('6400', 'Bad Debt Expense',         'expense',   false, false),
            ('6500', 'Bank Charges',             'expense',   false, false),
            ('6600', 'Inventory Variance',       'expense',   false, false)
        ) AS t(code, name, kind, control, cash_account)
    LOOP
        INSERT INTO ledger.accounts (
            org_id, code, name, account_type, is_control_account, is_cash_account
        ) VALUES (
            p_org_id, v_row.code, v_row.name, v_row.kind::ledger.account_type,
            v_row.control, v_row.cash_account
        ) ON CONFLICT (org_id, code) DO NOTHING;

        SELECT id INTO v_id
        FROM ledger.accounts
        WHERE org_id = p_org_id AND code = v_row.code;

        v_result := v_result || jsonb_build_object(v_row.code, v_id);
    END LOOP;

    RETURN v_result;
END;
$$;

-- Suppliers ─────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION ledger.create_supplier(
    p_org_id uuid,
    p_name text,
    p_code text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_org_id IS NULL OR coalesce(length(trim(p_name)), 0) = 0 THEN
        RAISE EXCEPTION 'organisation and supplier name are required';
    END IF;

    INSERT INTO ledger.suppliers (org_id, code, name)
    VALUES (p_org_id, nullif(trim(coalesce(p_code, '')), ''), trim(p_name))
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- Product master ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION ledger.create_product(
    p_org_id uuid,
    p_sku text,
    p_name text,
    p_unit text,
    p_sale_price numeric,
    p_cost numeric DEFAULT 0,
    p_reorder_point numeric DEFAULT 0
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_org_id IS NULL
       OR coalesce(length(trim(p_sku)), 0) = 0
       OR coalesce(length(trim(p_name)), 0) = 0 THEN
        RAISE EXCEPTION 'organisation, sku and name are required';
    END IF;

    IF p_sale_price < 0 OR p_cost < 0 OR p_reorder_point < 0 THEN
        RAISE EXCEPTION 'price, cost and reorder point must not be negative';
    END IF;

    -- A duplicate SKU in the same organisation is rejected by the unique
    -- constraint (org_id, sku).
    INSERT INTO ledger.products (
        org_id, sku, name, unit, current_sale_price, current_cost, reorder_point
    ) VALUES (
        p_org_id, trim(p_sku), trim(p_name), coalesce(nullif(trim(p_unit), ''), 'each'),
        p_sale_price, coalesce(p_cost, 0), coalesce(p_reorder_point, 0)
    ) RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- Changing the cost is a forward-looking act: historical stock movements keep
-- their own cost snapshot, so past cost of goods sold is untouched.
CREATE OR REPLACE FUNCTION ledger.update_product(
    p_org_id uuid,
    p_product_id uuid,
    p_sale_price numeric DEFAULT NULL,
    p_cost numeric DEFAULT NULL,
    p_reorder_point numeric DEFAULT NULL,
    p_is_active boolean DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_org_id IS NULL OR p_product_id IS NULL THEN
        RAISE EXCEPTION 'organisation and product are required';
    END IF;

    UPDATE ledger.products
    SET current_sale_price = coalesce(p_sale_price, current_sale_price),
        current_cost = coalesce(p_cost, current_cost),
        reorder_point = coalesce(p_reorder_point, reorder_point),
        is_active = coalesce(p_is_active, is_active)
    WHERE id = p_product_id AND org_id = p_org_id
    RETURNING id INTO v_id;

    IF v_id IS NULL THEN
        RAISE EXCEPTION 'product was not found in this organisation';
    END IF;

    RETURN v_id;
END;
$$;

-- Receivables and payables ───────────────────────────────────

CREATE OR REPLACE FUNCTION ledger.create_open_item(
    p_org_id uuid,
    p_item_type ledger.open_item_type,
    p_counterparty_id uuid,
    p_journal_entry_id uuid,
    p_amount numeric,
    p_due_on date DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_org_id IS NULL OR p_counterparty_id IS NULL
       OR p_journal_entry_id IS NULL OR p_amount IS NULL THEN
        RAISE EXCEPTION 'organisation, counterparty, entry and amount are required';
    END IF;

    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'an open item must have a positive amount';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM ledger.journal_entries
        WHERE id = p_journal_entry_id AND org_id = p_org_id
    ) THEN
        RAISE EXCEPTION 'an open item must reference a journal entry in the same organisation';
    END IF;

    INSERT INTO ledger.open_items (
        org_id, item_type, counterparty_id, originating_journal_entry_id,
        original_amount, due_on
    ) VALUES (
        p_org_id, p_item_type, p_counterparty_id, p_journal_entry_id,
        p_amount, p_due_on
    ) RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- Settle an open item. A positive amount settles it (payment received, bad
-- debt written off, sale reversed). A negative amount reverses an earlier
-- settlement and must point at the allocation it reverses.
CREATE OR REPLACE FUNCTION ledger.allocate_payment(
    p_org_id uuid,
    p_open_item_id uuid,
    p_journal_entry_id uuid,
    p_amount numeric,
    p_reversal_of_id uuid DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
    v_original numeric(19,4);
    v_allocated numeric(19,4);
    v_reversed_amount numeric(19,4);
BEGIN
    IF p_org_id IS NULL OR p_open_item_id IS NULL
       OR p_journal_entry_id IS NULL OR p_amount IS NULL THEN
        RAISE EXCEPTION 'organisation, open item, entry and amount are required';
    END IF;

    SELECT original_amount INTO v_original
    FROM ledger.open_items
    WHERE id = p_open_item_id AND org_id = p_org_id;

    IF v_original IS NULL THEN
        RAISE EXCEPTION 'open item was not found in this organisation';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM ledger.journal_entries
        WHERE id = p_journal_entry_id AND org_id = p_org_id
    ) THEN
        RAISE EXCEPTION 'a settlement must reference a journal entry in the same organisation';
    END IF;

    IF p_reversal_of_id IS NOT NULL THEN
        SELECT amount INTO v_reversed_amount
        FROM ledger.payment_allocations
        WHERE id = p_reversal_of_id
          AND org_id = p_org_id
          AND open_item_id = p_open_item_id;

        IF v_reversed_amount IS NULL THEN
            RAISE EXCEPTION 'the settlement being reversed was not found on this open item';
        END IF;

        IF v_reversed_amount <= 0 THEN
            RAISE EXCEPTION 'only a positive settlement can be reversed';
        END IF;

        IF EXISTS (
            SELECT 1 FROM ledger.payment_allocations
            WHERE org_id = p_org_id AND reversal_of_id = p_reversal_of_id
        ) THEN
            RAISE EXCEPTION 'that settlement has already been reversed';
        END IF;

        IF p_amount <> -v_reversed_amount THEN
            RAISE EXCEPTION 'a reversal must offset the original settlement exactly';
        END IF;
    ELSE
        IF p_amount <= 0 THEN
            RAISE EXCEPTION 'a settlement amount must be positive';
        END IF;

        SELECT coalesce(sum(amount), 0) INTO v_allocated
        FROM ledger.payment_allocations
        WHERE open_item_id = p_open_item_id;

        IF v_allocated + p_amount > v_original THEN
            RAISE EXCEPTION 'settlement of % exceeds the outstanding balance of %',
                p_amount, v_original - v_allocated;
        END IF;
    END IF;

    INSERT INTO ledger.payment_allocations (
        org_id, open_item_id, payment_journal_entry_id, amount, reversal_of_id
    ) VALUES (
        p_org_id, p_open_item_id, p_journal_entry_id, p_amount, p_reversal_of_id
    ) RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- Accounting periods ─────────────────────────────────────────

CREATE OR REPLACE FUNCTION ledger.create_accounting_period(
    p_org_id uuid,
    p_period_start date,
    p_period_end date
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_org_id IS NULL OR p_period_start IS NULL OR p_period_end IS NULL THEN
        RAISE EXCEPTION 'organisation and period boundaries are required';
    END IF;

    IF p_period_end < p_period_start THEN
        RAISE EXCEPTION 'period end must not precede period start';
    END IF;

    INSERT INTO ledger.accounting_periods (
        org_id, period_start, period_end, is_closed
    ) VALUES (
        p_org_id, p_period_start, p_period_end, false
    ) RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION ledger.set_accounting_period_state(
    p_org_id uuid,
    p_period_start date,
    p_period_end date,
    p_is_closed boolean
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
BEGIN
    IF p_org_id IS NULL OR p_period_start IS NULL OR p_period_end IS NULL THEN
        RAISE EXCEPTION 'organisation and period boundaries are required';
    END IF;

    UPDATE ledger.accounting_periods
    SET is_closed = coalesce(p_is_closed, is_closed),
        closed_at = CASE
            WHEN coalesce(p_is_closed, is_closed) THEN coalesce(closed_at, now())
            ELSE closed_at
        END
    WHERE org_id = p_org_id
      AND period_start = p_period_start
      AND period_end = p_period_end
    RETURNING id INTO v_id;

    IF v_id IS NULL THEN
        RAISE EXCEPTION 'accounting period was not found in this organisation';
    END IF;

    RETURN v_id;
END;
$$;

-- Receipts and credit notes ──────────────────────────────────

CREATE OR REPLACE FUNCTION ledger.create_receipt_document(
    p_org_id uuid,
    p_journal_entry_id uuid,
    p_currency_code char(3),
    p_lines jsonb,
    p_customer_id uuid DEFAULT NULL,
    p_notes text DEFAULT NULL,
    p_receipt_type ledger.receipt_type DEFAULT 'receipt',
    p_credit_note_of_id uuid DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_receipt_id uuid;
    v_receipt_number bigint;
    v_total numeric(19,4);
BEGIN
    IF p_org_id IS NULL OR p_journal_entry_id IS NULL THEN
        RAISE EXCEPTION 'organisation and journal entry are required';
    END IF;

    IF jsonb_typeof(p_lines) <> 'array' OR jsonb_array_length(p_lines) = 0 THEN
        RAISE EXCEPTION 'a receipt requires at least one line';
    END IF;

    IF p_currency_code IS NULL OR p_currency_code !~ '^[A-Z]{3}$' THEN
        RAISE EXCEPTION 'a three letter currency code is required';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM ledger.journal_entries
        WHERE id = p_journal_entry_id AND org_id = p_org_id
    ) THEN
        RAISE EXCEPTION 'receipt must reference a journal entry in the same organisation';
    END IF;

    IF p_receipt_type = 'credit_note' AND p_credit_note_of_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM ledger.receipts
            WHERE id = p_credit_note_of_id AND org_id = p_org_id
        ) THEN
            RAISE EXCEPTION 'a credit note must reference a receipt in the same organisation';
        END IF;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_lines) AS x(value)
        WHERE coalesce(length(trim(value ->> 'description')), 0) = 0
           OR coalesce((value ->> 'quantity')::numeric, 0) <= 0
           OR coalesce((value ->> 'unit_price')::numeric, 0) < 0
    ) THEN
        RAISE EXCEPTION 'every receipt line needs a description, a positive quantity and a price';
    END IF;

    SELECT sum(((value ->> 'quantity')::numeric(19,4))
               * ((value ->> 'unit_price')::numeric(19,4)))
      INTO v_total
      FROM jsonb_array_elements(p_lines);

    INSERT INTO ledger.org_sequences (org_id, next_journal_number, next_receipt_number)
    VALUES (p_org_id, 1, 2)
    ON CONFLICT (org_id) DO UPDATE
        SET next_receipt_number = ledger.org_sequences.next_receipt_number + 1
    RETURNING next_receipt_number - 1 INTO v_receipt_number;

    INSERT INTO ledger.receipts (
        org_id, receipt_number, journal_entry_id, currency_code,
        customer_id, total_amount, notes, receipt_type, credit_note_of_id
    ) VALUES (
        p_org_id, v_receipt_number, p_journal_entry_id, p_currency_code,
        p_customer_id, v_total, p_notes, p_receipt_type, p_credit_note_of_id
    ) RETURNING id INTO v_receipt_id;

    INSERT INTO ledger.receipt_lines (
        receipt_id, line_number, description, quantity, unit_price
    )
    SELECT
        v_receipt_id,
        row_number() OVER (),
        value ->> 'description',
        (value ->> 'quantity')::numeric(19,4),
        (value ->> 'unit_price')::numeric(19,4)
    FROM jsonb_array_elements(p_lines);

    RETURN v_receipt_id;
END;
$$;

CREATE OR REPLACE FUNCTION ledger.issue_receipt(
    p_org_id uuid,
    p_journal_entry_id uuid,
    p_currency_code char(3),
    p_lines jsonb,
    p_customer_id uuid DEFAULT NULL,
    p_notes text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
BEGIN
    RETURN ledger.create_receipt_document(
        p_org_id, p_journal_entry_id, p_currency_code, p_lines,
        p_customer_id, p_notes, 'receipt', NULL
    );
END;
$$;

-- A credit note is a new document. Historical receipts are never edited.
CREATE OR REPLACE FUNCTION ledger.issue_credit_note(
    p_org_id uuid,
    p_journal_entry_id uuid,
    p_currency_code char(3),
    p_lines jsonb,
    p_customer_id uuid DEFAULT NULL,
    p_notes text DEFAULT NULL,
    p_credit_note_of_id uuid DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
BEGIN
    RETURN ledger.create_receipt_document(
        p_org_id, p_journal_entry_id, p_currency_code, p_lines,
        p_customer_id, p_notes, 'credit_note', p_credit_note_of_id
    );
END;
$$;

-- Sale lines ─────────────────────────────────────────────────

-- Attaches product level detail to a posted sale entry. The revenue posted on
-- the entry must equal the sum of the line totals, otherwise the sales summary
-- would stop reconciling with the profit and loss report.
CREATE OR REPLACE FUNCTION ledger.post_sale_lines(
    p_org_id uuid,
    p_journal_entry_id uuid,
    p_lines jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_line_total numeric(19,4);
    v_revenue numeric(19,4);
    v_ids jsonb;
BEGIN
    IF p_org_id IS NULL OR p_journal_entry_id IS NULL THEN
        RAISE EXCEPTION 'organisation and journal entry are required';
    END IF;

    IF jsonb_typeof(p_lines) <> 'array' OR jsonb_array_length(p_lines) = 0 THEN
        RAISE EXCEPTION 'a sale requires at least one line';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM ledger.journal_entries
        WHERE id = p_journal_entry_id AND org_id = p_org_id
    ) THEN
        RAISE EXCEPTION 'sale lines must reference a journal entry in the same organisation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_lines) AS x(value)
        WHERE coalesce((value ->> 'quantity')::numeric, 0) <= 0
           OR coalesce((value ->> 'unit_price')::numeric, 0) < 0
           OR coalesce((value ->> 'cogs_amount')::numeric, 0) < 0
    ) THEN
        RAISE EXCEPTION 'every sale line needs a positive quantity and non-negative amounts';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_lines) AS x(value)
        WHERE NOT EXISTS (
            SELECT 1 FROM ledger.products p
            WHERE p.id = (value ->> 'product_id')::uuid
              AND p.org_id = p_org_id
              AND p.is_active
        )
    ) THEN
        RAISE EXCEPTION 'every sale line must use an active product in this organisation';
    END IF;

    SELECT sum(((value ->> 'quantity')::numeric(19,4))
               * ((value ->> 'unit_price')::numeric(19,4)))
      INTO v_line_total
      FROM jsonb_array_elements(p_lines);

    SELECT coalesce(sum(jl.credit - jl.debit), 0)
      INTO v_revenue
      FROM ledger.journal_lines jl
      JOIN ledger.accounts a ON a.id = jl.account_id AND a.org_id = jl.org_id
      WHERE jl.org_id = p_org_id
        AND jl.journal_entry_id = p_journal_entry_id
        AND a.account_type = 'revenue';

    IF v_revenue <> v_line_total THEN
        RAISE EXCEPTION 'sale lines total % but the entry posts revenue of %',
            v_line_total, v_revenue;
    END IF;

    INSERT INTO ledger.sale_lines (
        org_id, journal_entry_id, line_number, product_id,
        quantity, unit_price, cogs_amount
    )
    SELECT
        p_org_id,
        p_journal_entry_id,
        row_number() OVER (),
        (value ->> 'product_id')::uuid,
        (value ->> 'quantity')::numeric(19,4),
        (value ->> 'unit_price')::numeric(19,4),
        coalesce((value ->> 'cogs_amount')::numeric(19,4), 0)
    FROM jsonb_array_elements(p_lines)
    RETURNING jsonb_agg(id) INTO v_ids;

    RETURN v_ids;
END;
$$;

-- Direct table privileges are removed again: the new functions join the
-- existing posting surface, and the runtime role still cannot write a row.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ledger FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA ledger FROM PUBLIC;
