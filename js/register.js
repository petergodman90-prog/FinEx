const registerForm = document.getElementById("register-form");
const registerMessage = document.getElementById("register-message");
const registerSubmitButton = registerForm.querySelector('button[type="submit"]');

function showNotification(type, title, message) {
    const icons = {
        success: "✓",
        error: "✕",
        warning: "!"
    };

    registerMessage.className = `notification notification-${type}`;
    registerMessage.replaceChildren();

    const icon = document.createElement("span");
    icon.className = "notification-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = icons[type] || "!";

    const content = document.createElement("div");
    content.className = "notification-content";

    const heading = document.createElement("strong");
    heading.className = "notification-title";
    heading.textContent = title;

    const description = document.createElement("span");
    description.textContent = message;

    content.append(heading, description);
    registerMessage.append(icon, content);

    registerMessage.hidden = false;
    registerMessage.setAttribute(
        "role",
        type === "error" ? "alert" : "status"
    );

    registerMessage.scrollIntoView({
        behavior: "smooth",
        block: "nearest"
    });
}

function hideNotification() {
    registerMessage.hidden = true;
    registerMessage.className = "notification";
    registerMessage.replaceChildren();
}

registerForm.addEventListener("input", hideNotification);

registerForm.addEventListener("submit", async function (event) {
    event.preventDefault();

    const fullName = document.getElementById("full-name").value.trim();
    const businessName = document.getElementById("business-name").value.trim();
    const emailInput = document.getElementById("register-email");
    const email = emailInput.value.trim();
    const phoneInput = document.getElementById("phone");
    const phone = phoneInput.value.trim();
    const password = document.getElementById("register-password").value;
    const confirmPassword = document.getElementById("confirm-password").value;

    // 1. Check email
    if (!email) {
        showNotification(
            "error",
            "Email required",
            "Please enter your email address to continue."
        );

        emailInput.focus();
        return;
    }

    if (!emailInput.validity.valid) {
        showNotification(
            "error",
            "Invalid email address",
            "Enter a valid email address, such as name@example.com."
        );

        emailInput.focus();
        return;
    }

    // 2. Check other required fields
    if (!fullName || !businessName || !phone || !password || !confirmPassword) {
        showNotification(
            "warning",
            "Missing information",
            "Please complete all the other fields before creating your account."
        );

        return;
    }

    // 3. Check phone number
    if (!phoneInput.validity.valid || !/^[+()\d\s-]{7,20}$/.test(phone)) {
        showNotification(
            "error",
            "Invalid phone number",
            "Enter a valid phone number using digits and, if needed, +, spaces, parentheses or hyphens."
        );

        phoneInput.focus();
        return;
    }

    // 4. Check password length
    if (password.length < 8) {
        showNotification(
            "error",
            "Password too short",
            "Your password must contain at least 8 characters."
        );

        document.getElementById("register-password").focus();
        return;
    }

    // 5. Check password confirmation
    if (password !== confirmPassword) {
        showNotification(
            "error",
            "Passwords do not match",
            "Please make sure both password fields contain the same password."
        );

        document.getElementById("confirm-password").focus();
        return;
    }

    // 6. Create the account and sign in. The API sets HttpOnly cookies.
    if (!window.FinexAPI) {
        showNotification(
            "error",
            "Cannot reach the server",
            "The API client failed to load. Refresh the page and try again."
        );
        return;
    }

    registerSubmitButton.disabled = true;
    showNotification(
        "success",
        "Creating your workspace...",
        "Setting up your account and first organisation."
    );

    try {
        await window.FinexAPI.request("/auth/register", {
            method: "POST",
            body: JSON.stringify({
                org_name: businessName,
                full_name: fullName,
                email: email,
                phone: phone || null,
                password: password
            })
        });

        window.location.assign("dashboard.html");
    } catch (error) {
        registerSubmitButton.disabled = false;
        showNotification(
            "error",
            "Could not create account",
            error.message || "Please check your details and try again."
        );
    }
});

// Password visibility toggle

const passwordToggleButtons = document.querySelectorAll(".password-toggle");

passwordToggleButtons.forEach(function (button) {
    button.addEventListener("click", function () {
        const targetId = button.dataset.target;
        const passwordInput = document.getElementById(targetId);

        if (!passwordInput) return;

        const isHidden = passwordInput.type === "password";

        passwordInput.type = isHidden ? "text" : "password";

        // Update the eye icon
        button.innerHTML = isHidden
            ? `<svg viewBox="0 0 24 24" aria-hidden="true">
                   <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/>
                   <circle cx="12" cy="12" r="3"/>
                   <path d="M3 3 21 21"/>
               </svg>`
            : `<svg viewBox="0 0 24 24" aria-hidden="true">
                   <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/>
                   <circle cx="12" cy="12" r="3"/>
               </svg>`;

        button.setAttribute(
            "aria-label",
            isHidden ? "Hide password" : "Show password"
        );

        button.setAttribute("aria-pressed", String(isHidden));
    });
});