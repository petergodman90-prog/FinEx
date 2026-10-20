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
