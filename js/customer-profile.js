(function () {
    "use strict";

    const API = window.FinexAPI;
    const profileName = document.getElementById("customer-profile-name");
    const profileMessage = document.getElementById("customer-profile-message");
    const profileCard = document.getElementById("customer-profile-card");
    const historyCard = document.getElementById("customer-history-card");
    const editLink = document.getElementById("customer-profile-edit");
    const historyStatus = document.getElementById("customer-history-status");
    const touchpointList = document.getElementById("customer-touchpoints");
    const historyRefresh = document.getElementById("customer-history-refresh");
    const touchpointForm = document.getElementById("customer-touchpoint-form");
    const touchpointKind = document.getElementById("touchpoint-kind");
    const touchpointSummary = document.getElementById("touchpoint-summary");
    const touchpointSubmit = document.getElementById("touchpoint-submit");
    const touchpointFormError = document.getElementById("touchpoint-form-error");
    const sidebar = document.getElementById("sidebar");
    const mobileMenuToggle = document.getElementById("mobile-menu-toggle");

    let customerId = "";
    let customer = null;
    let touchpointLoadInProgress = false;
    let touchpointSubmitInProgress = false;
    let noticeTimer = null;
    let redirectingForAuth = false;

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    }

    function showProfileMessage(message, isError) {
        if (!profileMessage) return;
        profileMessage.textContent = message;
        profileMessage.classList.toggle("is-error", Boolean(isError));
        profileMessage.hidden = !message;
    }

    function showTouchpointFormError(message) {
        if (!touchpointFormError) return;
        touchpointFormError.textContent = message;
        touchpointFormError.hidden = !message;
    }

    function showNotice(message, isError) {
        if (!profileMessage) return;
        window.clearTimeout(noticeTimer);
        profileMessage.textContent = message;
        profileMessage.classList.toggle("is-error", Boolean(isError));
        profileMessage.hidden = false;
        noticeTimer = window.setTimeout(function () {
            profileMessage.hidden = true;
            profileMessage.textContent = "";
        }, 6500);
    }

    function handleAuthError(error) {
        // A 403 is a role restriction, not an expired session. Leave the
        // profile visible and let the caller report the permission error.
        if (error && error.status === 401) {
            if (!redirectingForAuth) {
                redirectingForAuth = true;
                window.location.replace("../index.html");
            }
            return true;
        }
        return false;
    }

    function isValidCustomerId(value) {
        return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value || "");
    }

    function formatDate(value) {
        if (!value) return "Not available";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString();
    }

    function setDetailValue(container, value, type) {
        const element = document.getElementById(container);
        if (!element) return;
        element.replaceChildren();
        if (!value) {
            element.textContent = "Not provided";
            return;
        }

        if (type === "email") {
            const link = document.createElement("a");
            link.href = `mailto:${encodeURIComponent(value)}`;
            link.textContent = value;
            element.appendChild(link);
            return;
        }
        if (type === "phone") {
            const link = document.createElement("a");
            link.href = `tel:${encodeURIComponent(value)}`;
            link.textContent = value;
            element.appendChild(link);
            return;
        }
        element.textContent = value;
    }

    function renderCustomerProfile(record) {
        customer = record;
        const displayName = record.name || "Customer profile";
        profileName.textContent = displayName;
        setText("customer-profile-name-value", displayName);
        setDetailValue("customer-profile-email", record.email, "email");
        setDetailValue("customer-profile-phone", record.phone, "phone");
        setDetailValue("customer-profile-company", record.company, "text");
        setText("customer-profile-notes", record.notes || "Not provided");
        setText("customer-profile-tags", Array.isArray(record.tags) && record.tags.length
            ? record.tags.filter(function (tag) { return typeof tag === "string" && tag.trim(); }).join(", ") || "Not provided"
            : "Not provided");
        setText("customer-profile-source", record.source || "Not available");
        setText("customer-profile-created", formatDate(record.created_at));
        setText("customer-profile-updated", formatDate(record.updated_at));

        editLink.href = `customers.html?edit=${encodeURIComponent(record.id)}`;
        editLink.hidden = false;
        profileCard.hidden = false;
        historyCard.hidden = false;
    }

    function appendHistoryState(message) {
        touchpointList.replaceChildren();
        const item = document.createElement("li");
        item.className = "customer-touchpoint-empty";
        item.textContent = message;
        touchpointList.appendChild(item);
    }

    function renderTouchpoints(entries, total) {
        touchpointList.replaceChildren();
        if (!entries.length) {
            appendHistoryState("No interactions have been recorded for this customer yet.");
            historyStatus.textContent = "No interactions recorded.";
            return;
        }

        const chronologicalEntries = entries.slice().sort(function (left, right) {
            const leftTime = Date.parse(left.occurred_at);
            const rightTime = Date.parse(right.occurred_at);
            const leftValid = Number.isFinite(leftTime);
            const rightValid = Number.isFinite(rightTime);
            if (!leftValid && !rightValid) return 0;
            if (!leftValid) return 1;
            if (!rightValid) return -1;
            return leftTime - rightTime;
        });

        chronologicalEntries.forEach(function (entry) {
            const item = document.createElement("li");
            item.className = "customer-touchpoint";

            const header = document.createElement("header");
            const title = document.createElement("h3");
            title.textContent = entry.kind || "Interaction";
            const date = document.createElement("time");
            const dateValue = new Date(entry.occurred_at);
            if (Number.isNaN(dateValue.getTime())) {
                date.textContent = "Date unavailable";
            } else {
                date.dateTime = dateValue.toISOString();
                date.textContent = dateValue.toLocaleString();
            }
            header.append(title, date);

            const summary = document.createElement("p");
            summary.textContent = entry.summary || "No summary provided.";
            item.append(header, summary);

            if (entry.ref_type || entry.ref_id) {
                const reference = document.createElement("small");
                reference.className = "customer-touchpoint-reference";
                reference.textContent = [entry.ref_type, entry.ref_id].filter(Boolean).join(" · ");
                item.appendChild(reference);
            }
            touchpointList.appendChild(item);
        });

        const loadedCount = entries.length;
        const totalCount = Number.isFinite(total) ? total : loadedCount;
        historyStatus.textContent = totalCount > loadedCount
            ? `Showing the most recent ${loadedCount} of ${totalCount} interactions, ordered oldest to newest.`
            : `${loadedCount} interaction${loadedCount === 1 ? "" : "s"}, ordered oldest to newest.`;
    }

    async function loadTouchpoints(announceError) {
        if (!customerId || touchpointLoadInProgress) return false;
        touchpointLoadInProgress = true;
        historyRefresh.disabled = true;
        historyStatus.textContent = "Loading interaction history…";
        historyStatus.classList.remove("is-error");
        appendHistoryState("Loading interactions…");

        try {
            const page = await API.request(`/customers/${encodeURIComponent(customerId)}/touchpoints?limit=100`);
            if (!page || !Array.isArray(page.items)) {
                throw new Error("The interaction history response was not in the expected format.");
            }
            renderTouchpoints(page.items, page.total);
            historyStatus.classList.remove("is-error");
            return true;
        } catch (error) {
            if (handleAuthError(error)) return false;
            historyStatus.textContent = "Interaction history could not be loaded.";
            historyStatus.classList.add("is-error");
            appendHistoryState(error.status === 404
                ? "This customer's interaction history is unavailable."
                : "Could not load interaction history. Use Refresh history to try again.");
            if (announceError) showNotice(error.message || "Could not load interaction history.", true);
            return false;
        } finally {
            historyRefresh.disabled = false;
            touchpointLoadInProgress = false;
        }
    }

    async function loadCustomer() {
        if (!API) {
            showProfileMessage("Customer services are unavailable right now.", true);
            return;
        }
        showProfileMessage("Loading customer profile…", false);

        try {
            const record = await API.request(`/customers/${encodeURIComponent(customerId)}`);
            if (!record || String(record.id).toLowerCase() !== customerId) {
                showProfileMessage("The requested customer profile could not be identified.", true);
                return;
            }
            renderCustomerProfile(record);
            showProfileMessage("", false);
            await loadTouchpoints(false);
        } catch (error) {
            if (handleAuthError(error)) return;
            showProfileMessage(error.status === 404
                ? "This customer could not be found or is no longer available."
                : (error.message || "Could not load this customer profile."), true);
        }
    }

    function setTouchpointBusy(isBusy) {
        touchpointSubmitInProgress = isBusy;
        touchpointSubmit.disabled = isBusy;
        historyRefresh.disabled = isBusy || touchpointLoadInProgress;
        touchpointSubmit.textContent = isBusy ? "Recording…" : "Record Interaction";
    }

    async function submitTouchpoint(event) {
        event.preventDefault();
        if (touchpointSubmitInProgress || !customer) return;

        const kind = touchpointKind.value.trim();
        const summary = touchpointSummary.value.trim();
        touchpointKind.value = kind;
        touchpointSummary.value = summary;
        showTouchpointFormError("");

        if (!kind || !summary) {
            showTouchpointFormError("Enter an interaction type and summary.");
            if (!kind) touchpointKind.focus();
            else touchpointSummary.focus();
            return;
        }
        if (!touchpointForm.reportValidity()) return;

        setTouchpointBusy(true);
        try {
            await API.request(`/customers/${encodeURIComponent(customerId)}/touchpoints`, {
                method: "POST",
                body: JSON.stringify({ kind: kind, summary: summary })
            });
            touchpointForm.reset();
            showTouchpointFormError("");
            const historyLoaded = await loadTouchpoints(false);
            showNotice(historyLoaded
                ? "Interaction recorded."
                : "Interaction recorded, but the history could not be refreshed. Use Refresh history to retry.", !historyLoaded);
        } catch (error) {
            if (!handleAuthError(error)) {
                showTouchpointFormError(error.message || "Could not record this interaction.");
            }
        } finally {
            setTouchpointBusy(false);
        }
    }

    function bindMobileSidebar() {
        if (!sidebar || !mobileMenuToggle) return;

        function setOpen(isOpen, restoreFocus) {
            sidebar.classList.toggle("mobile-open", isOpen);
            mobileMenuToggle.setAttribute("aria-expanded", String(isOpen));
            mobileMenuToggle.setAttribute("aria-label", isOpen ? "Close navigation menu" : "Open navigation menu");
            if (restoreFocus) mobileMenuToggle.focus();
        }

        mobileMenuToggle.addEventListener("click", function () {
            const isOpen = mobileMenuToggle.getAttribute("aria-expanded") !== "true";
            setOpen(isOpen, false);
            if (isOpen) sidebar.querySelector(".sidebar-nav .nav-link")?.focus();
        });
        sidebar.addEventListener("click", function (event) {
            if (event.target.closest(".nav-link") && sidebar.classList.contains("mobile-open")) {
                setOpen(false, false);
            }
        });
        document.addEventListener("click", function (event) {
            if (sidebar.classList.contains("mobile-open") && !sidebar.contains(event.target) && !mobileMenuToggle.contains(event.target)) {
                setOpen(false, false);
            }
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && sidebar.classList.contains("mobile-open")) setOpen(false, true);
        });
        window.addEventListener("resize", function () {
            if (!window.matchMedia("(max-width: 650px)").matches) setOpen(false, false);
        }, { passive: true });
    }

    const requestedId = new URLSearchParams(window.location.search).get("id");
    const hasValidRequestedId = isValidCustomerId(requestedId);
    if (hasValidRequestedId) {
        customerId = requestedId.toLowerCase();
        editLink.href = `customers.html?edit=${encodeURIComponent(customerId)}`;
        editLink.hidden = false;
    }

    historyRefresh.addEventListener("click", function () {
        loadTouchpoints(true);
    });
    touchpointForm.addEventListener("submit", submitTouchpoint);
    touchpointKind.addEventListener("input", function () { showTouchpointFormError(""); });
    touchpointSummary.addEventListener("input", function () { showTouchpointFormError(""); });
    bindMobileSidebar();

    if (!hasValidRequestedId) {
        showProfileMessage("A valid customer ID is required to open this profile. Return to Customers and select a profile.", true);
        historyCard.hidden = true;
        profileCard.hidden = true;
        editLink.hidden = true;
        if (API) {
            API.request("/auth/me").catch(function (error) {
                handleAuthError(error);
            });
        }
        return;
    }

    loadCustomer();
})();
