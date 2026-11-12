/* Ledger-derived financial reports. The browser only formats server values. */
(function () {
    "use strict";

    const API = window.FinexAPI;
    const Fx = window.FinexFinance;
    if (!API || !Fx) {
        return;
    }

    const statusElement = document.getElementById("reports-status");
    const refreshButton = document.getElementById("reports-refresh");
    const plStart = document.getElementById("pl-start");
    const plEnd = document.getElementById("pl-end");
    const plRun = document.getElementById("pl-run");
    const bsAsOf = document.getElementById("bs-as-of");
    const bsRun = document.getElementById("bs-run");

    const tabButtons = Array.from(document.querySelectorAll("[data-report-tab]"));
    const sections = {
        pl: document.getElementById("report-pl"),
        bs: document.getElementById("report-bs"),
        tb: document.getElementById("report-tb")
    };
    const hashByTab = {
        pl: "#profit-and-loss",
        bs: "#balance-sheet",
        tb: "#trial-balance"
    };
    const tabByHash = {
        "#profit-and-loss": "pl",
        "#balance-sheet": "bs",
        "#trial-balance": "tb"
    };

    function element(id) {
        return document.getElementById(id);
    }

    function setAmount(id, value, signed) {
        const target = element(id);
        if (!target) {
            return;
        }
        target.textContent = Fx.formatMoney(value);
        if (signed) {
            const number = Number(value);
            target.classList.toggle("is-positive", Number.isFinite(number) && number > 0);
            target.classList.toggle("is-negative", Number.isFinite(number) && number < 0);
        }
    }

    function typeLabel(value) {
        return String(value || "")
            .replace(/_/g, " ")
            .replace(/\b\w/g, function (letter) {
                return letter.toUpperCase();
            });
    }

    function accountCell(line) {
        const cell = document.createElement("td");
        const name = document.createElement("span");
        name.className = "transaction-name";
        name.textContent = line.account_name;
        const code = document.createElement("div");
        code.className = "transaction-reference";
        code.textContent = line.account_code;
        cell.append(name, code);
        return cell;
    }

    function moneyCell(value, strong) {
        const cell = document.createElement("td");
        cell.className = strong ? "finance-money is-strong" : "finance-money";
        cell.textContent = Fx.formatMoney(value);
        return cell;
    }

    function renderLines(bodyId, lines) {
        const body = element(bodyId);
        if (!body) {
            return;
        }
        body.replaceChildren();
        if (!lines || lines.length === 0) {
            Fx.emptyRow(body, 5, "No posted lines for this section.");
            return;
        }
        lines.forEach(function (line) {
            const row = document.createElement("tr");
            row.append(
                accountCell(line),
                (function () {
                    const cell = document.createElement("td");
                    cell.textContent = typeLabel(line.account_type);
                    return cell;
                })(),
                moneyCell(line.debit_total, false),
                moneyCell(line.credit_total, false),
                moneyCell(line.amount, true)
            );
            body.appendChild(row);
        });
    }

    function setBanner(id, text, isError) {
        const banner = element(id);
        const label = element(`${id}-text`);
        if (banner) {
            banner.classList.toggle("is-error", Boolean(isError));
        }
        if (label) {
            label.textContent = text;
        }
    }

    function setCheck(id, balanced) {
        const target = element(id);
        if (!target) {
            return;
        }
        target.textContent = balanced ? "Balanced" : "Out of balance";
        target.classList.toggle("is-positive", balanced);
        target.classList.toggle("is-negative", !balanced);
    }

    function applyProfitAndLoss(report) {
        setAmount("pl-revenue", report.revenue_total, false);
        setAmount("pl-cogs", report.cogs_total, false);
        setAmount("pl-gross", report.gross_profit, true);
        setAmount("pl-operating", report.operating_expense_total, false);
        setAmount("pl-net", report.net_profit, true);
        renderLines("pl-revenue-body", report.revenue_lines);
        renderLines("pl-cogs-body", report.cogs_lines);
        renderLines("pl-expense-body", report.operating_expense_lines);
    }

    function applyBalanceSheet(report) {
        setAmount("bs-assets", report.total_assets, false);
        setAmount("bs-liabilities", report.total_liabilities, false);
        setAmount("bs-equity", report.total_equity, false);
        setAmount("bs-earnings", report.current_earnings, true);
        setCheck("bs-check", report.is_balanced);
        setBanner(
            "bs-banner",
            report.is_balanced
                ? "Balance sheet is balanced: assets equal liabilities plus equity."
                : "Balance sheet is out of balance. Review the ledger before using these figures.",
            !report.is_balanced
        );
        renderLines("bs-assets-body", report.asset_lines);
        renderLines("bs-liabilities-body", report.liability_lines);
        renderLines("bs-equity-body", report.equity_lines);
    }

    function applyTrialBalance(report) {
        setAmount("tb-debits", report.total_debits, false);
        setAmount("tb-credits", report.total_credits, false);
        setCheck("tb-check", report.is_balanced);
        setBanner(
            "tb-banner",
            report.is_balanced
                ? "Trial balance is balanced: total debits equal total credits."
                : "Trial balance is out of balance. Review the ledger before using these figures.",
            !report.is_balanced
        );

        const body = element("tb-body");
        if (!body) {
            return;
        }
        body.replaceChildren();
        if (!report.rows || report.rows.length === 0) {
            Fx.emptyRow(body, 5, "No posted ledger lines yet.");
            return;
        }
        report.rows.forEach(function (row) {
            const tr = document.createElement("tr");
            tr.append(
                accountCell(row),
                (function () {
                    const cell = document.createElement("td");
                    cell.textContent = typeLabel(row.account_type);
                    return cell;
                })(),
                moneyCell(row.debit_total, false),
                moneyCell(row.credit_total, false),
                moneyCell(row.net_debit_balance, true)
            );
            body.appendChild(tr);
        });
    }

    async function loadProfitAndLoss() {
        const start = plStart ? plStart.value : "";
        const end = plEnd ? plEnd.value : "";
        if (!start || !end) {
            throw new Error("Choose both a period start and a period end.");
        }
        if (start > end) {
            throw new Error("The period start cannot be after the period end.");
        }
        const params = new URLSearchParams({ start: start, end: end });
        const report = await API.request(`/reports/profit-and-loss?${params.toString()}`);
        applyProfitAndLoss(report);
    }

    async function loadBalanceSheet() {
        const asOf = bsAsOf ? bsAsOf.value : "";
        if (!asOf) {
            throw new Error("Choose a balance-sheet date.");
        }
        const params = new URLSearchParams({ as_of: asOf });
        const report = await API.request(`/reports/balance-sheet?${params.toString()}`);
        applyBalanceSheet(report);
    }

    async function loadTrialBalance() {
        const report = await API.request("/reports/trial-balance");
        applyTrialBalance(report);
    }

    function setLoading(isLoading) {
        if (refreshButton) {
            refreshButton.disabled = isLoading;
            refreshButton.textContent = isLoading ? "Refreshing…" : "Refresh Reports";
        }
        [plRun, bsRun].forEach(function (button) {
            if (button) {
                button.disabled = isLoading;
            }
        });
    }

    async function loadAll() {
        setLoading(true);
        Fx.showStatus(statusElement, "Loading reports from the ledger…");
        const results = await Promise.allSettled([
            loadProfitAndLoss(),
            loadBalanceSheet(),
            loadTrialBalance()
        ]);
        const errors = results
            .filter(function (result) {
                return result.status === "rejected";
            })
            .map(function (result) {
                return result.reason;
            });

        const authError = errors.find(function (error) {
            return error && error.status === 401;
        });
        if (authError && Fx.handleAuthError(authError)) {
            return;
        }

        if (errors.length > 0) {
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(errors[0], "Some reports could not be loaded."),
                true
            );
        } else {
            Fx.showStatus(statusElement, "Reports refreshed from the ledger.");
        }
        setLoading(false);
    }

    async function runSingle(loader, button, busyText, idleText) {
        if (button) {
            button.disabled = true;
            button.textContent = busyText;
        }
        Fx.showStatus(statusElement, "Loading report…");
        try {
            await loader();
            Fx.showStatus(statusElement, "Report refreshed from the ledger.");
        } catch (error) {
            if (!Fx.handleAuthError(error)) {
                Fx.showStatus(
                    statusElement,
                    Fx.errorMessage(error, "The report could not be loaded."),
                    true
                );
            }
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = idleText;
            }
        }
    }

    function activateTab(key, updateHash) {
        const active = sections[key] ? key : "pl";
        tabButtons.forEach(function (button) {
            const selected = button.dataset.reportTab === active;
            button.classList.toggle("is-active", selected);
            button.setAttribute("aria-selected", String(selected));
            button.tabIndex = selected ? 0 : -1;
        });
        Object.keys(sections).forEach(function (sectionKey) {
            const section = sections[sectionKey];
            if (section) {
                section.hidden = sectionKey !== active;
            }
        });
        if (updateHash && window.location.hash !== hashByTab[active]) {
            window.history.replaceState(null, "", hashByTab[active]);
        }
    }

    tabButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            activateTab(button.dataset.reportTab, true);
        });
        button.addEventListener("keydown", function (event) {
            if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
                return;
            }
            event.preventDefault();
            const index = tabButtons.indexOf(button);
            const offset = event.key === "ArrowRight" ? 1 : -1;
            const next = tabButtons[(index + offset + tabButtons.length) % tabButtons.length];
            activateTab(next.dataset.reportTab, true);
            next.focus();
        });
    });

    window.addEventListener("hashchange", function () {
        const tab = tabByHash[window.location.hash];
        if (tab) {
            activateTab(tab, false);
        }
    });

    refreshButton?.addEventListener("click", loadAll);
    plRun?.addEventListener("click", function () {
        runSingle(loadProfitAndLoss, plRun, "Running…", "Run Report");
    });
    bsRun?.addEventListener("click", function () {
        runSingle(loadBalanceSheet, bsRun, "Running…", "Run Report");
    });

    if (plStart) plStart.value = Fx.monthStartIso();
    if (plEnd) plEnd.value = Fx.todayIso();
    if (bsAsOf) bsAsOf.value = Fx.todayIso();
    activateTab(tabByHash[window.location.hash] || "pl", false);

    Fx.loadMe()
        .then(loadAll)
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
