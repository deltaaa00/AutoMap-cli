/* Map preview zoom, pan transform, fullscreen, and PNG download actions. Shared pointer state is defined in state.js and wired in bootstrap.js. */
function zoomMap(delta) {
    currentZoom = Math.max(0.5, Math.min(3.0, +(currentZoom + delta).toFixed(2)));
    applyZoom();
}

function resetZoom() {
    currentZoom = 1;
    panX = 0;
    panY = 0;
    applyZoom();
}

function applyZoom() {
    const wrapper = document.getElementById('mapCanvasWrapper');
    wrapper.style.transform = `translate(${panX}px, ${panY}px) scale(${currentZoom})`;
    document.getElementById('zoomLevelText').textContent = `${Math.round(currentZoom * 100)}%`;
}

function toggleFullscreenMap() {
    const viewport = document.getElementById('mapViewport');
    if (!document.fullscreenElement) {
        viewport.requestFullscreen().catch(() => {});
    } else {
        document.exitFullscreen();
    }
}

// --- Download Map (real generated PNG) ---
function downloadCurrentMap() {
    const serverImg = document.getElementById('serverMapImage');
    if (serverImg.classList.contains('hidden') || !serverImg.src) {
        showToast("Generate a map first.", "warn");
        return;
    }
    const a = document.createElement('a');
    a.href = serverImg.src;
    a.download = `result_map.png`;
    a.click();
    showToast("Downloading PNG map...", "success");
}

// --- Color Filling Studio (`filling_txt`, `filling_num`, & Smart Map Inspector) ---
