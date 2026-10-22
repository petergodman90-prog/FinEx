-- Person A — posting-service hardening
-- PostgreSQL 15+
--
-- Forward-only support for the application posting service introduced in
-- `backend/app/services/posting_service.py`:
--
--   * `post_journal_entry` now checks the transaction's organisation context
--     and short-circuits on an idempotency key *before* re-validating the
--     payload.  A retry of an already-committed posting therefore returns the
--     original entry even if the period has since been closed or the entry has
--     since been reversed.
--   * `post_stock_movement` does the same, and returns the existing movement
--     for the same `(org_id, source_type, source_id, product_id)` instead of
--     raising a unique violation on retry.
--   * The revenue/COGS rule now requires a COGS line only when inventory is
--     actually credited.  A zero-cost line is a valid sale; it still creates a
--     stock movement, it just has no inventory value to relieve.
--   * `update_account` lets the account setup API rename or deactivate an
--     account without changing its immutable code or type.
--
-- The application service validates cross-domain references (customers,
-- products, suppliers) before calling these functions; the database still
-- refuses a mismatched `app.current_org_id` as the last line of defence.

-- ══════════════════════════════════════════════════════════════
-- Journal posting: context guard + early idempotency
-- ══════════════════════════════════════════════════════════════

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
    v_has_asset_credit boolean;
    v_ctx text := current_setting('app.current_org_id', true);
BEGIN
    IF p_org_id IS NULL OR p_source_id IS NULL OR p_idempotency_key IS NULL
       OR p_effective_at IS NULL OR coalesce(length(trim(p_source_type)), 0) = 0 THEN
        RAISE EXCEPTION 'organisation, source, idempotency key, and effective time are required';
    END IF;

    -- A privileged SECURITY DEFINER owner bypasses RLS, so reject a foreign
    -- org before the idempotent-return path can expose another tenant's entry.
    IF v_ctx IS NOT NULL AND v_ctx <> '' AND p_org_id::text <> v_ctx THEN
        RAISE EXCEPTION
            'organisation context mismatch: posting org % does not match session org %',
            p_org_id, v_ctx
            USING ERRCODE = '42501';
    END IF;

    -- Idempotency is checked first: a retry must return the original entry
    -- even if it would no longer pass today's period/reversal validations.
    SELECT je.id INTO v_entry_id
    FROM ledger.posting_events pe
    JOIN ledger.journal_entries je ON je.posting_event_id = pe.id
    WHERE pe.org_id = p_org_id AND pe.idempotency_key = p_idempotency_key;
    IF v_entry_id IS NOT NULL THEN
        RETURN v_entry_id;
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
        SELECT (value ->> 'account_id')::uuid AS account_id,
               coalesce((value ->> 'debit')::numeric, 0) AS debit,
               coalesce((value ->> 'credit')::numeric, 0) AS credit
        FROM jsonb_array_elements(p_lines)
    )
    SELECT
        bool_or(a.account_type = 'revenue'),
        bool_or(a.account_type = 'cogs'),
        bool_or(a.account_type = 'asset' AND p.credit > 0)
      INTO v_has_revenue, v_has_cogs, v_has_asset_credit
      FROM parsed p
      JOIN ledger.accounts a
        ON a.id = p.account_id AND a.org_id = p_org_id AND a.is_active;

    IF (SELECT count(*) FROM ledger.accounts a
        JOIN (SELECT (value ->> 'account_id')::uuid AS account_id
              FROM jsonb_array_elements(p_lines)) p ON p.account_id = a.id
        WHERE a.org_id = p_org_id AND a.is_active) <> v_line_count THEN
        RAISE EXCEPTION 'every line must use an active account in this organisation';
    END IF;

    -- A sale that relieves inventory must post COGS in the same transaction.
    -- Revenue without an inventory credit (a service sale) is allowed.
    IF coalesce(v_has_revenue, false)
       AND coalesce(v_has_asset_credit, false)
       AND NOT coalesce(v_has_cogs, false) THEN
        RAISE EXCEPTION 'revenue postings that credit inventory require a matching COGS line';
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

-- ══════════════════════════════════════════════════════════════
-- Stock posting: context guard + early idempotency
-- ══════════════════════════════════════════════════════════════

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
    v_ctx text := current_setting('app.current_org_id', true);
BEGIN
    IF p_org_id IS NULL OR p_product_id IS NULL OR p_journal_entry_id IS NULL
       OR p_source_id IS NULL OR p_occurred_at IS NULL
       OR coalesce(length(trim(p_source_type)), 0) = 0 THEN
        RAISE EXCEPTION 'organisation, product, entry, source and occurrence time are required';
    END IF;

    IF v_ctx IS NOT NULL AND v_ctx <> '' AND p_org_id::text <> v_ctx THEN
        RAISE EXCEPTION
            'organisation context mismatch: stock org % does not match session org %',
            p_org_id, v_ctx
            USING ERRCODE = '42501';
    END IF;

    -- The movement key is immutable: a retry of the same source returns the
    -- original row rather than tripping the unique constraint.
    SELECT id INTO v_movement_id
    FROM ledger.stock_movements
    WHERE org_id = p_org_id
      AND source_type = p_source_type
      AND source_id = p_source_id
      AND product_id = p_product_id;
    IF v_movement_id IS NOT NULL THEN
        RETURN v_movement_id;
    END IF;

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

-- ══════════════════════════════════════════════════════════════
-- Account setup
-- ══════════════════════════════════════════════════════════════

-- Code and account type are immutable once posted against; renaming and
-- activation are safe metadata changes.
CREATE OR REPLACE FUNCTION ledger.update_account(
    p_org_id uuid,
    p_account_id uuid,
    p_name text DEFAULT NULL,
    p_is_active boolean DEFAULT NULL,
    p_is_cash_account boolean DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ledger, pg_temp
AS $$
DECLARE
    v_id uuid;
    v_ctx text := current_setting('app.current_org_id', true);
BEGIN
    IF p_org_id IS NULL OR p_account_id IS NULL THEN
        RAISE EXCEPTION 'organisation and account are required';
    END IF;

    IF v_ctx IS NOT NULL AND v_ctx <> '' AND p_org_id::text <> v_ctx THEN
        RAISE EXCEPTION
            'organisation context mismatch: account org % does not match session org %',
            p_org_id, v_ctx
            USING ERRCODE = '42501';
    END IF;

    UPDATE ledger.accounts
    SET name = coalesce(nullif(trim(p_name), ''), name),
        is_active = coalesce(p_is_active, is_active),
        is_cash_account = coalesce(p_is_cash_account, is_cash_account)
    WHERE id = p_account_id AND org_id = p_org_id
    RETURNING id INTO v_id;

    IF v_id IS NULL THEN
        RAISE EXCEPTION 'account was not found in this organisation';
    END IF;

    RETURN v_id;
END;
$$;

-- ══════════════════════════════════════════════════════════════
-- Sale lines: fix aggregate in RETURNING
-- ══════════════════════════════════════════════════════════════

-- The 0002 body used `RETURNING jsonb_agg(id)`, which PostgreSQL rejects
-- ("aggregate functions are not allowed in RETURNING").  The CTE form below
-- attaches the same product detail and returns the new line ids.
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

    WITH inserted AS (
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
        RETURNING id
    )
    SELECT jsonb_agg(id) INTO v_ids FROM inserted;

    RETURN v_ids;
END;
$$;
