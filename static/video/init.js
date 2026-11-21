/**
 * Video Studio: Final DOMContentLoaded wiring, observers, pagehide cleanup, and toast
 * helper. Load after every Video Studio controller and before unchanged youtube-upload.js;
 * no async/defer/module loading.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
// Wire up render dropzones once the DOM is ready.
document.addEventListener('DOMContentLoaded', () => {
    setupDropzone('mapDropzone');
    setupDropzone('bgDropzone');
    setupWatermarkDropzone();
    window.videoStudioReady = restoreVideoStudio();
    loadCodecs();
    ['input', 'change'].forEach(name => document.addEventListener(name, event => {
        if (event.target.id === 'videoTitleInput') manualVideoTitle = true;
        if (VIDEO_SETTING_IDS.includes(event.target.id)) saveVideoSettings();
    }));
    const stage = document.querySelector('.video-preview-stage');
    new ResizeObserver(() => {
        renderPreviewFrame(previewAnimTime);
    }).observe(stage);
    new ResizeObserver(() => {
        fitStageMap();
        renderPreviewFrame(previewAnimTime);
    }).observe(document.getElementById('stageMapArea'));
    new IntersectionObserver(([entry]) => {
        previewVisible = entry.isIntersecting;
        updatePreviewPlayback();
    }).observe(stage);
    document.addEventListener('visibilitychange', updatePreviewPlayback);
    syncPreviewAnimationLoop();
    window.addEventListener('pagehide', (event) => {
        // Keep blob URLs alive when the browser caches this page for Back.
        if (event.persisted) return;
        if (titlePreviewRequest) titlePreviewRequest.abort();
        if (watermarkPreviewRequest) watermarkPreviewRequest.abort();
        clearTimeout(titlePreviewTimer);
        clearTimeout(watermarkPreviewTimer);
        Object.values(materialPreviewUrls).forEach(url => url && URL.revokeObjectURL(url));
        if (titlePreviewUrl) URL.revokeObjectURL(titlePreviewUrl);
        if (watermarkImageUrl) URL.revokeObjectURL(watermarkImageUrl);
        if (watermarkTextUrl) URL.revokeObjectURL(watermarkTextUrl);
        if (subscribePreviewUrl) URL.revokeObjectURL(subscribePreviewUrl);
        if (leftStickerPreviewUrl) URL.revokeObjectURL(leftStickerPreviewUrl);
        if (rightStickerPreviewUrl) URL.revokeObjectURL(rightStickerPreviewUrl);
        if (starsPreviewUrl) URL.revokeObjectURL(starsPreviewUrl);
        if (subscribeCustomUrl) URL.revokeObjectURL(subscribeCustomUrl);
        if (watermarkStarsCustomUrl) URL.revokeObjectURL(watermarkStarsCustomUrl);
        if (leftStickerCustomUrl) URL.revokeObjectURL(leftStickerCustomUrl);
        if (rightStickerCustomUrl) URL.revokeObjectURL(rightStickerCustomUrl);
    });
});

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
