/**
 * Minimal same-origin API client.
 *
 * Authentication uses HttpOnly cookies set by /api/v1/auth/*; JavaScript never
 * reads or stores tokens. Requests therefore run with credentials included.
 */
(function () {
    "use strict";

    const API_BASE = "/api/v1";

    async function request(path, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (options.body && !headers["Content-Type"]) {
            headers["Content-Type"] = "application/json";
        }

        const response = await fetch(`${API_BASE}${path}`, {
            credentials: "same-origin",
            ...options,
            headers
        });

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
