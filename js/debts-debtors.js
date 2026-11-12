/* Live debts and debtor workspace. Financial totals come from the ledger API. */
(function () {
    "use strict";

    const page = document.querySelector(".debts-debtors-page");
    if (!page || page.dataset.debtsInitialized === "true") {
        return;
    }

    const API = window.FinexAPI;
    const tabs = page.querySelector("[data-debt-section-nav]");
    const tableBody = page.querySelector("#debt-records-body");
    const searchInput = page.querySelector("#debt-search");
    const statusFilter = page.querySelector("#debt-status-filter");
    const emptyState = page.querySelector("#debt-empty-state");
    const countLabel = page.querySelector("#debt-record-count");
    const debtorsBody = page.querySelector("#debtors-body");
    const payablesBody = page.querySelector("#payables-body");
    const summaryGrid = page.querySelector("#debts-summary");
    const refreshButton = page.querySelector("#debt-refresh");
    const statusMessage = page.querySelector("#debts-status");

    const dialog = page.querySelector("#settlement-dialog");
    const settlementForm = page.querySelector("#settlement-form");
    const settlementDescription = page.querySelector("#settlement-description");
    const settlementAmount = page.querySelector("#settlement-amount");
    const settlementDate = page.querySelector("#settlement-date");
    const settlementCashAccount = page.querySelector("#settlement-cash-account");
    const settlementReceiptWrap = page.querySelector("#settlement-receipt-wrap");
    const settlementIssueReceipt = page.querySelector("#settlement-issue-receipt");
    const settlementError = page.querySelector("#settlement-error");
    const settlementSubmit = page.querySelector("#settlement-submit");
    const settlementClose = page.querySelector("#settlement-close");
    const settlementCancel = page.querySelector("#settlement-cancel");

    if (
        !API
        || !tabs
        || !tableBody
        || !searchInput
        || !statusFilter
        || !emptyState
    ) {
        return;
    }

    page.dataset.debtsInitialized = "true";

    const tabLinks = Array.from(tabs.querySelectorAll("a[href^='#']"));
    const writableRoles = new Set(["owner", "admin", "manager", "staff"]);

    let debts = [];
    let totalDebtRecords = 0;
    let currency = "USD";
    let canWrite = false;
    let selectedDebt = null;
    let loading = false;

    function setText(selector, value) {
        const element = page.querySelector(selector);
        if (element) {
            element.textContent = value;
        }
    }

    function formatMoney(amount) {
        if (amount === null || amount === undefined || amount === "") {
            return "—";
        }
        const value = Number(amount);
        if (Number.isNaN(value)) {
            return "—";
        }
        const code = String(currency || "USD").toUpperCase();
        try {
            return new Intl.NumberFormat(undefined, {
                style: "currency",
                currency: code,
                currencyDisplay: "narrowSymbol",
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            }).format(value);
        } catch (error) {
            return `${value.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            })} ${code}`;
        }
    }

    function formatDate(value) {
        if (!value) {
            return "No due date";
        }
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
        if (!match) {
            return "—";
        }
        return new Date(
            Number(match[1]),
            Number(match[2]) - 1,
            Number(match[3])
        ).toLocaleDateString(undefined, {
            year: "numeric",
            month: "short",
            day: "numeric"
        });
    }

    function formatDateTime(value) {
        if (!value) {
            return "—";
        }
        const date = new Date(value);
        return Number.isNaN(date.getTime())
            ? "—"
            : date.toLocaleString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit"
            });
    }

    function localDateTimeValue(date) {
        const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 16);
    }

    function debtStatus(debt) {
        const outstanding = Number(debt.outstanding_amount || 0);
        const allocated = Number(debt.allocated_amount || 0);
        if (outstanding <= 0) {
            return { key: "paid", label: "Paid" };
        }
        if (debt.is_overdue) {
            return { key: "overdue", label: "Overdue" };
        }
        if (allocated > 0) {
            return { key: "partially-paid", label: "Partially paid" };
        }
        return { key: "open", label: "Open" };
    }

    function statusPill(debt) {
        const status = debtStatus(debt);
        const pill = document.createElement("span");
        pill.className = `status-pill status-${status.key}`;
        pill.textContent = status.label;
        return pill;
    }

    function makeCell(text, className) {
        const cell = document.createElement("td");
        if (className) {
            cell.className = className;
        }
        cell.textContent = text;
        return cell;
    }

    function titleCase(value) {
        return String(value || "")
            .replace(/_/g, " ")
            .replace(/\b\w/g, (letter) => letter.toUpperCase());
    }

    function counterpartyLabel(debt) {
        return debt.counterparty_name
            || (debt.item_type === "customer_receivable"
                ? "Unknown customer"
                : "Unknown supplier");
    }

    function handleAuthError(error) {
        if (error && error.status === 401) {
            window.location.replace("../index.html");
            return true;
        }
        return false;
    }

    function showStatus(text, isError) {
        if (!statusMessage) {
            return;
        }
        statusMessage.textContent = text;
        statusMessage.classList.toggle("is-error", Boolean(isError));
    }

    function updateActiveTab() {
        const requestedHash = window.location.hash || "#overview";
        const activeLink = tabLinks.find((link) => link.getAttribute("href") === requestedHash)
            || tabLinks.find((link) => link.getAttribute("href") === "#overview");

        tabLinks.forEach((link) => {
            const isActive = link === activeLink;
            link.classList.toggle("is-active", isActive);
            if (isActive) {
                link.setAttribute("aria-current", "location");
            } else {
                link.removeAttribute("aria-current");
            }
        });
    }

    function filteredDebts() {
        const query = searchInput.value.trim().toLowerCase();
        const selectedStatus = statusFilter.value;

        return debts.filter((debt) => {
            const status = debtStatus(debt);
            const haystack = [
                debt.id,
                debt.item_type,
                titleCase(debt.item_type),
                counterpartyLabel(debt),
                status.label,
                debt.due_on || ""
            ].join(" ").toLowerCase();
            const matchesSearch = !query || haystack.includes(query);
            const matchesStatus = selectedStatus === "all"
                || status.key === selectedStatus;
            return matchesSearch && matchesStatus;
        });
    }

    function buildMainRow(debt) {
        const row = document.createElement("tr");
        row.dataset.debtRecord = "true";
        row.dataset.status = debtStatus(debt).key;

        const recordCell = document.createElement("td");
        const recordTitle = document.createElement("span");
        recordTitle.className = "transaction-name";
        recordTitle.textContent = titleCase(debt.item_type);
        const recordReference = document.createElement("div");
        recordReference.className = "transaction-reference";
        recordReference.textContent = debt.id.slice(0, 8).toUpperCase();
        recordCell.append(recordTitle, recordReference);

        const counterpartyCell = document.createElement("td");
        const counterparty = document.createElement("span");
        counterparty.className = "transaction-name";
        counterparty.textContent = counterpartyLabel(debt);
        const counterpartyType = document.createElement("div");
        counterpartyType.className = "transaction-reference";
        counterpartyType.textContent = debt.item_type === "customer_receivable"
            ? "Customer"
            : "Supplier";
        counterpartyCell.append(counterparty, counterpartyType);

        const dueCell = makeCell(formatDate(debt.due_on));
        if (debt.is_overdue && Number(debt.outstanding_amount) > 0) {
            const overdue = document.createElement("div");
            overdue.className = "debt-overdue-note";
            overdue.textContent = "Past due";
            dueCell.appendChild(overdue);
        }

        const statusCell = document.createElement("td");
        statusCell.appendChild(statusPill(debt));

        const originalCell = makeCell(
            formatMoney(debt.original_amount),
            "debt-money-cell"
        );
        const outstandingCell = makeCell(
            formatMoney(debt.outstanding_amount),
            "debt-money-cell debt-outstanding-cell"
        );

        const actionsCell = document.createElement("td");
        if (canWrite && Number(debt.outstanding_amount) > 0) {
            const settleButton = document.createElement("button");
            settleButton.type = "button";
            settleButton.className = "setting-button debt-settle-button";
            settleButton.textContent = "Settle";
            settleButton.setAttribute(
                "aria-label",
                `Stage a settlement for ${counterpartyLabel(debt)}`
            );
            settleButton.addEventListener("click", () => {
                openSettlementDialog(debt);
            });
            actionsCell.appendChild(settleButton);
        } else {
            actionsCell.textContent = "—";
        }

        row.append(
            recordCell,
            counterpartyCell,
            dueCell,
            statusCell,
            originalCell,
            outstandingCell,
            actionsCell
        );
        row.title = `Opened ${formatDateTime(debt.opened_at)}`;
        return row;
    }

    function renderMainTable(rows) {
        tableBody.querySelectorAll("tr[data-debt-record]").forEach((row) => {
            row.remove();
        });

        rows.forEach((debt) => {
            tableBody.insertBefore(buildMainRow(debt), emptyState);
        });

        emptyState.hidden = rows.length > 0;
        if (rows.length === 0) {
            const hasFilters = Boolean(searchInput.value.trim())
                || statusFilter.value !== "all";
            emptyState.querySelector("td").textContent = hasFilters
                ? "No debt records match your search and filters."
                : "No debt records have been posted yet.";
        }

        if (countLabel) {
            const shown = rows.length;
            const loaded = debts.length;
            const total = Math.max(totalDebtRecords, loaded);
            let text = `${shown} of ${loaded} loaded debt records shown.`;
            if (total > loaded) {
                text += ` ${total} total records exist; the newest ${loaded} are loaded.`;
            }
            if (shown === 0) {
                text = "No debt records match the current filters.";
            }
            countLabel.textContent = text;
        }
    }

    function renderBreakdownTable(body, rows, emptyText, nameHeading) {
        if (!body) {
            return;
        }
        body.replaceChildren();

        if (rows.length === 0) {
            const row = document.createElement("tr");
            row.className = "debt-empty-state";
            const cell = document.createElement("td");
            cell.colSpan = 4;
            cell.textContent = emptyText;
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }

        rows.forEach((debt) => {
            const row = document.createElement("tr");
            row.dataset.debtRecord = "true";

            const nameCell = document.createElement("td");
            const name = document.createElement("span");
            name.className = "transaction-name";
            name.textContent = counterpartyLabel(debt);
            nameCell.appendChild(name);
            nameCell.title = nameHeading;

            const dueCell = makeCell(formatDate(debt.due_on));
            const amountCell = makeCell(
                formatMoney(debt.outstanding_amount),
                "debt-money-cell debt-outstanding-cell"
            );
            const statusCell = document.createElement("td");
            statusCell.appendChild(statusPill(debt));

            row.append(nameCell, dueCell, amountCell, statusCell);
            body.appendChild(row);
        });
    }

    function sortByDueDate(rows) {
        return rows.slice().sort((left, right) => {
            if (!left.due_on && !right.due_on) {
                return counterpartyLabel(left).localeCompare(counterpartyLabel(right));
            }
            if (!left.due_on) {
                return 1;
            }
            if (!right.due_on) {
                return -1;
            }
            return left.due_on.localeCompare(right.due_on);
        });
    }

    function renderBreakdowns() {
        const outstanding = debts.filter(
            (debt) => Number(debt.outstanding_amount) > 0
        );
        const debtors = sortByDueDate(outstanding.filter(
            (debt) => debt.item_type === "customer_receivable"
        ));
        const payables = sortByDueDate(outstanding.filter(
            (debt) => debt.item_type === "supplier_payable"
        ));

        renderBreakdownTable(
            debtorsBody,
            debtors,
            "No outstanding customer receivables.",
            "Customer"
        );
        renderBreakdownTable(
            payablesBody,
            payables,
            "No outstanding supplier payables.",
            "Supplier"
        );
    }

    function renderSummary(summary) {
        if (!summary) {
            return;
        }
        currency = String(summary.currency || currency || "USD").toUpperCase();

        const debtorsCount = Number(summary.total_debtors || 0);
        const creditorsCount = Number(summary.total_creditors || 0);
        const overdueAmount = Number(summary.overdue_receivables || 0)
            + Number(summary.overdue_payables || 0);
        const overdueCount = Number(summary.overdue_count || 0);

        setText("#summary-receivables", formatMoney(summary.total_receivables));
        setText(
            "#summary-receivables-note",
            `${debtorsCount} debtor${debtorsCount === 1 ? "" : "s"} with a balance`
        );
        setText("#summary-payables", formatMoney(summary.total_payables));
        setText(
            "#summary-payables-note",
            `${creditorsCount} supplier${creditorsCount === 1 ? "" : "s"} with a balance`
        );
        setText("#summary-debtors", debtorsCount.toLocaleString());
        setText(
            "#summary-debtors-note",
            `${formatMoney(summary.total_receivables)} outstanding`
        );
        setText("#summary-overdue", formatMoney(overdueAmount));
        setText(
            "#summary-overdue-note",
            `${overdueCount} overdue item${overdueCount === 1 ? "" : "s"}`
        );

        if (summaryGrid) {
            summaryGrid.setAttribute("aria-busy", "false");
        }
    }

    function renderAll() {
        const rows = filteredDebts();
        renderMainTable(rows);
        renderBreakdowns();
    }

    function setLoading(isLoading) {
        loading = isLoading;
        if (refreshButton) {
            refreshButton.disabled = isLoading;
            refreshButton.setAttribute("aria-busy", String(isLoading));
        }
        if (summaryGrid && isLoading) {
            summaryGrid.setAttribute("aria-busy", "true");
        }
    }

    async function loadAll(announceRefresh) {
        if (loading) {
            return;
        }
        setLoading(true);
        showStatus(announceRefresh ? "Refreshing debt balances…" : "Loading debt balances…");

        try {
            const [me, summary, pageData] = await Promise.all([
                API.request("/auth/me"),
                API.request("/debts/summary"),
                API.request("/debts?outstanding_only=false&limit=200")
            ]);

            canWrite = writableRoles.has(String(me.role || "").toLowerCase());
            debts = Array.isArray(pageData.items) ? pageData.items : [];
            totalDebtRecords = Number(pageData.total || debts.length);
            renderSummary(summary);
            renderAll();

            showStatus(
                announceRefresh
                    ? "Debt balances refreshed."
                    : "Live balances loaded from the ledger."
            );
        } catch (error) {
            if (handleAuthError(error)) {
                return;
            }
            showStatus(
                error.message || "Could not load debt balances.",
                true
            );
            if (summaryGrid) {
                summaryGrid.setAttribute("aria-busy", "false");
            }
            if (debts.length === 0) {
                emptyState.hidden = false;
                emptyState.querySelector("td").textContent =
                    "Debt balances could not be loaded. Try refreshing.";
                if (debtorsBody) {
                    renderBreakdownTable(
                        debtorsBody,
                        [],
                        "Customer receivables could not be loaded.",
                        "Customer"
                    );
                }
                if (payablesBody) {
                    renderBreakdownTable(
                        payablesBody,
                        [],
                        "Supplier payables could not be loaded.",
                        "Supplier"
                    );
                }
            }
        } finally {
            setLoading(false);
        }
    }

    function openSettlementDialog(debt) {
        if (!dialog || !settlementForm || !canWrite) {
            return;
        }
        selectedDebt = debt;
        const outstanding = Number(debt.outstanding_amount || 0);
        currency = String(currency || "USD").toUpperCase();

        if (settlementDescription) {
            settlementDescription.textContent =
                `Stage a payment for ${counterpartyLabel(debt)} `
                + `(${formatMoney(outstanding)} outstanding). `
                + "A manager, administrator, or owner must approve the proposal before the ledger is updated.";
        }
        if (settlementAmount) {
            settlementAmount.value = outstanding.toFixed(2);
            settlementAmount.max = outstanding.toFixed(2);
        }
        if (settlementDate) {
            settlementDate.value = localDateTimeValue(new Date());
        }
        if (settlementCashAccount) {
            settlementCashAccount.value = "1000";
        }
        if (settlementReceiptWrap && settlementIssueReceipt) {
            const canIssueReceipt = debt.item_type === "customer_receivable";
            settlementReceiptWrap.hidden = !canIssueReceipt;
            settlementIssueReceipt.checked = false;
        }
        if (settlementError) {
            settlementError.hidden = true;
            settlementError.textContent = "";
        }
        if (settlementSubmit) {
            settlementSubmit.disabled = false;
            settlementSubmit.textContent = "Stage settlement";
        }

        if (typeof dialog.showModal === "function") {
            dialog.showModal();
        } else {
            dialog.setAttribute("open", "");
        }
        settlementAmount?.focus();
        settlementAmount?.select();
    }

    function closeSettlementDialog() {
        selectedDebt = null;
        if (!dialog) {
            return;
        }
        if (typeof dialog.close === "function") {
            dialog.close();
        } else {
            dialog.removeAttribute("open");
        }
    }

    function createIdempotencyKey() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }
        return `settlement-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    async function submitSettlement(event) {
        event.preventDefault();
        if (!selectedDebt || !canWrite) {
            return;
        }

        const debt = selectedDebt;
        const outstanding = Number(debt.outstanding_amount || 0);
        const amount = Number(settlementAmount?.value);
        const effectiveValue = settlementDate?.value || "";
        const cashAccount = (settlementCashAccount?.value || "").trim();
        const issueReceipt = Boolean(
            settlementReceiptWrap
            && !settlementReceiptWrap.hidden
            && settlementIssueReceipt?.checked
        );

        function fail(message) {
            if (settlementError) {
                settlementError.textContent = message;
                settlementError.hidden = false;
            }
            return false;
        }

        if (!Number.isFinite(amount) || amount <= 0) {
            return fail("Enter a settlement amount greater than zero.");
        }
        if (amount > outstanding) {
            return fail("The settlement amount cannot exceed the outstanding balance.");
        }
        if (!effectiveValue) {
            return fail("Choose an effective date and time.");
        }
        const effectiveAt = new Date(effectiveValue);
        if (Number.isNaN(effectiveAt.getTime())) {
            return fail("Choose a valid effective date and time.");
        }
        if (!cashAccount) {
            return fail("Enter a cash account code.");
        }

        if (settlementError) {
            settlementError.hidden = true;
            settlementError.textContent = "";
        }
        if (settlementSubmit) {
            settlementSubmit.disabled = true;
            settlementSubmit.textContent = "Staging…";
        }

        try {
            await API.request(
                `/debts/${encodeURIComponent(debt.id)}/settlements`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        amount: amount.toFixed(2),
                        effective_at: effectiveAt.toISOString(),
                        currency: currency,
                        cash_account_code: cashAccount,
                        issue_receipt: issueReceipt,
                        notes: "Settlement staged from the debts workspace",
                        idempotency_key: createIdempotencyKey()
                    })
                }
            );

            closeSettlementDialog();
            showStatus(
                "Settlement proposal staged. A manager, administrator, or owner must approve it before the payment is posted."
            );
        } catch (error) {
            if (handleAuthError(error)) {
                return;
            }
            fail(error.message || "Could not stage the settlement.");
            if (settlementSubmit) {
                settlementSubmit.disabled = false;
                settlementSubmit.textContent = "Stage settlement";
            }
        }
    }

    tabs.addEventListener("click", (event) => {
        const link = event.target.closest("a[href^='#']");
        if (!link || !tabs.contains(link)) return;
        updateActiveTab();
    });

    window.addEventListener("hashchange", updateActiveTab);
    searchInput.addEventListener("input", renderAll);
    statusFilter.addEventListener("change", renderAll);
    refreshButton?.addEventListener("click", () => loadAll(true));

    settlementForm?.addEventListener("submit", submitSettlement);
    settlementClose?.addEventListener("click", closeSettlementDialog);
    settlementCancel?.addEventListener("click", closeSettlementDialog);
    dialog?.addEventListener("click", (event) => {
        if (event.target === dialog) {
            closeSettlementDialog();
        }
    });
    dialog?.addEventListener("close", () => {
        selectedDebt = null;
        if (settlementError) {
            settlementError.hidden = true;
            settlementError.textContent = "";
        }
    });

    updateActiveTab();
    loadAll(false);
})();
