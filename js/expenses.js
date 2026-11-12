/* Live expenses workspace: list and stage proposal-backed expenses. */
(function () {
    "use strict";

    const API = window.FinexAPI;
    const Fx = window.FinexFinance;
    if (!API || !Fx) {
        return;
    }

    const statusElement = document.getElementById("expenses-status");
    const refreshButton = document.getElementById("expenses-refresh");
    const addButton = document.getElementById("expense-add-open");
    const statusFilter = document.getElementById("expense-status-filter");
    const body = document.getElementById("expenses-body");

    const dialog = document.getElementById("expense-dialog");
    const form = document.getElementById("expense-form");
    const dialogClose = document.getElementById("expense-dialog-close");
    const cancelButton = document.getElementById("expense-cancel");
    const errorElement = document.getElementById("expense-error");
    const submitButton = document.getElementById("expense-submit");

    const titleInput = document.getElementById("expense-title");
    const amountInput = document.getElementById("expense-amount");
    const currencyInput = document.getElementById("expense-currency");
    const effectiveInput = document.getElementById("expense-effective");
    const categoryInput = document.getElementById("expense-category");
    const vendorInput = document.getElementById("expense-vendor");
    const paymentMethodInput = document.getElementById("expense-payment-method");
    const cashGroup = document.getElementById("expense-cash-group");
    const cashInput = document.getElementById("expense-cash-account");
    const supplierGroup = document.getElementById("expense-supplier-group");
    const supplierInput = document.getElementById("expense-supplier");
    const dueGroup = document.getElementById("expense-due-group");
    const dueInput = document.getElementById("expense-due-on");
    const descriptionInput = document.getElementById("expense-description");

    let expenses = [];
    let suppliers = [];
    let supplierById = new Map();
    let loading = false;
    let pendingReload = false;
    let canWrite = false;

    function setCounts(page, pendingPage, appliedPage) {
        Fx.setText("expense-total", Number(page.total || 0).toLocaleString());
        Fx.setText("expense-pending", Number(pendingPage.total || 0).toLocaleString());
        Fx.setText("expense-applied", Number(appliedPage.total || 0).toLocaleString());
    }

    function supplierName(id) {
        const supplier = supplierById.get(String(id || ""));
        return supplier ? supplier.name : "";
    }

    function categoryLabel(value) {
        return String(value || "")
            .replace(/_/g, " ")
            .replace(/\b\w/g, function (letter) {
                return letter.toUpperCase();
            });
    }

    function buildRow(expense) {
        const row = document.createElement("tr");

        const expenseCell = document.createElement("td");
        const title = document.createElement("span");
        title.className = "transaction-name";
        title.textContent = expense.title;
        const reference = document.createElement("div");
        reference.className = "transaction-reference";
        reference.textContent = expense.id.slice(0, 8).toUpperCase();
        expenseCell.append(title, reference);

        const vendorCell = document.createElement("td");
        const vendorName = expense.vendor || supplierName(expense.supplier_id);
        vendorCell.textContent = vendorName || "—";
        if (expense.vendor && expense.supplier_id) {
            const supplierNote = document.createElement("div");
            supplierNote.className = "transaction-reference";
            supplierNote.textContent = supplierName(expense.supplier_id);
            vendorCell.appendChild(supplierNote);
        }

        const categoryCell = document.createElement("td");
        categoryCell.textContent = expense.category
            ? categoryLabel(expense.category)
            : (expense.expense_account_code || "—");
        if (expense.category && expense.expense_account_code) {
            const account = document.createElement("div");
            account.className = "transaction-reference";
            account.textContent = expense.expense_account_code;
            categoryCell.appendChild(account);
        }

        const effectiveCell = document.createElement("td");
        effectiveCell.textContent = Fx.formatDateTime(expense.effective_at);

        const paymentCell = document.createElement("td");
        const method = String(expense.payment_method || "cash");
        paymentCell.textContent = method === "payable" ? "Payable" : "Cash";
        const paymentNote = document.createElement("div");
        paymentNote.className = "transaction-reference";
        paymentNote.textContent = method === "payable"
            ? (expense.due_on ? `Due ${Fx.formatDate(expense.due_on)}` : "No due date")
            : (expense.cash_account_code ? `Account ${expense.cash_account_code}` : "");
        paymentCell.appendChild(paymentNote);

        const statusCell = document.createElement("td");
        statusCell.appendChild(Fx.statusPill(expense.status, expense));

        const amountCell = document.createElement("td");
        amountCell.className = "finance-money is-strong";
        amountCell.textContent = Fx.formatMoney(expense.amount);

        row.append(
            expenseCell,
            vendorCell,
            categoryCell,
            effectiveCell,
            paymentCell,
            statusCell,
            amountCell
        );
        return row;
    }

    function renderExpenses() {
        body.replaceChildren();
        if (expenses.length === 0) {
            Fx.emptyRow(
                body,
                7,
                statusFilter && statusFilter.value
                    ? "No expenses match the selected status."
                    : "No expenses have been staged yet."
            );
            return;
        }
        expenses.forEach(function (expense) {
            body.appendChild(buildRow(expense));
        });
    }

    function buildSupplierOptions(selectedId) {
        supplierInput.replaceChildren();
        const active = suppliers.filter(function (supplier) {
            return supplier.is_active !== false;
        });
        if (active.length === 0) {
            const empty = document.createElement("option");
            empty.value = "";
            empty.textContent = "No active suppliers";
            supplierInput.appendChild(empty);
            return;
        }
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Select a supplier";
        supplierInput.appendChild(placeholder);
        active.forEach(function (supplier) {
            const option = document.createElement("option");
            option.value = supplier.id;
            option.textContent = supplier.code
                ? `${supplier.name} (${supplier.code})`
                : supplier.name;
            supplierInput.appendChild(option);
        });
        if (selectedId) {
            supplierInput.value = selectedId;
        }
    }

    function setPaymentMode() {
        const payable = paymentMethodInput.value === "payable";
        cashGroup.hidden = payable;
        supplierGroup.hidden = !payable;
        dueGroup.hidden = !payable;
        cashInput.required = !payable;
        supplierInput.required = payable;
        if (payable) {
            buildSupplierOptions(supplierInput.value);
        }
    }

    function resetForm() {
        form.reset();
        titleInput.value = "";
        amountInput.value = "";
        currencyInput.value = Fx.state.currency || "USD";
        effectiveInput.value = Fx.localDateTimeValue(new Date());
        categoryInput.value = "operating";
        paymentMethodInput.value = "cash";
        cashInput.value = "1000";
        dueInput.value = "";
        vendorInput.value = "";
        descriptionInput.value = "";
        buildSupplierOptions("");
        setPaymentMode();
        errorElement.hidden = true;
        errorElement.textContent = "";
        submitButton.disabled = false;
        submitButton.textContent = "Stage expense";
    }

    function openExpenseDialog() {
        if (!canWrite) {
            return;
        }
        resetForm();
        Fx.openDialog(dialog);
        titleInput.focus();
    }

    function closeExpenseDialog() {
        Fx.closeDialog(dialog);
    }

    function showFormError(text) {
        errorElement.textContent = text;
        errorElement.hidden = false;
    }

    async function submitExpense(event) {
        event.preventDefault();
        const title = titleInput.value.trim();
        const amount = Number(amountInput.value);
        const currency = currencyInput.value.trim().toUpperCase();
        const effectiveAt = Fx.toIsoDateTime(effectiveInput.value);
        const method = paymentMethodInput.value;
        const cashAccount = cashInput.value.trim();
        const supplierId = supplierInput.value;

        if (!title) {
            return showFormError("Enter a title for the expense.");
        }
        if (!Number.isFinite(amount) || amount <= 0) {
            return showFormError("Enter an amount greater than zero.");
        }
        if (!/^[A-Za-z]{3}$/.test(currency)) {
            return showFormError("Enter a three-letter currency code.");
        }
        if (!effectiveAt) {
            return showFormError("Choose a valid effective date and time.");
        }
        const payment = {
            method: method,
            cash_account_code: cashAccount || "1000"
        };
        if (method === "payable") {
            if (!supplierId) {
                return showFormError("Choose the supplier for a payable expense.");
            }
            payment.supplier_id = supplierId;
            if (dueInput.value) {
                payment.due_on = dueInput.value;
            }
        }

        const payload = {
            title: title,
            amount: amount.toFixed(2),
            currency: currency,
            effective_at: effectiveAt,
            category: categoryInput.value,
            payment: payment,
            idempotency_key: Fx.makeIdempotencyKey("expense")
        };
        if (vendorInput.value.trim()) {
            payload.vendor = vendorInput.value.trim();
        }
        if (descriptionInput.value.trim()) {
            payload.description = descriptionInput.value.trim();
        }

        errorElement.hidden = true;
        submitButton.disabled = true;
        submitButton.textContent = "Staging…";
        try {
            await API.request("/expenses", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            closeExpenseDialog();
            await loadAll(false);
            Fx.showStatus(
                statusElement,
                "Expense proposal staged. Approve it from the dashboard to post the expense."
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            showFormError(Fx.errorMessage(error, "The expense could not be staged."));
            submitButton.disabled = false;
            submitButton.textContent = "Stage expense";
        }
    }

    async function loadAll(announce) {
        if (loading) {
            pendingReload = true;
            return;
        }
        loading = true;
        refreshButton.disabled = true;
        refreshButton.textContent = "Refreshing…";
        Fx.showStatus(statusElement, "Loading expenses from the ledger…");

        try {
            await Fx.loadMe();
            canWrite = Fx.state.canWrite;
            addButton.hidden = !canWrite;

            const filter = statusFilter.value;
            const listParams = new URLSearchParams({ limit: "100" });
            if (filter) {
                listParams.set("status", filter);
            }
            const supplierPromise = API.request(
                "/suppliers?limit=200&include_inactive=true"
            ).catch(function () {
                return { items: [] };
            });

            const [page, pendingPage, appliedPage, supplierPage] = await Promise.all([
                API.request(`/expenses?${listParams.toString()}`),
                API.request("/expenses?status=pending&limit=1"),
                API.request("/expenses?status=applied&limit=1"),
                supplierPromise
            ]);

            expenses = Array.isArray(page.items) ? page.items : [];
            suppliers = Array.isArray(supplierPage.items) ? supplierPage.items : [];
            supplierById = new Map(
                suppliers.map(function (supplier) {
                    return [String(supplier.id), supplier];
                })
            );
            setCounts(page, pendingPage, appliedPage);
            renderExpenses();
            buildSupplierOptions(supplierInput ? supplierInput.value : "");
            Fx.showStatus(
                statusElement,
                announce === false
                    ? "Expenses refreshed."
                    : "Live expenses loaded from the proposal ledger."
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            expenses = [];
            renderExpenses();
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Expenses could not be loaded."),
                true
            );
        } finally {
            loading = false;
            refreshButton.disabled = false;
            refreshButton.textContent = "Refresh";
            if (pendingReload) {
                pendingReload = false;
                loadAll(announce);
            }
        }
    }

    addButton?.addEventListener("click", openExpenseDialog);
    refreshButton?.addEventListener("click", function () {
        loadAll(true);
    });
    statusFilter?.addEventListener("change", function () {
        loadAll(true);
    });
    paymentMethodInput?.addEventListener("change", setPaymentMode);
    dialogClose?.addEventListener("click", closeExpenseDialog);
    cancelButton?.addEventListener("click", closeExpenseDialog);
    form?.addEventListener("submit", submitExpense);
    dialog?.addEventListener("click", function (event) {
        if (event.target === dialog) {
            closeExpenseDialog();
        }
    });
    dialog?.addEventListener("close", function () {
        errorElement.hidden = true;
        errorElement.textContent = "";
    });

    Fx.loadMe()
        .then(function () {
            canWrite = Fx.state.canWrite;
            addButton.hidden = !canWrite;
            setPaymentMode();
            return loadAll(false);
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
