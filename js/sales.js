/* Live sales workspace: proposal list, approval, receivables, and staging. */
(function () {
    "use strict";

    const API = window.FinexAPI;
    const Fx = window.FinexFinance;
    if (!API || !Fx) {
        return;
    }

    const statusElement = document.getElementById("sales-status");
    const addButton = document.getElementById("sale-add-open");
    const refreshButton = document.getElementById("sales-refresh");
    const filterInput = document.getElementById("sales-filter");
    const salesBody = document.getElementById("sales-body");

    const dialog = document.getElementById("sale-dialog");
    const form = document.getElementById("sale-form");
    const dialogClose = document.getElementById("sale-dialog-close");
    const cancelButton = document.getElementById("sale-cancel");
    const errorElement = document.getElementById("sale-error");
    const submitButton = document.getElementById("sale-submit");
    const customerInput = document.getElementById("sale-customer");
    const currencyInput = document.getElementById("sale-currency");
    const effectiveInput = document.getElementById("sale-effective");
    const paymentMethodInput = document.getElementById("sale-payment-method");
    const cashGroup = document.getElementById("sale-cash-group");
    const cashAccountInput = document.getElementById("sale-cash-account");
    const receivableGroup = document.getElementById("sale-receivable-group");
    const dueOnInput = document.getElementById("sale-due-on");
    const receiptGroup = document.getElementById("sale-receipt-group");
    const issueReceiptInput = document.getElementById("sale-issue-receipt");
    const memoInput = document.getElementById("sale-memo");
    const linesBody = document.getElementById("sale-lines-body");
    const addLineButton = document.getElementById("sale-add-line");
    const projectedTotal = document.getElementById("sale-projected-total");

    let sales = [];
    let customers = [];
    let products = [];
    let canWrite = false;
    let canApprove = false;
    let loadingSales = false;
    let loadingLookups = false;
    let pendingSalesLoad = false;
    let pendingLookupsLoad = false;
    let lineCounter = 0;

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
        addOption(customerInput, "", customers.length ? "Select a customer" : "No customers available");
        customers.forEach(function (customer) {
            addOption(customerInput, customer.id, customerLabel(customer));
        });
        if (customers.some(function (customer) { return customer.id === current; })) {
            customerInput.value = current;
        }
    }

    function fillLineProductSelect(select) {
        select.replaceChildren();
        addOption(select, "", products.length ? "Select product" : "No active products");
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

    function updateSaleTotal() {
        let total = 0;
        Array.from(linesBody.querySelectorAll("tr")).forEach(function (row) {
            const quantity = Number(row.querySelector(".sale-line-quantity")?.value || 0);
            const price = Number(row.querySelector(".sale-line-price")?.value || 0);
            const lineTotal = row.querySelector(".sale-line-total");
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

    function addSaleLine() {
        const row = document.createElement("tr");
        lineCounter += 1;

        const numberCell = document.createElement("td");
        numberCell.className = "line-number";
        numberCell.textContent = String(lineCounter);

        const productCell = document.createElement("td");
        const productSelect = document.createElement("select");
        productSelect.className = "form-control sale-line-product";
        productSelect.setAttribute("aria-label", "Sale line product");
        fillLineProductSelect(productSelect);
        productCell.appendChild(productSelect);

        const quantityCell = document.createElement("td");
        const quantityInput = document.createElement("input");
        quantityInput.type = "number";
        quantityInput.className = "form-control sale-line-quantity";
        quantityInput.min = "0.0001";
        quantityInput.step = "0.0001";
        quantityInput.value = "1";
        quantityInput.setAttribute("aria-label", "Sale line quantity");
        quantityCell.appendChild(quantityInput);

        const priceCell = document.createElement("td");
        const priceInput = document.createElement("input");
        priceInput.type = "number";
        priceInput.className = "form-control sale-line-price";
        priceInput.min = "0";
        priceInput.step = "0.0001";
        priceInput.setAttribute("aria-label", "Sale line unit price");
        priceCell.appendChild(priceInput);

        const totalCell = document.createElement("td");
        totalCell.className = "line-total sale-line-total";
        totalCell.textContent = "—";

        const removeCell = document.createElement("td");
        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "setting-button line-remove";
        removeButton.textContent = "Remove";
        removeButton.addEventListener("click", function () {
            row.remove();
            updateLineNumbers();
            updateSaleTotal();
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
            updateSaleTotal();
        });
        quantityInput.addEventListener("input", updateSaleTotal);
        priceInput.addEventListener("input", updateSaleTotal);

        linesBody.appendChild(row);
        updateLineNumbers();
        updateSaleTotal();
    }

    function resetDialog() {
        form.reset();
        customerInput.replaceChildren();
        fillCustomerSelect();
        currencyInput.value = Fx.state.currency;
        effectiveInput.value = Fx.localDateTimeValue();
        paymentMethodInput.value = "cash";
        cashAccountInput.value = "1000";
        dueOnInput.value = "";
        issueReceiptInput.checked = true;
        memoInput.value = "";
        linesBody.replaceChildren();
        lineCounter = 0;
        addSaleLine();
        errorElement.hidden = true;
        errorElement.textContent = "";
        syncPaymentFields();
        Fx.setBusy(submitButton, false, null, "Stage sale");
        submitButton.textContent = "Stage sale";
    }

    function syncPaymentFields() {
        const isCash = paymentMethodInput.value === "cash";
        cashGroup.hidden = !isCash;
        receiptGroup.hidden = !isCash;
        receivableGroup.hidden = isCash;
    }

    function showFormError(text) {
        errorElement.textContent = text;
        errorElement.hidden = false;
    }

    function openDialog() {
        if (!canWrite) {
            return;
        }
        if (customers.length === 0) {
            Fx.showStatus(
                statusElement,
                "Create a customer before staging a sale.",
                true
            );
            return;
        }
        if (products.length === 0) {
            Fx.showStatus(
                statusElement,
                "Add an active product before staging a sale.",
                true
            );
            return;
        }
        resetDialog();
        Fx.openDialog(dialog);
        customerInput.focus();
    }

    function closeDialog() {
        Fx.closeDialog(dialog);
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

    function buildSaleRow(sale) {
        const row = document.createElement("tr");

        const saleCell = document.createElement("td");
        const saleName = document.createElement("span");
        saleName.className = "transaction-name";
        saleName.textContent = `Sale ${shortId(sale.id)}`;
        const saleReference = document.createElement("div");
        saleReference.className = "transaction-reference";
        saleReference.textContent = sale.title;
        saleCell.append(saleName, saleReference);

        const customerCell = document.createElement("td");
        customerCell.textContent = sale.customer_name
            || (sale.customer_id ? `Customer ${shortId(sale.customer_id)}` : "—");

        const effectiveCell = document.createElement("td");
        effectiveCell.textContent = Fx.formatDateTime(sale.effective_at);

        const paymentCell = document.createElement("td");
        const paymentMethod = sale.payment && sale.payment.method === "receivable"
            ? "Receivable"
            : "Cash";
        const paymentName = document.createElement("span");
        paymentName.className = "transaction-name";
        paymentName.textContent = paymentMethod;
        paymentCell.appendChild(paymentName);
        if (paymentMethod === "Receivable" && sale.payment && sale.payment.due_on) {
            const due = document.createElement("div");
            due.className = "transaction-reference";
            due.textContent = `Due ${Fx.formatDate(sale.payment.due_on)}`;
            paymentCell.appendChild(due);
        }

        const documentCell = document.createElement("td");
        if (sale.receipt_number !== null && sale.receipt_number !== undefined) {
            const label = document.createElement("span");
            label.className = "transaction-name";
            label.textContent = `Receipt #${sale.receipt_number}`;
            const reference = document.createElement("div");
            reference.className = "transaction-reference";
            reference.textContent = shortId(sale.receipt_id);
            documentCell.append(label, reference);
        } else if (sale.open_item_id) {
            const label = document.createElement("span");
            label.className = "transaction-name";
            label.textContent = "Open item";
            const reference = document.createElement("div");
            reference.className = "transaction-reference";
            reference.textContent = shortId(sale.open_item_id);
            documentCell.append(label, reference);
        } else {
            documentCell.textContent = "—";
        }

        const statusCell = document.createElement("td");
        statusCell.appendChild(Fx.statusPill(sale.status, sale));

        const amountCell = document.createElement("td");
        amountCell.className = "finance-money is-strong";
        amountCell.textContent = Fx.formatMoney(sale.amount, sale.currency);

        const actionCell = document.createElement("td");
        const actions = document.createElement("div");
        actions.className = "finance-table-actions";
        if (sale.status === "pending" && Fx.canApproveProposal(sale)) {
            const approveButton = document.createElement("button");
            approveButton.type = "button";
            approveButton.className = "setting-button";
            approveButton.textContent = "Approve";
            approveButton.addEventListener("click", function () {
                approveSale(sale, approveButton);
            });
            actions.appendChild(approveButton);
        }
        if (sale.receipt_id) {
            const copyButton = document.createElement("button");
            copyButton.type = "button";
            copyButton.className = "setting-button";
            copyButton.textContent = "Copy ID";
            copyButton.title = "Copy the full receipt ID for a credit note";
            copyButton.addEventListener("click", function () {
                copyReceiptId(sale);
            });
            actions.appendChild(copyButton);
        }
        if (actions.childElementCount === 0) {
            actions.textContent = sale.status === "pending" && canApprove
                ? "Awaiting another approver"
                : "—";
        }
        actionCell.appendChild(actions);

        row.append(
            saleCell,
            customerCell,
            effectiveCell,
            paymentCell,
            documentCell,
            statusCell,
            amountCell,
            actionCell
        );
        return row;
    }

    function renderSales() {
        salesBody.replaceChildren();
        if (sales.length === 0) {
            Fx.emptyRow(
                salesBody,
                8,
                filterInput.value
                    ? "No sales match the selected status."
                    : "No sales have been staged yet."
            );
            return;
        }
        sales.forEach(function (sale) {
            salesBody.appendChild(buildSaleRow(sale));
        });
    }

    async function loadSalesList() {
        if (loadingSales) {
            pendingSalesLoad = true;
            return false;
        }
        loadingSales = true;
        refreshButton.disabled = true;
        refreshButton.textContent = "Refreshing…";
        try {
            const params = new URLSearchParams({ limit: "200" });
            if (filterInput.value) {
                params.set("status", filterInput.value);
            }
            const page = await API.request(`/sales?${params.toString()}`);
            sales = Array.isArray(page.items) ? page.items : [];
            setMetric("sales-total", Fx.formatNumber(page.total, 0));
            renderSales();
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            sales = [];
            setMetric("sales-total", "—");
            renderSales();
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Sales could not be loaded."),
                true
            );
            return false;
        } finally {
            loadingSales = false;
            refreshButton.disabled = false;
            refreshButton.textContent = "Refresh";
            if (pendingSalesLoad) {
                pendingSalesLoad = false;
                loadSalesList();
            }
        }
    }

    async function loadCountsAndSummary() {
        try {
            const [pendingPage, postedPage, summary] = await Promise.all([
                API.request("/sales?status=pending&limit=1"),
                API.request("/sales?status=applied&limit=1"),
                API.request("/debts/summary")
            ]);
            setMetric("sales-pending", Fx.formatNumber(pendingPage.total, 0));
            setMetric("sales-posted", Fx.formatNumber(postedPage.total, 0));
            setMetric("sales-receivables", Fx.formatMoney(summary.total_receivables, summary.currency));
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            setMetric("sales-pending", "—");
            setMetric("sales-posted", "—");
            setMetric("sales-receivables", "—");
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Sales totals could not be loaded."),
                true
            );
            return false;
        }
    }

    async function loadSalesData() {
        const results = await Promise.all([loadSalesList(), loadCountsAndSummary()]);
        return results.every(Boolean);
    }

    async function approveSale(sale, button) {
        if (!Fx.canApproveProposal(sale) || button.disabled) {
            return;
        }
        button.disabled = true;
        button.textContent = "Approving…";
        try {
            const decision = await API.request(`/proposals/${sale.id}/approve`, {
                method: "POST",
                body: JSON.stringify({})
            });
            await loadSalesData();
            if (decision && decision.posting_error) {
                Fx.showStatus(
                    statusElement,
                    "The sale was approved, but posting failed. Retry it from the dashboard.",
                    true
                );
            } else {
                Fx.showStatus(statusElement, "Sale approved and posted to the ledger.");
            }
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "The sale could not be approved."),
                true
            );
            button.disabled = false;
            button.textContent = "Approve";
        }
    }

    async function copyReceiptId(sale) {
        if (!sale.receipt_id) {
            return;
        }
        const value = String(sale.receipt_id);
        try {
            await navigator.clipboard.writeText(value);
            Fx.showStatus(statusElement, "Receipt ID copied. Paste it into a credit note.");
        } catch (error) {
            Fx.showStatus(statusElement, `Receipt ID: ${value}`);
        }
    }

    async function submitSale(event) {
        event.preventDefault();
        const customerId = customerInput.value;
        const currency = currencyInput.value.trim().toUpperCase();
        const effectiveAt = Fx.toIsoDateTime(effectiveInput.value);
        const paymentMethod = paymentMethodInput.value;
        const cashAccount = cashAccountInput.value.trim() || "1000";

        if (!customerId) {
            showFormError("Select a customer.");
            return;
        }
        if (!/^[A-Za-z]{3}$/.test(currency)) {
            showFormError("Enter a three-letter currency code.");
            return;
        }
        if (!effectiveAt) {
            showFormError("Enter a valid effective date and time.");
            return;
        }

        const lines = [];
        const seen = new Set();
        const rows = Array.from(linesBody.querySelectorAll("tr"));
        for (const row of rows) {
            const productId = row.querySelector(".sale-line-product")?.value;
            const quantity = row.querySelector(".sale-line-quantity")?.value.trim() || "";
            const unitPrice = row.querySelector(".sale-line-price")?.value.trim() || "";
            if (!productId) {
                showFormError("Select a product for every sale line.");
                return;
            }
            if (seen.has(productId)) {
                showFormError("Each product can appear only once in a sale.");
                return;
            }
            seen.add(productId);
            if (!quantity || Number(quantity) <= 0) {
                showFormError("Every sale line needs a quantity greater than zero.");
                return;
            }
            if (unitPrice === "" || Number(unitPrice) < 0) {
                showFormError("Every sale line needs a valid unit price.");
                return;
            }
            lines.push({
                product_id: productId,
                quantity: quantity,
                unit_price: unitPrice
            });
        }
        if (lines.length === 0) {
            showFormError("Add at least one sale line.");
            return;
        }

        const payment = {
            method: paymentMethod,
            cash_account_code: cashAccount
        };
        if (paymentMethod === "receivable") {
            const dueOn = dueOnInput.value;
            if (dueOn) {
                payment.due_on = dueOn;
            }
        } else {
            payment.issue_receipt = issueReceiptInput.checked;
        }

        const payload = {
            customer_id: customerId,
            currency: currency,
            effective_at: effectiveAt,
            lines: lines,
            payment: payment,
            idempotency_key: Fx.makeIdempotencyKey("sale")
        };
        const memo = memoInput.value.trim();
        if (memo) {
            payload.memo = memo;
        }

        errorElement.hidden = true;
        Fx.setBusy(submitButton, true, "Staging…", "Stage sale");
        try {
            await API.request("/sales", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            closeDialog();
            await loadSalesData();
            Fx.showStatus(
                statusElement,
                "Sale proposal staged. Approve it from the sales table or dashboard to post it."
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            showFormError(Fx.errorMessage(error, "The sale could not be staged."));
            Fx.setBusy(submitButton, false, null, "Stage sale");
        }
    }

    addButton?.addEventListener("click", openDialog);
    refreshButton?.addEventListener("click", function () {
        loadSalesData();
    });
    filterInput?.addEventListener("change", loadSalesList);
    addLineButton?.addEventListener("click", addSaleLine);
    paymentMethodInput?.addEventListener("change", syncPaymentFields);
    dialogClose?.addEventListener("click", closeDialog);
    cancelButton?.addEventListener("click", closeDialog);
    form?.addEventListener("submit", submitSale);
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
            return Promise.all([loadLookups(), loadSalesData()]);
        })
        .then(function (results) {
            if (results && results.every(Boolean)) {
                Fx.showStatus(statusElement, "Sales data loaded.");
                const action = new URLSearchParams(window.location.search).get("action");
                if (canWrite && action === "sale") {
                    window.history.replaceState(null, "", window.location.pathname);
                    openDialog();
                }
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
