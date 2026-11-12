/* Shared helpers for the live financial workspaces. */
(function () {
    "use strict";

    const API = window.FinexAPI;
    if (!API) {
        return;
    }

    const WRITE_ROLES = new Set(["owner", "admin", "manager", "staff"]);
    const APPROVE_ROLES = new Set(["owner", "admin", "manager"]);
    const statusNames = {
        pending: "Pending",
        approved: "Approved",
        applied: "Posted",
        posting_failed: "Posting failed",
        rejected: "Rejected",
        expired: "Expired",
        draft: "Draft"
    };

    const state = {
        me: null,
        loadingMe: null,
        role: "",
        currency: "USD",
        canWrite: false,
        canApprove: false
    };

    function normaliseRole(value) {
        return String(value || "").toLowerCase();
    }

    async function loadMe() {
        if (state.me) {
            return state.me;
        }
        if (!state.loadingMe) {
            state.loadingMe = API.request("/auth/me")
                .then(function (me) {
                    state.me = me;
                    state.role = normaliseRole(me.role);
                    state.currency = String(me.currency || "USD").toUpperCase();
                    state.canWrite = WRITE_ROLES.has(state.role);
                    state.canApprove = APPROVE_ROLES.has(state.role);
                    document.body.dataset.finexRole = state.role;
                    document.body.dataset.finexCurrency = state.currency;
                    return me;
                })
                .finally(function () {
                    state.loadingMe = null;
                });
        }
        return state.loadingMe;
    }

    function handleAuthError(error) {
        // Only an expired/invalid session should leave the application. A 403
        // remains visible in the page so the user can see the restriction.
        if (error && error.status === 401) {
            window.location.replace("../index.html");
            return true;
        }
        return false;
    }

    function setText(elementOrId, value) {
        const element = typeof elementOrId === "string"
            ? document.getElementById(elementOrId)
            : elementOrId;
        if (element) {
            element.textContent = value;
        }
    }

    function formatMoney(amount, currency) {
        if (amount === null || amount === undefined || amount === "") {
            return "—";
        }
        const value = Number(amount);
        if (Number.isNaN(value)) {
            return "—";
        }
        const code = String(currency || state.currency || "USD").toUpperCase();
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

    function formatNumber(value, maximumFractionDigits) {
        const number = Number(value);
        if (Number.isNaN(number)) {
            return "—";
        }
        return number.toLocaleString(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: maximumFractionDigits === undefined
                ? 2
                : maximumFractionDigits
        });
    }

    function parseDateOnly(value) {
        const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
        if (!match) {
            return null;
        }
        return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    }

    function formatDate(value, fallback) {
        if (!value) {
            return fallback || "—";
        }
        const date = parseDateOnly(value) || new Date(value);
        return Number.isNaN(date.getTime())
            ? (fallback || "—")
            : date.toLocaleDateString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric"
            });
    }

    function formatDateTime(value, fallback) {
        if (!value) {
            return fallback || "—";
        }
        const date = new Date(value);
        return Number.isNaN(date.getTime())
            ? (fallback || "—")
            : date.toLocaleString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit"
            });
    }

    function todayIso() {
        const now = new Date();
        const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 10);
    }

    function monthStartIso() {
        return `${todayIso().slice(0, 7)}-01`;
    }

    function localDateTimeValue(date) {
        const value = date || new Date();
        const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 16);
    }

    function toIsoDateTime(value) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }

    function makeIdempotencyKey(prefix) {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return `${prefix || "ui"}-${window.crypto.randomUUID()}`;
        }
        return `${prefix || "ui"}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function statusInfo(status, proposal) {
        const raw = String(status || "").toLowerCase().replace(/ /g, "_");
        if (raw === "applied" && proposal && proposal.posting_error) {
            return { key: "posting-failed", label: "Posting failed" };
        }
        if (raw === "approved" && proposal && !proposal.posting_error) {
            return { key: "approved", label: "Posting pending" };
        }
        return {
            key: raw.replace(/_/g, "-") || "draft",
            label: statusNames[raw] || (status ? String(status) : "Unknown")
        };
    }

    function statusPill(status, proposal) {
        const info = statusInfo(status, proposal);
        const pill = document.createElement("span");
        pill.className = `status-pill status-${info.key}`;
        pill.textContent = info.label;
        return pill;
    }

    function showStatus(element, text, isError) {
        if (!element) {
            return;
        }
        element.textContent = text;
        element.classList.toggle("is-error", Boolean(isError));
        element.hidden = false;
    }

    function hideStatus(element) {
        if (!element) {
            return;
        }
        element.hidden = true;
        element.textContent = "";
        element.classList.remove("is-error");
    }

    function openDialog(dialog) {
        if (!dialog) {
            return;
        }
        if (typeof dialog.showModal === "function") {
            if (!dialog.open) {
                dialog.showModal();
            }
        } else {
            dialog.setAttribute("open", "");
        }
    }

    function closeDialog(dialog) {
        if (!dialog) {
            return;
        }
        if (typeof dialog.close === "function" && dialog.open) {
            dialog.close();
        } else {
            dialog.removeAttribute("open");
        }
    }

    function setBusy(button, busy, busyText, idleText) {
        if (!button) {
            return;
        }
        button.disabled = Boolean(busy);
        if (busyText !== undefined && busyText !== null) {
            button.textContent = busy ? busyText : (idleText || "Save");
        }
    }

    function emptyRow(body, columns, text, className) {
        if (!body) {
            return;
        }
        body.replaceChildren();
        const row = document.createElement("tr");
        row.className = className || "finance-empty";
        const cell = document.createElement("td");
        cell.colSpan = columns;
        cell.textContent = text;
        row.appendChild(cell);
        body.appendChild(row);
    }

    function errorMessage(error, fallback) {
        return error && error.message ? error.message : (fallback || "Something went wrong. Please try again.");
    }

    window.FinexFinance = {
        API,
        state,
        loadMe,
        handleAuthError,
        setText,
        formatMoney,
        formatNumber,
        formatDate,
        formatDateTime,
        todayIso,
        monthStartIso,
        localDateTimeValue,
        toIsoDateTime,
        makeIdempotencyKey,
        statusInfo,
        statusPill,
        showStatus,
        hideStatus,
        openDialog,
        closeDialog,
        setBusy,
        emptyRow,
        errorMessage
    };
})();
