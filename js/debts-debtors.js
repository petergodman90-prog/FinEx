/* Feature-only interactions. No financial API calls or browser-side balances. */
(function () {
    "use strict";

    const page = document.querySelector(".debts-debtors-page");
    if (!page || page.dataset.debtsInitialized === "true") {
        return;
    }

    const tabs = page.querySelector("[data-debt-section-nav]");
    const tableBody = page.querySelector("#debt-records-body");
    const searchInput = page.querySelector("#debt-search");
    const statusFilter = page.querySelector("#debt-status-filter");
    const emptyState = page.querySelector("#debt-empty-state");

    if (!tabs || !tableBody || !searchInput || !statusFilter || !emptyState) {
        return;
    }

    page.dataset.debtsInitialized = "true";

    const tabLinks = Array.from(tabs.querySelectorAll("a[href^='#']"));
    const recordRows = Array.from(tableBody.querySelectorAll("tr[data-debt-record]"));

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

    function filterRecords() {
        const query = searchInput.value.trim().toLocaleLowerCase();
        const selectedStatus = statusFilter.value;
        let visibleCount = 0;

        recordRows.forEach((row) => {
            const matchesSearch = !query || row.textContent.toLocaleLowerCase().includes(query);
            const matchesStatus = selectedStatus === "all" || row.dataset.status === selectedStatus;
            const isVisible = matchesSearch && matchesStatus;

            row.hidden = !isVisible;
            if (isVisible) visibleCount += 1;
        });

        emptyState.hidden = visibleCount > 0;
        if (visibleCount > 0) {
            emptyState.querySelector("td").textContent = "";
            return;
        }

        const hasFilters = Boolean(query) || selectedStatus !== "all";
        emptyState.querySelector("td").textContent = hasFilters
            ? "No debt records match your search and filters. Financial records will appear here when the backend integration is available."
            : "Financial records will appear here when the relevant backend integration is available.";
    }

    tabs.addEventListener("click", (event) => {
        const link = event.target.closest("a[href^='#']");
        if (!link || !tabs.contains(link)) return;
        updateActiveTab();
    });

    window.addEventListener("hashchange", updateActiveTab);
    searchInput.addEventListener("input", filterRecords);
    statusFilter.addEventListener("change", filterRecords);

    updateActiveTab();
    filterRecords();
})();
