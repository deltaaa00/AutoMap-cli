/* Workspace reset, clipboard fallbacks, and toast feedback used by the other feature controllers. Functions remain global for existing inline HTML actions. */
function resetForm() {
    updateLabelOffset(0);
    updateAiTemperature(DEFAULT_AI_TEMPERATURE);
    document.querySelector('input[name="mode"][value="text"]').checked = true;
    document.querySelector('input[name="map_type"][value="default"]').checked = true;
    syncGsm(15);
    setSelectedExtraOption('none');
    const legendToggle = document.getElementById('showLegendToggle');
    if (legendToggle) legendToggle.checked = false;
    const hlToggle = document.getElementById('highlightExtremaToggle');
    if (hlToggle) hlToggle.checked = false;
    const hlHigh = document.getElementById('highlightHighColor');
    if (hlHigh) hlHigh.value = '#00e5ff';
    const hlLow = document.getElementById('highlightLowColor');
    if (hlLow) hlLow.value = '#ff2daa';
    changeAiProvider('gemini', fallbackModels.find(model => modelProvider(model) === 'gemini'));
    document.getElementById('fontSelect').selectedIndex = 0;
    document.getElementById('topicInput').value = '';
    updateCommandPreview();
    updateApiKeyIndicator();
    showToast("Settings reset to defaults", "info");
}

// --- Clipboard Helpers ---
function copyTextToClipboard(text, onSuccess) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            if (onSuccess) onSuccess();
        }).catch(() => {
            fallbackCopyText(text, onSuccess);
        });
    } else {
        fallbackCopyText(text, onSuccess);
    }
}

function fallbackCopyText(text, onSuccess) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        if (onSuccess) onSuccess();
    } catch (err) {
        console.error('Copy error', err);
    }
    document.body.removeChild(textarea);
}

function copyCliCommand() {
    const cmd = document.getElementById('cliCommandDisplay').textContent;
    copyTextToClipboard(cmd, () => {
        const icon = document.getElementById('copyIcon');
        const txt = document.getElementById('copyText');
        icon.className = "fa-solid fa-check text-emerald-400";
        txt.textContent = "Copied";
        showToast("CLI command copied to clipboard", "success");
        setTimeout(() => {
            icon.className = "fa-regular fa-copy";
            txt.textContent = "Copy";
        }, 2000);
    });
}

// --- Toast ---
function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    const icon = document.getElementById('toastIcon');
    const msg = document.getElementById('toastMsg');

    msg.textContent = message;
    if (type === 'success') icon.className = "fa-solid fa-circle-check text-emerald-400 text-base";
    else if (type === 'warn') icon.className = "fa-solid fa-triangle-exclamation text-amber-400 text-base";
    else if (type === 'error') icon.className = "fa-solid fa-circle-xmark text-slate-400 text-base";
    else icon.className = "fa-solid fa-circle-info text-accent-300 text-base";

    toast.classList.remove('translate-y-20', 'opacity-0');
    clearTimeout(window.toastTimeout);
    window.toastTimeout = setTimeout(() => {
        toast.classList.add('translate-y-20', 'opacity-0');
    }, 3000);
}
