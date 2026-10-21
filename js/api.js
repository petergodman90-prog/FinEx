/**
 * Minimal same-origin API client.
 *
 * Authentication uses HttpOnly cookies set by /api/v1/auth/*; JavaScript never
 * reads or stores tokens. Requests therefore run with credentials included.
 *
 * When the short-lived access cookie expires, the client performs one silent
 * refresh using the HttpOnly refresh cookie and retries the original request.
 * Concurrent 401s share a single refresh so token rotation is not replayed.
 */
(function () {
    "use strict";

    const API_BASE = "/api/v1";
    const NO_REFRESH_PATHS = new Set([
        "/auth/login",
        "/auth/register",
        "/auth/refresh"
    ]);

    let refreshInFlight = null;

    async function refreshSession() {
        if (!refreshInFlight) {
            refreshInFlight = fetch(`${API_BASE}/auth/refresh`, {
                method: "POST",
                credentials: "same-origin"
            })
                .then(function (response) {
                    if (!response.ok) {
                        const error = new Error("Session expired");
                        error.status = response.status;
                        throw error;
                    }
                    return response.json().catch(function () {
                        return null;
                    });
                })
                .finally(function () {
                    refreshInFlight = null;
                });
        }

        return refreshInFlight;
    }

    async function request(path, options = {}, allowRefresh = true) {
        const headers = { ...(options.headers || {}) };
        if (options.body && !headers["Content-Type"]) {
            headers["Content-Type"] = "application/json";
        }

        const response = await fetch(`${API_BASE}${path}`, {
            credentials: "same-origin",
            ...options,
            headers
        });

        if (
            response.status === 401 &&
            allowRefresh &&
            !NO_REFRESH_PATHS.has(path)
        ) {
            try {
                await refreshSession();
                return request(path, options, false);
            } catch (error) {
                // Fall through and report the original 401.
            }
        }

        if (response.status === 204) {
            return null;
        }

        const text = await response.text();
        let data = null;
        if (text) {
            try {
                data = JSON.parse(text);
            } catch (error) {
                data = null;
            }
        }

        if (!response.ok) {
            let detail = `Request failed (${response.status})`;
            if (data && typeof data.detail === "string") {
                detail = data.detail;
            } else if (data && Array.isArray(data.detail)) {
                detail = data.detail
                    .map(function (item) {
                        return item && item.msg ? item.msg : null;
                    })
                    .filter(Boolean)
                    .join("; ") || detail;
            }
            const apiError = new Error(detail);
            apiError.status = response.status;
            apiError.data = data;
            throw apiError;
        }

        return data;
    }

    window.FinexAPI = { request };
})();
