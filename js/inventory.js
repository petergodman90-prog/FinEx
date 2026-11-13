/* Live products and stock workspace: product master, stock reads, and proposals. */
(function () {
    "use strict";

    const API = window.FinexAPI;
    const Fx = window.FinexFinance;
    if (!API || !Fx) {
        return;
    }

    const statusElement = document.getElementById("inventory-status");
    const productAddButton = document.getElementById("product-add-open");
    const openingStockButton = document.getElementById("opening-stock-open");
    const adjustmentButton = document.getElementById("stock-adjustment-open");

    const stockLevelSearch = document.getElementById("stock-level-search");
    const stockLevelFilter = document.getElementById("stock-level-filter");
    const stockLevelRefresh = document.getElementById("stock-levels-refresh");
    const stockLevelCount = document.getElementById("stock-levels-count");
    const stockLevelBody = document.getElementById("stock-levels-body");

    const valuationRefresh = document.getElementById("valuation-refresh");
    const valuationBody = document.getElementById("valuation-body");
    const valuationBanner = document.getElementById("valuation-banner");
    const valuationBannerText = document.getElementById("valuation-banner-text");

    const movementProduct = document.getElementById("movement-product");
    const movementType = document.getElementById("movement-type");
    const movementRefresh = document.getElementById("movements-refresh");
    const movementCount = document.getElementById("movements-count");
    const movementBody = document.getElementById("movements-body");

    const productSearch = document.getElementById("product-search");
    const productFilter = document.getElementById("product-filter");
    const productsRefresh = document.getElementById("products-refresh");
    const productCount = document.getElementById("products-count");
    const productBody = document.getElementById("products-body");

    const productDialog = document.getElementById("product-dialog");
    const productForm = document.getElementById("product-form");
    const productDialogTitle = document.getElementById("product-dialog-title");
    const productDialogDescription = document.getElementById("product-dialog-description");
    const productDialogClose = document.getElementById("product-dialog-close");
    const productCancel = document.getElementById("product-cancel");
    const productSubmit = document.getElementById("product-submit");
    const productError = document.getElementById("product-error");
    const productSku = document.getElementById("product-sku");
    const productName = document.getElementById("product-name");
    const productUnit = document.getElementById("product-unit");
    const productSalePrice = document.getElementById("product-sale-price");
    const productCost = document.getElementById("product-cost");
    const productReorderPoint = document.getElementById("product-reorder-point");
    const productActiveGroup = document.getElementById("product-active-group");
    const productIsActive = document.getElementById("product-is-active");

    const openingDialog = document.getElementById("opening-stock-dialog");
    const openingForm = document.getElementById("opening-stock-form");
    const openingDialogClose = document.getElementById("opening-stock-close");
    const openingCancel = document.getElementById("opening-stock-cancel");
    const openingSubmit = document.getElementById("opening-stock-submit");
    const openingError = document.getElementById("opening-stock-error");
    const openingProduct = document.getElementById("opening-stock-product");
    const openingQuantity = document.getElementById("opening-stock-quantity");
    const openingCost = document.getElementById("opening-stock-cost");
    const openingEffective = document.getElementById("opening-stock-effective");
    const openingMemo = document.getElementById("opening-stock-memo");

    const adjustmentDialog = document.getElementById("stock-adjustment-dialog");
    const adjustmentForm = document.getElementById("stock-adjustment-form");
    const adjustmentDialogClose = document.getElementById("stock-adjustment-close");
    const adjustmentCancel = document.getElementById("stock-adjustment-cancel");
    const adjustmentSubmit = document.getElementById("stock-adjustment-submit");
    const adjustmentError = document.getElementById("stock-adjustment-error");
    const adjustmentDirection = document.getElementById("stock-adjustment-direction");
    const adjustmentProduct = document.getElementById("stock-adjustment-product");
    const adjustmentQuantity = document.getElementById("stock-adjustment-quantity");
    const adjustmentCost = document.getElementById("stock-adjustment-cost");
    const adjustmentReason = document.getElementById("stock-adjustment-reason");
    const adjustmentEffective = document.getElementById("stock-adjustment-effective");
    const adjustmentMemo = document.getElementById("stock-adjustment-memo");

    const movementLabels = {
        opening_balance: "Opening balance",
        purchase: "Purchase",
        sale: "Sale",
        sale_reversal: "Sale reversal",
        adjustment_in: "Adjustment in",
        adjustment_out: "Adjustment out",
        stock_take: "Stock take",
        return_in: "Return in",
        return_out: "Return out"
    };

    let allProducts = [];
    let stockLevels = [];
    let movements = [];
    let editingProductId = null;
    let canWrite = false;
    let stockSearchTimer = null;
    let productSearchTimer = null;
    let loadingStock = false;
    let loadingProducts = false;
    let loadingValuation = false;
    let loadingMovements = false;
    let pendingStockLoad = false;
    let pendingProductLoad = false;
    let pendingMovementLoad = false;

    function setMetric(id, value) {
        Fx.setText(id, value);
    }

    function quantity(value) {
        return Fx.formatNumber(value, 4);
    }

    function typeLabel(value) {
        return String(value || "")
            .replace(/_/g, " ")
            .replace(/\b\w/g, function (letter) {
                return letter.toUpperCase();
            });
    }

    function shortId(value) {
        return String(value || "").slice(0, 8).toUpperCase();
    }

    function productLabel(product) {
        return `${product.sku} — ${product.name}`;
    }

    function addOption(select, value, label, disabled) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        option.disabled = Boolean(disabled);
        select.appendChild(option);
        return option;
    }

    function fillProductSelect(select, products, placeholder, includeInactive) {
        const current = select.value;
        select.replaceChildren();
        addOption(select, "", placeholder);
        const visible = products.filter(function (product) {
            return includeInactive || product.is_active;
        });
        if (visible.length === 0) {
            addOption(select, "", "No products available", true);
            return;
        }
        visible.forEach(function (product) {
            addOption(select, product.id, productLabel(product));
        });
        if (visible.some(function (product) { return product.id === current; })) {
            select.value = current;
        }
    }

    function populateProductSelects() {
        fillProductSelect(movementProduct, allProducts, "All products", true);
        fillProductSelect(openingProduct, allProducts, "Select a product", false);
        fillProductSelect(adjustmentProduct, allProducts, "Select a product", false);
    }

    function updateWriteControls() {
        productAddButton.hidden = !canWrite;
        openingStockButton.hidden = !canWrite;
        adjustmentButton.hidden = !canWrite;
    }

    function activateTab(name) {
        const tabs = document.querySelectorAll("[data-stock-tab]");
        tabs.forEach(function (tab) {
            const active = tab.dataset.stockTab === name;
            tab.classList.toggle("is-active", active);
            tab.setAttribute("aria-selected", active ? "true" : "false");
        });
        const sections = {
            levels: "stock-levels-section",
            valuation: "stock-valuation-section",
            movements: "stock-movements-section",
            products: "stock-products-section"
        };
        Object.keys(sections).forEach(function (key) {
            const section = document.getElementById(sections[key]);
            if (section) {
                section.hidden = key !== name;
            }
        });
    }

    function renderStockLevels() {
        stockLevelBody.replaceChildren();
        if (stockLevels.length === 0) {
            Fx.emptyRow(
                stockLevelBody,
                4,
                stockLevelSearch.value.trim()
                    ? "No stock records match your search."
                    : "No stock records have been posted yet."
            );
            return;
        }
        stockLevels.forEach(function (level) {
            const row = document.createElement("tr");

            const productCell = document.createElement("td");
            const name = document.createElement("span");
            name.className = "transaction-name";
            name.textContent = level.name;
            const reference = document.createElement("div");
            reference.className = "transaction-reference";
            reference.textContent = level.sku;
            productCell.append(name, reference);

            const quantityCell = document.createElement("td");
            quantityCell.textContent = quantity(level.quantity_on_hand);

            const reorderCell = document.createElement("td");
            reorderCell.textContent = quantity(level.reorder_point);

            const statusCell = document.createElement("td");
            const pill = document.createElement("span");
            pill.className = `status-pill ${level.needs_reorder ? "status-warning" : "status-reconciled"}`;
            pill.textContent = level.needs_reorder ? "Needs reorder" : "In stock";
            statusCell.appendChild(pill);

            row.append(productCell, quantityCell, reorderCell, statusCell);
            stockLevelBody.appendChild(row);
        });
    }

    async function loadStockLevels() {
        if (loadingStock) {
            pendingStockLoad = true;
            return false;
        }
        loadingStock = true;
        stockLevelRefresh.disabled = true;
        stockLevelRefresh.textContent = "Refreshing…";
        try {
            const params = new URLSearchParams({
                limit: "200",
                include_inactive: "true",
                needs_reorder: stockLevelFilter.value === "reorder" ? "true" : "false"
            });
            const search = stockLevelSearch.value.trim();
            if (search) {
                params.set("q", search);
            }
            const page = await API.request(`/inventory/stock-levels?${params.toString()}`);
            stockLevels = Array.isArray(page.items) ? page.items : [];
            renderStockLevels();
            stockLevelCount.textContent = stockLevels.length === 0
                ? "No stock records match the current view."
                : `Showing ${stockLevels.length} of ${Number(page.total || 0)} stock record${Number(page.total || 0) === 1 ? "" : "s"}.`;
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            stockLevels = [];
            renderStockLevels();
            stockLevelCount.textContent = "Stock levels could not be loaded.";
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Stock levels could not be loaded."),
                true
            );
            return false;
        } finally {
            loadingStock = false;
            stockLevelRefresh.disabled = false;
            stockLevelRefresh.textContent = "Refresh";
            if (pendingStockLoad) {
                pendingStockLoad = false;
                loadStockLevels();
            }
        }
    }

    function renderValuation(valuation) {
        valuationBody.replaceChildren();
        const rows = Array.isArray(valuation.rows) ? valuation.rows : [];
        setMetric("inventory-stocked-count", Fx.formatNumber(rows.length, 0));
        setMetric("inventory-book-value", Fx.formatMoney(valuation.total_book_value));
        setMetric("inventory-ledger-balance", Fx.formatMoney(valuation.inventory_account_balance));
        setMetric(
            "inventory-reconciliation",
            valuation.is_reconciled ? "Reconciled" : "Mismatch"
        );
        const reconciliationValue = document.getElementById("inventory-reconciliation");
        if (reconciliationValue) {
            reconciliationValue.classList.toggle("is-positive", Boolean(valuation.is_reconciled));
            reconciliationValue.classList.toggle("is-negative", !valuation.is_reconciled);
        }
        if (valuationBanner && valuationBannerText) {
            valuationBanner.classList.toggle("is-error", !valuation.is_reconciled);
            valuationBannerText.textContent = valuation.is_reconciled
                ? `Book value ${Fx.formatMoney(valuation.total_book_value)} matches the ledger inventory balance.`
                : `Book value ${Fx.formatMoney(valuation.total_book_value)} differs from the ledger balance ${Fx.formatMoney(valuation.inventory_account_balance)}.`;
        }
        if (rows.length === 0) {
            Fx.emptyRow(
                valuationBody,
                4,
                "No inventory is on hand yet. Record opening stock to begin valuation."
            );
            return;
        }
        rows.forEach(function (item) {
            const row = document.createElement("tr");

            const productCell = document.createElement("td");
            const name = document.createElement("span");
            name.className = "transaction-name";
            name.textContent = item.name;
            const reference = document.createElement("div");
            reference.className = "transaction-reference";
            reference.textContent = item.sku;
            productCell.append(name, reference);

            const quantityCell = document.createElement("td");
            quantityCell.textContent = quantity(item.quantity_on_hand);

            const costCell = document.createElement("td");
            costCell.className = "finance-money";
            costCell.textContent = Fx.formatMoney(item.average_unit_cost);

            const valueCell = document.createElement("td");
            valueCell.className = "finance-money is-strong";
            valueCell.textContent = Fx.formatMoney(item.book_value);

            row.append(productCell, quantityCell, costCell, valueCell);
            valuationBody.appendChild(row);
        });
    }

    async function loadValuation() {
        if (loadingValuation) {
            return false;
        }
        loadingValuation = true;
        valuationRefresh.disabled = true;
        valuationRefresh.textContent = "Refreshing…";
        try {
            const valuation = await API.request("/inventory/valuation");
            renderValuation(valuation);
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            setMetric("inventory-stocked-count", "—");
            setMetric("inventory-book-value", "—");
            setMetric("inventory-ledger-balance", "—");
            setMetric("inventory-reconciliation", "—");
            if (valuationBanner && valuationBannerText) {
                valuationBanner.classList.add("is-error");
                valuationBannerText.textContent = "Valuation could not be loaded.";
            }
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Stock valuation could not be loaded."),
                true
            );
            return false;
        } finally {
            loadingValuation = false;
            valuationRefresh.disabled = false;
            valuationRefresh.textContent = "Refresh";
        }
    }

    function renderMovements() {
        movementBody.replaceChildren();
        if (movements.length === 0) {
            Fx.emptyRow(
                movementBody,
                6,
                "No stock movements match the current filters."
            );
            return;
        }
        movements.forEach(function (movement) {
            const row = document.createElement("tr");

            const occurredCell = document.createElement("td");
            occurredCell.textContent = Fx.formatDateTime(movement.occurred_at);

            const productCell = document.createElement("td");
            const name = document.createElement("span");
            name.className = "transaction-name";
            name.textContent = movement.name;
            const reference = document.createElement("div");
            reference.className = "transaction-reference";
            reference.textContent = movement.sku;
            productCell.append(name, reference);

            const typeCell = document.createElement("td");
            typeCell.textContent = movementLabels[movement.movement_type] || typeLabel(movement.movement_type);

            const quantityCell = document.createElement("td");
            quantityCell.textContent = quantity(movement.quantity_delta);

            const costCell = document.createElement("td");
            costCell.className = "finance-money";
            costCell.textContent = Fx.formatMoney(movement.unit_cost);

            const sourceCell = document.createElement("td");
            const sourceName = document.createElement("span");
            sourceName.className = "transaction-name";
            sourceName.textContent = typeLabel(movement.source_type);
            const sourceReference = document.createElement("div");
            sourceReference.className = "transaction-reference";
            sourceReference.textContent = `${shortId(movement.source_id)} · Journal ${shortId(movement.journal_entry_id)}`;
            sourceCell.append(sourceName, sourceReference);

            row.append(occurredCell, productCell, typeCell, quantityCell, costCell, sourceCell);
            movementBody.appendChild(row);
        });
    }

    async function loadMovements() {
        if (loadingMovements) {
            pendingMovementLoad = true;
            return false;
        }
        loadingMovements = true;
        movementRefresh.disabled = true;
        movementRefresh.textContent = "Refreshing…";
        try {
            const params = new URLSearchParams({ limit: "100" });
            if (movementProduct.value) {
                params.set("product_id", movementProduct.value);
            }
            if (movementType.value) {
                params.set("movement_type", movementType.value);
            }
            const page = await API.request(`/inventory/movements?${params.toString()}`);
            movements = Array.isArray(page.items) ? page.items : [];
            renderMovements();
            movementCount.textContent = movements.length === 0
                ? "No stock movements match the current filters."
                : `Showing ${movements.length} of ${Number(page.total || 0)} stock movement${Number(page.total || 0) === 1 ? "" : "s"}.`;
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            movements = [];
            renderMovements();
            movementCount.textContent = "Stock movements could not be loaded.";
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Stock movements could not be loaded."),
                true
            );
            return false;
        } finally {
            loadingMovements = false;
            movementRefresh.disabled = false;
            movementRefresh.textContent = "Refresh";
            if (pendingMovementLoad) {
                pendingMovementLoad = false;
                loadMovements();
            }
        }
    }

    function buildProductRow(product) {
        const row = document.createElement("tr");

        const productCell = document.createElement("td");
        const name = document.createElement("span");
        name.className = "transaction-name";
        name.textContent = product.name;
        const reference = document.createElement("div");
        reference.className = "transaction-reference";
        reference.textContent = product.sku;
        productCell.append(name, reference);

        const unitCell = document.createElement("td");
        unitCell.textContent = product.unit;

        const salePriceCell = document.createElement("td");
        salePriceCell.className = "finance-money";
        salePriceCell.textContent = Fx.formatMoney(product.current_sale_price);

        const costCell = document.createElement("td");
        costCell.className = "finance-money";
        costCell.textContent = Fx.formatMoney(product.current_cost);

        const reorderCell = document.createElement("td");
        reorderCell.textContent = quantity(product.reorder_point);

        const statusCell = document.createElement("td");
        const pill = document.createElement("span");
        pill.className = `status-pill ${product.is_active ? "status-applied" : "status-draft"}`;
        pill.textContent = product.is_active ? "Active" : "Inactive";
        statusCell.appendChild(pill);

        const actionsCell = document.createElement("td");
        const actions = document.createElement("div");
        actions.className = "finance-table-actions";
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.className = "setting-button";
        editButton.textContent = "Edit";
        editButton.hidden = !canWrite;
        editButton.addEventListener("click", function () {
            openProductDialog(product);
        });
        actions.appendChild(editButton);
        actionsCell.appendChild(actions);

        row.append(productCell, unitCell, salePriceCell, costCell, reorderCell, statusCell, actionsCell);
        return row;
    }

    function renderProducts(page) {
        productBody.replaceChildren();
        const items = Array.isArray(page.items) ? page.items : [];
        if (items.length === 0) {
            Fx.emptyRow(
                productBody,
                7,
                productSearch.value.trim()
                    ? "No products match your search."
                    : "No products have been created yet."
            );
            return;
        }
        items.forEach(function (product) {
            productBody.appendChild(buildProductRow(product));
        });
    }

    async function loadProductsAndOptions() {
        if (loadingProducts) {
            pendingProductLoad = true;
            return false;
        }
        loadingProducts = true;
        productsRefresh.disabled = true;
        productsRefresh.textContent = "Refreshing…";
        try {
            const tableParams = new URLSearchParams({
                limit: "200",
                include_inactive: productFilter.value === "all" ? "true" : "false"
            });
            const search = productSearch.value.trim();
            if (search) {
                tableParams.set("q", search);
            }
            const [allPage, page] = await Promise.all([
                API.request("/products?limit=200&include_inactive=true"),
                API.request(`/products?${tableParams.toString()}`)
            ]);
            allProducts = Array.isArray(allPage.items) ? allPage.items : [];
            setMetric("inventory-product-count", Fx.formatNumber(allPage.total, 0));
            populateProductSelects();
            renderProducts(page);
            productCount.textContent = page.total === 0
                ? "No products match the current view."
                : `Showing ${Number(page.items.length || 0)} of ${Number(page.total || 0)} product${Number(page.total || 0) === 1 ? "" : "s"}.`;
            return true;
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return false;
            }
            allProducts = [];
            setMetric("inventory-product-count", "—");
            populateProductSelects();
            Fx.emptyRow(productBody, 7, "Products could not be loaded.");
            productCount.textContent = "Products could not be loaded.";
            Fx.showStatus(
                statusElement,
                Fx.errorMessage(error, "Products could not be loaded."),
                true
            );
            return false;
        } finally {
            loadingProducts = false;
            productsRefresh.disabled = false;
            productsRefresh.textContent = "Refresh";
            if (pendingProductLoad) {
                pendingProductLoad = false;
                loadProductsAndOptions();
            }
        }
    }

    function findProduct(productId) {
        return allProducts.find(function (product) {
            return product.id === productId;
        });
    }

    function resetProductForm() {
        productForm.reset();
        productSku.value = "";
        productName.value = "";
        productUnit.value = "each";
        productSalePrice.value = "";
        productCost.value = "0";
        productReorderPoint.value = "0";
        productIsActive.checked = true;
        productSku.disabled = false;
        productName.disabled = false;
        productUnit.disabled = false;
        productActiveGroup.hidden = true;
        productError.hidden = true;
        productError.textContent = "";
        Fx.setBusy(productSubmit, false, null, "Add product");
        productSubmit.textContent = "Add product";
    }

    function openProductDialog(product) {
        if (!canWrite) {
            return;
        }
        resetProductForm();
        if (product) {
            editingProductId = product.id;
            productDialogTitle.textContent = "Edit product";
            productDialogDescription.textContent = "Sale price, cost, reorder point, and active status can be updated. SKU and name are immutable.";
            productSku.value = product.sku;
            productName.value = product.name;
            productUnit.value = product.unit;
            productSalePrice.value = product.current_sale_price;
            productCost.value = product.current_cost;
            productReorderPoint.value = product.reorder_point;
            productIsActive.checked = Boolean(product.is_active);
            productSku.disabled = true;
            productName.disabled = true;
            productUnit.disabled = true;
            productActiveGroup.hidden = false;
            productSubmit.textContent = "Save changes";
        } else {
            editingProductId = null;
            productDialogTitle.textContent = "Add a product";
            productDialogDescription.textContent = "Products are used by sales, stock movements, and inventory valuation.";
            productSubmit.textContent = "Add product";
        }
        Fx.openDialog(productDialog);
        productName.focus();
    }

    function closeProductDialog() {
        Fx.closeDialog(productDialog);
    }

    function showFormError(element, text) {
        element.textContent = text;
        element.hidden = false;
    }

    async function submitProduct(event) {
        event.preventDefault();
        const sku = productSku.value.trim();
        const name = productName.value.trim();
        const unit = productUnit.value.trim();
        const salePrice = productSalePrice.value.trim();
        const cost = productCost.value.trim() || "0";
        const reorderPoint = productReorderPoint.value.trim() || "0";

        if (!editingProductId && (!sku || !name || !unit)) {
            showFormError(productError, "Enter the SKU, product name, and unit.");
            return;
        }
        if (salePrice === "" || Number.isNaN(Number(salePrice))) {
            showFormError(productError, "Enter a valid sale price.");
            return;
        }

        productError.hidden = true;
        const idleText = editingProductId ? "Save changes" : "Add product";
        Fx.setBusy(productSubmit, true, "Saving…", idleText);
        try {
            if (editingProductId) {
                await API.request(`/products/${editingProductId}`, {
                    method: "PATCH",
                    body: JSON.stringify({
                        sale_price: salePrice,
                        cost: cost,
                        reorder_point: reorderPoint,
                        is_active: productIsActive.checked
                    })
                });
            } else {
                await API.request("/products", {
                    method: "POST",
                    body: JSON.stringify({
                        sku: sku,
                        name: name,
                        unit: unit,
                        sale_price: salePrice,
                        cost: cost,
                        reorder_point: reorderPoint
                    })
                });
            }
            closeProductDialog();
            await Promise.all([loadProductsAndOptions(), loadStockLevels()]);
            Fx.showStatus(
                statusElement,
                editingProductId ? "Product updated." : "Product created.",
                false
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            showFormError(productError, Fx.errorMessage(error, "The product could not be saved."));
            Fx.setBusy(productSubmit, false, null, idleText);
        }
    }

    function resetOpeningForm() {
        openingForm.reset();
        openingQuantity.value = "";
        openingCost.value = "";
        openingMemo.value = "";
        openingEffective.value = Fx.localDateTimeValue();
        openingError.hidden = true;
        openingError.textContent = "";
        Fx.setBusy(openingSubmit, false, null, "Stage opening stock");
        openingSubmit.textContent = "Stage opening stock";
        populateProductSelects();
        if (openingProduct.options.length > 1) {
            openingProduct.selectedIndex = 1;
        }
    }

    function openOpeningStockDialog() {
        if (!canWrite) {
            return;
        }
        resetOpeningForm();
        Fx.openDialog(openingDialog);
        openingProduct.focus();
    }

    function closeOpeningDialog() {
        Fx.closeDialog(openingDialog);
    }

    async function submitOpeningStock(event) {
        event.preventDefault();
        const productId = openingProduct.value;
        const quantityValue = openingQuantity.value.trim();
        const unitCost = openingCost.value.trim();
        const effectiveAt = Fx.toIsoDateTime(openingEffective.value);
        if (!productId) {
            showFormError(openingError, "Select a product.");
            return;
        }
        if (!quantityValue || Number(quantityValue) <= 0) {
            showFormError(openingError, "Enter a quantity greater than zero.");
            return;
        }
        if (!effectiveAt) {
            showFormError(openingError, "Enter a valid effective date and time.");
            return;
        }

        const line = {
            product_id: productId,
            quantity: quantityValue
        };
        if (unitCost !== "") {
            line.unit_cost = unitCost;
        }
        const payload = {
            effective_at: effectiveAt,
            lines: [line],
            idempotency_key: Fx.makeIdempotencyKey("opening-stock")
        };
        const memo = openingMemo.value.trim();
        if (memo) {
            payload.memo = memo;
        }

        openingError.hidden = true;
        Fx.setBusy(openingSubmit, true, "Staging…", "Stage opening stock");
        try {
            await API.request("/inventory/opening-stock", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            closeOpeningDialog();
            Fx.showStatus(
                statusElement,
                "Opening stock proposal staged. Approve it from the dashboard to post the stock and ledger entry."
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            showFormError(openingError, Fx.errorMessage(error, "Opening stock could not be staged."));
            Fx.setBusy(openingSubmit, false, null, "Stage opening stock");
        }
    }

    function resetAdjustmentForm() {
        adjustmentForm.reset();
        adjustmentDirection.value = "in";
        adjustmentQuantity.value = "";
        adjustmentCost.value = "";
        adjustmentReason.value = "";
        adjustmentMemo.value = "";
        adjustmentEffective.value = Fx.localDateTimeValue();
        adjustmentError.hidden = true;
        adjustmentError.textContent = "";
        Fx.setBusy(adjustmentSubmit, false, null, "Stage adjustment");
        adjustmentSubmit.textContent = "Stage adjustment";
        populateProductSelects();
        if (adjustmentProduct.options.length > 1) {
            adjustmentProduct.selectedIndex = 1;
        }
    }

    function openAdjustmentDialog() {
        if (!canWrite) {
            return;
        }
        resetAdjustmentForm();
        Fx.openDialog(adjustmentDialog);
        adjustmentProduct.focus();
    }

    function closeAdjustmentDialog() {
        Fx.closeDialog(adjustmentDialog);
    }

    async function submitAdjustment(event) {
        event.preventDefault();
        const productId = adjustmentProduct.value;
        const quantityValue = adjustmentQuantity.value.trim();
        const unitCost = adjustmentCost.value.trim();
        const reason = adjustmentReason.value.trim();
        const effectiveAt = Fx.toIsoDateTime(adjustmentEffective.value);
        if (!productId) {
            showFormError(adjustmentError, "Select a product.");
            return;
        }
        if (!quantityValue || Number(quantityValue) <= 0) {
            showFormError(adjustmentError, "Enter a quantity greater than zero.");
            return;
        }
        if (!reason) {
            showFormError(adjustmentError, "Enter a reason for the adjustment.");
            return;
        }
        if (!effectiveAt) {
            showFormError(adjustmentError, "Enter a valid effective date and time.");
            return;
        }

        const line = {
            product_id: productId,
            quantity: quantityValue
        };
        if (unitCost !== "") {
            line.unit_cost = unitCost;
        }
        const payload = {
            direction: adjustmentDirection.value,
            effective_at: effectiveAt,
            lines: [line],
            reason: reason,
            idempotency_key: Fx.makeIdempotencyKey("stock-adjustment")
        };
        const memo = adjustmentMemo.value.trim();
        if (memo) {
            payload.memo = memo;
        }

        adjustmentError.hidden = true;
        Fx.setBusy(adjustmentSubmit, true, "Staging…", "Stage adjustment");
        try {
            await API.request("/inventory/adjustments", {
                method: "POST",
                body: JSON.stringify(payload)
            });
            closeAdjustmentDialog();
            Fx.showStatus(
                statusElement,
                "Stock adjustment proposal staged. Approve it from the dashboard to post the stock and ledger entry."
            );
        } catch (error) {
            if (Fx.handleAuthError(error)) {
                return;
            }
            showFormError(adjustmentError, Fx.errorMessage(error, "The stock adjustment could not be staged."));
            Fx.setBusy(adjustmentSubmit, false, null, "Stage adjustment");
        }
    }

    async function loadAll() {
        const results = await Promise.all([
            loadProductsAndOptions(),
            loadStockLevels(),
            loadValuation(),
            loadMovements()
        ]);
        if (results.every(Boolean)) {
            Fx.showStatus(statusElement, "Inventory data loaded from the ledger.");
        }
    }

    function bindDialog(dialog, closeButton, cancelButton, form, errorElement) {
        closeButton?.addEventListener("click", function () {
            Fx.closeDialog(dialog);
        });
        cancelButton?.addEventListener("click", function () {
            Fx.closeDialog(dialog);
        });
        dialog?.addEventListener("click", function (event) {
            if (event.target === dialog) {
                Fx.closeDialog(dialog);
            }
        });
        dialog?.addEventListener("close", function () {
            errorElement.hidden = true;
            errorElement.textContent = "";
        });
        form?.addEventListener("submit", function (event) {
            event.preventDefault();
        });
    }

    document.querySelectorAll("[data-stock-tab]").forEach(function (tab) {
        tab.addEventListener("click", function () {
            activateTab(tab.dataset.stockTab);
        });
    });

    productAddButton?.addEventListener("click", function () {
        openProductDialog(null);
    });
    openingStockButton?.addEventListener("click", openOpeningStockDialog);
    adjustmentButton?.addEventListener("click", openAdjustmentDialog);
    stockLevelRefresh?.addEventListener("click", loadStockLevels);
    valuationRefresh?.addEventListener("click", loadValuation);
    movementRefresh?.addEventListener("click", loadMovements);
    productsRefresh?.addEventListener("click", loadProductsAndOptions);
    stockLevelFilter?.addEventListener("change", loadStockLevels);
    movementProduct?.addEventListener("change", loadMovements);
    movementType?.addEventListener("change", loadMovements);
    productFilter?.addEventListener("change", loadProductsAndOptions);

    stockLevelSearch?.addEventListener("input", function () {
        window.clearTimeout(stockSearchTimer);
        stockSearchTimer = window.setTimeout(loadStockLevels, 300);
    });
    productSearch?.addEventListener("input", function () {
        window.clearTimeout(productSearchTimer);
        productSearchTimer = window.setTimeout(loadProductsAndOptions, 300);
    });

    productForm?.addEventListener("submit", submitProduct);
    openingForm?.addEventListener("submit", submitOpeningStock);
    adjustmentForm?.addEventListener("submit", submitAdjustment);

    bindDialog(productDialog, productDialogClose, productCancel, productForm, productError);
    bindDialog(openingDialog, openingDialogClose, openingCancel, openingForm, openingError);
    bindDialog(adjustmentDialog, adjustmentDialogClose, adjustmentCancel, adjustmentForm, adjustmentError);

    const requestedTab = (window.location.hash || "").replace("#", "");
    if (["levels", "valuation", "movements", "products"].includes(requestedTab)) {
        activateTab(requestedTab);
    } else {
        activateTab("levels");
    }

    Fx.loadMe()
        .then(function () {
            canWrite = Fx.state.canWrite;
            updateWriteControls();
            return loadAll().then(function () {
                const action = new URLSearchParams(window.location.search).get("action");
                if (canWrite && action === "adjustment") {
                    window.history.replaceState(
                        null,
                        "",
                        `${window.location.pathname}${window.location.hash}`
                    );
                    openAdjustmentDialog();
                } else if (canWrite && action === "opening-stock") {
                    window.history.replaceState(
                        null,
                        "",
                        `${window.location.pathname}${window.location.hash}`
                    );
                    openOpeningStockDialog();
                } else if (canWrite && action === "product") {
                    window.history.replaceState(
                        null,
                        "",
                        `${window.location.pathname}${window.location.hash}`
                    );
                    openProductDialog(null);
                }
            });
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
