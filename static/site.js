const menuToggle = document.querySelector(".menu-toggle");
const primaryNavigation = document.querySelector(".primary-nav");

if (menuToggle && primaryNavigation) {
    menuToggle.addEventListener("click", () => {
        const isOpen = menuToggle.getAttribute("aria-expanded") === "true";
        menuToggle.setAttribute("aria-expanded", String(!isOpen));
        menuToggle.setAttribute("aria-label", isOpen ? "Open navigation" : "Close navigation");
        primaryNavigation.classList.toggle("is-open", !isOpen);
    });

    primaryNavigation.querySelectorAll("a").forEach((link) => {
        link.addEventListener("click", () => {
            menuToggle.setAttribute("aria-expanded", "false");
            menuToggle.setAttribute("aria-label", "Open navigation");
            primaryNavigation.classList.remove("is-open");
        });
    });
}

document.querySelectorAll("[data-current-year]").forEach((year) => {
    year.textContent = new Date().getFullYear();
});
