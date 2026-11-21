/**
 * Video Studio: Debounced worker-rasterized title previews. Uses stage-layout.js state,
 * overlay-images.js, and shared console/toast handlers.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
function shouldSyncStickersOnTitleEdit(cur) {
    const box = document.getElementById('autoStickerVariantsBox');
    const boxOpen = box && !box.classList.contains('hidden');
    return boxOpen || isDynamicStickerValue(cur.top_left_sticker) || isDynamicStickerValue(cur.top_right_sticker);
}

function updateStageTitlePreview(val) {
    document.dispatchEvent(new Event('automap:video-topic-changed'));
    const title = (val || '').trim();
    const banner = document.getElementById('stageTitleBanner');
    const status = document.getElementById('titlePreviewStatus');
    document.getElementById('titleCharCount').textContent = `${title.length} chars`;
    clearTimeout(titlePreviewTimer);
    if (titlePreviewRequest) titlePreviewRequest.abort();
    const version = ++titlePreviewVersion;
    showLiveComposite();
    if (!title) {
        banner.replaceChildren();
        banner.classList.add('hidden');
        if (titlePreviewUrl) URL.revokeObjectURL(titlePreviewUrl);
        titlePreviewUrl = null;
        status.textContent = '';
        const cur = titleSettings();
        if (shouldSyncStickersOnTitleEdit(cur)) {
            updateAutoMontagePreview();
        }
        return;
    }
    status.textContent = 'Updating preview…';
    // Use the same title rasterizer as the final render, including wrapping,
    // font metrics, shadows and automatic fitting. Debounce quick edits.
    titlePreviewTimer = setTimeout(async () => {
        const cur = titleSettings();
        if (shouldSyncStickersOnTitleEdit(cur)) {
            updateAutoMontagePreview();
        }
        const controller = new AbortController();
        titlePreviewRequest = controller;
        let url;
        try {
            const response = await fetch('/api/video_title_preview', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ video_title: title, ...titleSettings() }),
                signal: controller.signal,
            });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Title preview is unavailable.');
            }
            url = URL.createObjectURL(await response.blob());
            const image = new Image();
            image.alt = title;
            image.src = url;
            await image.decode();
            if (version !== titlePreviewVersion) {
                URL.revokeObjectURL(url);
                return;
            }
            banner.replaceChildren(image);
            banner.classList.remove('hidden');
            if (titlePreviewUrl) URL.revokeObjectURL(titlePreviewUrl);
            titlePreviewUrl = url;
            status.textContent = 'Preview matches rendered title';
        } catch (error) {
            if (url) URL.revokeObjectURL(url);
            if (version === titlePreviewVersion && error.name !== 'AbortError') {
                status.textContent = error.message;
                banner.classList.add('hidden');
            }
        }
    }, 180);
}
