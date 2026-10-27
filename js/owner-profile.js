(function () {
    "use strict";

    const heading = document.getElementById("owner-profile-heading");
    const message = document.getElementById("owner-profile-message");
    if (!heading || !message || !window.FinexAPI) return;

    function display(id, value, fallback) {
        const target = document.getElementById(id);
        if (target) target.textContent = value || fallback;
    }

    function formatDate(value) {
        if (!value) return "—";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
    }

    window.FinexAPI.request("/auth/me").then(function (profile) {
        const user = profile && profile.user;
        if (!user) throw new Error("Profile data is unavailable.");

        heading.textContent = user.full_name || "Name not provided";
        display("owner-profile-email", user.email, "Email not provided");
        display("owner-profile-role", profile.role, "Role not provided");
        const membership = (profile.memberships || []).find(function (item) {
            return item.org_id === profile.active_org_id;
        });
        display("owner-profile-workspace", membership && membership.org_name, "Workspace not available");
        display("owner-profile-member-since", formatDate(membership ? membership.joined_at : user.created_at), "—");
        message.textContent = "Profile details are loaded from the authenticated session.";
    }).catch(function (error) {
        if (error.status === 401) {
            window.location.replace("../index.html");
            return;
        }
        heading.textContent = "Profile unavailable";
        display("owner-profile-email", "", "No authenticated profile details are available.");
        display("owner-profile-role", "", "");
        display("owner-profile-workspace", "", "");
        display("owner-profile-member-since", "", "—");
        message.textContent = "Could not load profile details. Please try again later.";
    });
})();
