// minimal placeholder file; our inline scripts already handle chat in templates
console.log("main.js loaded");

// --- Dark Mode Logic ---
const STORAGE_KEY = 'college-kb-dark-mode';
const toggleButton = document.getElementById('darkModeToggle');

function enableDarkMode() {
    document.body.classList.add('dark-mode');
    localStorage.setItem(STORAGE_KEY, 'enabled');
    if (toggleButton) toggleButton.textContent = '🔆'; // Sun icon
}

function disableDarkMode() {
    document.body.classList.remove('dark-mode');
    localStorage.setItem(STORAGE_KEY, 'disabled');
    if (toggleButton) toggleButton.textContent = '🌙'; // Moon icon
}

function toggleDarkMode() {
    if (document.body.classList.contains('dark-mode')) {
        disableDarkMode();
    } else {
        enableDarkMode();
    }
}

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    // Check local storage preference
    const storedPreference = localStorage.getItem(STORAGE_KEY);
    if (storedPreference === 'enabled') {
        enableDarkMode();
    } else if (storedPreference === 'disabled') {
        disableDarkMode();
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        // Fallback to system preference if no stored value
        enableDarkMode();
    } else {
        disableDarkMode();
    }

    // Attach listener to the button
    if (toggleButton) {
        toggleButton.addEventListener('click', toggleDarkMode);
    }
});
