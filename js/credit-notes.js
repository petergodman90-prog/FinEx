/* Live credit notes workspace: receipts-linked refunds and restocking proposals. */
(function () {
    "use strict";

    const API = window.FinexAPI;
    const Fx = window.FinexFinance;
    if (!API || !Fx) {
        return;
    }

    const statusElement = document.getElementById("credit-notes-status");
    const addButton = document.getElementById("credit-note-add-open");
    const refreshButton = document.getElementById("credit-notes-refresh");
    const filterInput = document.getElementById("credit-notes-filter");
    const notesBody = document.getElementById("credit-notes-body");

    const dialog = document.getElementById("credit-note-dialog");
    const form = document.getElementById("credit-note-form");
    const dialogClose = document.getElementById("credit-note-dialog-close");
    const cancelButton = document.getElementById("credit-note-cancel");
    const errorElement = document.getElementById("credit-note-error");
    const submitButton = document.getElementById("credit-note-submit");
    const receiptIdInput = document.getElementById("credit-note-receipt-id");
    const customerGroup = document.getElementById("credit-customer-group");
    const customerInput = document.getElementById("credit-note-customer");
    const currencyGroup = document.getElementById("credit-currency-group");
    const currencyInput = document.getElementById("credit-note-currency");
    const effectiveInput = document.getElementById("credit-note-effective");
    const cashAccountInput = document.getElementById("credit-note-cash-account");
    const restockInput = document.getElementById("credit-note-restock");
    const restockHint = document.getElementById("credit-note-restock-hint");
    const notesInput = document.getElementById("credit-note-notes");
    const linesBody = document.getElementById("credit-note-lines-body");
    const addLineButton = document.getElementById("credit-note-add-line");
    const projectedTotal = document.getElementById("credit-note-projected-total");

    let creditNotes = [];
    let customers = [];
    let products = [];
    let canWrite = false;
    let canApprove = false;
    let loadingNotes = false;
    let loadingLookups = false;
    let pendingNotesLoad = false;
    let pendingLookupsLoad = false;
    let lineCounter = 0;
    let receiptWasLinked = null;

    function shortId(value) {
        return String(value || "").slice(0, 8).toUpperCase();
    }

    function setMetric(id, value) {
        Fx.setText(id, value);
    }

    function addOption(select, value, label) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        select.appendChild(option);
        return option;
    }

    function updateWriteControls() {
        addButton.hidden = !canWrite;
    }

    function customerLabel(customer) {
        if (customer.company) {
            return `${customer.name} — ${customer.company}`;
        }
        return customer.name;
    }

    function productLabel(product) {
        return `${product.sku} — ${product.name}`;
    }

    function fillCustomerSelect() {
        const current = customerInput.value;
        customerInput.replaceChildren();
        addOption(
            customerInput,
            "",
            customers.length ? "Select a customer" : "No customers available"
        );
        customers.forEach(function (customer) {
            addOption(customerInput, customer.id, customerLabel(customer));
        });
        if (customers.some(function (customer) { return customer.id === current; })) {
            customerInput.value = current;
        }
    }

    function fillLineProductSelect(select) {
        select.replaceChildren();
        addOption(select, "", "Select product (optional)");
        products.forEach(function (product) {
            addOption(select, product.id, productLabel(product));
        });
    }

    function updateLineNumbers() {
        Array.from(linesBody.querySelectorAll("tr")).forEach(function (row, index) {
            const cell = row.querySelector(".line-number");
            if (cell) {
                cell.textContent = String(index + 1);
            }
        });
    }

    function updateCreditTotal() {
        let total = 0;
        Array.from(linesBody.querySelectorAll("tr")).forEach(function (row) {
            const quantity = Number(row.querySelector(".credit-line-quantity")?.value || 0);
            const price = Number(row.querySelector(".credit-line-price")?.value || 0);
            const lineTotal = row.querySelector(".credit-line-total");
            if (!Number.isFinite(quantity) || !Number.isFinite(price)) {
                if (lineTotal) {
                    lineTotal.textContent = "—";
                }
                return;
            }
            const amount = quantity * price;
            total += amount;
            if (lineTotal) {
                lineTotal.textContent = Fx.formatMoney(amount);
            }
        });
        Fx.setText(projectedTotal, Fx.formatMoney(total));
    }

    function addCreditLine() {
        const row = document.createElement("tr");
        lineCounter += 1;

        const numberCell = document.createElement("td");
        numberCell.className = "line-number";
        numberCell.textContent = String(lineCounter);

        const productCell = document.createElement("td");
        const productSelect = document.createElement("select");
        productSelect.className = "form-control credit-line-product";
        productSelect.setAttribute("aria-label", "Credit note line product");
        fillLineProductSelect(productSelect);
        productCell.appendChild(productSelect);

        const quantityCell = document.createElement("td");
        const quantityInput = document.createElement("input");
        quantityInput.type = "number";
        quantityInput.className = "form-control credit-line-quantity";
        quantityInput.min = "0.0001";
        quantityInput.step = "0.0001";
        quantityInput.value = "1";
        quantityInput.setAttribute("aria-label", "Credit note line quantity");
        quantityCell.appendChild(quantityInput);

        const priceCell = document.createElement("td");
        const priceInput = document.createElement("input");
        priceInput.type = "number";
        priceInput.className = "form-control credit-line-price";
        priceInput.min = "0";
        priceInput.step = "0.0001";
        priceInput.setAttribute("aria-label", "Credit note line unit price");
        priceCell.appendChild(priceInput);

        const totalCell = document.createElement("td");
        totalCell.className = "line-total credit-line-total";
        totalCell.textContent = "—";

        const removeCell = document.createElement("td");
        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "setting-button line-remove";
        removeButton.textContent = "Remove";
        removeButton.addEventListener("click", function () {
            row.remove();
            updateLineNumbers();
            updateCreditTotal();
        });
        removeCell.appendChild(removeButton);

        row.append(numberCell, productCell, quantityCell, priceCell, totalCell, removeCell);

        productSelect.addEventListener("change", function () {
            const product = products.find(function (item) {
                return item.id === productSelect.value;
            });
            if (product) {
                priceInput.value = product.current_sale_price;
            }
            updateCreditTotal();
        });
        quantityInput.addEventListener("input", updateCreditTotal);
        priceInput.addEventListener("input", updateCreditTotal);

        linesBody.appendChild(row);
        updateLineNumbers();
        updateCreditTotal();
    }

    function syncReceiptFields() {
        const linked = receiptIdInput.value.trim() !== "";
        customerGroup.hidden = linked;
        currencyGroup.hidden = linked;
        customerInput.required = !linked;
        currencyInput.required = !linked;

        // Inventory can only be returned against a known sale. An unlinked
        // service credit remains available, but it must never restock.
        restockInput.disabled = !linked;
        if (!linked) {
            restockInput.checked = false;
        } else if (receiptWasLinked === false) {
            restockInput.checked = true;
        }
        if (restockHint) {
            restockHint.textContent = linked
                ? "Returned stock is costed from the original sale and needs a linked receipt."
                : "Restocking requires a linked original receipt.";
        }
        receiptWasLinked = linked;
    }

    function resetDialog() {
        form.reset();
        fillCustomerSelect();
        receiptIdInput.value = "";
        currencyInput.value = Fx.state.currency;
        effectiveInput.value = Fx.localDateTimeValue();
        cashAccountInput.value = "1000";
        restockInput.checked = true;
        notesInput.value = "";
        linesBody.replaceChildren();
        lineCounter = 0;
        addCreditLine();
        errorElement.hidden = true;
        errorElement.textContent = "";
        syncReceiptFields();
        Fx.setBusy(submitButton, false, null, "Stage credit note");
        submitButton.textContent = "Stage credit note";
    }

    function openDialog() {
        if (!canWrite) {
            return;
        }
        resetDialog();
        Fx.openDialog(dialog);
        receiptIdInput.focus();
    }

    function closeDialog() {
        Fx.closeDialog(dialog);
    }

    function showFormError(text) {
        errorElement.textContent = text;
        errorElement.hidden = false;
    }

    function isUuid(value) {
        return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
    }

    async function loadLookups() {
        if (loadingLookups) {
            pendingLookupsLoad = true;
            return false;
        }
        loadingLookups = true;
        try {
            const [customerPage, productPage] = await Promise.all([
                API.request("/customers?limit=100"),
                API.request("/products?limit=200&include_inactive=false")
            ]);
            customers = Array.isArray(customerPage.items) ? customerPage.items : [];
            products = Array.isArray(productPage.items) ? productPage.items : [];
            fillCustomerSelect();
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            customers = [];
            products = [];
            fillCustomerSelect();
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Customers and products could not be loaded."),
                true
            );
            return false;
        } finally {
            loadingLookups = false;
            if (pendingLookupsLoad) {
                pendingLookupsLoad = false;
                loadLookups();
            }
        }
    }

    function buildCreditNoteRow(creditNote) {
        const row = document.createElement("tr");

        const noteCell = document.createElement("td");
        const noteName = document.createElement("span");
        noteName.className = "transaction-name";
        noteName.textContent = creditNote.credit_note_number !== null
            && creditNote.credit_note_number !== undefined
            ? `Credit #${creditNote.credit_note_number}`
            : `Credit ${shortId(creditNote.id)}`;
        const noteReference = document.createElement("div");
        noteReference.className = "transaction-reference";
        noteReference.textContent = creditNote.title;
        noteCell.append(noteName, noteReference);

        const customerCell = document.createElement("td");
        customerCell.textContent = creditNote.customer_name
            || (creditNote.customer_id ? `Customer ${shortId(creditNote.customer_id)}` : "—");

        const receiptCell = document.createElement("td");
        if (creditNote.original_receipt_id) {
            const receiptLabel = document.createElement("span");
            receiptLabel.className = "transaction-name";
            receiptLabel.textContent = "Linked receipt";
            const receiptReference = document.createElement("div");
            receiptReference.className = "transaction-reference";
            receiptReference.textContent = shortId(creditNote.original_receipt_id);
            receiptCell.append(receiptLabel, receiptReference);
        } else {
            receiptCell.textContent = "Unlinked";
        }

        const restockCell = document.createElement("td");
        const restockPill = document.createElement("span");
        restockPill.className = `status-pill ${creditNote.restock ? "status-applied" : "status-draft"}`;
        restockPill.textContent = creditNote.restock ? "Restock" : "No restock";
        restockCell.appendChild(restockPill);

        const effectiveCell = document.createElement("td");
        effectiveCell.textContent = Fx.formatDateTime(creditNote.effective_at);

        const statusCell = document.createElement("td");
        statusCell.appendChild(Fx.statusPill(creditNote.status, creditNote));

        const amountCell = document.createElement("td");
        amountCell.className = "finance-money is-strong";
        amountCell.textContent = Fx.formatMoney(creditNote.amount, creditNote.currency);

        const actionCell = document.createElement("td");
        const actions = document.createElement("div");
        actions.className = "finance-table-actions";
        if (creditNote.status === "pending" && Fx.canApproveProposal(creditNote)) {
            const approveButton = document.createElement("button");
            approveButton.type = "button";
            approveButton.className = "setting-button";
            approveButton.textContent = "Approve";
            approveButton.addEventListener("click", function () {
                approveCreditNote(creditNote, approveButton);
            });
            actions.appendChild(approveButton);
        }
        if (actions.childElementCount === 0) {
            actions.textContent = creditNote.status === "pending" && canApprove
                ? "Awaiting another approver"
                : "—";
        }
        actionCell.appendChild(actions);

        row.append(
            noteCell,
            customerCell,
            receiptCell,
            restockCell,
            effectiveCell,
            statusCell,
            amountCell,
            actionCell
        );
        return row;
    }

    function renderCreditNotes() {
        notesBody.replaceChildren();
        if (creditNotes.length === 0) {
            Fx.emptyRow(
                notesBody,
                8,
                filterInput.value
                    ? "No credit notes match the selected status."
                    : "No credit notes have been staged yet."
            );
            return;
        }
        creditNotes.forEach(function (creditNote) {
            notesBody.appendChild(buildCreditNoteRow(creditNote));
        });
    }

    async function loadCreditNotesList() {
        if (loadingNotes) {
            pendingNotesLoad = true;
            return false;
        }
        loadingNotes = true;
        refreshButton.disabled = true;
        refreshButton.textContent = "Refreshing…";
        try {
            const params = new URLSearchParams({ limit: "200" });
            if (filterInput.value) {
                params.set("status", filterInput.value);
            }
            const page = await API.request(`/sales/credit-notes?${params.toString()}`);
            creditNotes = Array.isArray(page.items) ? page.items : [];
            setMetric("credit-notes-total", Fx.formatNumber(page.total, 0));
            renderCreditNotes();
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            creditNotes = [];
            setMetric("credit-notes-total", "—");
            renderCreditNotes();
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Credit notes could not be loaded."),
                true
            );
            return false;
        } finally {
            loadingNotes = false;
            refreshButton.disabled = false;
            refreshButton.textContent = "Refresh";
            if (pendingNotesLoad) {
                pendingNotesLoad = false;
                loadCreditNotesList();
            }
        }
    }

    async function loadCounts() {
        try {
            const [pendingPage, postedPage] = await Promise.all([
                API.request("/sales/credit-notes?status=pending&limit=1"),
                API.request("/sales/credit-notes?status=applied&limit=1")
            ]);
            setMetric("credit-notes-pending", Fx.formatNumber(pendingPage.total, 0));
            setMetric("credit-notes-posted", Fx.formatNumber(postedPage.total, 0));
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            setMetric("credit-notes-pending", "—");
            setMetric("credit-notes-posted", "—");
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Credit note totals could not be loaded."),
                true
            );
            return false;
        }
    }

    async function loadCreditNoteData() {
        const results = await Promise.all([loadCreditNotesList(), loadCounts()]);
        return results.every(Boolean);
    }

    async function approveCreditNote(creditNote, button) {
        if (!Fx.canApproveProposal(creditNote) || button.disabled) {
            return;
        }
        button.disabled = true;
        button.textContent = "Approving…";
        try {
            const decision = await API.request(`/proposals/${creditNote.id}/approve`, {
                method: "POST",
                body: JSON.stringify({})
            });
            await loadCreditNoteData();
            if (decision && decision.posting_error) {
                Fx.showStatus(
                    statusElement,
                    "The credit note was approved, but posting failed. Retry it from the dashboard.",
                    true
                );
            } else {
                Fx.showStatus(statusElement, "Credit note approved and posted to the ledger.");
            }
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "The credit note could not be approved."),
                true
            );
            button.disabled = false;
            button.textContent = "Approve";
        }
    }

    async function submitCreditNote(event) {
        event.preventDefault();
        const receiptId = receiptIdInput.value.trim();
        const customerId = customerInput.value;
        const currency = currencyInput.value.trim().toUpperCase();
        const effectiveAt = Fx.toIsoDateTime(effectiveInput.value);
        const cashAccount = cashAccountInput.value.trim() || "1000";
        const restock = restockInput.checked;

        if (receiptId && !isUuid(receiptId)) {
            showFormError("Enter a valid receipt UUID, or leave the field blank.");
            return;
        }
        if (!receiptId) {
            if (!customerId) {
                showFormError("Select a customer, or link an original receipt.");
                return;
            }
            if (!/^[A-Za-z]{3}$/.test(currency)) {
                showFormError("Enter a three-letter currency code.");
                return;
            }
        }
        if (!effectiveAt) {
            showFormError("Enter a valid effective date and time.");
            return;
        }
        if (!receiptId && restock) {
            showFormError("Restocking requires a linked original receipt.");
            return;
        }

        const lines = [];
        const seen = new Set();
        const rows = Array.from(linesBody.querySelectorAll("tr"));
        for (const row of rows) {
            const productId = row.querySelector(".credit-line-product")?.value || "";
            const quantity = row.querySelector(".credit-line-quantity")?.value.trim() || "";
            const unitPrice = row.querySelector(".credit-line-price")?.value.trim() || "";
            if (restock && !productId) {
                showFormError("Every restocked credit line needs a product.");
                return;
            }
            if (productId && seen.has(productId)) {
                showFormError("Each product can appear only once in a credit note.");
                return;
            }
            if (productId) {
                seen.add(productId);
            }
            if (!quantity || Number(quantity) <= 0) {
                showFormError("Every credit line needs a quantity greater than zero.");
                return;
            }
            if (unitPrice === "" || Number(unitPrice) < 0) {
                showFormError("Every credit line needs a valid unit price.");
                return;
            }
            const line = {
                quantity: quantity,
                unit_price: unitPrice
            };
            if (productId) {
                line.product_id = productId;
            }
            lines.push(line);
        }
        if (lines.length === 0) {
            showFormError("Add at least one credit line.");
            return;
        }

        const payload = {
            effective_at: effectiveAt,
            lines: lines,
            restock: restock,
            refund_method: "cash",
            cash_account_code: cashAccount,
            idempotency_key: Fx.makeIdempotencyKey("credit-note")
        };
        if (receiptId) {
            payload.original_receipt_id = receiptId;
        } else {
            payload.customer_id = customerId;
            payload.currency = currency;
        }
        const notes = notesInput.value.trim();
        if (notes) {
            payload.notes = notes;
        }

        errorElement.hidden = true;
        Fx.setBusy(submitButton, true, "Staging…", "Stage credit note");
        try {
            await API.request("/sales/credit-notes", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            closeDialog();
            await loadCreditNoteData();
            Fx.showStatus(
                statusElement,
                "Credit note proposal staged. Approve it from the credit note table or dashboard to post it."
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            showFormError(Fx.errorMessage(error, "The credit note could not be staged."));
            Fx.setBusy(submitButton, false, null, "Stage credit note");
        }
    }

    addButton?.addEventListener("click", openDialog);
    refreshButton?.addEventListener("click", function () {
        loadCreditNoteData();
    });
    filterInput?.addEventListener("change", loadCreditNotesList);
    receiptIdInput?.addEventListener("input", syncReceiptFields);
    addLineButton?.addEventListener("click", addCreditLine);
    dialogClose?.addEventListener("click", closeDialog);
    cancelButton?.addEventListener("click", closeDialog);
    form?.addEventListener("submit", submitCreditNote);
    dialog?.addEventListener("click", function (event) {
        if (event.target === dialog) {
            closeDialog();
        }
    });
    dialog?.addEventListener("close", function () {
        errorElement.hidden = true;
        errorElement.textContent = "";
    });

    Fx.loadMe()
        .then(function () {
            canWrite = Fx.state.canWrite;
            canApprove = Fx.state.canApprove;
            updateWriteControls();
            return Promise.all([loadLookups(), loadCreditNoteData()]);
        })
        .then(function (results) {
            if (results && results.every(Boolean)) {
                Fx.showStatus(statusElement, "Credit note data loaded.");
            }
        })
        .catch(function (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Your account details could not be loaded."),
                true
            );
        });
})();
