-- Person A domain foundation
-- PostgreSQL 15+
--
-- This migration intentionally does not create organisations, users, sessions,
-- RLS policies, UI tables, AI proposals, or integration transport tables. Those
-- belong to the other ownership areas. `org_id` is present on every record so
-- the tenant layer can apply its context and policies around this domain.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS ledger;

CREATE TYPE ledger.account_type AS ENUM (
    'asset', 'liability', 'equity', 'revenue', 'expense', 'cogs'
);

CREATE TYPE ledger.entry_status AS ENUM ('posted', 'reversed');

CREATE TYPE ledger.stock_movement_type AS ENUM (
    'opening_balance', 'purchase', 'sale', 'sale_reversal',
    'adjustment_in', 'adjustment_out', 'stock_take', 'return_in', 'return_out'
);

CREATE TYPE ledger.open_item_type AS ENUM (
    'customer_receivable', 'supplier_payable'
);

CREATE TABLE ledger.accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    account_type ledger.account_type NOT NULL,
    is_control_account boolean NOT NULL DEFAULT false,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, code),
    UNIQUE (org_id, id),
    CHECK (length(trim(code)) > 0),
    CHECK (length(trim(name)) > 0)
);

CREATE TABLE ledger.accounting_periods (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    is_closed boolean NOT NULL DEFAULT false,
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, period_start, period_end),
    CHECK (period_end >= period_start),
    CHECK ((is_closed AND closed_at IS NOT NULL) OR (NOT is_closed))
);

-- A private, per-organisation sequence table keeps document numbers gap-free
-- within committed posting transactions. It is not an application API.
CREATE TABLE ledger.org_sequences (
    org_id uuid PRIMARY KEY,
    next_journal_number bigint NOT NULL DEFAULT 1,
    next_receipt_number bigint NOT NULL DEFAULT 1,
    CHECK (next_journal_number > 0),
    CHECK (next_receipt_number > 0)
);

CREATE TABLE ledger.posting_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    source_type text NOT NULL,
    source_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    requested_by uuid,
    payload jsonb NOT NULL,
    posted_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, idempotency_key),
    UNIQUE (org_id, source_type, source_id),
    CHECK (length(trim(source_type)) > 0)
);

CREATE TABLE ledger.journal_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    entry_number bigint NOT NULL,
    posting_event_id uuid NOT NULL,
    effective_at timestamptz NOT NULL,
    memo text,
    status ledger.entry_status NOT NULL DEFAULT 'posted',
    reversal_of_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid,
    PRIMARY KEY (id),
    UNIQUE (org_id, entry_number),
    UNIQUE (org_id, id),
    UNIQUE (posting_event_id),
    FOREIGN KEY (posting_event_id) REFERENCES ledger.posting_events(id) ON DELETE RESTRICT,
    FOREIGN KEY (org_id, reversal_of_id)
        REFERENCES ledger.journal_entries (org_id, id) ON DELETE RESTRICT,
    CHECK (entry_number > 0),
    CHECK (reversal_of_id IS NULL OR reversal_of_id <> id)
);

CREATE TABLE ledger.journal_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    journal_entry_id uuid NOT NULL,
    line_number smallint NOT NULL,
    account_id uuid NOT NULL,
    debit numeric(19,4) NOT NULL DEFAULT 0,
    credit numeric(19,4) NOT NULL DEFAULT 0,
    description text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (journal_entry_id, line_number),
    FOREIGN KEY (org_id, journal_entry_id)
        REFERENCES ledger.journal_entries (org_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (org_id, account_id)
        REFERENCES ledger.accounts (org_id, id) ON DELETE RESTRICT,
    CHECK (line_number > 0),
    CHECK ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0))
);

CREATE INDEX journal_lines_org_account_idx
    ON ledger.journal_lines (org_id, account_id, created_at);
CREATE INDEX journal_entries_org_effective_idx
    ON ledger.journal_entries (org_id, effective_at, entry_number);

CREATE TABLE ledger.products (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    sku text NOT NULL,
    name text NOT NULL,
    unit text NOT NULL DEFAULT 'each',
    current_sale_price numeric(19,4) NOT NULL,
    reorder_point numeric(19,4) NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, sku),
    UNIQUE (org_id, id),
    CHECK (length(trim(sku)) > 0),
    CHECK (length(trim(name)) > 0),
    CHECK (length(trim(unit)) > 0),
    CHECK (current_sale_price >= 0),
    CHECK (reorder_point >= 0)
);

-- There is deliberately no stock_quantity column. Current stock is calculated
-- from these immutable movements, each carrying its historical cost snapshot.
CREATE TABLE ledger.stock_movements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    product_id uuid NOT NULL,
    journal_entry_id uuid NOT NULL,
    movement_type ledger.stock_movement_type NOT NULL,
    quantity_delta numeric(19,4) NOT NULL,
    unit_cost numeric(19,4) NOT NULL,
    source_type text NOT NULL,
    source_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL,
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, source_type, source_id, product_id),
    FOREIGN KEY (org_id, product_id)
        REFERENCES ledger.products (org_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (org_id, journal_entry_id)
        REFERENCES ledger.journal_entries (org_id, id) ON DELETE RESTRICT,
    CHECK (quantity_delta <> 0),
    CHECK (unit_cost >= 0),
    CHECK (length(trim(source_type)) > 0),
    CHECK (
        (movement_type IN ('opening_balance', 'purchase', 'adjustment_in', 'return_in')
            AND quantity_delta > 0)
        OR (movement_type IN ('sale', 'adjustment_out', 'return_out')
            AND quantity_delta < 0)
        OR movement_type IN ('sale_reversal', 'stock_take')
    )
);

CREATE INDEX stock_movements_org_product_idx
    ON ledger.stock_movements (org_id, product_id, occurred_at);

-- Open items represent the document and payment allocation state for AR/AP.
-- Their journal linkage means receivable and payable reporting remains ledger-led.
CREATE TABLE ledger.open_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    item_type ledger.open_item_type NOT NULL,
    counterparty_id uuid NOT NULL,
    originating_journal_entry_id uuid NOT NULL,
    original_amount numeric(19,4) NOT NULL,
    due_on date,
    opened_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, id),
    FOREIGN KEY (org_id, originating_journal_entry_id)
        REFERENCES ledger.journal_entries (org_id, id) ON DELETE RESTRICT,
    CHECK (original_amount > 0)
);

CREATE TABLE ledger.payment_allocations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    open_item_id uuid NOT NULL,
    payment_journal_entry_id uuid NOT NULL,
    amount numeric(19,4) NOT NULL,
    allocated_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (org_id, open_item_id)
        REFERENCES ledger.open_items (org_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (org_id, payment_journal_entry_id)
        REFERENCES ledger.journal_entries (org_id, id) ON DELETE RESTRICT,
    UNIQUE (open_item_id, payment_journal_entry_id),
    CHECK (amount > 0)
);

CREATE TABLE ledger.receipts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL,
    receipt_number bigint NOT NULL,
    journal_entry_id uuid NOT NULL,
    currency_code char(3) NOT NULL,
    issued_at timestamptz NOT NULL DEFAULT now(),
    customer_id uuid,
    total_amount numeric(19,4) NOT NULL,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, receipt_number),
    UNIQUE (org_id, id),
    FOREIGN KEY (org_id, journal_entry_id)
        REFERENCES ledger.journal_entries (org_id, id) ON DELETE RESTRICT,
    CHECK (receipt_number > 0),
    CHECK (total_amount >= 0),
    CHECK (currency_code ~ '^[A-Z]{3}$')
);

CREATE TABLE ledger.receipt_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_id uuid NOT NULL REFERENCES ledger.receipts(id) ON DELETE RESTRICT,
    line_number smallint NOT NULL,
    description text NOT NULL,
    quantity numeric(19,4) NOT NULL,
    unit_price numeric(19,4) NOT NULL,
    line_total numeric(19,4) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    UNIQUE (receipt_id, line_number),
    CHECK (line_number > 0),
    CHECK (length(trim(description)) > 0),
    CHECK (quantity > 0),
    CHECK (unit_price >= 0)
);

CREATE OR REPLACE FUNCTION ledger.reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% records are immutable; create a reversing entry instead', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER journal_entries_are_immutable
BEFORE UPDATE OR DELETE ON ledger.journal_entries
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE TRIGGER journal_lines_are_immutable
BEFORE UPDATE OR DELETE ON ledger.journal_lines
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE TRIGGER stock_movements_are_immutable
BEFORE UPDATE OR DELETE ON ledger.stock_movements
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE TRIGGER posting_events_are_immutable
BEFORE UPDATE OR DELETE ON ledger.posting_events
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE TRIGGER receipts_are_immutable
BEFORE UPDATE OR DELETE ON ledger.receipts
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE TRIGGER receipt_lines_are_immutable
BEFORE UPDATE OR DELETE ON ledger.receipt_lines
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE TRIGGER open_items_are_immutable
BEFORE UPDATE OR DELETE ON ledger.open_items
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE TRIGGER payment_allocations_are_immutable
BEFORE UPDATE OR DELETE ON ledger.payment_allocations
FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE OR REPLACE FUNCTION ledger.post_journal_entry(
    p_org_id uuid,
    p_source_type text,
    p_source_id uuid,
    p_idempotency_key uuid,
    p_effective_at timestamptz,
    p_memo text,
    p_lines jsonb,
    p_requested_by uuid DEFAULT NULL,
    p_reversal_of_id uuid DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_entry_id uuid;
    v_event_id uuid;
    v_entry_number bigint;
    v_line_count integer;
    v_debit_total numeric(19,4);
    v_credit_total numeric(19,4);
    v_has_revenue boolean;
    v_has_cogs boolean;
BEGIN
    IF p_org_id IS NULL OR p_source_id IS NULL OR p_idempotency_key IS NULL
       OR p_effective_at IS NULL OR coalesce(length(trim(p_source_type)), 0) = 0 THEN
        RAISE EXCEPTION 'organisation, source, idempotency key, and effective time are required';
    END IF;

    IF jsonb_typeof(p_lines) <> 'array' OR jsonb_array_length(p_lines) < 2 THEN
        RAISE EXCEPTION 'a journal entry requires at least two lines';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM ledger.accounting_periods
        WHERE org_id = p_org_id
          AND is_closed
          AND p_effective_at::date BETWEEN period_start AND period_end
    ) THEN
        RAISE EXCEPTION 'cannot post into a closed accounting period' USING ERRCODE = '55000';
    END IF;

    IF p_reversal_of_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM ledger.journal_entries
            WHERE id = p_reversal_of_id AND org_id = p_org_id
        ) THEN
            RAISE EXCEPTION 'reversal must reference an entry in the same organisation';
        END IF;
        IF EXISTS (
            SELECT 1 FROM ledger.journal_entries
            WHERE reversal_of_id = p_reversal_of_id
        ) THEN
            RAISE EXCEPTION 'journal entry % has already been reversed', p_reversal_of_id;
        END IF;
    END IF;

    WITH parsed AS (
        SELECT
            (value ->> 'account_id')::uuid AS account_id,
            coalesce((value ->> 'debit')::numeric, 0)::numeric(19,4) AS debit,
            coalesce((value ->> 'credit')::numeric, 0)::numeric(19,4) AS credit
        FROM jsonb_array_elements(p_lines)
    )
    SELECT count(*), coalesce(sum(debit), 0), coalesce(sum(credit), 0)
      INTO v_line_count, v_debit_total, v_credit_total
      FROM parsed;

    IF v_debit_total <> v_credit_total THEN
        RAISE EXCEPTION 'journal entry is unbalanced: debit % does not equal credit %',
            v_debit_total, v_credit_total;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_lines) AS x(value)
        WHERE coalesce((value ->> 'debit')::numeric, 0) < 0
           OR coalesce((value ->> 'credit')::numeric, 0) < 0
           OR (coalesce((value ->> 'debit')::numeric, 0) = 0
               AND coalesce((value ->> 'credit')::numeric, 0) = 0)
           OR (coalesce((value ->> 'debit')::numeric, 0) > 0
               AND coalesce((value ->> 'credit')::numeric, 0) > 0)
    ) THEN
        RAISE EXCEPTION 'each journal line must contain a debit XOR a credit';
    END IF;

    WITH parsed AS (
        SELECT (value ->> 'account_id')::uuid AS account_id
        FROM jsonb_array_elements(p_lines)
    )
    SELECT bool_or(a.account_type = 'revenue'), bool_or(a.account_type = 'cogs')
      INTO v_has_revenue, v_has_cogs
      FROM parsed p
      JOIN ledger.accounts a
        ON a.id = p.account_id AND a.org_id = p_org_id AND a.is_active;

    IF (SELECT count(*) FROM ledger.accounts a
        JOIN (SELECT (value ->> 'account_id')::uuid AS account_id
              FROM jsonb_array_elements(p_lines)) p ON p.account_id = a.id
        WHERE a.org_id = p_org_id AND a.is_active) <> v_line_count THEN
        RAISE EXCEPTION 'every line must use an active account in this organisation';
    END IF;

    IF coalesce(v_has_revenue, false) AND NOT coalesce(v_has_cogs, false) THEN
        RAISE EXCEPTION 'revenue postings require a matching COGS line in the same transaction';
    END IF;

    -- Idempotency returns the original successful posting, never posts twice.
    SELECT je.id INTO v_entry_id
    FROM ledger.posting_events pe
    JOIN ledger.journal_entries je ON je.posting_event_id = pe.id
    WHERE pe.org_id = p_org_id AND pe.idempotency_key = p_idempotency_key;
    IF v_entry_id IS NOT NULL THEN
        RETURN v_entry_id;
    END IF;

    INSERT INTO ledger.org_sequences (org_id, next_journal_number)
    VALUES (p_org_id, 2)
    ON CONFLICT (org_id) DO UPDATE
        SET next_journal_number = ledger.org_sequences.next_journal_number + 1
    RETURNING next_journal_number - 1 INTO v_entry_number;

    INSERT INTO ledger.posting_events (
        org_id, source_type, source_id, idempotency_key, requested_by, payload
    ) VALUES (
        p_org_id, p_source_type, p_source_id, p_idempotency_key, p_requested_by,
        jsonb_build_object('memo', p_memo, 'effective_at', p_effective_at, 'lines', p_lines)
    ) RETURNING id INTO v_event_id;

    INSERT INTO ledger.journal_entries (
        org_id, entry_number, posting_event_id, effective_at, memo, created_by, reversal_of_id
    ) VALUES (
        p_org_id, v_entry_number, v_event_id, p_effective_at, p_memo, p_requested_by,
        p_reversal_of_id
    ) RETURNING id INTO v_entry_id;

    INSERT INTO ledger.journal_lines (
        org_id, journal_entry_id, line_number, account_id, debit, credit, description
    )
    SELECT
        p_org_id,
        v_entry_id,
        row_number() OVER (),
        (value ->> 'account_id')::uuid,
        coalesce((value ->> 'debit')::numeric, 0)::numeric(19,4),
        coalesce((value ->> 'credit')::numeric, 0)::numeric(19,4),
        nullif(value ->> 'description', '')
    FROM jsonb_array_elements(p_lines);

    RETURN v_entry_id;
END;
$$;

CREATE OR REPLACE FUNCTION ledger.reverse_journal_entry(
    p_org_id uuid,
    p_original_entry_id uuid,
    p_source_type text,
    p_source_id uuid,
    p_idempotency_key uuid,
    p_effective_at timestamptz,
    p_memo text,
    p_requested_by uuid DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_reversal_lines jsonb;
BEGIN
    SELECT jsonb_agg(
        jsonb_build_object(
            'account_id', account_id,
            'debit', credit,
            'credit', debit,
            'description', coalesce(description, 'Reversal of entry ' || je.entry_number)
        ) ORDER BY line_number
    )
    INTO v_reversal_lines
    FROM ledger.journal_lines jl
    JOIN ledger.journal_entries je ON je.id = jl.journal_entry_id
    WHERE jl.org_id = p_org_id AND jl.journal_entry_id = p_original_entry_id;

    IF v_reversal_lines IS NULL THEN
        RAISE EXCEPTION 'original journal entry was not found in this organisation';
    END IF;

    RETURN ledger.post_journal_entry(
        p_org_id,
        p_source_type,
        p_source_id,
        p_idempotency_key,
        p_effective_at,
        p_memo,
        v_reversal_lines,
        p_requested_by,
        p_original_entry_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION ledger.post_stock_movement(
    p_org_id uuid,
    p_product_id uuid,
    p_journal_entry_id uuid,
    p_movement_type ledger.stock_movement_type,
    p_quantity_delta numeric(19,4),
    p_unit_cost numeric(19,4),
    p_source_type text,
    p_source_id uuid,
    p_occurred_at timestamptz,
    p_note text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_movement_id uuid;
    v_stock_after numeric(19,4);
BEGIN
    -- The product row is the per-SKU lock. Every stock movement uses this
    -- posting function, so competing sales see each other's committed movement.
    PERFORM 1 FROM ledger.products
    WHERE id = p_product_id AND org_id = p_org_id AND is_active
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'product is not active in this organisation';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM ledger.journal_entries
                   WHERE id = p_journal_entry_id AND org_id = p_org_id) THEN
        RAISE EXCEPTION 'stock movements require a posted journal entry in the same organisation';
    END IF;

    SELECT coalesce(sum(quantity_delta), 0) + p_quantity_delta INTO v_stock_after
    FROM ledger.stock_movements
    WHERE org_id = p_org_id AND product_id = p_product_id;

    IF v_stock_after < 0 THEN
        RAISE EXCEPTION 'insufficient stock for product %', p_product_id USING ERRCODE = '23514';
    END IF;

    INSERT INTO ledger.stock_movements (
        org_id, product_id, journal_entry_id, movement_type, quantity_delta,
        unit_cost, source_type, source_id, occurred_at, note
    ) VALUES (
        p_org_id, p_product_id, p_journal_entry_id, p_movement_type, p_quantity_delta,
        p_unit_cost, p_source_type, p_source_id, p_occurred_at, p_note
    ) RETURNING id INTO v_movement_id;

    RETURN v_movement_id;
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
DECLARE
    v_receipt_id uuid;
    v_receipt_number bigint;
    v_total numeric(19,4);
BEGIN
    IF jsonb_typeof(p_lines) <> 'array' OR jsonb_array_length(p_lines) = 0 THEN
        RAISE EXCEPTION 'a receipt requires at least one line';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ledger.journal_entries
                   WHERE id = p_journal_entry_id AND org_id = p_org_id) THEN
        RAISE EXCEPTION 'receipt must reference a journal entry in the same organisation';
    END IF;

    SELECT sum(((value ->> 'quantity')::numeric(19,4))
               * ((value ->> 'unit_price')::numeric(19,4)))
      INTO v_total
      FROM jsonb_array_elements(p_lines);

    INSERT INTO ledger.org_sequences (org_id, next_receipt_number)
    VALUES (p_org_id, 1, 2)
    ON CONFLICT (org_id) DO UPDATE
        SET next_receipt_number = ledger.org_sequences.next_receipt_number + 1
    RETURNING next_receipt_number - 1 INTO v_receipt_number;

    INSERT INTO ledger.receipts (
        org_id, receipt_number, journal_entry_id, currency_code,
        customer_id, total_amount, notes
    ) VALUES (
        p_org_id, v_receipt_number, p_journal_entry_id, p_currency_code,
        p_customer_id, v_total, p_notes
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

-- Read models only. UI clients render values from these views and must never
-- recompute balances or quantities in the browser.
CREATE VIEW ledger.v_trial_balance AS
SELECT
    jl.org_id,
    jl.account_id,
    a.code AS account_code,
    a.name AS account_name,
    a.account_type,
    sum(jl.debit) AS debit_total,
    sum(jl.credit) AS credit_total,
    sum(jl.debit - jl.credit) AS net_debit_balance
FROM ledger.journal_lines jl
JOIN ledger.accounts a ON a.id = jl.account_id AND a.org_id = jl.org_id
GROUP BY jl.org_id, jl.account_id, a.code, a.name, a.account_type;

CREATE VIEW ledger.v_inventory_on_hand AS
SELECT
    sm.org_id,
    sm.product_id,
    p.sku,
    p.name,
    sum(sm.quantity_delta) AS quantity_on_hand,
    sum(sm.quantity_delta * sm.unit_cost) AS movement_cost_value,
    p.reorder_point,
    sum(sm.quantity_delta) <= p.reorder_point AS needs_reorder
FROM ledger.stock_movements sm
JOIN ledger.products p ON p.id = sm.product_id AND p.org_id = sm.org_id
GROUP BY sm.org_id, sm.product_id, p.sku, p.name, p.reorder_point;

CREATE VIEW ledger.v_open_item_balances AS
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
        WHEN oi.due_on IS NULL OR current_date <= oi.due_on THEN '0-30'
        WHEN current_date - oi.due_on <= 30 THEN '31-60'
        WHEN current_date - oi.due_on <= 60 THEN '61-90'
        ELSE '90+'
    END AS aging_bucket
FROM ledger.open_items oi
LEFT JOIN ledger.payment_allocations pa ON pa.open_item_id = oi.id
GROUP BY oi.org_id, oi.id, oi.item_type, oi.counterparty_id,
         oi.original_amount, oi.due_on;

-- Direct table privileges are intentionally removed. Deployment must grant only
-- EXECUTE on the posting functions to the application's runtime role; that role
-- must not own this schema or these tables.
REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA ledger FROM PUBLIC;
REVOKE ALL ON FUNCTION ledger.post_journal_entry(uuid, text, uuid, uuid, timestamptz, text, jsonb, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION ledger.reverse_journal_entry(uuid, uuid, text, uuid, uuid, timestamptz, text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION ledger.post_stock_movement(uuid, uuid, uuid, ledger.stock_movement_type, numeric, numeric, text, uuid, timestamptz, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ledger.issue_receipt(uuid, uuid, char(3), jsonb, uuid, text) FROM PUBLIC;
