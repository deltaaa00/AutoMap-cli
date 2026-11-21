/* Initialize the map workspace once all feature scripts and DOM nodes exist. Restores preferences/history and wires viewport events; keep this script last. */
// --- Initialize on Load ---
window.addEventListener('DOMContentLoaded', () => {
    restoreSearchPreferences();
    window.runHistoryReady = restoreRunHistory();
    setInterval(pruneRunHistory, 60000);
    window.addEventListener('focus', pruneRunHistory);
    restoreSavedMapInputs();
    try { updateLabelOffset(localStorage.getItem(LABEL_OFFSET_STORAGE_KEY) || 0, false); } catch {}
    try {
        const savedSuffix = localStorage.getItem(LABEL_SUFFIX_STORAGE_KEY);
        const suffixEl = document.getElementById('labelSuffixInput');
        if (suffixEl && typeof savedSuffix === 'string') suffixEl.value = savedSuffix.slice(0, 16);
    } catch {}
    updateAiTemperature(DEFAULT_AI_TEMPERATURE, false);
    try {
        const saved = localStorage.getItem(AI_TEMPERATURE_STORAGE_KEY);
        if (saved !== null) updateAiTemperature(saved, false);
    } catch {
        // Keep the default when browser storage is unavailable.
    }
    populateProviderModels(activeAiProvider);
    restoreMapPaletteTheme();
    updateCommandPreview();
    renderFillingStudio();
    // Discover real fonts from the backend on startup (silent — no toast)
    fetchFontsFromServer(true);
    loadAvailableModels();

    // Ask the server whether an API key from .env is available.
    checkApiKeyStatus();

    // Close modal on Escape key
    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const modal = document.getElementById('fillingStudioModal');
            if (modal && !modal.classList.contains('hidden')) {
                closeFillingStudio();
            }
        }
    });

    // Sync fullscreen icon state
    document.addEventListener('fullscreenchange', () => {
        const fsIcon = document.getElementById('fullscreenIcon');
        if (fsIcon) {
            fsIcon.className = document.fullscreenElement
                ? 'fa-solid fa-compress text-xs'
                : 'fa-solid fa-expand text-xs';
        }
    });

    // Mouse wheel zoom & drag-to-pan support on the map viewport
    const viewport = document.getElementById('mapViewport');
    viewport.addEventListener('wheel', (e) => {
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.14 : -0.14;
        zoomMap(delta);
    }, { passive: false });

    viewport.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        isDraggingMap = true;
        dragStartX = e.clientX - panX;
        dragStartY = e.clientY - panY;
    });

    window.addEventListener('mousemove', (e) => {
        if (!isDraggingMap) return;
        panX = e.clientX - dragStartX;
        panY = e.clientY - dragStartY;
        applyZoom();
    });

    window.addEventListener('mouseup', () => {
        isDraggingMap = false;
    });
});
