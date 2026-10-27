(function () {
    "use strict";

    const API = window.FinexAPI;
    const layout = document.querySelector(".dashboard-layout");
    const sidebar = document.getElementById("sidebar");
    const mobileMenuToggle = document.getElementById("mobile-menu-toggle");
    const customerDialog = document.getElementById("customer-dialog");
    const customerForm = document.getElementById("customer-form");
    const customerNameInput = document.getElementById("customer-name");
    const customerEmailInput = document.getElementById("customer-email");
    const customerPhoneInput = document.getElementById("customer-phone");
    const customerSubmit = document.getElementById("customer-submit");
    const customerFormError = document.getElementById("customer-form-error");
    const customerPhoneError = document.getElementById("customer-phone-error");
    const customerBody = document.getElementById("customers-body");
    const customerSearch = document.getElementById("customer-search");
    const customerSort = document.getElementById("customer-sort");
    const customerResultsCount = document.getElementById("customers-results-count");
    const previousPageButton = document.getElementById("customers-previous-page");
    const nextPageButton = document.getElementById("customers-next-page");
    const pageStatus = document.getElementById("customers-page-status");
    const customerMessage = document.getElementById("customers-message");
    const refreshButton = document.getElementById("refresh-customers");

    const CUSTOMER_PAGE_SIZE = 50;
    const SEARCH_DEBOUNCE_MS = 300;
    let customers = [];
    let customerTotal = 0;
    let customerOffset = 0;
    let customerRequestSequence = 0;
    let searchDebounceTimer = null;
    let customerLoadState = "loading";
    let submitInProgress = false;
    let dialogMode = "create";
    let editingCustomer = null;
    let phoneValidationAttempted = false;
    let messageTimer = null;
    let redirectingForAuth = false;

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    }

    function showMessage(message, isError) {
        if (!customerMessage) return;
        window.clearTimeout(messageTimer);
        customerMessage.textContent = message;
        customerMessage.classList.toggle("is-error", Boolean(isError));
        customerMessage.hidden = false;
        messageTimer = window.setTimeout(function () {
            customerMessage.hidden = true;
            customerMessage.textContent = "";
        }, 6000);
    }

    function formatCustomerRequestError(error, fallback) {
        const detail = error && typeof error.message === "string" && error.message.trim()
            ? error.message.trim()
            : fallback;
        const status = error && Number.isInteger(error.status) ? error.status : 0;
        if (!status || detail.includes(`HTTP ${status}`) || detail.includes(`(${status})`)) {
            return detail;
        }
        return `${detail} (HTTP ${status})`;
    }

    function handleAuthError(error) {
        if (error && (error.status === 401 || error.status === 403)) {
            if (!redirectingForAuth) {
                redirectingForAuth = true;
                window.location.replace("../index.html");
            }
            return true;
        }
        return false;
    }

    function formatDate(value) {
        if (!value) return "—";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
    }

    function isValidCustomerPhone(phone) {
        if (!phone) return true;
        if (!/^\+?[0-9](?:[0-9\s().-]*[0-9])?$/.test(phone)) return false;

        const digitCount = phone.replace(/\D/g, "").length;
        if (digitCount < 7 || digitCount > 15) return false;

        let parenthesisDepth = 0;
        let digitsInParentheses = 0;
        for (const character of phone) {
            if (character === "(") {
                if (parenthesisDepth > 0) return false;
                parenthesisDepth += 1;
                digitsInParentheses = 0;
            } else if (character === ")") {
                parenthesisDepth -= 1;
                if (parenthesisDepth < 0 || digitsInParentheses === 0) return false;
            } else if (parenthesisDepth > 0 && /[0-9]/.test(character)) {
                digitsInParentheses += 1;
            }
        }
        return parenthesisDepth === 0;
    }

    function isValidCustomerId(customerId) {
        return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(customerId || "");
    }

    function appendStateRow(message) {
        if (!customerBody) return;
        const row = document.createElement("tr");
        row.className = "customers-state-row";
        const cell = document.createElement("td");
        cell.className = "customers-state-cell";
        cell.colSpan = 5;
        cell.textContent = message;
        row.appendChild(cell);
        customerBody.replaceChildren(row);
    }

    function renderCustomers() {
        if (!customerBody) return;

        if (customerLoadState === "loading") {
            appendStateRow("Loading customers…");
            if (customerResultsCount) customerResultsCount.textContent = "Loading customer records…";
            return;
        }

        if (customerLoadState === "error") {
            appendStateRow("Customer records could not be loaded. Use Refresh to try again.");
            if (customerResultsCount) customerResultsCount.textContent = "Customer list unavailable";
            return;
        }

        const query = customerSearch ? customerSearch.value.trim().toLocaleLowerCase() : "";
        const visibleCustomers = customers.filter(function (customer) {
            const searchableText = [customer.name, customer.email, customer.phone]
                .filter(Boolean)
                .join(" ")
                .toLocaleLowerCase();
            return searchableText.includes(query);
        });

        const sortMode = customerSort ? customerSort.value : "created-desc";
        visibleCustomers.sort(function (left, right) {
            if (sortMode === "name-asc" || sortMode === "name-desc") {
                const result = (left.name || "").localeCompare(right.name || "", undefined, { sensitivity: "base" });
                return sortMode === "name-asc" ? result : -result;
            }

            const leftDate = Date.parse(left.created_at);
            const rightDate = Date.parse(right.created_at);
            const leftValid = Number.isFinite(leftDate);
            const rightValid = Number.isFinite(rightDate);
            if (!leftValid && !rightValid) return 0;
            if (!leftValid) return 1;
            if (!rightValid) return -1;
            return sortMode === "created-asc" ? leftDate - rightDate : rightDate - leftDate;
        });

        if (customerResultsCount) {
            customerResultsCount.textContent = `Showing ${visibleCustomers.length} of ${customers.length} loaded records`;
        }

        if (!visibleCustomers.length) {
            const emptyMessage = customers.length === 0
                ? "No customers yet. Choose Add Customer to create the first record."
                : `No loaded customers match “${customerSearch.value.trim()}”.`;
            appendStateRow(emptyMessage);
            return;
        }

        customerBody.replaceChildren();
        visibleCustomers.forEach(function (customer) {
            const row = document.createElement("tr");
            const nameCell = document.createElement("td");
            nameCell.className = "customer-name-cell";
            nameCell.textContent = customer.name || "—";

            const emailCell = document.createElement("td");
            emailCell.textContent = customer.email || "—";

            const phoneCell = document.createElement("td");
            phoneCell.textContent = customer.phone || "—";

            const createdCell = document.createElement("td");
            createdCell.textContent = formatDate(customer.created_at);

            const actionCell = document.createElement("td");
            const actions = document.createElement("div");
            actions.className = "customer-row-actions";
            const profileLink = document.createElement("a");
            profileLink.className = "customer-profile-link";
            profileLink.href = `customer-profile.html?id=${encodeURIComponent(customer.id)}`;
            profileLink.textContent = "Profile";
            profileLink.setAttribute("aria-label", `View profile for ${customer.name || "customer"}`);
            const editButton = document.createElement("button");
            editButton.type = "button";
            editButton.className = "customer-edit-button";
            editButton.textContent = "Edit";
            editButton.setAttribute("aria-label", `Edit ${customer.name || "customer"}`);
            editButton.addEventListener("click", function () {
                openEditCustomerDialog(customer);
            });
            const deleteButton = document.createElement("button");
            deleteButton.type = "button";
            deleteButton.className = "customer-delete-button";
            deleteButton.textContent = "Delete";
            deleteButton.setAttribute("aria-label", `Delete ${customer.name || "customer"}`);
            deleteButton.addEventListener("click", function () {
                deleteCustomer(customer, deleteButton);
            });
            actions.append(profileLink, editButton, deleteButton);
            actionCell.appendChild(actions);

            row.append(nameCell, emailCell, phoneCell, createdCell, actionCell);
            customerBody.appendChild(row);
        });
    }

    async function loadCustomers() {
        customerLoadState = "loading";
        renderCustomers();

        try {
            const page = await API.request("/customers?limit=50");
            if (!page || !Array.isArray(page.items)) {
                throw new Error("The customer list response was not in the expected format.");
            }
            customers = page.items;
            customerLoadState = "loaded";
            renderCustomers();
            return true;
        } catch (error) {
            customerLoadState = "error";
            renderCustomers();
            throw error;
        }
    }

    async function loadMetrics() {
        const totalElement = document.getElementById("customers-total");
        const recentElement = document.getElementById("customers-new-total");
        if (totalElement) totalElement.textContent = "Loading…";
        if (recentElement) recentElement.textContent = "Loading…";

        try {
            const metrics = await API.request("/dashboard/metrics");
            if (totalElement) totalElement.textContent = metrics.customers_total ?? "—";
            if (recentElement) recentElement.textContent = metrics.customers_new_30d ?? "—";
            return true;
        } catch (error) {
            if (totalElement) totalElement.textContent = "Unavailable";
            if (recentElement) recentElement.textContent = "Unavailable";
            throw error;
        }
    }

    async function refreshCustomerData(announce) {
        if (!API) {
            showMessage("Customer services are unavailable right now.", true);
            return;
        }

        if (refreshButton) refreshButton.disabled = true;
        const results = await Promise.allSettled([loadCustomers(), loadMetrics()]);
        if (refreshButton) refreshButton.disabled = false;

        const failures = results.filter(function (result) {
            return result.status === "rejected";
        });
        const authFailure = failures.some(function (result) {
            return handleAuthError(result.reason);
        });

        if (authFailure) return;
        if (failures.length) {
            showMessage("Some customer information could not be refreshed. Check the error state and try again.", true);
        } else if (announce) {
            showMessage("Customer directory refreshed.", false);
        }
    }

    async function deleteCustomer(customer, button) {
        if (!window.confirm(`Delete ${customer.name || "this customer"}? This action cannot be undone.`)) {
            return;
        }

        button.disabled = true;
        try {
            await API.request(`/customers/${customer.id}`, { method: "DELETE" });
            customers = customers.filter(function (item) {
                return item.id !== customer.id;
            });
            customerLoadState = "loaded";
            renderCustomers();

            const results = await Promise.allSettled([loadCustomers(), loadMetrics()]);
            const refreshFailed = results.some(function (result) {
                return result.status === "rejected";
            });
            const authFailure = results.some(function (result) {
                return result.status === "rejected" && handleAuthError(result.reason);
            });
            if (!authFailure) {
                showMessage(refreshFailed
                    ? "Customer deleted, but the directory could not fully refresh. Use Refresh to retry."
                    : "Customer deleted.", refreshFailed);
            }
        } catch (error) {
            button.disabled = false;
            if (!handleAuthError(error)) {
                showMessage(error.message || "Could not delete this customer.", true);
            }
        }
    }

    function setDialogBusy(isBusy) {
        submitInProgress = isBusy;
        if (customerSubmit) customerSubmit.disabled = isBusy;
        document.querySelectorAll("#customer-dialog-close, #customer-dialog-cancel").forEach(function (button) {
            button.disabled = isBusy;
        });
        if (customerSubmit) {
            customerSubmit.textContent = isBusy
                ? (dialogMode === "edit" ? "Saving…" : "Adding…")
                : (dialogMode === "edit" ? "Save Changes" : "Add Customer");
        }
    }

    function clearCustomerFormError() {
        if (customerFormError) {
            customerFormError.hidden = true;
            customerFormError.textContent = "";
        }
        customerNameInput.setCustomValidity("");
        customerPhoneInput.setCustomValidity("");
        phoneValidationAttempted = false;
        clearPhoneFieldError();
    }

    function clearPhoneFieldError() {
        if (customerPhoneError) {
            customerPhoneError.hidden = true;
            customerPhoneError.textContent = "";
        }
        customerPhoneInput.setAttribute("aria-invalid", "false");
        customerPhoneInput.setCustomValidity("");
    }

    function showPhoneFieldError(message) {
        if (customerPhoneError) {
            customerPhoneError.textContent = message;
            customerPhoneError.hidden = false;
        }
        customerPhoneInput.setAttribute("aria-invalid", "true");
        customerPhoneInput.setCustomValidity(message);
    }

    function showCustomerFormError(message, invalidField) {
        if (invalidField === customerPhoneInput) {
            showPhoneFieldError(message);
            invalidField.reportValidity();
            return;
        }
        if (customerFormError) {
            customerFormError.textContent = message;
            customerFormError.hidden = false;
        }
        if (invalidField) {
            invalidField.setCustomValidity(message);
            invalidField.reportValidity();
        }
    }

    function openCustomerDialog() {
        if (!customerDialog || !customerForm) return;
        dialogMode = "create";
        editingCustomer = null;
        customerForm.reset();
        clearCustomerFormError();
        customerPhoneInput.setAttribute("aria-invalid", "false");
        setText("customer-dialog-title", "Add Customer");
        setText("customer-dialog-description", "Add a customer to the active workspace.");
        setText("customer-submit", "Add Customer");
        document.getElementById("customer-dialog-close")?.setAttribute("aria-label", "Close Add Customer dialog");
        setDialogBusy(false);
        if (typeof customerDialog.showModal === "function") {
            customerDialog.showModal();
        } else {
            customerDialog.setAttribute("open", "");
        }
        customerNameInput.focus();
    }

    function openEditCustomerDialog(customer) {
        if (!customerDialog || !customerForm) return;
        dialogMode = "edit";
        editingCustomer = customer;
        customerForm.reset();
        clearCustomerFormError();
        customerPhoneInput.setAttribute("aria-invalid", "false");
        customerNameInput.value = customer.name || "";
        customerEmailInput.value = customer.email || "";
        customerPhoneInput.value = customer.phone || "";
        setText("customer-dialog-title", "Edit Customer");
        setText("customer-dialog-description", `Update the details for ${customer.name || "this customer"}.`);
        setText("customer-submit", "Save Changes");
        document.getElementById("customer-dialog-close")?.setAttribute("aria-label", "Close Edit Customer dialog");
        setDialogBusy(false);
        if (typeof customerDialog.showModal === "function") {
            customerDialog.showModal();
        } else {
            customerDialog.setAttribute("open", "");
        }
        customerNameInput.focus();
    }

    async function openRequestedCustomerEdit() {
        const customerId = new URLSearchParams(window.location.search).get("edit");
        if (!customerId) return;

        window.history.replaceState(null, "", window.location.pathname + window.location.hash);
        if (!isValidCustomerId(customerId)) {
            showMessage("That customer ID is invalid, so the edit form could not be opened.", true);
            return;
        }
        const normalizedCustomerId = customerId.toLowerCase();
        if (!API) {
            showMessage("Customer services are unavailable right now.", true);
            return;
        }

        try {
            const customer = await API.request(`/customers/${encodeURIComponent(normalizedCustomerId)}`);
            if (!customer || String(customer.id).toLowerCase() !== normalizedCustomerId) {
                showMessage("The requested customer could not be identified.", true);
                return;
            }
            openEditCustomerDialog(customer);
        } catch (error) {
            if (!handleAuthError(error)) {
                showMessage(error.message || "Could not load this customer for editing.", true);
            }
        }
    }

    function closeCustomerDialog() {
        if (!customerDialog || submitInProgress) return;
        if (typeof customerDialog.close === "function") {
            customerDialog.close();
        } else {
            customerDialog.removeAttribute("open");
        }
    }

    function bindCustomerForm() {
        if (!customerForm || !customerNameInput || !customerSubmit) return;

        customerNameInput.addEventListener("input", function () {
            if (customerFormError) {
                customerFormError.hidden = true;
                customerFormError.textContent = "";
            }
            customerNameInput.setCustomValidity("");
        });

        customerPhoneInput.addEventListener("input", function () {
            if (customerFormError) {
                customerFormError.hidden = true;
                customerFormError.textContent = "";
            }
            if (!customerPhoneInput.value.trim() || isValidCustomerPhone(customerPhoneInput.value.trim())) {
                clearPhoneFieldError();
            } else if (phoneValidationAttempted) {
                showPhoneFieldError("Enter a valid phone number using 7–15 digits. You may include a leading +, spaces, hyphens, and parentheses.");
            }
        });

        customerForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            if (submitInProgress) return;

            const name = customerNameInput.value.trim().split(/\s+/).filter(Boolean).join(" ");
            const nameParts = name ? name.split(" ") : [];
            if (nameParts.length < 2) {
                showCustomerFormError("Enter at least two name parts, such as John Doe.", customerNameInput);
                return;
            }
            customerNameInput.value = name;

            const phone = customerPhoneInput.value.trim();
            if (!isValidCustomerPhone(phone)) {
                phoneValidationAttempted = true;
                showCustomerFormError("Enter a valid phone number using 7–15 digits. You may include a leading +, spaces, hyphens, and parentheses.", customerPhoneInput);
                return;
            }
            clearPhoneFieldError();

            clearCustomerFormError();
            if (!customerForm.reportValidity()) return;

            if (!API) {
                if (customerFormError) {
                    customerFormError.textContent = "Customer services are unavailable right now.";
                    customerFormError.hidden = false;
                }
                return;
            }

            setDialogBusy(true);
            clearCustomerFormError();
            try {
                const customerPayload = {
                    name: name,
                    email: customerEmailInput.value.trim() || null,
                    phone: phone || null
                };
                const savedCustomer = dialogMode === "edit"
                    ? await API.request(`/customers/${editingCustomer.id}`, {
                        method: "PATCH",
                        body: JSON.stringify(customerPayload)
                    })
                    : await API.request("/customers", {
                        method: "POST",
                        body: JSON.stringify(customerPayload)
                    });
                customerForm.reset();
                if (customerDialog && typeof customerDialog.close === "function") {
                    customerDialog.close();
                } else if (customerDialog) {
                    customerDialog.removeAttribute("open");
                }

                if (dialogMode === "edit" && savedCustomer) {
                    customers = customers.map(function (item) {
                        return item.id === savedCustomer.id ? savedCustomer : item;
                    });
                }
                const results = dialogMode === "edit"
                    ? await Promise.allSettled([loadCustomers()])
                    : await Promise.allSettled([loadCustomers(), loadMetrics()]);
                const refreshFailed = results.some(function (result) {
                    return result.status === "rejected";
                });
                const authFailure = results.some(function (result) {
                    return result.status === "rejected" && handleAuthError(result.reason);
                });
                if (!authFailure) {
                    const action = dialogMode === "edit" ? "updated" : "created";
                    showMessage(refreshFailed
                        ? `Customer ${action}, but the directory could not fully refresh. Use Refresh to retry.`
                        : `Customer ${action}.`, refreshFailed);
                }
            } catch (error) {
                const errorMessage = formatCustomerRequestError(
                    error,
                    dialogMode === "edit" ? "Could not update this customer." : "Could not create this customer."
                );
                if (customerFormError) {
                    customerFormError.textContent = errorMessage;
                    customerFormError.hidden = false;
                }
                if (!handleAuthError(error)) {
                    showMessage(errorMessage, true);
                }
            } finally {
                setDialogBusy(false);
            }
        });
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
            if (sidebar.classList.contains("mobile-open") &&
                !sidebar.contains(event.target) &&
                !mobileMenuToggle.contains(event.target)) {
                setOpen(false, false);
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && sidebar.classList.contains("mobile-open")) {
                setOpen(false, true);
            }
        });

        window.addEventListener("resize", function () {
            if (!window.matchMedia("(max-width: 650px)").matches) {
                setOpen(false, false);
            }
        }, { passive: true });
    }

    document.getElementById("customer-add-open")?.addEventListener("click", openCustomerDialog);
    document.getElementById("customer-dialog-close")?.addEventListener("click", closeCustomerDialog);
    document.getElementById("customer-dialog-cancel")?.addEventListener("click", closeCustomerDialog);
    customerDialog?.addEventListener("click", function (event) {
        if (event.target === customerDialog) closeCustomerDialog();
    });
    customerDialog?.addEventListener("cancel", function (event) {
        if (submitInProgress) event.preventDefault();
    });
    customerSearch?.addEventListener("input", renderCustomers);
    customerSort?.addEventListener("change", renderCustomers);
    refreshButton?.addEventListener("click", function () {
        refreshCustomerData(true);
    });

    bindCustomerForm();
    bindMobileSidebar();

    if (!API) {
        customerLoadState = "error";
        renderCustomers();
        setText("customers-total", "Unavailable");
        setText("customers-new-total", "Unavailable");
        showMessage("Customer services are unavailable right now.", true);
    } else {
        refreshCustomerData(false);
        openRequestedCustomerEdit();
    }
})();
