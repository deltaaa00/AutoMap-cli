/**
 * Video Studio: Custom subscribe/stars/corner image ownership, validation, and matching
 * worker previews. Uses settings and sticker presets; version checks prevent stale
 * asynchronous previews.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
let subscribePreviewUrl;
let leftStickerPreviewUrl;
let rightStickerPreviewUrl;
let starsPreviewUrl;
let autoMontageVersion = 0;

let subscribeImageFile = null;
let subscribeCustomUrl = null;
let subscribeUploadVersion = 0;

let watermarkStarsFile = null;
let watermarkStarsCustomUrl = null;
let watermarkStarsUploadVersion = 0;

let leftStickerFile = null;
let leftStickerCustomUrl = null;
let leftStickerUploadVersion = 0;

let rightStickerFile = null;
let rightStickerCustomUrl = null;
let rightStickerUploadVersion = 0;

const OVERLAY_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];

async function validateOverlayImageFile(file, inputEl, label) {
    if (!file) return null;
    if (!OVERLAY_IMAGE_TYPES.includes(file.type) || file.size > MAX_MATERIAL_BYTES) {
        showToast(`Choose a PNG, JPEG, WebP or GIF ${label} up to 15 MB.`, 'error');
        if (inputEl) inputEl.value = '';
        return null;
    }
    const url = URL.createObjectURL(file);
    const probe = new Image();
    probe.src = url;
    try {
        await probe.decode();
        if (probe.naturalWidth * probe.naturalHeight > 20000000) {
            throw new Error('Image is too large.');
        }
        return url;
    } catch {
        URL.revokeObjectURL(url);
        if (inputEl) inputEl.value = '';
        showToast(`Choose a valid ${label} image up to 20 megapixels.`, 'error');
        return null;
    }
}

function clearSubscribeImage(persist = true) {
    subscribeUploadVersion++;
    subscribeImageFile = null;
    const input = document.getElementById('subscribeImageInput');
    if (input) input.value = '';
    const nameEl = document.getElementById('subscribeFileName');
    if (nameEl) nameEl.textContent = 'Built-in [ Subscribe! ▶] or upload file';
    if (subscribeCustomUrl) {
        URL.revokeObjectURL(subscribeCustomUrl);
        subscribeCustomUrl = null;
    }
    showLiveComposite();
    updateAutoMontagePreview();
    if (persist) rememberVideoAsset('video-subscribe-image', null);
}

async function selectSubscribeImage(file, persist = true) {
    const input = document.getElementById('subscribeImageInput');
    const version = ++subscribeUploadVersion;
    if (!file) return;
    const url = await validateOverlayImageFile(file, input, 'subscribe badge');
    if (!url) return;
    if (version !== subscribeUploadVersion) {
        URL.revokeObjectURL(url);
        return;
    }
    if (subscribeCustomUrl) URL.revokeObjectURL(subscribeCustomUrl);
    subscribeImageFile = file;
    subscribeCustomUrl = url;
    document.getElementById('subscribeFileName').textContent = file.name;
    if (persist) {
        document.getElementById('subscribeBadgeToggle').checked = true;
    }
    showLiveComposite();
    updateAutoMontagePreview();
    if (persist) rememberVideoAsset('video-subscribe-image', file);
}

function clearWatermarkStarsImage(persist = true) {
    watermarkStarsUploadVersion++;
    watermarkStarsFile = null;
    const input = document.getElementById('watermarkStarsImageInput');
    if (input) input.value = '';
    const nameEl = document.getElementById('watermarkStarsFileName');
    if (nameEl) nameEl.textContent = 'Built-in or upload file';
    if (watermarkStarsCustomUrl) {
        URL.revokeObjectURL(watermarkStarsCustomUrl);
        watermarkStarsCustomUrl = null;
    }
    showLiveComposite();
    updateWatermarkPreview();
    if (persist) rememberVideoAsset('video-stars-image', null);
}

async function selectWatermarkStarsImage(file, persist = true) {
    const input = document.getElementById('watermarkStarsImageInput');
    const version = ++watermarkStarsUploadVersion;
    if (!file) return;
    const url = await validateOverlayImageFile(file, input, 'stars / footer badge');
    if (!url) return;
    if (version !== watermarkStarsUploadVersion) {
        URL.revokeObjectURL(url);
        return;
    }
    if (watermarkStarsCustomUrl) URL.revokeObjectURL(watermarkStarsCustomUrl);
    watermarkStarsFile = file;
    watermarkStarsCustomUrl = url;
    document.getElementById('watermarkStarsFileName').textContent = file.name;
    if (persist) {
        document.getElementById('watermarkStarsToggle').checked = true;
    }
    showLiveComposite();
    updateWatermarkPreview();
    if (persist) rememberVideoAsset('video-stars-image', file);
}

function clearLeftStickerImage(persist = true) {
    leftStickerUploadVersion++;
    leftStickerFile = null;
    const input = document.getElementById('leftStickerImageInput');
    if (input) input.value = '';
    const nameEl = document.getElementById('leftStickerFileName');
    if (nameEl) nameEl.textContent = 'Preset or custom file';
    if (leftStickerCustomUrl) {
        URL.revokeObjectURL(leftStickerCustomUrl);
        leftStickerCustomUrl = null;
    }
    showLiveComposite();
    updateAutoMontagePreview();
    if (persist) rememberVideoAsset('video-left-sticker-image', null);
}

async function selectLeftStickerImage(file, persist = true) {
    const input = document.getElementById('leftStickerImageInput');
    const version = ++leftStickerUploadVersion;
    if (!file) return;
    const url = await validateOverlayImageFile(file, input, 'top-left sticker');
    if (!url) return;
    if (version !== leftStickerUploadVersion) {
        URL.revokeObjectURL(url);
        return;
    }
    if (leftStickerCustomUrl) URL.revokeObjectURL(leftStickerCustomUrl);
    leftStickerFile = file;
    leftStickerCustomUrl = url;
    document.getElementById('leftStickerFileName').textContent = file.name;
    showLiveComposite();
    updateAutoMontagePreview();
    if (persist) rememberVideoAsset('video-left-sticker-image', file);
}

function clearRightStickerImage(persist = true) {
    rightStickerUploadVersion++;
    rightStickerFile = null;
    const input = document.getElementById('rightStickerImageInput');
    if (input) input.value = '';
    const nameEl = document.getElementById('rightStickerFileName');
    if (nameEl) nameEl.textContent = 'Preset or custom file';
    if (rightStickerCustomUrl) {
        URL.revokeObjectURL(rightStickerCustomUrl);
        rightStickerCustomUrl = null;
    }
    showLiveComposite();
    updateAutoMontagePreview();
    if (persist) rememberVideoAsset('video-right-sticker-image', null);
}

async function selectRightStickerImage(file, persist = true) {
    const input = document.getElementById('rightStickerImageInput');
    const version = ++rightStickerUploadVersion;
    if (!file) return;
    const url = await validateOverlayImageFile(file, input, 'top-right sticker');
    if (!url) return;
    if (version !== rightStickerUploadVersion) {
        URL.revokeObjectURL(url);
        return;
    }
    if (rightStickerCustomUrl) URL.revokeObjectURL(rightStickerCustomUrl);
    rightStickerFile = file;
    rightStickerCustomUrl = url;
    document.getElementById('rightStickerFileName').textContent = file.name;
    showLiveComposite();
    updateAutoMontagePreview();
    if (persist) rememberVideoAsset('video-right-sticker-image', file);
}

async function fetchPreviewBlobUrl(payload) {
    const response = await fetch('/api/video_title_preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!response.ok) return null;
    return URL.createObjectURL(await response.blob());
}

async function customOverlayPreview(file, url, maxWidth, maxHeight) {
    const img = new Image();
    img.alt = file.name || 'Custom overlay';
    img.src = url;
    await img.decode();
    const scale = Math.min(maxWidth / img.naturalWidth, maxHeight / img.naturalHeight);
    img.style.width = `${Math.round(img.naturalWidth * scale)}px`;
    img.style.height = `${Math.round(img.naturalHeight * scale)}px`;
    img.className = 'custom-overlay';
    return img;
}

async function updateAutoMontagePreview() {
    const version = ++autoMontageVersion;
    const settings = titleSettings();
    const currentTitle = document.getElementById('videoTitleInput').value.trim();
    const compositeBox = document.getElementById('stageCompositeBox');
    const leftBox = document.getElementById('stageTopLeftSticker');
    const rightBox = document.getElementById('stageTopRightSticker');
    const subBox = document.getElementById('stageSubscribeBanner');
    const accentRow = document.getElementById('stageAccentRow');

    const hasLeftSticker = Boolean(leftStickerFile || (settings.top_left_sticker && settings.top_left_sticker !== 'none'));
    const hasRightSticker = Boolean(rightStickerFile || (settings.top_right_sticker && settings.top_right_sticker !== 'none'));
    const hasStickers = hasLeftSticker || hasRightSticker;
    compositeBox.classList.toggle('has-top-stickers', hasStickers);
    accentRow.classList.toggle('hidden', !settings.subscribe_badge);
    renderAutoStickerVariants();
    saveVideoSettings();

    // Left sticker preview (custom file takes precedence over built-in preset)
    if (leftStickerFile && leftStickerCustomUrl) {
        const img = await customOverlayPreview(leftStickerFile, leftStickerCustomUrl, 140, 140);
        if (version !== autoMontageVersion) return;
        leftBox.replaceChildren(img);
        leftBox.classList.remove('hidden');
    } else if (settings.top_left_sticker && settings.top_left_sticker !== 'none') {
        try {
            const url = await fetchPreviewBlobUrl({
                preview_kind: 'sticker',
                sticker_kind: settings.top_left_sticker,
                sticker_side: 'left',
                video_title: currentTitle,
                ...settings,
            });
            if (version === autoMontageVersion && url) {
                const img = new Image();
                img.alt = settings.top_left_sticker;
                img.src = url;
                await img.decode();
                leftBox.replaceChildren(img);
                leftBox.classList.remove('hidden');
                if (leftStickerPreviewUrl) URL.revokeObjectURL(leftStickerPreviewUrl);
                leftStickerPreviewUrl = url;
            } else if (url) {
                URL.revokeObjectURL(url);
            }
        } catch {
            leftBox.classList.add('hidden');
        }
    } else {
        leftBox.replaceChildren();
        leftBox.classList.add('hidden');
    }

    // Right sticker preview (custom file takes precedence over built-in preset)
    if (rightStickerFile && rightStickerCustomUrl) {
        const img = await customOverlayPreview(rightStickerFile, rightStickerCustomUrl, 140, 140);
        if (version !== autoMontageVersion) return;
        rightBox.replaceChildren(img);
        rightBox.classList.remove('hidden');
    } else if (settings.top_right_sticker && settings.top_right_sticker !== 'none') {
        try {
            const url = await fetchPreviewBlobUrl({
                preview_kind: 'sticker',
                sticker_kind: settings.top_right_sticker,
                sticker_side: 'right',
                video_title: currentTitle,
                ...settings,
            });
            if (version === autoMontageVersion && url) {
                const img = new Image();
                img.alt = settings.top_right_sticker;
                img.src = url;
                await img.decode();
                rightBox.replaceChildren(img);
                rightBox.classList.remove('hidden');
                if (rightStickerPreviewUrl) URL.revokeObjectURL(rightStickerPreviewUrl);
                rightStickerPreviewUrl = url;
            } else if (url) {
                URL.revokeObjectURL(url);
            }
        } catch {
            rightBox.classList.add('hidden');
        }
    } else {
        rightBox.replaceChildren();
        rightBox.classList.add('hidden');
    }

    // Subscribe pill preview (custom uploaded badge image or built-in pill)
    if (settings.subscribe_badge) {
        if (subscribeImageFile && subscribeCustomUrl) {
            const img = await customOverlayPreview(subscribeImageFile, subscribeCustomUrl, 680, 115);
            if (version !== autoMontageVersion) return;
            subBox.replaceChildren(img);
            subBox.classList.remove('hidden');
        } else {
            try {
                const url = await fetchPreviewBlobUrl({
                    preview_kind: 'subscribe',
                    subscribe_text: settings.subscribe_text,
                    ...settings,
                });
                if (version === autoMontageVersion && url) {
                    const img = new Image();
                    img.alt = settings.subscribe_text;
                    img.src = url;
                    await img.decode();
                    subBox.replaceChildren(img);
                    subBox.classList.remove('hidden');
                    if (subscribePreviewUrl) URL.revokeObjectURL(subscribePreviewUrl);
                    subscribePreviewUrl = url;
                } else if (url) {
                    URL.revokeObjectURL(url);
                }
            } catch {
                subBox.classList.add('hidden');
            }
        }
    } else {
        subBox.replaceChildren();
        subBox.classList.add('hidden');
    }
    fitStageMap();
}
