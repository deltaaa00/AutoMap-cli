/**
 * Video Studio: Watermark files, text artwork, and branding layout. Uses overlay helpers
 * and calls title preview functions when settings change.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
let watermarkFile = null;
let watermarkImageUrl;
let watermarkTextUrl;
let watermarkPreviewTimer;
let watermarkPreviewRequest;
let watermarkPreviewVersion = 0;
let watermarkUploadVersion = 0;

function watermarkSettings() {
    return {
        watermark_text: document.getElementById('watermarkTextInput').value.trim(),
        watermark_color: document.getElementById('watermarkColor').value,
        watermark_font: document.getElementById('watermarkFontSelect').value,
        watermark_stars: document.getElementById('watermarkStarsToggle').checked,
    };
}

function syncBrandingLayout() {
    const wm = watermarkSettings();
    const box = document.getElementById('stageCompositeBox');
    box.classList.toggle('has-branding',
        Boolean(watermarkFile || wm.watermark_text || wm.watermark_stars));
    box.classList.toggle('has-logo', Boolean(watermarkFile));
    box.classList.toggle('has-stars', Boolean(wm.watermark_stars));
    fitStageMap();
}

function updateVisualEffects() {
    document.querySelector('.video-preview-stage').classList.toggle('effects-on',
        document.getElementById('visualEffects').checked);
    updateTitleStyle();
}

function clearWatermarkImage(persist = true) {
    watermarkUploadVersion++;
    watermarkFile = null;
    document.getElementById('watermarkFileInput').value = '';
    document.getElementById('watermarkFileName').textContent = 'No logo selected';
    const image = document.getElementById('stageWatermarkImage');
    image.removeAttribute('src');
    image.classList.add('hidden');
    const thumbnail = document.getElementById('watermarkThumbPreview');
    thumbnail.removeAttribute('src');
    thumbnail.classList.add('hidden');
    document.getElementById('watermarkDropzone').classList.remove('has-logo');
    document.getElementById('watermarkUploadPrompt').textContent = 'Drop your watermark image here, or click to browse';
    if (watermarkImageUrl) URL.revokeObjectURL(watermarkImageUrl);
    watermarkImageUrl = null;
    showLiveComposite();
    syncBrandingLayout();
    if (persist) rememberVideoAsset('video-watermark', null);
}

async function selectWatermark(file, persist = true) {
    const input = document.getElementById('watermarkFileInput');
    const version = ++watermarkUploadVersion;
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > MAX_MATERIAL_BYTES) {
        showToast('Choose a PNG, JPEG or WebP logo up to 15 MB.', 'error');
        input.value = '';
        return;
    }
    const url = URL.createObjectURL(file);
    const probe = new Image();
    probe.src = url;
    try {
        await probe.decode();
        if (probe.naturalWidth * probe.naturalHeight > 20000000) throw new Error('Logo is too large.');
        // Ignore a decode that finished after a different selection or removal.
        if (version !== watermarkUploadVersion) { URL.revokeObjectURL(url); return; }
        clearWatermarkImage(false);
        watermarkFile = file;
        watermarkImageUrl = url;
        document.getElementById('watermarkFileName').textContent = file.name;
        const image = document.getElementById('stageWatermarkImage');
        image.src = url;
        image.classList.remove('hidden');
        const thumbnail = document.getElementById('watermarkThumbPreview');
        thumbnail.src = url;
        thumbnail.classList.remove('hidden');
        document.getElementById('watermarkDropzone').classList.add('has-logo');
        document.getElementById('watermarkUploadPrompt').textContent = 'Logo selected · drop or click to replace';
        syncBrandingLayout();
        if (persist) rememberVideoAsset('video-watermark', file);
    } catch (error) {
        URL.revokeObjectURL(url);
        if (version !== watermarkUploadVersion) return;
        input.value = '';
        showToast('Choose a valid logo image up to 20 megapixels.', 'error');
    }
}

function updateWatermarkPreview() {
    const settings = watermarkSettings();
    const banner = document.getElementById('stageWatermarkText');
    const starsBox = document.getElementById('stageWatermarkStars');
    const status = document.getElementById('watermarkPreviewStatus');
    clearTimeout(watermarkPreviewTimer);
    if (watermarkPreviewRequest) watermarkPreviewRequest.abort();
    const version = ++watermarkPreviewVersion;
    showLiveComposite();
    syncBrandingLayout();
    saveVideoSettings();

    if (!settings.watermark_stars) {
        starsBox.replaceChildren();
        starsBox.classList.add('hidden');
        if (starsPreviewUrl) URL.revokeObjectURL(starsPreviewUrl);
        starsPreviewUrl = null;
    } else if (watermarkStarsFile && watermarkStarsCustomUrl) {
        const img = new Image();
        img.alt = watermarkStarsFile.name || 'Custom stars badge';
        img.src = watermarkStarsCustomUrl;
        starsBox.replaceChildren(img);
        starsBox.classList.remove('hidden');
    } else {
        fetchPreviewBlobUrl({
            preview_kind: 'stars',
            ...titleSettings(),
            ...settings,
        }).then(async (url) => {
            if (version !== watermarkPreviewVersion || !url) {
                if (url) URL.revokeObjectURL(url);
                return;
            }
            const img = new Image();
            img.alt = '';
            img.src = url;
            await img.decode();
            starsBox.replaceChildren(img);
            starsBox.classList.remove('hidden');
            if (starsPreviewUrl) URL.revokeObjectURL(starsPreviewUrl);
            starsPreviewUrl = url;
        }).catch(() => {
            starsBox.classList.add('hidden');
        });
    }

    if (!settings.watermark_text) {
        banner.replaceChildren();
        banner.classList.add('hidden');
        if (watermarkTextUrl) URL.revokeObjectURL(watermarkTextUrl);
        watermarkTextUrl = null;
        status.textContent = '';
        return;
    }
    status.textContent = 'Updating watermark…';
    watermarkPreviewTimer = setTimeout(async () => {
        const controller = new AbortController();
        watermarkPreviewRequest = controller;
        let url;
        try {
            const response = await fetch('/api/video_title_preview', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                signal: controller.signal,
                body: JSON.stringify({video_title: settings.watermark_text,
                    preview_kind: 'watermark', ...titleSettings(), ...settings}),
            });
            if (!response.ok) throw new Error((await response.json()).error || 'Watermark preview unavailable.');
            url = URL.createObjectURL(await response.blob());
            const image = new Image();
            image.alt = settings.watermark_text;
            image.src = url;
            await image.decode();
            if (version !== watermarkPreviewVersion) { URL.revokeObjectURL(url); return; }
            banner.replaceChildren(image);
            banner.classList.remove('hidden');
            if (watermarkTextUrl) URL.revokeObjectURL(watermarkTextUrl);
            watermarkTextUrl = url;
            status.textContent = 'Preview matches rendered watermark';
        } catch (error) {
            if (url) URL.revokeObjectURL(url);
            if (version === watermarkPreviewVersion && error.name !== 'AbortError') {
                status.textContent = error.message;
                banner.classList.add('hidden');
            }
        }
    }, 180);
}
