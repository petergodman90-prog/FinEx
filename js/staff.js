/* Live staff directory backed by /api/v1/staff. */
(function () {
    "use strict";

    const API = window.FinexAPI;
    if (!API) {
        return;
    }

    const ACCESS_LABELS = {
        owner: "Owner",
        admin: "Admin",
        manager: "Manager",
        staff: "Staff",
        viewer: "Viewer"
    };
    const ACCESS_ORDER = ["owner", "admin", "manager", "staff", "viewer"];
    const READ_ROLES = new Set(["owner", "admin", "manager"]);
    const MANAGE_ROLES = new Set(["owner", "admin"]);

    const byId = function (id) {
        return document.getElementById(id);
    };

    const state = {
        me: null,
        canRead: false,
        canManage: false,
        items: [],
        summary: null,
        departments: [],
        roles: [],
        invitations: [],
        selected: null,
        dismissTarget: null,
        editingDepartment: null,
        editingRole: null,
        busy: false
    };

    const els = {
        feedback: byId("staff-page-feedback"),
        permissionPanel: byId("staff-permission-panel"),
        permissionMessage: byId("staff-permission-message"),
        workspace: byId("staff-workspace"),
        addOpen: byId("staff-add-open"),
        total: byId("staff-total"),
        active: byId("staff-active"),
        pending: byId("staff-pending"),
        suspended: byId("staff-suspended"),
        search: byId("staff-search"),
        roleFilter: byId("staff-role-filter"),
        statusFilter: byId("staff-status-filter"),
        departmentFilter: byId("staff-department-filter"),
        empty: byId("staff-empty-state"),
        tableBody: byId("staff-directory-body"),
        organogram: byId("staff-organogram"),
        rolesBody: byId("staff-roles-list"),
        rolesFeedback: byId("roles-feedback"),
        roleOpen: byId("custom-role-open"),
        departmentsList: byId("departments-list"),
        departmentsFeedback: byId("departments-feedback"),
        departmentOpen: byId("department-open"),
        invitationsPanel: byId("staff-invitations-panel"),
        invitationsList: byId("staff-invitations-list")
    };

    function errorMessage(error, fallback) {
        return error && error.message ? error.message : (fallback || "Something went wrong. Please try again.");
    }

    function handleAuthError(error) {
        if (error && error.status === 401) {
            window.location.replace("../index.html");
            return true;
        }
        return false;
    }

    function showPageMessage(text, kind) {
        if (!els.feedback) {
            return;
        }
        els.feedback.textContent = text;
        els.feedback.classList.toggle("is-error", kind === "error");
        els.feedback.hidden = false;
    }

    function hidePageMessage() {
        if (els.feedback) {
            els.feedback.hidden = true;
            els.feedback.textContent = "";
            els.feedback.classList.remove("is-error");
        }
    }

    function showFormMessage(element, text, kind) {
        if (!element) {
            return;
        }
        element.textContent = text;
        element.classList.toggle("is-error", kind === "error");
        element.hidden = false;
    }

    function hideFormMessage(element) {
        if (element) {
            element.hidden = true;
            element.textContent = "";
            element.classList.remove("is-error");
        }
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

    function accessLabel(value) {
        return ACCESS_LABELS[value] || value || "Member";
    }

    function setOptions(select, options, placeholder, selectedValue) {
        if (!select) {
            return;
        }
        select.replaceChildren();
        if (placeholder !== null) {
            const empty = document.createElement("option");
            empty.value = "";
            empty.textContent = placeholder;
            select.appendChild(empty);
        }
        options.forEach(function (option) {
            const item = document.createElement("option");
            item.value = String(option.value);
            item.textContent = option.label;
            select.appendChild(item);
        });
        if (selectedValue !== undefined && selectedValue !== null) {
            select.value = String(selectedValue);
        }
        if (!select.value && options.length) {
            select.value = String(options[0].value);
        }
    }

    function accessGrantOptions() {
        const actorRole = state.me ? state.me.role : "";
        if (actorRole === "owner") {
            return ACCESS_ORDER.map(function (role) {
                return { value: role, label: accessLabel(role) };
            });
        }
        if (actorRole === "admin") {
            return ["manager", "staff", "viewer"].map(function (role) {
                return { value: role, label: accessLabel(role) };
            });
        }
        return [];
    }

    function staffOptionLabel(person) {
        const role = person.job_title || accessLabel(person.role);
        return `${person.full_name} — ${role}`;
    }

    function activeStaffOptions(excludeId) {
        return state.items
            .filter(function (person) {
                return person.status === "active" && person.id !== excludeId;
            })
            .sort(function (a, b) {
                return a.full_name.localeCompare(b.full_name);
            })
            .map(function (person) {
                return { value: person.id, label: staffOptionLabel(person) };
            });
    }

    function jobTitleOptions(currentValue) {
        const options = state.roles.map(function (role) {
            return { value: role.name, label: role.name };
        });
        if (currentValue && !options.some(function (option) {
            return option.value === currentValue;
        })) {
            options.push({ value: currentValue, label: currentValue });
        }
        return options;
    }

    function departmentOptions() {
        return state.departments.map(function (department) {
            return { value: department.id, label: department.name };
        });
    }

    function formatLastSeen(value) {
        if (!value) {
            return "Never";
        }
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return "Unknown";
        }
        const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
        if (seconds < 60) {
            return "Just now";
        }
        if (seconds < 3600) {
            return `${Math.floor(seconds / 60)} min ago`;
        }
        if (seconds < 86400) {
            return `${Math.floor(seconds / 3600)} hr ago`;
        }
        if (seconds < 604800) {
            return `${Math.floor(seconds / 86400)} day${Math.floor(seconds / 86400) === 1 ? "" : "s"} ago`;
        }
        return date.toLocaleDateString(undefined, {
            year: "numeric",
            month: "short",
            day: "numeric"
        });
    }

    function formatDate(value) {
        if (!value) {
            return "—";
        }
        const date = new Date(value);
        return Number.isNaN(date.getTime())
            ? "—"
            : date.toLocaleDateString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric"
            });
    }

    function statusBadge(status) {
        const badge = document.createElement("span");
        badge.className = "badge";
        if (status === "active") {
            badge.classList.add("badge-success");
            badge.textContent = "Active";
        } else if (status === "pending") {
            badge.classList.add("badge-warning");
            badge.textContent = "Pending";
        } else {
            badge.classList.add("badge-danger");
            badge.textContent = "Suspended";
        }
        return badge;
    }

    function renderSummary() {
        const summary = state.summary || {
            total: state.items.length,
            active: state.items.filter(function (item) { return item.status === "active"; }).length,
            pending: state.items.filter(function (item) { return item.status === "pending"; }).length,
            suspended: state.items.filter(function (item) { return item.status === "suspended"; }).length
        };
        if (els.total) els.total.textContent = String(summary.total || 0);
        if (els.active) els.active.textContent = String(summary.active || 0);
        if (els.pending) els.pending.textContent = String(summary.pending || 0);
        if (els.suspended) els.suspended.textContent = String(summary.suspended || 0);
    }

    function renderDirectory() {
        if (!els.tableBody) {
            return;
        }
        const term = els.search ? els.search.value.trim().toLowerCase() : "";
        const role = els.roleFilter ? els.roleFilter.value : "all";
        const status = els.statusFilter ? els.statusFilter.value : "all";
        const department = els.departmentFilter ? els.departmentFilter.value : "all";

        const visible = state.items.filter(function (person) {
            if (role !== "all" && person.role !== role) {
                return false;
            }
            if (status !== "all" && person.status !== status) {
                return false;
            }
            if (department !== "all" && person.department_id !== department) {
                return false;
            }
            if (!term) {
                return true;
            }
            return [
                person.full_name,
                person.email || "",
                person.role,
                person.job_title || "",
                person.department_name || ""
            ].join(" ").toLowerCase().includes(term);
        });

        els.tableBody.replaceChildren();

        if (!visible.length) {
            els.empty.textContent = state.items.length
                ? "No staff match these filters."
                : "No staff members yet. Add an existing FinEx account to invite them.";
            els.empty.hidden = false;
            return;
        }
        els.empty.hidden = true;

        visible.forEach(function (person) {
            const row = document.createElement("tr");
            row.dataset.staffId = person.id;
            row.dataset.role = person.role;
            row.dataset.status = person.status;

            const nameCell = document.createElement("td");
            nameCell.className = "transaction-name";
            const name = document.createElement("strong");
            name.textContent = person.full_name;
            nameCell.appendChild(name);
            if (person.is_self) {
                const self = document.createElement("span");
                self.className = "staff-self-label";
                self.textContent = " You";
                nameCell.appendChild(self);
            }
            row.appendChild(nameCell);

            const emailCell = document.createElement("td");
            if (person.email) {
                const link = document.createElement("a");
                link.href = `mailto:${person.email}`;
                link.textContent = person.email;
                emailCell.appendChild(link);
            } else {
                emailCell.textContent = "—";
            }
            row.appendChild(emailCell);

            const accessCell = document.createElement("td");
            accessCell.textContent = accessLabel(person.role);
            row.appendChild(accessCell);

            const jobTitleCell = document.createElement("td");
            jobTitleCell.textContent = person.job_title || "—";
            row.appendChild(jobTitleCell);

            const departmentCell = document.createElement("td");
            departmentCell.textContent = person.department_name || "—";
            row.appendChild(departmentCell);

            const statusCell = document.createElement("td");
            statusCell.appendChild(statusBadge(person.status));
            row.appendChild(statusCell);

            const lastSeenCell = document.createElement("td");
            lastSeenCell.textContent = formatLastSeen(person.last_seen_at);
            row.appendChild(lastSeenCell);

            const actionCell = document.createElement("td");
            const actions = document.createElement("div");
            actions.className = "staff-row-actions";
            const manage = document.createElement("button");
            manage.type = "button";
            manage.className = "btn btn-secondary";
            manage.textContent = person.can_manage ? "Manage" : "View";
            manage.setAttribute("aria-label", `${manage.textContent} ${person.full_name}`);
            manage.addEventListener("click", function () {
                openDetails(person.id);
            });
            actions.appendChild(manage);
            actionCell.appendChild(actions);
            row.appendChild(actionCell);

            els.tableBody.appendChild(row);
        });
    }

    function renderDepartmentFilter() {
        if (!els.departmentFilter) {
            return;
        }
        const selected = els.departmentFilter.value || "all";
        els.departmentFilter.replaceChildren();
        const all = document.createElement("option");
        all.value = "all";
        all.textContent = "All departments";
        els.departmentFilter.appendChild(all);
        departmentOptions().forEach(function (option) {
            const item = document.createElement("option");
            item.value = option.value;
            item.textContent = option.label;
            els.departmentFilter.appendChild(item);
        });
        els.departmentFilter.value = departmentOptions().some(function (option) {
            return option.value === selected;
        }) ? selected : "all";
    }

    function organogramCard(person, children) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "organogram-person-card";
        if (children.length) {
            card.classList.add("organogram-manager-card");
            card.setAttribute("aria-expanded", "true");
        }

        const name = document.createElement("span");
        name.className = "organogram-person-name";
        name.textContent = person.full_name;
        card.appendChild(name);

        const role = document.createElement("span");
        role.className = "organogram-person-role";
        role.textContent = person.job_title || accessLabel(person.role);
        card.appendChild(role);

        const department = document.createElement("span");
        department.className = "organogram-person-department";
        department.textContent = person.department_name || "Unassigned";
        card.appendChild(department);

        if (children.length) {
            const count = document.createElement("span");
            count.className = "organogram-team-count";
            count.textContent = `${children.length} direct report${children.length === 1 ? "" : "s"}`;
            card.appendChild(count);

            const indicator = document.createElement("span");
            indicator.className = "organogram-toggle-indicator";
            indicator.setAttribute("aria-hidden", "true");
            card.appendChild(indicator);
        }
        return card;
    }

    function renderOrganogram() {
        if (!els.organogram) {
            return;
        }
        els.organogram.replaceChildren();
        const active = state.items.filter(function (person) {
            return person.status === "active";
        });
        if (!active.length) {
            const empty = document.createElement("p");
            empty.className = "organogram-empty-state";
            empty.textContent = "The organogram appears when the workspace has active staff.";
            els.organogram.appendChild(empty);
            return;
        }

        const byIdMap = new Map(active.map(function (person) {
            return [person.id, person];
        }));
        const childrenByManager = new Map();
        active.forEach(function (person) {
            const managerId = person.reports_to_id && byIdMap.has(person.reports_to_id)
                ? person.reports_to_id
                : null;
            if (!childrenByManager.has(managerId)) {
                childrenByManager.set(managerId, []);
            }
            childrenByManager.get(managerId).push(person);
        });
        childrenByManager.forEach(function (people) {
            people.sort(function (a, b) {
                return a.full_name.localeCompare(b.full_name);
            });
        });

        const visited = new Set();

        function buildNode(person) {
            if (visited.has(person.id)) {
                return null;
            }
            visited.add(person.id);
            const children = (childrenByManager.get(person.id) || [])
                .map(buildNode)
                .filter(Boolean);
            const node = document.createElement("li");
            node.className = "organogram-node";
            const card = organogramCard(person, children);
            node.appendChild(card);
            if (children.length) {
                const list = document.createElement("ul");
                list.className = "organogram-children";
                children.forEach(function (child) { list.appendChild(child); });
                node.appendChild(list);
                card.addEventListener("click", function () {
                    const expanded = card.getAttribute("aria-expanded") !== "false";
                    card.setAttribute("aria-expanded", String(!expanded));
                    list.hidden = expanded;
                });
            }
            return node;
        }

        function appendGroup(title, people) {
            const nodes = people.map(buildNode).filter(Boolean);
            if (!nodes.length) {
                return;
            }
            const group = document.createElement("section");
            group.className = "organogram-group";
            const heading = document.createElement("h4");
            heading.className = "organogram-group-title";
            heading.textContent = title;
            const tree = document.createElement("ul");
            tree.className = "organogram-tree";
            nodes.forEach(function (node) { tree.appendChild(node); });
            group.append(heading, tree);
            els.organogram.appendChild(group);
        }

        const rootsByDepartment = new Map();
        (childrenByManager.get(null) || []).forEach(function (person) {
            const department = person.department_name || "Unassigned";
            if (!rootsByDepartment.has(department)) {
                rootsByDepartment.set(department, []);
            }
            rootsByDepartment.get(department).push(person);
        });
        rootsByDepartment.forEach(function (people, department) {
            appendGroup(department, people);
        });

        // Cycles or a broken reporting chain are still visible for review
        // instead of silently disappearing from the organogram.
        const remaining = active.filter(function (person) {
            return !visited.has(person.id);
        });
        if (remaining.length) {
            const review = document.createElement("section");
            review.className = "organogram-group organogram-review-group";
            const heading = document.createElement("h4");
            heading.className = "organogram-group-title";
            heading.textContent = "Review reporting structure";
            const tree = document.createElement("ul");
            tree.className = "organogram-tree";
            remaining.forEach(function (person) {
                const node = buildNode(person);
                if (node) {
                    tree.appendChild(node);
                }
            });
            review.append(heading, tree);
            els.organogram.appendChild(review);
        }
    }

    function renderDepartments() {
        if (!els.departmentsList) {
            return;
        }
        els.departmentsList.replaceChildren();
        if (!state.departments.length) {
            const empty = document.createElement("p");
            empty.className = "department-empty-message";
            empty.textContent = "No departments yet.";
            els.departmentsList.appendChild(empty);
            return;
        }

        state.departments.forEach(function (department) {
            const card = document.createElement("article");
            card.className = "department-card";
            const title = document.createElement("h4");
            title.textContent = department.name;
            card.appendChild(title);
            const description = document.createElement("p");
            description.className = "department-description";
            description.textContent = department.description || "No description.";
            card.appendChild(description);
            const count = state.items.filter(function (person) {
                return person.department_id === department.id;
            }).length;
            const countLine = document.createElement("p");
            countLine.className = "department-staff-count";
            countLine.textContent = `${count} staff member${count === 1 ? "" : "s"}`;
            card.appendChild(countLine);

            if (state.canManage) {
                const actions = document.createElement("div");
                actions.className = "department-actions";
                const edit = document.createElement("button");
                edit.type = "button";
                edit.className = "btn btn-secondary";
                edit.textContent = "Edit";
                edit.addEventListener("click", function () {
                    openDepartmentDialog(department);
                });
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "btn btn-staff-dismiss";
                remove.textContent = "Delete";
                remove.addEventListener("click", function () {
                    deleteDepartment(department);
                });
                actions.append(edit, remove);
                card.appendChild(actions);
            }
            els.departmentsList.appendChild(card);
        });
    }

    function renderRoles() {
        if (!els.rolesBody) {
            return;
        }
        els.rolesBody.replaceChildren();
        if (!state.roles.length) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");
            cell.colSpan = 5;
            cell.className = "live-empty";
            cell.textContent = "No job titles available.";
            row.appendChild(cell);
            els.rolesBody.appendChild(row);
            return;
        }
        state.roles.forEach(function (role) {
            const row = document.createElement("tr");
            const name = document.createElement("td");
            name.className = "transaction-name";
            name.textContent = role.name;
            row.appendChild(name);
            const description = document.createElement("td");
            description.textContent = role.description || "—";
            row.appendChild(description);
            const type = document.createElement("td");
            type.textContent = role.is_custom ? "Custom" : "Built-in";
            row.appendChild(type);
            const assigned = document.createElement("td");
            assigned.textContent = String(role.assigned_count || 0);
            row.appendChild(assigned);
            const actions = document.createElement("td");
            if (role.is_custom && state.canManage) {
                const wrap = document.createElement("div");
                wrap.className = "staff-row-actions role-row-actions";
                const edit = document.createElement("button");
                edit.type = "button";
                edit.className = "btn btn-secondary";
                edit.textContent = "Edit";
                edit.addEventListener("click", function () { openRoleDialog(role); });
                const remove = document.createElement("button");
                remove.type = "button";
                remove.className = "btn btn-staff-dismiss";
                remove.textContent = "Delete";
                remove.addEventListener("click", function () { deleteRole(role); });
                wrap.append(edit, remove);
                actions.appendChild(wrap);
            } else {
                actions.textContent = "—";
            }
            row.appendChild(actions);
            els.rolesBody.appendChild(row);
        });
    }

    function renderInvitations() {
        if (!els.invitationsPanel || !els.invitationsList) {
            return;
        }
        els.invitationsList.replaceChildren();
        els.invitationsPanel.hidden = state.invitations.length === 0;
        state.invitations.forEach(function (invitation) {
            const card = document.createElement("article");
            card.className = "department-card";
            const title = document.createElement("h4");
            title.textContent = invitation.org_name;
            card.appendChild(title);
            const detail = document.createElement("p");
            detail.className = "department-description";
            detail.textContent = `Invited as ${accessLabel(invitation.role)}${invitation.job_title ? ` · ${invitation.job_title}` : ""}. Sent ${formatDate(invitation.invited_at)}.`;
            card.appendChild(detail);
            const actions = document.createElement("div");
            actions.className = "department-actions";
            const accept = document.createElement("button");
            accept.type = "button";
            accept.className = "btn btn-primary";
            accept.textContent = "Accept";
            accept.addEventListener("click", function () { acceptInvitation(invitation, accept); });
            const decline = document.createElement("button");
            decline.type = "button";
            decline.className = "btn btn-staff-dismiss";
            decline.textContent = "Decline";
            decline.addEventListener("click", function () { declineInvitation(invitation, decline); });
            actions.append(accept, decline);
            card.appendChild(actions);
            els.invitationsList.appendChild(card);
        });
    }

    function renderDetails() {
        const person = state.selected;
        if (!person) {
            return;
        }
        const email = byId("staff-detail-email");
        byId("staff-detail-name").textContent = person.full_name;
        if (person.email && email) {
            email.textContent = person.email;
            email.href = `mailto:${person.email}`;
        } else if (email) {
            email.textContent = "No email on file";
            email.removeAttribute("href");
        }
        byId("staff-detail-role").textContent = accessLabel(person.role);
        byId("staff-detail-job-title").textContent = person.job_title || "Not specified";
        byId("staff-detail-status").textContent = person.status === "active"
            ? "Active"
            : person.status === "pending" ? "Pending" : "Suspended";
        byId("staff-detail-last-seen").textContent = formatLastSeen(person.last_seen_at);
        byId("staff-detail-department").textContent = person.department_name || "Not specified";
        byId("staff-detail-reports-to").textContent = person.reports_to_name || "Not specified";

        const editable = state.canManage && !person.is_self;
        byId("staff-edit-open").hidden = !editable;
        const destructive = byId("staff-dialog-destructive-actions");
        destructive.hidden = !editable;
        const statusAction = byId("staff-dialog-status-action");
        const dismissAction = byId("staff-dialog-dismiss-action");
        if (person.status === "pending") {
            statusAction.hidden = true;
            dismissAction.textContent = "Cancel Invitation";
        } else {
            statusAction.hidden = false;
            statusAction.textContent = person.status === "active" ? "Suspend Staff" : "Restore Staff";
            statusAction.classList.toggle("btn-staff-suspend", person.status === "active");
            statusAction.classList.toggle("btn-staff-restore", person.status !== "active");
            dismissAction.textContent = "Dismiss Staff";
        }
        byId("staff-details-view").hidden = false;
        byId("staff-edit-form").hidden = true;
        hideFormMessage(byId("staff-details-message"));
    }

    function openDetails(membershipId) {
        const person = state.items.find(function (item) {
            return item.id === membershipId;
        });
        if (!person) {
            showPageMessage("That staff record is no longer available.", "error");
            return;
        }
        state.selected = person;
        renderDetails();
        openDialog(byId("staff-details-dialog"));
    }

    function populateAddForm() {
        setOptions(byId("staff-add-role"), accessGrantOptions(), null, "staff");
        setOptions(byId("staff-add-job-title"), jobTitleOptions(), "No job title");
        setOptions(byId("staff-add-department"), departmentOptions(), "No department");
        setOptions(byId("staff-add-reports-to"), activeStaffOptions(), "No manager");
    }

    function populateEditForm(person) {
        const accessOptions = accessGrantOptions();
        if (!accessOptions.some(function (option) { return option.value === person.role; })) {
            accessOptions.push({ value: person.role, label: accessLabel(person.role) });
        }
        setOptions(byId("staff-edit-access"), accessOptions, null, person.role);
        setOptions(byId("staff-edit-job-title"), jobTitleOptions(person.job_title), "No job title", person.job_title || "");
        setOptions(byId("staff-edit-department"), departmentOptions(), "No department", person.department_id || "");
        const managerOptions = activeStaffOptions(person.id);
        if (person.reports_to_id && !managerOptions.some(function (option) {
            return option.value === person.reports_to_id;
        })) {
            const manager = state.items.find(function (item) {
                return item.id === person.reports_to_id;
            });
            managerOptions.push({
                value: person.reports_to_id,
                label: manager ? `${manager.full_name} (inactive)` : "Current manager (inactive)"
            });
        }
        setOptions(byId("staff-edit-reports-to"), managerOptions, "No manager", person.reports_to_id || "");
    }

    async function loadDirectory() {
        const page = await API.request("/staff?limit=100");
        state.items = page.items || [];
        state.summary = page.summary || null;
        renderSummary();
        renderDirectory();
        renderOrganogram();
        renderDepartments();
        renderDepartmentFilter();
    }

    async function loadDepartments() {
        state.departments = await API.request("/staff/departments");
        renderDepartments();
        renderDepartmentFilter();
    }

    async function loadRoles() {
        state.roles = await API.request("/staff/roles");
        renderRoles();
    }

    async function refreshWorkspace() {
        await loadDirectory();
        await Promise.all([loadDepartments(), loadRoles()]);
    }

    async function loadInvitations() {
        state.invitations = await API.request("/auth/invitations");
        renderInvitations();
    }

    async function acceptInvitation(invitation, button) {
        if (state.busy) {
            return;
        }
        state.busy = true;
        button.disabled = true;
        try {
            await API.request(`/auth/invitations/${invitation.membership_id}/accept`, { method: "POST" });
        } catch (error) {
            state.busy = false;
            button.disabled = false;
            if (!handleAuthError(error)) {
                showPageMessage(errorMessage(error, "Could not accept the invitation."), "error");
            }
            return;
        }

        try {
            await API.request("/auth/switch-org", {
                method: "POST",
                body: JSON.stringify({ org_id: invitation.org_id })
            });
            window.location.reload();
        } catch (error) {
            state.busy = false;
            button.disabled = false;
            if (handleAuthError(error)) {
                return;
            }
            await loadInvitations().catch(function () {});
            showPageMessage(
                `Invitation to ${invitation.org_name} accepted. Open the dashboard and switch to that workspace.`
            );
        }
    }

    async function declineInvitation(invitation, button) {
        if (state.busy) {
            return;
        }
        state.busy = true;
        button.disabled = true;
        try {
            await API.request(`/auth/invitations/${invitation.membership_id}/decline`, { method: "POST" });
            await loadInvitations();
            showPageMessage("Invitation declined.");
        } catch (error) {
            if (!handleAuthError(error)) {
                showPageMessage(errorMessage(error, "Could not decline the invitation."), "error");
            }
        } finally {
            state.busy = false;
            button.disabled = false;
        }
    }

    async function submitAddStaff(event) {
        event.preventDefault();
        const button = byId("staff-add-save");
        const feedback = byId("staff-add-feedback");
        hideFormMessage(feedback);
        button.disabled = true;
        try {
            const role = byId("staff-add-role").value;
            const jobTitle = byId("staff-add-job-title").value;
            const departmentId = byId("staff-add-department").value;
            const reportsToId = byId("staff-add-reports-to").value;
            const invited = await API.request("/staff", {
                method: "POST",
                body: JSON.stringify({
                    email: byId("staff-add-email").value.trim(),
                    role: role,
                    job_title: jobTitle || null,
                    department_id: departmentId || null,
                    reports_to_id: reportsToId || null
                })
            });
            closeDialog(byId("staff-add-dialog"));
            byId("staff-add-form").reset();
            showPageMessage(`Invitation created for ${invited.full_name}. It stays pending until they accept it.`);
            await refreshWorkspace();
        } catch (error) {
            if (!handleAuthError(error)) {
                showFormMessage(feedback, errorMessage(error, "Could not create the invitation."), "error");
            }
        } finally {
            button.disabled = false;
        }
    }

    async function submitEditStaff(event) {
        event.preventDefault();
        const person = state.selected;
        if (!person) {
            return;
        }
        const button = byId("staff-edit-save");
        const feedback = byId("staff-edit-message");
        hideFormMessage(feedback);
        const payload = {};
        const role = byId("staff-edit-access").value;
        const jobTitle = byId("staff-edit-job-title").value;
        const departmentId = byId("staff-edit-department").value;
        const reportsToId = byId("staff-edit-reports-to").value;
        if (role && role !== person.role) payload.role = role;
        if (jobTitle !== (person.job_title || "")) payload.job_title = jobTitle || null;
        if (departmentId !== (person.department_id || "")) payload.department_id = departmentId || null;
        if (reportsToId !== (person.reports_to_id || "")) payload.reports_to_id = reportsToId || null;
        if (!Object.keys(payload).length) {
            showFormMessage(feedback, "No changes to save.", "error");
            return;
        }

        button.disabled = true;
        try {
            const updated = await API.request(`/staff/${person.id}`, {
                method: "PATCH",
                body: JSON.stringify(payload)
            });
            state.selected = updated;
            await refreshWorkspace();
            state.selected = state.items.find(function (item) {
                return item.id === updated.id;
            }) || updated;
            renderDetails();
            showFormMessage(byId("staff-details-message"), "Staff details updated.");
        } catch (error) {
            if (!handleAuthError(error)) {
                showFormMessage(feedback, errorMessage(error, "Could not update staff details."), "error");
            }
        } finally {
            button.disabled = false;
        }
    }

    async function changeStatus() {
        const person = state.selected;
        if (!person || person.status === "pending" || state.busy) {
            return;
        }
        state.busy = true;
        const button = byId("staff-dialog-status-action");
        button.disabled = true;
        const nextStatus = person.status === "active" ? "suspended" : "active";
        try {
            const updated = await API.request(`/staff/${person.id}`, {
                method: "PATCH",
                body: JSON.stringify({ status: nextStatus })
            });
            await refreshWorkspace();
            state.selected = state.items.find(function (item) {
                return item.id === updated.id;
            }) || updated;
            renderDetails();
            showFormMessage(
                byId("staff-details-message"),
                nextStatus === "suspended"
                    ? "Staff suspended. Their active sessions were revoked."
                    : "Staff restored. They can sign in to this workspace again."
            );
        } catch (error) {
            if (!handleAuthError(error)) {
                showFormMessage(byId("staff-details-message"), errorMessage(error, "Could not change the account status."), "error");
            }
        } finally {
            state.busy = false;
            button.disabled = false;
        }
    }

    function openDismissDialog() {
        const person = state.selected;
        if (!person) {
            return;
        }
        state.dismissTarget = person;
        byId("staff-dismiss-name").textContent = person.full_name;
        byId("staff-dismiss-title").textContent = person.status === "pending"
            ? "Cancel Invitation"
            : "Dismiss Staff";
        byId("staff-dismiss-confirm").textContent = person.status === "pending"
            ? "Cancel Invitation"
            : "Confirm Dismissal";
        openDialog(byId("staff-dismiss-dialog"));
    }

    async function confirmDismiss() {
        const person = state.dismissTarget;
        if (!person || state.busy) {
            return;
        }
        state.busy = true;
        const button = byId("staff-dismiss-confirm");
        button.disabled = true;
        try {
            await API.request(`/staff/${person.id}`, { method: "DELETE" });
            closeDialog(byId("staff-dismiss-dialog"));
            closeDialog(byId("staff-details-dialog"));
            state.selected = null;
            state.dismissTarget = null;
            showPageMessage(person.status === "pending" ? "Invitation cancelled." : "Staff member dismissed.");
            await refreshWorkspace();
        } catch (error) {
            if (!handleAuthError(error)) {
                showPageMessage(errorMessage(error, "Could not remove this staff member."), "error");
            }
        } finally {
            state.busy = false;
            button.disabled = false;
        }
    }

    function openDepartmentDialog(department) {
        state.editingDepartment = department || null;
        byId("department-dialog-title").textContent = department ? "Edit Department" : "Add Department";
        byId("department-name").value = department ? department.name : "";
        byId("department-description").value = department ? (department.description || "") : "";
        hideFormMessage(byId("department-form-message"));
        openDialog(byId("department-dialog"));
    }

    async function submitDepartment(event) {
        event.preventDefault();
        const button = byId("department-save");
        const feedback = byId("department-form-message");
        hideFormMessage(feedback);
        button.disabled = true;
        const editing = state.editingDepartment;
        try {
            const body = JSON.stringify({
                name: byId("department-name").value.trim(),
                description: byId("department-description").value.trim() || null
            });
            if (editing) {
                await API.request(`/staff/departments/${editing.id}`, { method: "PATCH", body: body });
            } else {
                await API.request("/staff/departments", { method: "POST", body: body });
            }
            closeDialog(byId("department-dialog"));
            showFormMessage(els.departmentsFeedback, editing ? "Department updated." : "Department created.");
            await refreshWorkspace();
        } catch (error) {
            if (!handleAuthError(error)) {
                showFormMessage(feedback, errorMessage(error, "Could not save the department."), "error");
            }
        } finally {
            button.disabled = false;
        }
    }

    async function deleteDepartment(department) {
        if (!window.confirm(`Delete the ${department.name} department? Staff must be reassigned first.`)) {
            return;
        }
        try {
            await API.request(`/staff/departments/${department.id}`, { method: "DELETE" });
            showFormMessage(els.departmentsFeedback, "Department deleted.");
            await refreshWorkspace();
        } catch (error) {
            if (!handleAuthError(error)) {
                showFormMessage(els.departmentsFeedback, errorMessage(error, "Could not delete the department."), "error");
            }
        }
    }

    function openRoleDialog(role) {
        state.editingRole = role || null;
        byId("custom-role-title").textContent = role ? "Edit Custom Role" : "Add Custom Role";
        byId("custom-role-name").value = role ? role.name : "";
        byId("custom-role-description").value = role ? (role.description || "") : "";
        hideFormMessage(byId("custom-role-form-message"));
        openDialog(byId("custom-role-dialog"));
    }

    async function submitRole(event) {
        event.preventDefault();
        const button = byId("custom-role-save");
        const feedback = byId("custom-role-form-message");
        hideFormMessage(feedback);
        button.disabled = true;
        const editing = state.editingRole;
        try {
            const body = JSON.stringify({
                name: byId("custom-role-name").value.trim(),
                description: byId("custom-role-description").value.trim() || null
            });
            if (editing) {
                await API.request(`/staff/roles/${editing.id}`, { method: "PATCH", body: body });
            } else {
                await API.request("/staff/roles", { method: "POST", body: body });
            }
            closeDialog(byId("custom-role-dialog"));
            showFormMessage(els.rolesFeedback, editing ? "Role updated." : "Custom role created.");
            await refreshWorkspace();
        } catch (error) {
            if (!handleAuthError(error)) {
                showFormMessage(feedback, errorMessage(error, "Could not save the role."), "error");
            }
        } finally {
            button.disabled = false;
        }
    }

    async function deleteRole(role) {
        if (!window.confirm(`Delete the ${role.name} role? Staff must be reassigned first.`)) {
            return;
        }
        try {
            await API.request(`/staff/roles/${role.id}`, { method: "DELETE" });
            showFormMessage(els.rolesFeedback, "Role deleted.");
            await refreshWorkspace();
        } catch (error) {
            if (!handleAuthError(error)) {
                showFormMessage(els.rolesFeedback, errorMessage(error, "Could not delete the role."), "error");
            }
        }
    }

    function bindEvents() {
        els.search?.addEventListener("input", renderDirectory);
        els.roleFilter?.addEventListener("change", renderDirectory);
        els.statusFilter?.addEventListener("change", renderDirectory);
        els.departmentFilter?.addEventListener("change", renderDirectory);

        els.addOpen?.addEventListener("click", function () {
            hidePageMessage();
            hideFormMessage(byId("staff-add-feedback"));
            byId("staff-add-form").reset();
            populateAddForm();
            openDialog(byId("staff-add-dialog"));
        });
        byId("staff-add-close")?.addEventListener("click", function () { closeDialog(byId("staff-add-dialog")); });
        byId("staff-add-cancel")?.addEventListener("click", function () { closeDialog(byId("staff-add-dialog")); });
        byId("staff-add-form")?.addEventListener("submit", submitAddStaff);

        byId("staff-details-close")?.addEventListener("click", function () { closeDialog(byId("staff-details-dialog")); });
        byId("staff-edit-open")?.addEventListener("click", function () {
            if (!state.selected) return;
            populateEditForm(state.selected);
            hideFormMessage(byId("staff-edit-message"));
            byId("staff-details-view").hidden = true;
            byId("staff-edit-form").hidden = false;
        });
        byId("staff-edit-cancel")?.addEventListener("click", function () {
            byId("staff-edit-form").hidden = true;
            byId("staff-details-view").hidden = false;
        });
        byId("staff-edit-form")?.addEventListener("submit", submitEditStaff);
        byId("staff-dialog-status-action")?.addEventListener("click", changeStatus);
        byId("staff-dialog-dismiss-action")?.addEventListener("click", openDismissDialog);
        byId("staff-dismiss-cancel")?.addEventListener("click", function () { closeDialog(byId("staff-dismiss-dialog")); });
        byId("staff-dismiss-confirm")?.addEventListener("click", confirmDismiss);

        els.departmentOpen?.addEventListener("click", function () { openDepartmentDialog(null); });
        byId("department-dialog-close")?.addEventListener("click", function () { closeDialog(byId("department-dialog")); });
        byId("department-cancel")?.addEventListener("click", function () { closeDialog(byId("department-dialog")); });
        byId("department-form")?.addEventListener("submit", submitDepartment);

        els.roleOpen?.addEventListener("click", function () { openRoleDialog(null); });
        byId("custom-role-close")?.addEventListener("click", function () { closeDialog(byId("custom-role-dialog")); });
        byId("custom-role-cancel")?.addEventListener("click", function () { closeDialog(byId("custom-role-dialog")); });
        byId("custom-role-form")?.addEventListener("submit", submitRole);

        [
            "staff-details-dialog",
            "staff-add-dialog",
            "department-dialog",
            "custom-role-dialog",
            "staff-dismiss-dialog"
        ].forEach(function (id) {
            const dialog = byId(id);
            dialog?.addEventListener("click", function (event) {
                if (event.target === dialog) {
                    closeDialog(dialog);
                }
            });
        });
    }

    async function loadMe() {
        const me = await API.request("/auth/me");
        state.me = me;
        const role = String(me.role || "").toLowerCase();
        state.canRead = READ_ROLES.has(role);
        state.canManage = MANAGE_ROLES.has(role);

        if (!state.canRead) {
            if (els.workspace) els.workspace.hidden = true;
            if (els.permissionPanel) els.permissionPanel.hidden = false;
            if (els.permissionMessage) {
                els.permissionMessage.textContent = "You need manager access or higher to view the staff directory. Pending invitations appear in their own section when available.";
            }
            els.addOpen.hidden = true;
            return;
        }

        if (els.workspace) els.workspace.hidden = false;
        if (els.permissionPanel) els.permissionPanel.hidden = true;
        els.addOpen.hidden = !state.canManage;
        els.roleOpen.hidden = !state.canManage;
        els.departmentOpen.hidden = !state.canManage;
    }

    async function init() {
        bindEvents();
        try {
            await loadMe();
            await loadInvitations();
            if (state.canRead) {
                await refreshWorkspace();
            }
        } catch (error) {
            if (!handleAuthError(error)) {
                showPageMessage(errorMessage(error, "Could not load staff data."), "error");
            }
        }
    }

    init();
})();
