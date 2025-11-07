// Initialize rotating spinner messages for any spinner on the page.
function initializeSpinnerRotation(spinnerSelector) {
    const spinner = document.querySelector(spinnerSelector);
    if (!spinner) {
        return;
    }

    const messagesContainer = spinner.querySelector(".search-spinner__messages");
    const messages = spinner.querySelectorAll(".search-spinner__message");
    if (!messagesContainer || messages.length === 0) {
        return;
    }

    let currentIndex = 0;

    setInterval(function() {
        currentIndex = currentIndex + 1;
        const offset = currentIndex * -100;
        messagesContainer.style.transform = "translateY(" + offset + "%)";
    }, 2000);
}
