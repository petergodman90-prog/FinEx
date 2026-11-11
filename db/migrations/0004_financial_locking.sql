-- Concurrent settlement hardening.
--
-- Without a row lock, two transactions can both read the same outstanding
-- balance and each insert an allocation, over-settling the open item. Locking
-- the open-item row serialises every allocation and reversal for that item.

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

    -- The open item is the allocation lock for its whole lifetime.
    SELECT original_amount INTO v_original
    FROM ledger.open_items
    WHERE id = p_open_item_id AND org_id = p_org_id
    FOR UPDATE;

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
