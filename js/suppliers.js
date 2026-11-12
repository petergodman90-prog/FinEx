/* Live supplier directory: search, list, and create payable counterparties. */
(function () {
    "use strict";

    const API = window.FinexAPI;
    const Fx = window.FinexFinance;
    if (!API || !Fx) {
        return;
    }

    const statusElement = document.getElementById("suppliers-status");
    const refreshButton = document.getElementById("suppliers-refresh");
    const addButton = document.getElementById("supplier-add-open");
    const searchInput = document.getElementById("supplier-search");
    const filterInput = document.getElementById("supplier-filter");
    const body = document.getElementById("suppliers-body");

    const dialog = document.getElementById("supplier-dialog");
    const form = document.getElementById("supplier-form");
    const dialogClose = document.getElementById("supplier-dialog-close");
    const cancelButton = document.getElementById("supplier-cancel");
    const errorElement = document.getElementById("supplier-error");
    const submitButton = document.getElementById("supplier-submit");
    const nameInput = document.getElementById("supplier-name");
    const codeInput = document.getElementById("supplier-code");

    let suppliers = [];
    let loading = false;
    let pendingReload = false;
    let searchTimer = null;
    let canWrite = false;

    function setMetric(id, value) {
        Fx.setText(id, Number(value || 0).toLocaleString());
    }

    function buildRow(supplier) {
        const row = document.createElement("tr");

        const nameCell = document.createElement("td");
        const name = document.createElement("span");
        name.className = "transaction-name";
        name.textContent = supplier.name;
        const reference = document.createElement("div");
        reference.className = "transaction-reference";
        reference.textContent = supplier.id.slice(0, 8).toUpperCase();
        nameCell.append(name, reference);

        const codeCell = document.createElement("td");
        codeCell.textContent = supplier.code || "—";

        const statusCell = document.createElement("td");
        const pill = document.createElement("span");
        const active = supplier.is_active !== false;
        pill.className = `status-pill ${active ? "status-applied" : "status-draft"}`;
        pill.textContent = active ? "Active" : "Inactive";
        statusCell.appendChild(pill);

        row.append(nameCell, codeCell, statusCell);
        return row;
    }

    function renderSuppliers() {
        body.replaceChildren();
        if (suppliers.length === 0) {
            const hasSearch = Boolean(searchInput.value.trim());
            Fx.emptyRow(
                body,
                3,
                hasSearch
                    ? "No suppliers match your search."
                    : "No suppliers have been added yet."
            );
            return;
        }
        suppliers.forEach(function (supplier) {
            body.appendChild(buildRow(supplier));
        });
    }

    function openSupplierDialog() {
        if (!canWrite) {
            return;
        }
        form.reset();
        nameInput.value = "";
        codeInput.value = "";
        errorElement.hidden = true;
        errorElement.textContent = "";
        submitButton.disabled = false;
        submitButton.textContent = "Add supplier";
        Fx.openDialog(dialog);
        nameInput.focus();
    }

    function closeSupplierDialog() {
        Fx.closeDialog(dialog);
    }

    function showFormError(text) {
        errorElement.textContent = text;
        errorElement.hidden = false;
    }

    async function submitSupplier(event) {
        event.preventDefault();
        const name = nameInput.value.trim();
        const code = codeInput.value.trim();
        if (!name) {
            return showFormError("Enter the supplier name.");
        }

        errorElement.hidden = true;
        submitButton.disabled = true;
        submitButton.textContent = "Adding…";
        try {
            const payload = { name: name };
            if (code) {
                payload.code = code;
            }
            await API.request("/suppliers", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            closeSupplierDialog();
            await loadAll();
            Fx.showStatus(
                statusElement,
                `Supplier “${name}” added.`
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            showFormError(Fx.errorMessage(error, "The supplier could not be added."));
            submitButton.disabled = false;
            submitButton.textContent = "Add supplier";
        }
    }

    async function loadAll() {
        if (loading) {
            pendingReload = true;
            return;
        }
        loading = true;
        refreshButton.disabled = true;
        refreshButton.textContent = "Refreshing…";
        Fx.showStatus(statusElement, "Loading supplier directory…");

        try {
            await Fx.loadMe();
            canWrite = Fx.state.canWrite;
            addButton.hidden = !canWrite;

            const query = searchInput.value.trim();
            const includeInactive = filterInput.value === "all";
            const listParams = new URLSearchParams({
                limit: "200",
                include_inactive: String(includeInactive)
            });
            if (query) {
                listParams.set("q", query);
            }

            const allParams = new URLSearchParams({
                limit: "1",
                include_inactive: "true"
            });
            const activeParams = new URLSearchParams({
                limit: "1",
                include_inactive: "false"
            });
            if (query) {
                allParams.set("q", query);
                activeParams.set("q", query);
            }

            const [page, allPage, activePage] = await Promise.all([
                API.request(`/suppliers?${listParams.toString()}`),
                API.request(`/suppliers?${allParams.toString()}`),
                API.request(`/suppliers?${activeParams.toString()}`)
            ]);

            suppliers = Array.isArray(page.items) ? page.items : [];
            const activeTotal = Number(activePage.total || 0);
            const allTotal = Number(allPage.total || 0);
            setMetric("supplier-total", page.total);
            setMetric("supplier-active", activeTotal);
            setMetric("supplier-inactive", Math.max(0, allTotal - activeTotal));
            renderSuppliers();
            Fx.showStatus(
                statusElement,
                suppliers.length === 0
                    ? "No suppliers match the current view."
                    : `Showing ${suppliers.length} of ${Number(page.total || 0)} matching supplier${Number(page.total || 0) === 1 ? "" : "s"}.`
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            suppliers = [];
            renderSuppliers();
            setMetric("supplier-total", 0);
            setMetric("supplier-active", 0);
            setMetric("supplier-inactive", 0);
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "The supplier directory could not be loaded."),
                true
            );
        } finally {
            loading = false;
            refreshButton.disabled = false;
            refreshButton.textContent = "Refresh";
            if (pendingReload) {
                pendingReload = false;
                loadAll();
            }
        }
    }

    addButton?.addEventListener("click", openSupplierDialog);
    refreshButton?.addEventListener("click", loadAll);
    filterInput?.addEventListener("change", loadAll);
    searchInput?.addEventListener("input", function () {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(loadAll, 300);
    });
    dialogClose?.addEventListener("click", closeSupplierDialog);
    cancelButton?.addEventListener("click", closeSupplierDialog);
    form?.addEventListener("submit", submitSupplier);
    dialog?.addEventListener("click", function (event) {
        if (event.target === dialog) {
            closeSupplierDialog();
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
            return loadAll();
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
