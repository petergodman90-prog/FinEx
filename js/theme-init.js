(function () {
    try {
        var savedTheme = localStorage.getItem("finex-theme");
        document.documentElement.dataset.theme =
            savedTheme === "light" || savedTheme === "dark"
                ? savedTheme
                : "dark";
    } catch (error) {
        document.documentElement.dataset.theme = "dark";
    }
})();
