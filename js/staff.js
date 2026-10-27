/* Staff Management is a session-only frontend demonstration. */
(function () {
    "use strict";

    const requiredStaffPageIds = ['custom-role-cancel', 'custom-role-close', 'custom-role-description', 'custom-role-dialog', 'custom-role-form', 'custom-role-form-message', 'custom-role-name', 'custom-role-open', 'custom-role-save', 'custom-role-title', 'department-cancel', 'department-description', 'department-dialog', 'department-dialog-close', 'department-dialog-title', 'department-form', 'department-form-message', 'department-name', 'department-open', 'department-save', 'departments-feedback', 'departments-list', 'roles-feedback', 'staff-add-cancel', 'staff-add-close', 'staff-add-department', 'staff-add-department-search', 'staff-add-dialog', 'staff-add-email', 'staff-add-feedback', 'staff-add-form', 'staff-add-name', 'staff-add-open', 'staff-add-reports-to', 'staff-add-role', 'staff-detail-department', 'staff-detail-email', 'staff-detail-last-seen', 'staff-detail-name', 'staff-detail-reports-to', 'staff-detail-role', 'staff-detail-status', 'staff-details-dialog', 'staff-details-view', 'staff-dialog-destructive-actions', 'staff-dialog-dismiss-action', 'staff-dialog-status-action', 'staff-directory-table', 'staff-dismiss-cancel', 'staff-dismiss-confirm', 'staff-dismiss-dialog', 'staff-dismiss-name', 'staff-edit-cancel', 'staff-edit-department', 'staff-edit-department-search', 'staff-edit-email', 'staff-edit-form', 'staff-edit-name', 'staff-edit-open', 'staff-edit-reports-to', 'staff-edit-reports-to-error', 'staff-edit-reports-to-options', 'staff-edit-reports-to-search', 'staff-edit-role', 'staff-empty-state', 'staff-organogram', 'staff-role-filter', 'staff-role-save', 'staff-role-save-message', 'staff-role-select', 'staff-roles-list', 'staff-search', 'staff-status-filter'];
    if (!requiredStaffPageIds.every((id) => document.getElementById(id))) {
        return;
    }
    if (document.documentElement.dataset.staffManagementInitialized === "true") {
        return;
    }
    document.documentElement.dataset.staffManagementInitialized = "true";

// FINEX STAFF DIRECTORY SEARCH + FILTERS
// =========================================

(function () {
    const searchInput = document.getElementById("staff-search");
    const roleFilter = document.getElementById("staff-role-filter");
    const statusFilter = document.getElementById("staff-status-filter");
    const staffTable = document.getElementById("staff-directory-table");
    const emptyState = document.getElementById("staff-empty-state");

    if (!searchInput || !roleFilter || !statusFilter || !staffTable) {
        return;
    }

    const tableBody = staffTable.tBodies[0];

    if (!tableBody || staffTable.dataset.staffFiltersInitialized === "true") {
        return;
    }

    const staffRows = Array.from(tableBody.querySelectorAll("tr[data-role][data-status]"));
    const staffDirectoryPanel = staffTable.closest(".panel");

    const staffDetailsDialog = document.getElementById("staff-details-dialog");
    const detailName = document.getElementById("staff-detail-name");
    const detailEmail = document.getElementById("staff-detail-email");
    const detailRole = document.getElementById("staff-detail-role");
    const detailStatus = document.getElementById("staff-detail-status");
    const detailLastSeen = document.getElementById("staff-detail-last-seen");
    const detailDepartment = document.getElementById("staff-detail-department");
    const detailReportsTo = document.getElementById("staff-detail-reports-to");
    const roleSelect = document.getElementById("staff-role-select");
    const saveRoleButton = document.getElementById("staff-role-save");
    const saveMessage = document.getElementById("staff-role-save-message");
    const detailsView = document.getElementById("staff-details-view");
    const editOpenButton = document.getElementById("staff-edit-open");
    const editForm = document.getElementById("staff-edit-form");
    const editNameInput = document.getElementById("staff-edit-name");
    const editEmailInput = document.getElementById("staff-edit-email");
    const editRoleSelect = document.getElementById("staff-edit-role");
    const editCancelButton = document.getElementById("staff-edit-cancel");
    const editReportsTo = document.getElementById("staff-edit-reports-to");
    const editReportsToSearch = document.getElementById("staff-edit-reports-to-search");
    const editReportsToOptions = document.getElementById("staff-edit-reports-to-options");
    const editReportsToError = document.getElementById("staff-edit-reports-to-error");
    const dialogStatusActionButton = document.getElementById("staff-dialog-status-action");
    const dialogDismissActionButton = document.getElementById("staff-dialog-dismiss-action");
    const destructiveActions = document.getElementById("staff-dialog-destructive-actions");
    const staffDismissDialog = document.getElementById("staff-dismiss-dialog");
    const staffDismissName = document.getElementById("staff-dismiss-name");
    const dismissCancelButton = document.getElementById("staff-dismiss-cancel");
    const dismissConfirmButton = document.getElementById("staff-dismiss-confirm");
    const addStaffButton = document.getElementById("staff-add-open");
    const addStaffDialog = document.getElementById("staff-add-dialog");
    const addStaffCloseButton = document.getElementById("staff-add-close");
    const addStaffCancelButton = document.getElementById("staff-add-cancel");
    const addStaffForm = document.getElementById("staff-add-form");
    const addStaffName = document.getElementById("staff-add-name");
    const addStaffEmail = document.getElementById("staff-add-email");
    const addStaffRole = document.getElementById("staff-add-role");
    const addStaffDepartment = document.getElementById("staff-add-department");
    const addStaffDepartmentInput = document.getElementById("staff-add-department-search");
    const addStaffReportsTo = document.getElementById("staff-add-reports-to");
    const addStaffFeedback = document.getElementById("staff-add-feedback");
    const rolesList = document.getElementById("staff-roles-list");
    const rolesFeedback = document.getElementById("roles-feedback");
    const customRoleOpenButton = document.getElementById("custom-role-open");
    const customRoleDialog = document.getElementById("custom-role-dialog");
    const customRoleCloseButton = document.getElementById("custom-role-close");
    const customRoleCancelButton = document.getElementById("custom-role-cancel");
    const customRoleForm = document.getElementById("custom-role-form");
    const customRoleTitle = document.getElementById("custom-role-title");
    const customRoleName = document.getElementById("custom-role-name");
    const customRoleDescription = document.getElementById("custom-role-description");
    const customRoleFormMessage = document.getElementById("custom-role-form-message");
    const customRoleSaveButton = document.getElementById("custom-role-save");
    const departmentsList = document.getElementById("departments-list");
    const departmentsFeedback = document.getElementById("departments-feedback");
    const organogramContainer = document.getElementById("staff-organogram");
    const departmentOpenButton = document.getElementById("department-open");
    const departmentDialog = document.getElementById("department-dialog");
    const departmentDialogClose = document.getElementById("department-dialog-close");
    const departmentCancelButton = document.getElementById("department-cancel");
    const departmentForm = document.getElementById("department-form");
    const departmentTitle = document.getElementById("department-dialog-title");
    const departmentName = document.getElementById("department-name");
    const departmentDescription = document.getElementById("department-description");
    const departmentFormMessage = document.getElementById("department-form-message");
    const departmentSaveButton = document.getElementById("department-save");
    const editDepartment = document.getElementById("staff-edit-department");
    const builtInRoles = [
        { id: "manager", name: "Manager", description: "Oversees staff and business operations." },
        { id: "cashier", name: "Cashier", description: "Handles customer payments and sales." },
        { id: "accountant", name: "Accountant", description: "Supports bookkeeping and financial records." },
        { id: "inventory-staff", name: "Inventory Staff", description: "Tracks stock and inventory activity." }
    ];
    let customRoles = [];
    let departments = [];
    let staffIdFallback = 0;
    let customRoleIdFallback = 0;
    let departmentIdFallback = 0;
    let editingDepartmentId = null;
    let selectedStaffRow = null;
    let dismissalRow = null;

    function getAllRoles() {
        return [...builtInRoles, ...customRoles];
    }

    function normalizeRoleName(name) {
        return name.trim().replace(/\s+/g, " ");
    }

    function findRoleByName(name) {
        const normalizedName = normalizeRoleName(name).toLowerCase();
        return normalizedName
            ? getAllRoles().find((role) => role.name.toLowerCase() === normalizedName)
            : undefined;
    }

    const rolePickers = Array.from(document.querySelectorAll("[data-role-picker]"))
        .map((root) => ({
            root,
            input: root.querySelector("[data-role-input]"),
            value: root.querySelector("[data-role-value]"),
            list: root.querySelector("[role='listbox']"),
            prompt: root.querySelector("[data-role-prompt]"),
            promptMessage: root.querySelector("[data-role-prompt-message]"),
            createButton: root.querySelector("[data-role-create]"),
            cancelButton: root.querySelector("[data-role-cancel]"),
            error: root.querySelector("[data-role-error]"),
            success: root.querySelector("[data-role-success]"),
            activeIndex: -1,
            cancelledName: "",
            skipFocusRender: false
        }));

    function pickerForValueInput(valueInput) {
        return rolePickers.find((picker) => picker.value === valueInput);
    }

    function setRolePickerValue(valueInput, roleId) {
        const picker = pickerForValueInput(valueInput);
        const role = getAllRoles().find((item) => item.id === roleId);
        if (picker && role) {
            selectRoleInPicker(picker, role, false, false);
        }
    }

    function showRolePickerError(valueInput, message) {
        const picker = pickerForValueInput(valueInput);
        if (!picker) {
            return;
        }

        picker.error.textContent = message;
        picker.error.hidden = false;
        picker.input.setAttribute("aria-invalid", "true");
        picker.input.focus();
    }

    function closeRolePicker(picker) {
        picker.list.hidden = true;
        picker.prompt.hidden = true;
        picker.input.setAttribute("aria-expanded", "false");
        picker.input.removeAttribute("aria-activedescendant");
        picker.activeIndex = -1;
    }

    function resetRolePicker(picker) {
        picker.input.value = "";
        picker.value.value = "";
        picker.input.removeAttribute("aria-invalid");
        picker.error.hidden = true;
        picker.error.textContent = "";
        picker.success.hidden = true;
        picker.success.textContent = "";
        picker.cancelledName = "";
        closeRolePicker(picker);
    }

    function selectRoleInPicker(picker, role, showSuccess = false, moveFocus = true) {
        picker.input.value = role.name;
        picker.value.value = role.id;
        picker.error.hidden = true;
        picker.error.textContent = "";
        picker.success.hidden = !showSuccess;
        picker.success.textContent = showSuccess
            ? `${role.name} was created and selected. Continue with the current staff details.`
            : "";
        picker.cancelledName = "";
        closeRolePicker(picker);
        picker.input.setAttribute("aria-invalid", "false");
        if (moveFocus && document.activeElement !== picker.input) {
            picker.skipFocusRender = true;
            picker.input.focus();
        }
    }

    function renderRolePicker(picker, showAllWhenEmpty = false) {
        const query = normalizeRoleName(picker.input.value);
        const exactRole = findRoleByName(query);
        picker.value.value = exactRole ? exactRole.id : "";
        picker.error.hidden = true;
        picker.error.textContent = "";
        picker.input.setAttribute("aria-invalid", "false");
        picker.list.replaceChildren();

        if (!query && !showAllWhenEmpty) {
            closeRolePicker(picker);
            return;
        }

        const normalizedQuery = query.toLowerCase();
        const matches = getAllRoles().filter((role) =>
            !normalizedQuery || role.name.toLowerCase().includes(normalizedQuery)
        );

        if (matches.length) {
            matches.forEach((role, index) => {
                const option = document.createElement("li");
                option.id = `${picker.list.id}-option-${index}`;
                option.dataset.roleId = role.id;
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", role.id === exactRole?.id ? "true" : "false");
                option.textContent = role.name;
                picker.list.append(option);
            });
            picker.list.hidden = false;
            picker.prompt.hidden = true;
            picker.input.setAttribute("aria-expanded", "true");
            picker.activeIndex = -1;
            picker.input.removeAttribute("aria-activedescendant");
            return;
        }

        picker.list.hidden = true;
        picker.promptMessage.textContent =
            `“${query}” isn't an existing role. Would you like to create it as a custom role?`;
        picker.prompt.hidden = false;
        picker.input.setAttribute("aria-expanded", "true");
        picker.activeIndex = -1;
        picker.input.removeAttribute("aria-activedescendant");
    }

    function updateRoleFilter() {
        const selectedValue = roleFilter.value;
        const options = [new Option("All roles", "all")];
        getAllRoles().forEach((role) => options.push(new Option(role.name, role.id)));
        roleFilter.replaceChildren(...options);
        roleFilter.value = options.some((option) => option.value === selectedValue)
            ? selectedValue
            : "all";
    }

    function updateRoleControls() {
        updateRoleFilter();
        rolePickers.forEach((picker) => {
            const selectedRole = getAllRoles().find((role) => role.id === picker.value.value);
            if (selectedRole) {
                picker.input.value = selectedRole.name;
            }
            if (!picker.list.hidden || !picker.prompt.hidden) {
                renderRolePicker(picker, !picker.input.value.trim());
            }
        });
    }

    function setRolePickerActiveOption(picker, index) {
        const options = Array.from(picker.list.querySelectorAll("[role='option']"));
        if (!options.length) {
            return;
        }

        picker.activeIndex = (index + options.length) % options.length;
        options.forEach((option, optionIndex) => {
            option.setAttribute("aria-selected", optionIndex === picker.activeIndex ? "true" : "false");
        });
        picker.input.setAttribute("aria-activedescendant", options[picker.activeIndex].id);
        options[picker.activeIndex].scrollIntoView({ block: "nearest" });
    }

    function createRoleFromPicker(picker) {
        const roleName = normalizeRoleName(picker.input.value);
        if (!roleName) {
            return;
        }

        const existingRole = findRoleByName(roleName);
        if (existingRole) {
            selectRoleInPicker(picker, existingRole);
            return;
        }

        const newRole = {
            id: createCustomRoleId(),
            name: roleName,
            description: "",
            isCustom: true
        };
        customRoles.push(newRole);
        renderRolesList();
        updateRoleControls();
        filterStaffRows();
        selectRoleInPicker(picker, newRole, true);
        showRolesFeedback(`${newRole.name} was added for this page session.`);
    }

    rolePickers.forEach((picker) => {
        picker.input.addEventListener("input", () => {
            picker.cancelledName = "";
            picker.success.hidden = true;
            picker.success.textContent = "";
            renderRolePicker(picker);
        });

        picker.input.addEventListener("focus", () => {
            if (picker.skipFocusRender) {
                picker.skipFocusRender = false;
                return;
            }
            const currentName = normalizeRoleName(picker.input.value).toLowerCase();
            if (!currentName || currentName !== picker.cancelledName.toLowerCase()) {
                renderRolePicker(picker, !currentName);
            }
        });

        picker.input.addEventListener("keydown", (event) => {
            const options = picker.list.querySelectorAll("[role='option']");
            if (event.key === "ArrowDown" && !picker.list.hidden) {
                event.preventDefault();
                setRolePickerActiveOption(picker, picker.activeIndex + 1);
            } else if (event.key === "ArrowUp" && !picker.list.hidden) {
                event.preventDefault();
                const previousIndex = picker.activeIndex < 0
                    ? options.length - 1
                    : picker.activeIndex - 1;
                setRolePickerActiveOption(picker, previousIndex);
            } else if (event.key === "Enter" && (!picker.list.hidden || !picker.prompt.hidden)) {
                event.preventDefault();
                if (picker.activeIndex >= 0 && options[picker.activeIndex]) {
                    const role = getAllRoles().find((item) => item.id === options[picker.activeIndex].dataset.roleId);
                    if (role) selectRoleInPicker(picker, role);
                } else if (!picker.list.hidden) {
                    const exactRole = findRoleByName(picker.input.value);
                    const firstRole = exactRole || getAllRoles().find((role) =>
                        role.name.toLowerCase().includes(normalizeRoleName(picker.input.value).toLowerCase())
                    );
                    if (firstRole) selectRoleInPicker(picker, firstRole);
                } else {
                    picker.createButton.focus();
                }
            } else if (event.key === "Escape" && (!picker.list.hidden || !picker.prompt.hidden)) {
                event.preventDefault();
                picker.cancelledName = normalizeRoleName(picker.input.value);
                closeRolePicker(picker);
            }
        });

        picker.list.addEventListener("click", (event) => {
            const option = event.target.closest("[role='option'][data-role-id]");
            const role = option && getAllRoles().find((item) => item.id === option.dataset.roleId);
            if (role) selectRoleInPicker(picker, role);
        });

        picker.createButton.addEventListener("click", () => createRoleFromPicker(picker));
        picker.cancelButton.addEventListener("click", () => {
            picker.cancelledName = normalizeRoleName(picker.input.value);
            closeRolePicker(picker);
            picker.input.focus();
        });
        picker.root.addEventListener("focusout", (event) => {
            if (!picker.root.contains(event.relatedTarget)) {
                closeRolePicker(picker);
            }
        });
    });

    function createCustomRoleId() {
        let roleId;

        do {
            if (window.crypto && typeof window.crypto.randomUUID === "function") {
                roleId = `custom-${window.crypto.randomUUID()}`;
            } else {
                customRoleIdFallback += 1;
                roleId = `custom-${Date.now()}-${customRoleIdFallback}`;
            }
        } while (getAllRoles().some((role) => role.id === roleId));

        return roleId;
    }

    function renderRolesList() {
        rolesList.replaceChildren();

        getAllRoles().forEach((role) => {
            const row = document.createElement("tr");
            const nameCell = document.createElement("td");
            nameCell.className = "transaction-name";
            nameCell.textContent = role.name;
            row.append(nameCell);

            const descriptionCell = document.createElement("td");
            descriptionCell.textContent = role.description || "—";
            row.append(descriptionCell);

            const typeCell = document.createElement("td");
            const typeBadge = document.createElement("span");
            typeBadge.className = role.isCustom ? "badge badge-success" : "badge";
            typeBadge.textContent = role.isCustom ? "Custom" : "Built-in";
            typeCell.append(typeBadge);
            row.append(typeCell);

            const actionsCell = document.createElement("td");
            if (role.isCustom) {
                const actions = document.createElement("div");
                actions.className = "staff-row-actions role-row-actions";
                const editButton = document.createElement("button");
                editButton.type = "button";
                editButton.className = "btn btn-secondary";
                editButton.dataset.customRoleAction = "edit";
                editButton.dataset.roleId = role.id;
                editButton.setAttribute("aria-label", `Edit ${role.name} role`);
                editButton.textContent = "Edit";

                const deleteButton = document.createElement("button");
                deleteButton.type = "button";
                deleteButton.className = "btn btn-staff-dismiss";
                deleteButton.dataset.customRoleAction = "delete";
                deleteButton.dataset.roleId = role.id;
                deleteButton.setAttribute("aria-label", `Delete ${role.name} role`);
                deleteButton.textContent = "Delete";
                actions.append(editButton, deleteButton);
                actionsCell.append(actions);
            } else {
                actionsCell.textContent = "Built-in role";
            }

            row.append(actionsCell);
            rolesList.append(row);
        });
    }

    function showRolesFeedback(message, isError = false) {
        rolesFeedback.textContent = message;
        rolesFeedback.classList.toggle("is-error", isError);
        rolesFeedback.hidden = false;
    }

    function closeCustomRoleDialog() {
        customRoleDialog.close();
        customRoleForm.reset();
        customRoleFormMessage.hidden = true;
        customRoleFormMessage.textContent = "";
        customRoleOpenButton.focus();
    }

    let editingCustomRoleId = null;

    customRoleOpenButton?.addEventListener("click", () => {
        editingCustomRoleId = null;
        customRoleForm.reset();
        customRoleTitle.textContent = "Add Custom Role";
        customRoleSaveButton.textContent = "Save Role";
        customRoleFormMessage.hidden = true;
        customRoleFormMessage.textContent = "";
        customRoleDialog.showModal();
        customRoleName.focus();
    });

    customRoleCloseButton?.addEventListener("click", closeCustomRoleDialog);
    customRoleCancelButton?.addEventListener("click", closeCustomRoleDialog);
    customRoleDialog?.addEventListener("cancel", () => {
        customRoleForm.reset();
        customRoleFormMessage.hidden = true;
    });

    customRoleForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        const roleName = normalizeRoleName(customRoleName.value);
        const description = customRoleDescription.value.trim();

        if (!roleName) {
            customRoleFormMessage.textContent = "Enter a role name.";
            customRoleFormMessage.hidden = false;
            customRoleName.focus();
            return;
        }

        const duplicateRole = findRoleByName(roleName);

        if (duplicateRole && duplicateRole.id !== editingCustomRoleId) {
            customRoleFormMessage.textContent =
                `A role named “${duplicateRole.name}” already exists. Choose a different name.`;
            customRoleFormMessage.hidden = false;
            customRoleName.focus();
            return;
        }

        if (editingCustomRoleId) {
            const role = customRoles.find((item) => item.id === editingCustomRoleId);
            if (!role) {
                return;
            }

            role.name = roleName;
            role.description = description;
            staffRows.forEach((row) => {
                if (row.dataset.role === role.id) {
                    row.cells[2].textContent = role.name;
                    if (selectedStaffRow === row) {
                        detailRole.textContent = role.name;
                    }
                }
            });
            showRolesFeedback(`${roleName} was updated for this page session.`);
        } else {
            customRoles.push({
                id: createCustomRoleId(),
                name: roleName,
                description,
                isCustom: true
            });
            showRolesFeedback(`${roleName} was added for this page session.`);
        }

        renderRolesList();
        updateRoleControls();
        filterStaffRows();
        renderOrganogram();
        closeCustomRoleDialog();
    });

    rolesList?.addEventListener("click", (event) => {
        const actionButton = event.target.closest("button[data-custom-role-action]");
        if (!actionButton || !rolesList.contains(actionButton)) {
            return;
        }

        const role = customRoles.find((item) => item.id === actionButton.dataset.roleId);
        if (!role) {
            return;
        }

        if (actionButton.dataset.customRoleAction === "edit") {
            editingCustomRoleId = role.id;
            customRoleTitle.textContent = "Edit Custom Role";
            customRoleSaveButton.textContent = "Save Changes";
            customRoleName.value = role.name;
            customRoleDescription.value = role.description;
            customRoleFormMessage.hidden = true;
            customRoleFormMessage.textContent = "";
            customRoleDialog.showModal();
            customRoleName.focus();
            return;
        }

        const assignedStaff = staffRows.some((row) => row.dataset.role === role.id);
        if (assignedStaff) {
            showRolesFeedback(
                `“${role.name}” is assigned to a staff record. Reassign those staff members before deleting the role.`,
                true
            );
            return;
        }

        if (!window.confirm(`Delete the custom role “${role.name}”? Staff records will not be deleted.`)) {
            return;
        }

        customRoles = customRoles.filter((item) => item.id !== role.id);
        if (roleFilter.value === role.id) {
            roleFilter.value = "all";
        }
        renderRolesList();
        updateRoleControls();
        filterStaffRows();
        showRolesFeedback(`${role.name} was deleted for this page session.`);
    });

    function normalizeDepartmentName(name) {
        return name.trim().replace(/\s+/g, " ");
    }

    function getDepartmentById(id) {
        return departments.find((department) => department.id === id);
    }

    function findDepartmentByName(name) {
        const normalized = normalizeDepartmentName(name).toLowerCase();
        return normalized
            ? departments.find((department) => department.name.toLowerCase() === normalized)
            : undefined;
    }

    function createDepartmentId() {
        let id;
        do {
            if (window.crypto && typeof window.crypto.randomUUID === "function") {
                id = `department-${window.crypto.randomUUID()}`;
            } else {
                departmentIdFallback += 1;
                id = `department-${Date.now()}-${departmentIdFallback}`;
            }
        } while (departments.some((department) => department.id === id));
        return id;
    }

    const departmentPickers = Array.from(document.querySelectorAll("[data-department-picker]"))
        .map((root) => ({
            root,
            input: root.querySelector("[data-department-input]"),
            value: root.querySelector("[data-department-value]"),
            list: root.querySelector("[role='listbox']"),
            prompt: root.querySelector("[data-department-prompt]"),
            promptMessage: root.querySelector("[data-department-prompt-message]"),
            createButton: root.querySelector("[data-department-create]"),
            cancelButton: root.querySelector("[data-department-cancel]"),
            error: root.querySelector("[data-department-error]"),
            success: root.querySelector("[data-department-success]"),
            activeIndex: -1,
            cancelledName: ""
        }));

    function pickerForDepartmentValue(valueInput) {
        return departmentPickers.find((picker) => picker.value === valueInput);
    }

    function closeDepartmentPicker(picker) {
        picker.list.hidden = true;
        picker.prompt.hidden = true;
        picker.input.setAttribute("aria-expanded", "false");
        picker.input.removeAttribute("aria-activedescendant");
        picker.activeIndex = -1;
    }

    function selectDepartment(picker, department, showSuccess = false) {
        picker.input.value = department.name;
        picker.value.value = department.id;
        picker.error.hidden = true;
        picker.error.textContent = "";
        picker.success.hidden = !showSuccess;
        picker.success.textContent = showSuccess
            ? `${department.name} was created and selected. Continue with the current staff details.`
            : "";
        picker.input.setAttribute("aria-invalid", "false");
        picker.cancelledName = "";
        closeDepartmentPicker(picker);
    }

    function setDepartmentPickerValue(valueInput, departmentId) {
        const picker = pickerForDepartmentValue(valueInput);
        const department = getDepartmentById(departmentId);
        if (!picker) return;
        if (department) {
            selectDepartment(picker, department);
        } else {
            picker.input.value = "";
            picker.value.value = "";
            picker.error.hidden = true;
            picker.success.hidden = true;
            closeDepartmentPicker(picker);
        }
    }

    function showDepartmentPickerError(valueInput, message) {
        const picker = pickerForDepartmentValue(valueInput);
        if (!picker) return;
        picker.error.textContent = message;
        picker.error.hidden = false;
        picker.input.setAttribute("aria-invalid", "true");
        picker.input.focus();
    }

    function renderDepartmentPicker(picker, showAllWhenEmpty = false) {
        const query = normalizeDepartmentName(picker.input.value);
        const exact = findDepartmentByName(query);
        picker.value.value = exact?.id || "";
        picker.error.hidden = true;
        picker.error.textContent = "";
        picker.input.setAttribute("aria-invalid", "false");
        picker.list.replaceChildren();
        if (!query && !showAllWhenEmpty) {
            closeDepartmentPicker(picker);
            return;
        }
        const lowerQuery = query.toLowerCase();
        const matches = departments.filter((department) =>
            !lowerQuery || department.name.toLowerCase().includes(lowerQuery)
        );
        if (matches.length) {
            matches.forEach((department, index) => {
                const option = document.createElement("li");
                option.id = `${picker.list.id}-option-${index}`;
                option.dataset.departmentId = department.id;
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", department.id === exact?.id ? "true" : "false");
                option.textContent = department.name;
                picker.list.append(option);
            });
            picker.list.hidden = false;
            picker.prompt.hidden = true;
            picker.input.setAttribute("aria-expanded", "true");
            picker.activeIndex = -1;
            picker.input.removeAttribute("aria-activedescendant");
        } else if (query) {
            picker.list.hidden = true;
            picker.promptMessage.textContent = `“${query}” isn't an existing department. Would you like to create it?`;
            picker.prompt.hidden = false;
            picker.input.setAttribute("aria-expanded", "true");
        } else {
            closeDepartmentPicker(picker);
        }
    }

    function renderDepartmentList() {
        departmentsList.replaceChildren();
        if (!departments.length) {
            const empty = document.createElement("p");
            empty.className = "department-empty-message";
            empty.textContent = "No departments yet. Add a department to organize your team.";
            departmentsList.append(empty);
        }
        departments.forEach((department) => {
            const card = document.createElement("article");
            card.className = "department-card";
            const heading = document.createElement("h4");
            heading.textContent = department.name;
            const description = document.createElement("p");
            description.className = "department-description";
            description.textContent = department.description || "No description provided.";
            const count = staffRows.filter((row) =>
                row.dataset.departmentId === department.id && row.dataset.membershipStatus !== "dismissed"
            ).length;
            const countText = document.createElement("p");
            countText.className = "department-staff-count";
            countText.textContent = `${count} ${count === 1 ? "staff member" : "staff members"}`;
            const actions = document.createElement("div");
            actions.className = "department-actions";
            const edit = document.createElement("button");
            edit.type = "button";
            edit.className = "btn btn-secondary";
            edit.dataset.departmentAction = "edit";
            edit.dataset.departmentId = department.id;
            edit.textContent = "Edit";
            edit.setAttribute("aria-label", `Edit ${department.name} department`);
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "btn btn-staff-dismiss";
            remove.dataset.departmentAction = "delete";
            remove.dataset.departmentId = department.id;
            remove.textContent = "Delete";
            remove.setAttribute("aria-label", `Delete ${department.name} department`);
            actions.append(edit, remove);
            card.append(heading, description, countText, actions);
            departmentsList.append(card);
        });
        renderOrganogram();
    }

    function updateDepartmentControls() {
        departmentPickers.forEach((picker) => {
            const selected = getDepartmentById(picker.value.value);
            if (selected) picker.input.value = selected.name;
            if (!picker.list.hidden || !picker.prompt.hidden) {
                renderDepartmentPicker(picker, !picker.input.value.trim());
            }
        });
    }

    function showDepartmentsFeedback(message, isError = false) {
        departmentsFeedback.textContent = message;
        departmentsFeedback.classList.toggle("is-error", isError);
        departmentsFeedback.hidden = false;
    }

    departmentPickers.forEach((picker) => {
        picker.input.addEventListener("input", () => {
            picker.cancelledName = "";
            picker.success.hidden = true;
            picker.success.textContent = "";
            renderDepartmentPicker(picker);
        });
        picker.input.addEventListener("focus", () => {
            const current = normalizeDepartmentName(picker.input.value).toLowerCase();
            if (!current || current !== picker.cancelledName.toLowerCase()) {
                renderDepartmentPicker(picker, !current);
            }
        });
        picker.input.addEventListener("keydown", (event) => {
            const options = Array.from(picker.list.querySelectorAll("[role='option']"));
            if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !picker.list.hidden) {
                event.preventDefault();
                picker.activeIndex = event.key === "ArrowUp" && picker.activeIndex < 0
                    ? options.length - 1
                    : (picker.activeIndex + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
                options.forEach((option, index) => option.setAttribute("aria-selected", String(index === picker.activeIndex)));
                picker.input.setAttribute("aria-activedescendant", options[picker.activeIndex].id);
                options[picker.activeIndex].scrollIntoView({ block: "nearest" });
            } else if (event.key === "Enter" && (!picker.list.hidden || !picker.prompt.hidden)) {
                event.preventDefault();
                if (!picker.list.hidden) {
                    const item = options[picker.activeIndex] || options.find((option) =>
                        getDepartmentById(option.dataset.departmentId)?.name.toLowerCase() === normalizeDepartmentName(picker.input.value).toLowerCase()
                    ) || options[0];
                    const department = item && getDepartmentById(item.dataset.departmentId);
                    if (department) selectDepartment(picker, department);
                } else picker.createButton.focus();
            } else if (event.key === "Escape" && (!picker.list.hidden || !picker.prompt.hidden)) {
                event.preventDefault();
                picker.cancelledName = normalizeDepartmentName(picker.input.value);
                closeDepartmentPicker(picker);
            }
        });
        picker.list.addEventListener("click", (event) => {
            const option = event.target.closest("[role='option'][data-department-id]");
            const department = option && getDepartmentById(option.dataset.departmentId);
            if (department) selectDepartment(picker, department);
        });
        picker.createButton.addEventListener("click", () => {
            const name = normalizeDepartmentName(picker.input.value);
            if (!name) return;
            const existing = findDepartmentByName(name);
            if (existing) {
                selectDepartment(picker, existing);
                return;
            }
            const department = { id: createDepartmentId(), name, description: "" };
            departments.push(department);
            renderDepartmentList();
            updateDepartmentControls();
            selectDepartment(picker, department, true);
            showDepartmentsFeedback(`${name} was added for this page session.`);
        });
        picker.cancelButton.addEventListener("click", () => {
            picker.cancelledName = normalizeDepartmentName(picker.input.value);
            closeDepartmentPicker(picker);
            picker.input.focus();
        });
        picker.root.addEventListener("focusout", (event) => {
            if (!picker.root.contains(event.relatedTarget)) closeDepartmentPicker(picker);
        });
    });

    function closeDepartmentDialog() {
        departmentDialog.close();
        departmentForm.reset();
        departmentFormMessage.hidden = true;
        departmentFormMessage.textContent = "";
        departmentOpenButton.focus();
    }

    departmentOpenButton?.addEventListener("click", () => {
        editingDepartmentId = null;
        departmentForm.reset();
        departmentTitle.textContent = "Add Department";
        departmentSaveButton.textContent = "Save Department";
        departmentFormMessage.hidden = true;
        departmentDialog.showModal();
        departmentName.focus();
    });
    departmentDialogClose?.addEventListener("click", closeDepartmentDialog);
    departmentCancelButton?.addEventListener("click", closeDepartmentDialog);
    departmentDialog?.addEventListener("cancel", () => departmentForm.reset());
    departmentForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        const name = normalizeDepartmentName(departmentName.value);
        if (!name) {
            departmentFormMessage.textContent = "Enter a department name.";
            departmentFormMessage.hidden = false;
            departmentName.focus();
            return;
        }
        const duplicate = findDepartmentByName(name);
        if (duplicate && duplicate.id !== editingDepartmentId) {
            departmentFormMessage.textContent = `A department named “${duplicate.name}” already exists. Choose a different name.`;
            departmentFormMessage.hidden = false;
            departmentName.focus();
            return;
        }
        const description = departmentDescription.value.trim();
        if (editingDepartmentId) {
            const department = getDepartmentById(editingDepartmentId);
            if (!department) return;
            department.name = name;
            department.description = description;
            showDepartmentsFeedback(`${name} was updated for this page session.`);
        } else {
            departments.push({ id: createDepartmentId(), name, description });
            showDepartmentsFeedback(`${name} was added for this page session.`);
        }
        renderDepartmentList();
        updateDepartmentControls();
        if (selectedStaffRow) detailDepartment.textContent = getDepartmentName(selectedStaffRow) || "Not specified";
        closeDepartmentDialog();
    });
    departmentsList?.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-department-action]");
        if (!button || !departmentsList.contains(button)) return;
        const department = getDepartmentById(button.dataset.departmentId);
        if (!department) return;
        if (button.dataset.departmentAction === "edit") {
            editingDepartmentId = department.id;
            departmentTitle.textContent = "Edit Department";
            departmentSaveButton.textContent = "Save Changes";
            departmentName.value = department.name;
            departmentDescription.value = department.description;
            departmentFormMessage.hidden = true;
            departmentDialog.showModal();
            departmentName.focus();
            return;
        }
        const assigned = staffRows.some((row) => row.dataset.departmentId === department.id);
        if (assigned) {
            showDepartmentsFeedback(`“${department.name}” is assigned to a staff record. Reassign or clear those department assignments before deleting it.`, true);
            return;
        }
        if (!window.confirm(`Delete the unused department “${department.name}”? No staff records will be deleted.`)) return;
        departments = departments.filter((item) => item.id !== department.id);
        renderDepartmentList();
        updateDepartmentControls();
        showDepartmentsFeedback(`${department.name} was deleted for this page session.`);
    });

    function getDepartmentName(row) {
        return getDepartmentById(row.dataset.departmentId)?.name || row.dataset.department || "";
    }

    function renderOrganogram() {
        if (!organogramContainer) return;

        const currentStaff = staffRows.filter((row) => row.dataset.membershipStatus !== "dismissed");
        organogramContainer.replaceChildren();

        if (!currentStaff.length) {
            const emptyMessage = document.createElement("p");
            emptyMessage.className = "organogram-empty-state";
            emptyMessage.textContent = "No staff members are available to display yet.";
            organogramContainer.append(emptyMessage);
            return;
        }

        const staffById = new Map(
            currentStaff
                .filter((row) => row.dataset.staffId)
                .map((row) => [row.dataset.staffId, row])
        );
        const reports = new Map(currentStaff.map((row) => [row, []]));
        const roots = [];

        currentStaff.forEach((row) => {
            const manager = staffById.get(row.dataset.reportsToStaffId);
            if (manager && manager !== row && reports.has(manager)) {
                reports.get(manager).push(row);
            } else if (!manager) {
                // Missing or stale manager links become top-level entries. Self-links are reviewed below.
                roots.push(row);
            }
        });

        let listId = 0;
        const visited = new Set();

        function createPersonNode(row) {
            const item = document.createElement("li");
            item.className = "organogram-node";
            const directReports = (reports.get(row) || []).filter((report) => !visited.has(report));
            const card = directReports.length ? document.createElement("button") : document.createElement("article");
            if (directReports.length) {
                card.type = "button";
                card.className = "organogram-person-card organogram-manager-card";
            } else {
                card.className = "organogram-person-card";
            }

            const name = document.createElement("span");
            name.className = "organogram-person-name";
            name.textContent = row.cells[0]?.textContent.trim() || "Unnamed staff member";
            const role = document.createElement("span");
            role.className = "organogram-person-role";
            role.textContent = row.cells[2]?.textContent.trim() || "Role not assigned";
            const department = document.createElement("span");
            department.className = "organogram-person-department";
            department.textContent = getDepartmentName(row) || "Department not assigned";
            card.append(name, role, department);

            const status = row.dataset.status;
            if (status && status !== "active") {
                const statusLabel = document.createElement("span");
                statusLabel.className = "organogram-person-status";
                statusLabel.textContent = status.charAt(0).toUpperCase() + status.slice(1);
                card.append(statusLabel);
            }

            let childList = null;
            if (directReports.length) {
                childList = document.createElement("ul");
                childList.className = "organogram-children";
                childList.id = `organogram-team-${listId++}`;
                card.setAttribute("aria-expanded", "true");
                card.setAttribute("aria-controls", childList.id);
                card.setAttribute("aria-label", `${name.textContent}, ${role.textContent}; ${directReports.length} direct ${directReports.length === 1 ? "report" : "reports"}. Collapse team.`);
                const teamCount = document.createElement("span");
                teamCount.className = "organogram-team-count";
                teamCount.textContent = `${directReports.length} direct ${directReports.length === 1 ? "report" : "reports"}`;
                const indicator = document.createElement("span");
                indicator.className = "organogram-toggle-indicator";
                indicator.setAttribute("aria-hidden", "true");
                card.append(teamCount, indicator);
            }

            item.append(card);
            if (childList) item.append(childList);
            return { item, childList };
        }

        function appendPerson(list, row) {
            if (visited.has(row)) return;
            visited.add(row);
            const { item, childList } = createPersonNode(row);
            list.append(item);
            if (childList) {
                (reports.get(row) || []).forEach((report) => appendPerson(childList, report));
            }
        }

        function appendRootGroup(title, people, className = "") {
            if (!people.length) return;
            const group = document.createElement("section");
            group.className = `organogram-group${className ? ` ${className}` : ""}`;
            const heading = document.createElement("h4");
            heading.className = "organogram-group-title";
            heading.textContent = title;
            const tree = document.createElement("ul");
            tree.className = "organogram-tree";
            people.forEach((person) => appendPerson(tree, person));
            group.append(heading, tree);
            organogramContainer.append(group);
        }

        const rootsByDepartment = new Map();
        roots.forEach((row) => {
            const department = getDepartmentName(row) || "Department not assigned";
            if (!rootsByDepartment.has(department)) rootsByDepartment.set(department, []);
            rootsByDepartment.get(department).push(row);
        });
        rootsByDepartment.forEach((people, department) => appendRootGroup(department, people));

        // Any unvisited records belong to a circular reporting chain. Render them once as review roots.
        const unvisited = currentStaff.filter((row) => !visited.has(row));
        appendRootGroup("Reporting links to review", unvisited, "organogram-review-group");

    }

    organogramContainer?.addEventListener("click", (event) => {
        const toggle = event.target.closest("button[aria-controls^='organogram-team-']");
        if (!toggle || !organogramContainer.contains(toggle)) return;
        const team = document.getElementById(toggle.getAttribute("aria-controls"));
        if (!team) return;
        const isExpanded = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", String(!isExpanded));
        toggle.setAttribute(
            "aria-label",
            toggle.getAttribute("aria-label").replace(isExpanded ? "Collapse team." : "Expand team.", isExpanded ? "Expand team." : "Collapse team.")
        );
        team.hidden = isExpanded;
    });

    renderDepartmentList();
    updateDepartmentControls();

    updateRoleControls();
    renderRolesList();

    function updateReportsToOptions() {
        const selectedId = addStaffReportsTo.value;
        addStaffReportsTo.replaceChildren(new Option("No selection", ""));

        staffRows.forEach((row) => {
            if (row.dataset.status !== "active" || row.dataset.membershipStatus === "dismissed") {
                return;
            }

            const option = new Option(row.cells[0].textContent.trim(), row.dataset.staffId);
            addStaffReportsTo.add(option);
        });

        if (Array.from(addStaffReportsTo.options).some((option) => option.value === selectedId)) {
            addStaffReportsTo.value = selectedId;
        }
    }

    function updateEditReportsToOptions(staffRow) {
        const selectedManagerId = staffRow.dataset.reportsToStaffId || "";
        const manager = staffRows.find(
            (row) => row.dataset.staffId === selectedManagerId &&
                row !== staffRow && row.dataset.membershipStatus !== "dismissed"
        );
        editReportsTo.value = manager ? manager.dataset.staffId : "";
        editReportsTo.dataset.selectionValid = "true";
        editReportsToSearch.value = manager
            ? manager.cells[0].textContent.trim()
            : "No manager";
        editReportsToSearch.setAttribute("aria-invalid", "false");
        closeEditReportsToOptions();
        editReportsToError.hidden = true;
        editReportsToError.textContent = "";
    }

    function closeEditReportsToOptions() {
        editReportsToOptions.hidden = true;
        editReportsToSearch.setAttribute("aria-expanded", "false");
        editReportsToSearch.removeAttribute("aria-activedescendant");
        editReportsToOptions.dataset.activeIndex = "-1";
    }

    function selectEditReportsTo(staffRow, managerId) {
        const manager = staffRows.find(
            (row) => row.dataset.staffId === managerId && row !== staffRow &&
                row.dataset.membershipStatus !== "dismissed"
        );
        editReportsTo.value = manager ? manager.dataset.staffId : "";
        editReportsTo.dataset.selectionValid = "true";
        editReportsToSearch.value = manager
            ? manager.cells[0].textContent.trim()
            : "No manager";
        editReportsToSearch.setAttribute("aria-invalid", "false");
        editReportsToError.hidden = true;
        editReportsToError.textContent = "";
        closeEditReportsToOptions();
    }

    function renderEditReportsToOptions(staffRow) {
        const typedQuery = editReportsToSearch.value.trim().toLowerCase();
        const query = editReportsTo.dataset.selectionValid === "true" && typedQuery === "no manager"
            ? ""
            : typedQuery;
        const matches = staffRows.filter((row) => {
            if (row === staffRow || row.dataset.membershipStatus === "dismissed") return false;
            const name = row.cells[0].textContent.trim().toLowerCase();
            const role = row.cells[2].textContent.trim().toLowerCase();
            return !query || name.includes(query) || role.includes(query);
        });

        editReportsToOptions.replaceChildren();
        let optionIndex = 0;
        const addOption = (label, managerId) => {
            const option = document.createElement("li");
            option.id = `staff-edit-reports-to-option-${optionIndex++}`;
            option.setAttribute("role", "option");
            option.setAttribute(
                "aria-selected",
                String(editReportsTo.dataset.selectionValid === "true" && managerId === editReportsTo.value)
            );
            option.dataset.managerId = managerId;
            option.textContent = label;
            editReportsToOptions.append(option);
        };

        if (!query) addOption("No manager", "");
        matches.forEach((row) => {
            const name = row.cells[0].textContent.trim() || "Unnamed staff member";
            const role = row.cells[2].textContent.trim() || "Role not assigned";
            const status = row.dataset.status && row.dataset.status !== "active"
                ? ` (${row.dataset.status})`
                : "";
            addOption(`${name} — ${role}${status}`, row.dataset.staffId);
        });
        if (query) addOption("No manager", "");

        if (!matches.length && query) {
            const emptyOption = document.createElement("li");
            emptyOption.className = "role-picker-message";
            emptyOption.setAttribute("role", "presentation");
            emptyOption.textContent = "No matching staff members.";
            editReportsToOptions.append(emptyOption);
        }

        editReportsToOptions.hidden = false;
        editReportsToSearch.setAttribute("aria-expanded", "true");
        editReportsToOptions.dataset.activeIndex = "-1";
        editReportsToSearch.removeAttribute("aria-activedescendant");
    }

    editReportsToSearch.addEventListener("input", () => {
        editReportsTo.value = "";
        editReportsTo.dataset.selectionValid = "false";
        editReportsToSearch.setAttribute("aria-invalid", "false");
        editReportsToError.hidden = true;
        editReportsToError.textContent = "";
        renderEditReportsToOptions(selectedStaffRow);
    });

    editReportsToSearch.addEventListener("focus", () => {
        if (selectedStaffRow) renderEditReportsToOptions(selectedStaffRow);
    });

    editReportsToSearch.addEventListener("keydown", (event) => {
        const options = Array.from(editReportsToOptions.querySelectorAll("[role='option']"));
        let activeIndex = Number(editReportsToOptions.dataset.activeIndex || -1);

        if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !editReportsToOptions.hidden && options.length) {
            event.preventDefault();
            activeIndex = event.key === "ArrowUp" && activeIndex < 0
                ? options.length - 1
                : (activeIndex + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
            options.forEach((option, index) => option.setAttribute("aria-selected", String(index === activeIndex)));
            editReportsToOptions.dataset.activeIndex = String(activeIndex);
            editReportsToSearch.setAttribute("aria-activedescendant", options[activeIndex].id);
            options[activeIndex].scrollIntoView({ block: "nearest" });
        } else if (event.key === "Enter" && !editReportsToOptions.hidden && options.length) {
            event.preventDefault();
            const option = activeIndex >= 0
                ? options[activeIndex]
                : options.find((item) => item.dataset.managerId) ||
                    (editReportsToSearch.value.trim() ? null : options[0]);
            if (!option) return;
            selectEditReportsTo(selectedStaffRow, option.dataset.managerId);
        } else if (event.key === "Escape" && !editReportsToOptions.hidden) {
            event.preventDefault();
            closeEditReportsToOptions();
        }
    });

    editReportsToOptions.addEventListener("click", (event) => {
        const option = event.target.closest("[role='option'][data-manager-id]");
        if (option && selectedStaffRow) {
            selectEditReportsTo(selectedStaffRow, option.dataset.managerId);
        }
    });

    function wouldCreateReportingCycle(staffRow, managerId) {
        let manager = staffRows.find(
            (row) => row.dataset.staffId === managerId && row.dataset.membershipStatus !== "dismissed"
        );
        const visitedManagers = new Set();

        while (manager) {
            if (manager === staffRow || visitedManagers.has(manager)) return true;
            visitedManagers.add(manager);
            manager = staffRows.find(
                (row) => row.dataset.staffId === manager.dataset.reportsToStaffId &&
                    row.dataset.membershipStatus !== "dismissed"
            );
        }

        return false;
    }

    function createStaffId() {
        let staffId;

        do {
            if (window.crypto && typeof window.crypto.randomUUID === "function") {
                staffId = `staff-${window.crypto.randomUUID()}`;
            } else {
                staffIdFallback += 1;
                staffId = `staff-${Date.now()}-${staffIdFallback}`;
            }
        } while (staffRows.some((row) => row.dataset.staffId === staffId));

        return staffId;
    }

    function addStaffCell(row, content, className) {
        const cell = document.createElement("td");
        if (className) {
            cell.className = className;
        }
        cell.textContent = content;
        row.append(cell);
        return cell;
    }

    function closeAddStaffDialog() {
        addStaffDialog.close();
        addStaffForm.reset();
        resetRolePicker(pickerForValueInput(addStaffRole));
        setDepartmentPickerValue(addStaffDepartment, "");
        addStaffButton.focus();
    }

    addStaffButton?.addEventListener("click", () => {
        updateReportsToOptions();
        addStaffFeedback.hidden = true;
        addStaffFeedback.textContent = "";
        addStaffDialog.showModal();
        addStaffName.focus();
    });

    addStaffCloseButton?.addEventListener("click", closeAddStaffDialog);
    addStaffCancelButton?.addEventListener("click", closeAddStaffDialog);
    addStaffDialog?.addEventListener("cancel", () => {
        addStaffForm.reset();
        resetRolePicker(pickerForValueInput(addStaffRole));
        setDepartmentPickerValue(addStaffDepartment, "");
    });

    addStaffForm?.addEventListener("submit", (event) => {
        event.preventDefault();

        const fullName = addStaffName.value.trim();
        const emailAddress = addStaffEmail.value.trim();
        addStaffName.setCustomValidity(fullName ? "" : "Enter a full name.");
        addStaffEmail.setCustomValidity(emailAddress ? "" : "Enter an email address.");

        if (!addStaffForm.reportValidity()) {
            return;
        }

        const roleDefinition = getAllRoles().find((role) => role.id === addStaffRole.value);
        if (!roleDefinition) {
            showRolePickerError(addStaffRole, "Choose an existing role or create a custom role first.");
            return;
        }

        const departmentQuery = normalizeDepartmentName(addStaffDepartmentInput.value);
        const departmentDefinition = getDepartmentById(addStaffDepartment.value);
        if (departmentQuery && !departmentDefinition) {
            showDepartmentPickerError(addStaffDepartment, "Choose an existing department or create it first.");
            return;
        }

        const row = document.createElement("tr");
        row.dataset.staffId = createStaffId();
        row.dataset.role = addStaffRole.value;
        row.dataset.status = "pending";
        if (departmentDefinition) row.dataset.departmentId = departmentDefinition.id;
        row.dataset.reportsToStaffId = addStaffReportsTo.value;
        addStaffCell(row, fullName, "transaction-name");

        const emailCell = document.createElement("td");
        const emailLink = document.createElement("a");
        emailLink.href = `mailto:${emailAddress}`;
        emailLink.textContent = emailAddress;
        emailCell.append(emailLink);
        row.append(emailCell);

        const roleLabel = roleDefinition.name;
        addStaffCell(row, roleLabel);
        const statusCell = document.createElement("td");
        const statusBadge = document.createElement("span");
        statusBadge.className = "badge badge-warning";
        statusBadge.textContent = "Pending";
        statusCell.append(statusBadge);
        row.append(statusCell);
        addStaffCell(row, "Never");

        const actionsCell = document.createElement("td");
        const actions = document.createElement("div");
        actions.className = "staff-row-actions";
        const manageButton = document.createElement("button");
        manageButton.type = "button";
        manageButton.className = "btn btn-secondary";
        manageButton.setAttribute("aria-label", `Manage ${fullName}`);
        manageButton.textContent = "Manage";
        actions.append(manageButton);
        actionsCell.append(actions);
        row.append(actionsCell);

        staffRows.push(row);
        tableBody.append(row);
        renderDepartmentList();
        filterStaffRows();
        addStaffFeedback.textContent = row.hidden
            ? `Pending invitation created for ${fullName} in this page session. It is hidden by the current search or filters; no invitation was sent.`
            : `Pending invitation created for ${fullName} in this page session. No invitation was sent.`;
        addStaffFeedback.hidden = false;
        closeAddStaffDialog();
    });

    dismissCancelButton?.addEventListener("click", () => {
        dismissalRow = null;
        staffDismissDialog.close();
    });

    staffDismissDialog?.addEventListener("cancel", () => {
        dismissalRow = null;
    });

    dismissConfirmButton?.addEventListener("click", () => {
        if (!dismissalRow || dismissalRow.dataset.membershipStatus === "dismissed") {
            staffDismissDialog.close();
            return;
        }

        dismissalRow.dataset.membershipStatus = "dismissed";
        delete dismissalRow.dataset.reportsToStaffId;
        staffRows.forEach((row) => {
            if (row.dataset.reportsToStaffId === dismissalRow.dataset.staffId) {
                delete row.dataset.reportsToStaffId;
            }
        });
        if (selectedStaffRow === dismissalRow) {
            destructiveActions.hidden = true;
            saveMessage.textContent =
                "Membership dismissed from this business. Their FinEx account and other business memberships are unchanged.";
            saveMessage.hidden = false;
        }

        dismissalRow = null;
        renderDepartmentList();
        filterStaffRows();
        staffDismissDialog.close();
    });

    if (staffDetailsDialog && typeof staffDetailsDialog.showModal === "function") {
        const closeButton = staffDetailsDialog.querySelector("[data-staff-dialog-close]");

        function populateEditForm() {
            if (!selectedStaffRow) {
                return;
            }

            editNameInput.value = selectedStaffRow.cells[0].textContent.trim();
            editEmailInput.value =
                selectedStaffRow.cells[1].querySelector("a[href^='mailto:']").textContent.trim();
            setRolePickerValue(editRoleSelect, selectedStaffRow.dataset.role);
            setDepartmentPickerValue(editDepartment, selectedStaffRow.dataset.departmentId);
            updateEditReportsToOptions(selectedStaffRow);
        }

        editOpenButton?.addEventListener("click", () => {
            populateEditForm();
            saveMessage.hidden = true;
            detailsView.hidden = true;
            editForm.hidden = false;
            editNameInput.focus();
        });

        editCancelButton?.addEventListener("click", () => {
            populateEditForm();
            editForm.hidden = true;
            detailsView.hidden = false;
            saveMessage.hidden = true;
        });

        editForm?.addEventListener("submit", (event) => {
            event.preventDefault();

            if (!selectedStaffRow) {
                return;
            }

            const fullName = editNameInput.value.trim();
            editNameInput.setCustomValidity(fullName ? "" : "Enter a full name.");

            if (!editNameInput.reportValidity()) {
                return;
            }

            const emailAddress = editEmailInput.value.trim();
            editEmailInput.value = emailAddress;
            editEmailInput.setCustomValidity(
                emailAddress ? "" : "Enter an email address."
            );

            if (!editEmailInput.reportValidity()) {
                return;
            }

            const role = editRoleSelect.value;
            const roleDefinition = getAllRoles().find((item) => item.id === role);
            if (!roleDefinition) {
                showRolePickerError(editRoleSelect, "Choose an existing role or create a custom role first.");
                return;
            }
            const roleLabel = roleDefinition.name;
            const departmentQuery = normalizeDepartmentName(
                document.getElementById("staff-edit-department-search").value
            );
            const departmentDefinition = getDepartmentById(editDepartment.value);
            if (departmentQuery && !departmentDefinition) {
                showDepartmentPickerError(editDepartment, "Choose an existing department or create it first.");
                return;
            }
            const managerId = editReportsTo.value;
            const selectedManagerIsValid = editReportsTo.dataset.selectionValid === "true" &&
                (!managerId || staffRows.some((row) => row.dataset.staffId === managerId &&
                    row !== selectedStaffRow && row.dataset.membershipStatus !== "dismissed"));
            if (!selectedManagerIsValid) {
                editReportsToError.textContent = "Select a staff member from the suggestions, or choose No manager.";
                editReportsToError.hidden = false;
                editReportsToSearch.setAttribute("aria-invalid", "true");
                editReportsToSearch.focus();
                return;
            }
            if (managerId && wouldCreateReportingCycle(selectedStaffRow, managerId)) {
                editReportsToError.textContent = "Choose a manager who does not report to this staff member.";
                editReportsToError.hidden = false;
                editReportsToSearch.setAttribute("aria-invalid", "true");
                editReportsToSearch.focus();
                return;
            }
            editReportsToError.hidden = true;
            editReportsToError.textContent = "";
            editReportsToSearch.setAttribute("aria-invalid", "false");
            const emailLink = selectedStaffRow.cells[1].querySelector("a[href^='mailto:']");

            selectedStaffRow.cells[0].textContent = fullName;
            emailLink.textContent = emailAddress;
            emailLink.href = `mailto:${emailAddress}`;
            selectedStaffRow.cells[2].textContent = roleLabel;
            selectedStaffRow.dataset.role = role;
            if (departmentDefinition) {
                selectedStaffRow.dataset.departmentId = departmentDefinition.id;
                delete selectedStaffRow.dataset.department;
            } else {
                delete selectedStaffRow.dataset.departmentId;
                delete selectedStaffRow.dataset.department;
            }
            if (managerId) {
                selectedStaffRow.dataset.reportsToStaffId = managerId;
            } else {
                delete selectedStaffRow.dataset.reportsToStaffId;
            }

            const manageButton = selectedStaffRow.querySelector("button[aria-label^='Manage ']");
            manageButton?.setAttribute("aria-label", `Manage ${fullName}`);
            dialogDismissActionButton?.setAttribute("aria-label", `Dismiss ${fullName}`);
            dialogStatusActionButton?.setAttribute(
                "aria-label",
                `${selectedStaffRow.dataset.status === "suspended" ? "Restore" : "Suspend"} ${fullName}`
            );

            detailName.textContent = fullName;
            detailEmail.textContent = emailAddress;
            detailEmail.href = emailLink.href;
            detailRole.textContent = roleLabel;
            detailDepartment.textContent = getDepartmentName(selectedStaffRow) || "Not specified";
            const managerRow = staffRows.find(
                (row) => row.dataset.staffId === managerId && row.dataset.membershipStatus !== "dismissed"
            );
            detailReportsTo.textContent = managerRow
                ? managerRow.cells[0].textContent.trim()
                : "Not specified";
            setRolePickerValue(roleSelect, role);

            renderDepartmentList();
            filterStaffRows();

            editForm.hidden = true;
            detailsView.hidden = false;
            saveMessage.textContent = "Staff details updated for this page session.";
            saveMessage.hidden = false;
        });

        saveRoleButton?.addEventListener("click", () => {
            if (!selectedStaffRow) {
                return;
            }

            const selectedRole = roleSelect.value;

            const roleDefinition = getAllRoles().find((item) => item.id === selectedRole);
            if (!roleDefinition) {
                showRolePickerError(roleSelect, "Choose an existing role or create a custom role first.");
                return;
            }

            if (selectedStaffRow.dataset.role === selectedRole) {
                saveMessage.textContent = "The role is unchanged.";
                saveMessage.hidden = false;
                return;
            }

            selectedStaffRow.dataset.role = selectedRole;
            selectedStaffRow.cells[2].textContent = roleDefinition.name;
            detailRole.textContent = selectedStaffRow.cells[2].textContent;

            filterStaffRows();
            renderOrganogram();

            saveMessage.textContent = "Role updated for this page session.";
            saveMessage.hidden = false;
        });

        dialogStatusActionButton?.addEventListener("click", () => {
            if (!selectedStaffRow || selectedStaffRow.dataset.membershipStatus === "dismissed") {
                return;
            }

            const staffName = selectedStaffRow.cells[0].textContent.trim();
            const isSuspended = selectedStaffRow.dataset.status === "suspended";

            if (!isSuspended && !window.confirm(
                `Suspend ${staffName}? This demonstration only changes the displayed status and does not affect account access.`
            )) {
                return;
            }

            const newStatus = isSuspended ? "active" : "suspended";
            const statusBadge = selectedStaffRow.cells[3].querySelector(".badge");
            selectedStaffRow.dataset.status = newStatus;
            detailStatus.textContent = newStatus === "active" ? "Active" : "Suspended";

            if (statusBadge) {
                statusBadge.classList.remove("badge-success", "badge-warning", "badge-danger");
                statusBadge.classList.add(newStatus === "active" ? "badge-success" : "badge-danger");
                statusBadge.textContent = detailStatus.textContent;
            }

            dialogStatusActionButton.textContent =
                newStatus === "suspended" ? "Restore Staff" : "Suspend Staff";
            dialogStatusActionButton.classList.toggle("btn-staff-suspend", newStatus !== "suspended");
            dialogStatusActionButton.classList.toggle("btn-staff-restore", newStatus === "suspended");
            dialogStatusActionButton.setAttribute(
                "aria-label",
                `${newStatus === "suspended" ? "Restore" : "Suspend"} ${staffName}`
            );
            saveMessage.textContent = `Status updated to ${detailStatus.textContent} for this page session.`;
            saveMessage.hidden = false;
            filterStaffRows();
            renderOrganogram();
        });

        dialogDismissActionButton?.addEventListener("click", () => {
            if (!selectedStaffRow || selectedStaffRow.dataset.membershipStatus === "dismissed" ||
                !staffDismissDialog || typeof staffDismissDialog.showModal !== "function") {
                return;
            }

            dismissalRow = selectedStaffRow;
            staffDismissName.textContent = selectedStaffRow.cells[0].textContent.trim();
            staffDismissDialog.showModal();
        });

        closeButton?.addEventListener("click", () => staffDetailsDialog.close());
    }

    tableBody.addEventListener("click", (event) => {
        const manageButton = event.target.closest("button[aria-label^='Manage ']");

        if (!manageButton || !tableBody.contains(manageButton) ||
            !staffDetailsDialog || typeof staffDetailsDialog.showModal !== "function") {
            return;
        }

        const row = manageButton.closest("tr[data-role][data-status]");
        const emailLink = row?.cells[1]?.querySelector("a[href^='mailto:']");

        if (!row || !emailLink) {
            return;
        }

        if (row.dataset.membershipStatus === "dismissed") {
            return;
        }

        selectedStaffRow = row;
        detailName.textContent = row.cells[0].textContent.trim();
        detailEmail.textContent = emailLink.textContent.trim();
        detailEmail.href = emailLink.getAttribute("href");
        detailRole.textContent = row.cells[2].textContent.trim();
        detailStatus.textContent = row.cells[3].textContent.trim();
        detailLastSeen.textContent = row.cells[4].textContent.trim();
        detailDepartment.textContent = getDepartmentName(row) || "Not specified";
        const reportsToRow = staffRows.find(
            (staffRow) => staffRow.dataset.staffId === row.dataset.reportsToStaffId
        );
        detailReportsTo.textContent = reportsToRow
            ? reportsToRow.cells[0].textContent.trim()
            : "Not specified";
        setRolePickerValue(roleSelect, row.dataset.role);
        const isSuspended = row.dataset.status === "suspended";
        dialogStatusActionButton.textContent = isSuspended ? "Restore Staff" : "Suspend Staff";
        dialogStatusActionButton.classList.toggle("btn-staff-suspend", !isSuspended);
        dialogStatusActionButton.classList.toggle("btn-staff-restore", isSuspended);
        dialogStatusActionButton.setAttribute(
            "aria-label",
            `${isSuspended ? "Restore" : "Suspend"} ${row.cells[0].textContent.trim()}`
        );
        dialogDismissActionButton.setAttribute(
            "aria-label",
            `Dismiss ${row.cells[0].textContent.trim()}`
        );
        destructiveActions.hidden = row.dataset.membershipStatus === "dismissed";
        editNameInput.value = row.cells[0].textContent.trim();
        editEmailInput.value = emailLink.textContent.trim();
        setRolePickerValue(editRoleSelect, row.dataset.role);
        editForm.hidden = true;
        detailsView.hidden = false;
        saveMessage.hidden = true;
        saveMessage.textContent = "";

        staffDetailsDialog.showModal();
    });

    function filterStaffRows() {
        const searchTerm = searchInput.value.trim().toLowerCase();
        const selectedRole = roleFilter.value;
        const selectedStatus = statusFilter.value;
        let visibleRows = 0;

        staffRows.forEach((row) => {
            const staffName = row.cells[0].textContent.toLowerCase();
            const staffEmail = row.cells[1].textContent.toLowerCase();
            const staffRole = row.cells[2].textContent.toLowerCase();
            const matchesSearch =
                staffName.includes(searchTerm) ||
                staffEmail.includes(searchTerm) ||
                staffRole.includes(searchTerm);
            const matchesRole =
                selectedRole === "all" || row.dataset.role === selectedRole;
            const matchesStatus =
                selectedStatus === "all" || row.dataset.status === selectedStatus;
            const matchesAllFilters =
                row.dataset.membershipStatus !== "dismissed" &&
                matchesSearch && matchesRole && matchesStatus;

            row.hidden = !matchesAllFilters;

            if (matchesAllFilters) {
                visibleRows += 1;
            }
        });

        if (emptyState) {
            emptyState.hidden = visibleRows > 0;

            if (visibleRows === 0) {
                const enteredTerm = searchInput.value.trim();

                if (enteredTerm) {
                    emptyState.textContent =
                        `No staff member found matching '${enteredTerm}'. Check your search and try again.`;
                } else if (selectedRole !== "all" || selectedStatus !== "all") {
                    const activeFilters = [];

                    if (selectedRole !== "all") {
                        activeFilters.push(`Role: ${roleFilter.selectedOptions[0].textContent}`);
                    }

                    if (selectedStatus !== "all") {
                        activeFilters.push(`Status: ${statusFilter.selectedOptions[0].textContent}`);
                    }

                    emptyState.textContent =
                        `No staff members match the selected filters (${activeFilters.join(", ")}).`;
                } else {
                    emptyState.textContent = "No staff members are available.";
                }
            } else {
                emptyState.textContent = "";
            }
        }
    }

    searchInput.addEventListener("input", filterStaffRows);
    searchInput.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") {
            return;
        }

        event.preventDefault();
        filterStaffRows();

        if (staffDirectoryPanel) {
            const panelRect = staffDirectoryPanel.getBoundingClientRect();
            const panelCenter = panelRect.top + panelRect.height / 2;
            const requestedScrollTop =
                window.scrollY + panelCenter - window.innerHeight / 2;
            const maxScrollTop =
                document.documentElement.scrollHeight - window.innerHeight;
            const targetScrollTop = Math.min(
                Math.max(0, requestedScrollTop),
                Math.max(0, maxScrollTop)
            );

            window.scrollTo({ top: targetScrollTop, behavior: "smooth" });
        }
    });
    roleFilter.addEventListener("change", filterStaffRows);
    statusFilter.addEventListener("change", filterStaffRows);

    staffTable.dataset.staffFiltersInitialized = "true";
})();

    // Page-scoped mobile navigation behavior. Shared dashboard.js currently
    // handles desktop collapse but no longer wires the mobile menu button.
    const sidebar = document.getElementById("sidebar");
    const mobileSidebarToggle = document.getElementById("mobile-menu-toggle");
    if (sidebar && mobileSidebarToggle && mobileSidebarToggle.dataset.staffMobileBound !== "true") {
        mobileSidebarToggle.dataset.staffMobileBound = "true";

        function setStaffMobileSidebarOpen(isOpen) {
            sidebar.classList.toggle("mobile-open", isOpen);
            mobileSidebarToggle.setAttribute("aria-expanded", String(isOpen));
            mobileSidebarToggle.setAttribute(
                "aria-label",
                isOpen ? "Close navigation menu" : "Open navigation menu"
            );
        }

        mobileSidebarToggle.addEventListener("click", () => {
            const isOpen = mobileSidebarToggle.getAttribute("aria-expanded") !== "true";
            setStaffMobileSidebarOpen(isOpen);
            if (isOpen) sidebar.querySelector(".sidebar-nav a.nav-link")?.focus();
        });

        sidebar.addEventListener("click", (event) => {
            if (event.target.closest("a.nav-link") && sidebar.classList.contains("mobile-open")) {
                setStaffMobileSidebarOpen(false);
                mobileSidebarToggle.focus();
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && sidebar.classList.contains("mobile-open")) {
                setStaffMobileSidebarOpen(false);
                mobileSidebarToggle.focus();
            }
        });

        window.addEventListener("resize", () => {
            if (window.innerWidth > 650 && sidebar.classList.contains("mobile-open")) {
                setStaffMobileSidebarOpen(false);
            }
        });
    }
})();
