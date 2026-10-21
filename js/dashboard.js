// =========================================
// FINEX SIDEBAR TOGGLE
// =========================================

const dashboardLayout = document.querySelector(".dashboard-layout");
const sidebarToggle = document.getElementById("sidebar-toggle");

if (dashboardLayout && sidebarToggle) {
    const savedState = localStorage.getItem("finex-sidebar-collapsed");

    // Restore the user's previous preference.
    if (savedState === "true") {
        dashboardLayout.classList.add("sidebar-collapsed");
    }

    function updateSidebarButton() {
        const isCollapsed =
            dashboardLayout.classList.contains("sidebar-collapsed");

        sidebarToggle.setAttribute(
            "aria-expanded",
            String(!isCollapsed)
        );

        sidebarToggle.setAttribute(
            "aria-label",
            isCollapsed ? "Expand sidebar" : "Collapse sidebar"
        );

        sidebarToggle.setAttribute(
            "title",
            isCollapsed ? "Expand sidebar" : "Collapse sidebar"
        );

        document.querySelectorAll(".nav-link").forEach((link) => {
            const label = link.querySelector(".nav-label");

            if (!label) {
                return;
            }

            if (isCollapsed) {
                link.setAttribute("title", label.textContent.trim());
            } else {
                link.removeAttribute("title");
            }
        });
    }

    sidebarToggle.addEventListener("click", () => {
        dashboardLayout.classList.toggle("sidebar-collapsed");

        const isCollapsed =
            dashboardLayout.classList.contains("sidebar-collapsed");

        localStorage.setItem(
            "finex-sidebar-collapsed",
            String(isCollapsed)
        );

        sidebarToggle.classList.remove("is-toggling");
        void sidebarToggle.offsetWidth;
        sidebarToggle.classList.add("is-toggling");

        window.setTimeout(() => {
            sidebarToggle.classList.remove("is-toggling");
        }, 520);

        updateSidebarButton();
    });

    updateSidebarButton();
}
// =========================================
// FINEX SIDEBAR ACTIVE NAVIGATION
// =========================================

const navLinks = document.querySelectorAll(
    ".sidebar-nav .nav-link, .sidebar-bottom .nav-link:not(.logout-link)"
);

navLinks.forEach((link) => {
    link.addEventListener("click", () => {
        // Remove the active effect from every navigation link
        navLinks.forEach((navLink) => {
            navLink.classList.remove("active");
        });

        // Apply the liquid-glass effect to the clicked link
        link.classList.add("active");
    });
});
// =========================================
// FINEX MOVING GLASS SELECTOR - SUMMARY CARDS
// =========================================

const summaryGrid = document.querySelector(".summary-grid");

if (summaryGrid) {
    const summaryCards = summaryGrid.querySelectorAll(".summary-card");

    if (summaryCards.length > 0) {
        // Create the moving glass element (hidden until a card is clicked).
        const glassSelector = document.createElement("div");
        glassSelector.className = "summary-selector";
        glassSelector.setAttribute("aria-hidden", "true");
        glassSelector.style.opacity = "0";

        summaryGrid.prepend(glassSelector);

        function moveGlassSelector(card) {
            const gridRect = summaryGrid.getBoundingClientRect();
            const cardRect = card.getBoundingClientRect();

            glassSelector.style.width = `${cardRect.width}px`;
            glassSelector.style.height = `${cardRect.height}px`;

            glassSelector.style.transform =
                `translate(${cardRect.left - gridRect.left}px, ` +
                `${cardRect.top - gridRect.top}px)`;

            glassSelector.style.opacity = "1";

            summaryCards.forEach((item) => {
                item.classList.toggle("is-selected", item === card);
            });
        }

        // Make the cards keyboard accessible.
        summaryCards.forEach((card) => {
            card.setAttribute("tabindex", "0");
            card.setAttribute("role", "button");

            card.addEventListener("click", () => {
                moveGlassSelector(card);
            });

            card.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    moveGlassSelector(card);
                }
            });
        });

        // Keep the selector aligned when the layout changes.
        window.addEventListener("resize", () => {
            const selectedCard =
                summaryGrid.querySelector(".summary-card.is-selected");

            if (selectedCard) {
                moveGlassSelector(selectedCard);
            }
        });
    }
}
// =========================================
// SOCIAL MEDIA DROPDOWN
// =========================================

const socialMediaToggle = document.getElementById("social-media-toggle");
const socialMediaSubmenu = document.getElementById("social-media-submenu");

if (socialMediaToggle && socialMediaSubmenu) {
    socialMediaToggle.addEventListener("click", () => {
        const isExpanded =
            socialMediaToggle.getAttribute("aria-expanded") === "true";

        socialMediaToggle.setAttribute("aria-expanded", String(!isExpanded));
        socialMediaSubmenu.hidden = isExpanded;
    });
}
const productsStockToggle = document.getElementById("products-stock-toggle");
const productsStockSubmenu = document.getElementById("products-stock-submenu");

if (productsStockToggle && productsStockSubmenu) {
    productsStockToggle.addEventListener("click", () => {
        const isExpanded =
            productsStockToggle.getAttribute("aria-expanded") === "true";

        productsStockToggle.setAttribute(
            "aria-expanded",
            String(!isExpanded)
        );

        productsStockSubmenu.hidden = isExpanded;
    });
}
// =========================================
// FINEX LIGHT MODE SETTING
// =========================================

const themeToggle = document.getElementById("light-mode-toggle");

function applyTheme(theme, persist) {
    const root = document.documentElement;
    const isLight = theme === "light";

    root.dataset.theme = isLight ? "light" : "dark";

    if (themeToggle) {
        themeToggle.checked = isLight;
        themeToggle.setAttribute("aria-checked", String(isLight));
    }

    if (persist) {
        try {
            localStorage.setItem("finex-theme", root.dataset.theme);
        } catch (error) {
            // Storage may be unavailable in private browsing.
        }
    }
}

if (themeToggle) {
    applyTheme(document.documentElement.dataset.theme, false);

    themeToggle.addEventListener("change", function () {
        applyTheme(this.checked ? "light" : "dark", true);
    });
}

// =========================================
// LIVE API DATA (auth guard, metrics, CRM, proposals)
// =========================================

(function () {
    "use strict";

    const API = window.FinexAPI;
    if (!API) {
        return;
    }

    const messageBox = document.getElementById("dashboard-message");
    let messageTimer = null;

    function showMessage(text, kind) {
        if (!messageBox) {
            return;
        }
        messageBox.textContent = text;
        messageBox.classList.toggle("is-error", kind === "error");
        messageBox.hidden = false;
        window.clearTimeout(messageTimer);
        messageTimer = window.setTimeout(function () {
            messageBox.hidden = true;
        }, kind === "error" ? 7000 : 3500);
    }

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    }

    function formatAmount(amount, currency) {
        if (amount === null || amount === undefined || amount === "") {
            return "—";
        }
        const value = Number(amount);
        if (Number.isNaN(value)) {
            return "—";
        }
        return `${value.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        })} ${currency || "USD"}`;
    }

    function formatDate(value) {
        if (!value) {
            return "—";
        }
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
    }

    function emptyRow(body, columns, textText) {
        body.replaceChildren();
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = columns;
        cell.className = "live-empty";
        cell.textContent = textText;
        row.appendChild(cell);
        body.appendChild(row);
    }

    function handleAuthError(error) {
        if (error && (error.status === 401 || error.status === 403)) {
            window.location.replace("../index.html");
            return true;
        }
        return false;
    }

    async function loadMe() {
        const me = await API.request("/auth/me");
        const firstName = (me.user.full_name || "there").split(/\s+/)[0];
        setText("profile-name", me.user.full_name || "Business Owner");

        const welcome = document.querySelector(".welcome-section h2");
        if (welcome) {
            welcome.textContent = `Welcome back, ${firstName}`;
        }

        const membership = (me.memberships || []).find(function (item) {
            return item.org_id === me.active_org_id;
        });
        const orgName = membership ? membership.org_name : "Workspace";
        const breadcrumb = document.querySelector(".breadcrumb");
        if (breadcrumb) {
            breadcrumb.textContent = `${orgName} /`;
        }
        const description = document.querySelector(".welcome-description");
        if (description) {
            description.textContent = `Here's what's happening at ${orgName} today.`;
        }
    }

    async function loadMetrics() {
        const metrics = await API.request("/dashboard/metrics");
        setText("live-customers", metrics.customers_total);
        setText("live-new-customers", metrics.customers_new_30d);
        setText("live-touchpoints", metrics.touchpoints_30d);
        setText("live-pending-proposals", metrics.proposals_pending);
        setText(
            "live-pending-amount",
            metrics.proposals_pending_amount
                ? `${formatAmount(metrics.proposals_pending_amount, "USD")} awaiting approval`
                : "Awaiting approval"
        );

        // Financial cards depend on ledger posting, which is not exposed yet.
        ["total-revenue", "total-orders", "total-expenses", "net-profit",
         "snapshot-revenue", "snapshot-expenses", "snapshot-profit",
         "chart-total"].forEach(function (id) {
            setText(id, "—");
        });
    }

    async function deleteCustomer(customer) {
        if (!window.confirm(`Delete ${customer.name}?`)) {
            return;
        }
        try {
            await API.request(`/customers/${customer.id}`, { method: "DELETE" });
            showMessage("Customer deleted.");
            await Promise.all([loadCustomers(), loadMetrics()]);
        } catch (error) {
            if (!handleAuthError(error)) {
                showMessage(error.message, "error");
            }
        }
    }

    async function loadCustomers() {
        const body = document.getElementById("customers-body");
        if (!body) {
            return;
        }
        const page = await API.request("/customers?limit=50");
        if (!page.items || page.items.length === 0) {
            emptyRow(body, 4, "No customers yet. Add your first one above.");
            return;
        }

        body.replaceChildren();
        page.items.forEach(function (customer) {
            const row = document.createElement("tr");

            const nameCell = document.createElement("td");
            const name = document.createElement("strong");
            name.textContent = customer.name;
            nameCell.appendChild(name);
            if (customer.company) {
                const company = document.createElement("div");
                company.className = "card-caption";
                company.textContent = customer.company;
                nameCell.appendChild(company);
            }

            const contactCell = document.createElement("td");
            contactCell.textContent = customer.email || customer.phone || "—";

            const createdCell = document.createElement("td");
            createdCell.textContent = formatDate(customer.created_at);

            const actionCell = document.createElement("td");
            const actions = document.createElement("div");
            actions.className = "live-table-actions";
            const deleteButton = document.createElement("button");
            deleteButton.type = "button";
            deleteButton.textContent = "Delete";
            deleteButton.addEventListener("click", function () {
                deleteCustomer(customer);
            });
            actions.appendChild(deleteButton);
            actionCell.appendChild(actions);

            row.append(nameCell, contactCell, createdCell, actionCell);
            body.appendChild(row);
        });
    }

    async function decideProposal(proposal, decision) {
        try {
            await API.request(`/proposals/${proposal.id}/${decision}`, {
                method: "POST",
                body: JSON.stringify({ note: "" })
            });
            showMessage(`Proposal ${decision === "approve" ? "approved" : "rejected"}.`);
            await Promise.all([loadProposals(), loadMetrics()]);
        } catch (error) {
            if (!handleAuthError(error)) {
                showMessage(error.message, "error");
            }
        }
    }

    async function loadProposals() {
        const body = document.getElementById("proposals-body");
        if (!body) {
            return;
        }
        const page = await API.request("/proposals?limit=50");
        if (!page.items || page.items.length === 0) {
            emptyRow(body, 4, "No proposals yet. Create one above.");
            return;
        }

        body.replaceChildren();
        page.items.forEach(function (proposal) {
            const row = document.createElement("tr");

            const titleCell = document.createElement("td");
            const title = document.createElement("strong");
            title.textContent = proposal.title;
            titleCell.appendChild(title);
            const type = document.createElement("div");
            type.className = "card-caption";
            type.textContent = proposal.proposal_type;
            titleCell.appendChild(type);

            const amountCell = document.createElement("td");
            amountCell.textContent = formatAmount(proposal.amount, proposal.currency);

            const statusCell = document.createElement("td");
            const pill = document.createElement("span");
            pill.className = `status-pill status-${proposal.status}`;
            pill.textContent = proposal.status;
            statusCell.appendChild(pill);

            const actionCell = document.createElement("td");
            if (proposal.status === "pending") {
                const actions = document.createElement("div");
                actions.className = "live-table-actions";

                const approveButton = document.createElement("button");
                approveButton.type = "button";
                approveButton.textContent = "Approve";
                approveButton.addEventListener("click", function () {
                    decideProposal(proposal, "approve");
                });

                const rejectButton = document.createElement("button");
                rejectButton.type = "button";
                rejectButton.textContent = "Reject";
                rejectButton.addEventListener("click", function () {
                    decideProposal(proposal, "reject");
                });

                actions.append(approveButton, rejectButton);
                actionCell.appendChild(actions);
            } else {
                actionCell.textContent = "—";
            }

            row.append(titleCell, amountCell, statusCell, actionCell);
            body.appendChild(row);
        });
    }

    async function loadAll() {
        try {
            await loadMe();
            await Promise.all([loadMetrics(), loadCustomers(), loadProposals()]);
        } catch (error) {
            if (!handleAuthError(error)) {
                showMessage(error.message || "Could not load workspace data.", "error");
            }
        }
    }

    const customerForm = document.getElementById("customer-form");
    if (customerForm) {
        customerForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            const nameInput = document.getElementById("customer-name");
            const emailInput = document.getElementById("customer-email");
            const phoneInput = document.getElementById("customer-phone");
            const button = customerForm.querySelector('button[type="submit"]');

            button.disabled = true;
            try {
                await API.request("/customers", {
                    method: "POST",
                    body: JSON.stringify({
                        name: nameInput.value.trim(),
                        email: emailInput.value.trim() || null,
                        phone: phoneInput.value.trim() || null
                    })
                });
                customerForm.reset();
                showMessage("Customer created.");
                await Promise.all([loadCustomers(), loadMetrics()]);
            } catch (error) {
                if (!handleAuthError(error)) {
                    showMessage(error.message, "error");
                }
            } finally {
                button.disabled = false;
            }
        });
    }

    const proposalForm = document.getElementById("proposal-form");
    if (proposalForm) {
        proposalForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            const titleInput = document.getElementById("proposal-title");
            const amountInput = document.getElementById("proposal-amount");
            const typeInput = document.getElementById("proposal-type");
            const button = proposalForm.querySelector('button[type="submit"]');
            const amount = amountInput.value.trim();

            button.disabled = true;
            try {
                await API.request("/proposals", {
                    method: "POST",
                    body: JSON.stringify({
                        proposal_type: typeInput.value,
                        title: titleInput.value.trim(),
                        amount: amount ? Number(amount) : null,
                        currency: amount ? "USD" : null
                    })
                });
                proposalForm.reset();
                showMessage("Proposal created.");
                await Promise.all([loadProposals(), loadMetrics()]);
            } catch (error) {
                if (!handleAuthError(error)) {
                    showMessage(error.message, "error");
                }
            } finally {
                button.disabled = false;
            }
        });
    }

    async function refreshPanel(loader) {
        try {
            await loader();
        } catch (error) {
            if (!handleAuthError(error)) {
                showMessage(error.message || "Could not refresh data.", "error");
            }
        }
    }

    const refreshCustomers = document.getElementById("refresh-customers");
    if (refreshCustomers) {
        refreshCustomers.addEventListener("click", function () {
            refreshPanel(loadCustomers);
        });
    }

    const refreshProposals = document.getElementById("refresh-proposals");
    if (refreshProposals) {
        refreshProposals.addEventListener("click", function () {
            refreshPanel(loadProposals);
        });
    }

    const logoutLink = document.getElementById("logout-link");
    if (logoutLink) {
        logoutLink.addEventListener("click", async function (event) {
            event.preventDefault();
            try {
                await API.request("/auth/logout", { method: "POST" });
            } catch (error) {
                // Clear local navigation even if the session already expired.
            } finally {
                window.location.assign("../index.html");
            }
        });
    }

    const dateElement = document.getElementById("current-date");
    if (dateElement) {
        dateElement.textContent = new Date().toLocaleDateString(undefined, {
            weekday: "short",
            day: "numeric",
            month: "short",
            year: "numeric"
        });
    }

    loadAll();
})();
