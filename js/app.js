const loginForm = document.getElementById("login-form");
const loginMessage = document.getElementById("login-message");

// Display a colored notification.
function showLoginNotification(type, title, message) {
    const icons = {
        success: "✓",
        error: "✕",
        warning: "!"
    };

    loginMessage.className = `notification notification-${type}`;
    loginMessage.replaceChildren();

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
    loginMessage.append(icon, content);

    loginMessage.hidden = false;
    loginMessage.setAttribute(
        "role",
        type === "error" ? "alert" : "status"
    );

    loginMessage.scrollIntoView({
        behavior: "smooth",
        block: "nearest"
    });
}

// Clear the notification.
function hideLoginNotification() {
    loginMessage.hidden = true;
    loginMessage.className = "notification";
    loginMessage.replaceChildren();
}

// Validate the login form.
loginForm.addEventListener("submit", function (event) {
    event.preventDefault();

    const emailInput = document.getElementById("email");
    const passwordInput = document.getElementById("password");

    const email = emailInput.value.trim();
    const password = passwordInput.value;

    // 1. Check for empty fields.
    if (!email || !password) {
        showLoginNotification(
            "warning",
            "Missing information",
            "Please enter your email address or phone number and password."
        );

        if (!email) {
            emailInput.focus();
        } else {
            passwordInput.focus();
        }

        return;
    }

    // 2. Validate the email address or phone number.
    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const phonePattern = /^\+?[\d\s()-]{7,20}$/;

    const isEmail = emailPattern.test(email);
    const isPhone = phonePattern.test(email) && /\d/.test(email);

    if (!isEmail && !isPhone) {
        showLoginNotification(
            "error",
            "Invalid login details",
            "Enter a valid email address or phone number."
        );

        emailInput.focus();
        return;
    }

    // 3. Check that a password has been entered.
    if (!password.trim()) {
        showLoginNotification(
            "warning",
            "Password required",
            "Please enter your password to continue."
        );

        passwordInput.focus();
        return;
    }

    // 4. Frontend validation only; authentication comes later.
    showLoginNotification(
        "success",
        "Input validated successfully!",
        "Your details look good. Actual sign-in will be connected later."
    );
});

// Clear old messages when the user edits the form.
loginForm.addEventListener("input", hideLoginNotification);

// Show or hide the password.
const passwordInput = document.getElementById("password");
const togglePassword = document.getElementById("toggle-password");
const eyeIcon = document.getElementById("eye-icon");

togglePassword.addEventListener("click", function () {
    const isPasswordHidden = passwordInput.type === "password";

    passwordInput.type = isPasswordHidden ? "text" : "password";

    togglePassword.setAttribute(
        "aria-label",
        isPasswordHidden ? "Hide password" : "Show password"
    );

    togglePassword.setAttribute(
        "aria-pressed",
        String(isPasswordHidden)
    );

    if (isPasswordHidden) {
        // Password is visible: display an open eye.
        eyeIcon.innerHTML = `
            <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/>
            <circle cx="12" cy="12" r="3"/>
        `;
    } else {
        // Password is hidden: display a crossed-out eye.
        eyeIcon.innerHTML = `
            <path d="M3 3l18 18"/>
            <path d="M10.6 10.6a2 2 0 0 0 2.8 2.8"/>
            <path d="M9.9 5.2A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a15.8 15.8 0 0 1-4 4.8"/>
            <path d="M6.2 6.2C3.5 8 2 12 2 12s3.5 7 10 7c1 0 2-.2 2.9-.5"/>
        `;
    }
});

// Forgot-password message.
const forgotPasswordLink = document.getElementById("forgot-password");

forgotPasswordLink.addEventListener("click", function (event) {
    event.preventDefault();

    showLoginNotification(
        "warning",
        "Password reset coming soon",
        "Password recovery is not available yet. We will add it in a future update."
    );
});