/**
 * Video Studio: Saved settings, cross-tab topic transfer, and asynchronous IndexedDB
 * material restore. Depends on StudioStorage; restore runs from init.js after all
 * controllers load.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
const VIDEO_SETTINGS_KEY = 'automap_video_settings_v1';
const VIDEO_SETTING_IDS = ['musicUrlInput', 'musicCookiesInput', 'cutStartInput', 'cutDurationInput',
    'videoTitleInput', 'titleFontSelect', 'titleFontSize', 'titleFirstLineColor',
    'titleOtherLinesColor', 'titleUppercase', 'visualEffects', 'introFadeToggle',
    'mapSize', 'mapOffsetX', 'mapZoomToggle', 'mapZoomX', 'mapZoomY', 'mapZoomCountry', 'mapZoomCountryKey',
    'mapZoomScale', 'mapZoomStart', 'mapZoomDuration', 'mapZoomOutToggle', 'mapZoomOutDuration', 'revealCountryToggle', 'revealTimeInput', 'revealColorInput',
    'topLeftStickerSelect', 'topRightStickerSelect', 'subscribeBadgeToggle',
    'subscribeTextInput', 'subscribeDelayInput', 'watermarkTextInput',
    'watermarkFontSelect', 'watermarkColor', 'watermarkStarsToggle', 'codecSelect'];
let videoSettingsReady = false;
let manualVideoTitle = false;
let appliedIdeaId = null;
let selectedIdeaUpdatedAt = 0;
const materialVersions = {mapFileInput: 0, bgFileInput: 0};
let pendingAssetSaves = Promise.resolve();

function saveVideoSettings() {
    if (!videoSettingsReady) return;
    const values = Object.fromEntries(VIDEO_SETTING_IDS.map(id => {
        const input = document.getElementById(id);
        return [id, input.type === 'checkbox' ? input.checked : input.value];
    }));
    values.manualVideoTitle = manualVideoTitle;
    values.appliedIdeaId = appliedIdeaId;
    try {
        localStorage.setItem(VIDEO_SETTINGS_KEY, JSON.stringify(values));
    } catch (error) {
        document.getElementById('studioSaveStatus').textContent = 'Settings cannot be saved in this browser.';
    }
}

function rememberVideoAsset(key, file, metadata = {}) {
    // Serialize writes so a removal cannot be overtaken by an earlier upload.
    pendingAssetSaves = pendingAssetSaves.then(() => StudioStorage.setAsset(key, file, metadata)).catch(() => {
        document.getElementById('studioSaveStatus').textContent = 'Some files could not be saved. You can still render this video.';
    });
    return pendingAssetSaves;
}

async function restoreVideoStudio() {
    try {
        const saved = JSON.parse(localStorage.getItem(VIDEO_SETTINGS_KEY) || '{}');
        if (saved && typeof saved === 'object') {
            appliedIdeaId = saved.appliedIdeaId || null;
            // Older saved titles were entered manually before topic transfer existed.
            manualVideoTitle = typeof saved.manualVideoTitle === 'boolean'
                ? saved.manualVideoTitle : !!saved.videoTitleInput;
            VIDEO_SETTING_IDS.forEach(id => {
                const input = document.getElementById(id);
                const value = saved[id];
                if (input.type === 'checkbox') {
                    if (typeof value === 'boolean') input.checked = value;
                } else if (typeof value === 'string') {
                    let normalized = value;
                    if ((id === 'topLeftStickerSelect' || id === 'topRightStickerSelect') && value.startsWith('gen_')) {
                        normalized = normalizeDynamicStickerValue(value);
                    }
                    if (input.tagName === 'SELECT' && ![...input.options].some(option => option.value === normalized)) return;
                    input.value = input.maxLength > 0 ? normalized.slice(0, input.maxLength) : normalized;
                }
            });
            if (typeof saved.mapZoomCountryKey !== 'string') {
                const savedCountry = (document.getElementById('mapZoomCountry')?.value || '').trim();
                const keyEl = document.getElementById('mapZoomCountryKey');
                if (keyEl) {
                    keyEl.value = (!savedCountry || savedCountry.toLowerCase() === 'custom point')
                        ? ''
                        : savedCountry.toLowerCase().replace(/\s+/g, '_');
                }
            }
            updateMapZoomUI();
            updateRevealUI();
        }
    } catch (error) {
        // Invalid or unavailable saved settings should not prevent opening the studio.
    }
    let selectedIdea = null;
    try {
        selectedIdea = JSON.parse(localStorage.getItem('automap-selected-idea') || 'null');
    } catch {}
    if (selectedIdea && typeof selectedIdea.topic === 'string' && selectedIdea.topic.trim() &&
        typeof selectedIdea.id === 'string' && Number.isFinite(selectedIdea.updatedAt)) {
        selectedIdeaUpdatedAt = selectedIdea.updatedAt;
        if (selectedIdea.id !== appliedIdeaId) {
            const title = document.getElementById('videoTitleInput');
            title.value = selectedIdea.topic.trim().slice(0, title.maxLength);
            manualVideoTitle = false;
            appliedIdeaId = selectedIdea.id;
            updateStageTitlePreview(title.value);
        }
    }
    videoSettingsReady = true;
    saveVideoSettings();
    updateMapLayout();
    if (isDynamicStickerValue(document.getElementById('topLeftStickerSelect').value) ||
        isDynamicStickerValue(document.getElementById('topRightStickerSelect').value)) {
        document.getElementById('autoStickerVariantsBox').classList.remove('hidden');
    }
    setCutDuration(document.getElementById('cutDurationInput').value);
    updateVisualEffects();
    try {
        const [
            latest, selected, background, logo, cutAudio, sourceAudio,
            savedSubImg, savedStarsImg, savedLeftStickerImg, savedRightStickerImg,
        ] = await Promise.all([
            StudioStorage.getAsset('latest-map'),
            StudioStorage.getAsset('video-map'),
            StudioStorage.getAsset('video-background'),
            StudioStorage.getAsset('video-watermark'),
            StudioStorage.getAsset('video-cut-audio'),
            StudioStorage.getAsset('video-source-audio'),
            StudioStorage.getAsset('video-subscribe-image'),
            StudioStorage.getAsset('video-stars-image'),
            StudioStorage.getAsset('video-left-sticker-image'),
            StudioStorage.getAsset('video-right-sticker-image'),
        ]);
        // A newer generated map wins. A manually selected or removed map
        // remains the user's choice until another map is generated.
        const map = latest && (!selected || latest.updatedAt > selected.updatedAt) ? latest : selected;
        const isNewGeneratedMap = !!(map && map === latest);
        if (map?.file && materialVersions.mapFileInput === 0) {
            const file = isNewGeneratedMap ? await prepareGeneratedMap(map.file) : map.file;
            if (materialVersions.mapFileInput === 0) {
                acceptFile('mapFileInput', file, isNewGeneratedMap, map.topic);
                if (renderFiles.mapFileInput === file && !manualVideoTitle && map.updatedAt > selectedIdeaUpdatedAt && typeof map.topic === 'string' && map.topic.trim()) {
                    const title = document.getElementById('videoTitleInput');
                    title.value = map.topic.trim().slice(0, title.maxLength);
                    manualVideoTitle = false;
                    updateStageTitlePreview(title.value);
                    saveVideoSettings();
                }
            }
        }
        if (background?.file && materialVersions.bgFileInput === 0) {
            acceptFile('bgFileInput', background.file, false);
        }
        if (logo?.file && watermarkUploadVersion === 0) await selectWatermark(logo.file, false);
        if (savedSubImg?.file && subscribeUploadVersion === 0) await selectSubscribeImage(savedSubImg.file, false);
        if (savedStarsImg?.file && watermarkStarsUploadVersion === 0) await selectWatermarkStarsImage(savedStarsImg.file, false);
        if (savedLeftStickerImg?.file && leftStickerUploadVersion === 0) await selectLeftStickerImage(savedLeftStickerImg.file, false);
        if (savedRightStickerImg?.file && rightStickerUploadVersion === 0) await selectRightStickerImage(savedRightStickerImg.file, false);
        if (sourceAudio?.file && audioSelectionVersion === 0) {
            const sourceUrl = await fileToDataUrl(sourceAudio.file);
            if (audioSelectionVersion === 0) {
                musicAudioFile = sourceAudio.file;
                musicDataUrl = sourceUrl;
                musicFileName = musicAudioFile.name.replace(/\.mp3$/i, '');
                saveMusicTitle(musicFileName);
                updateCutTitleHint(musicFileName);
                document.getElementById('musicPreview').src = sourceUrl;
                document.getElementById('musicPreview').classList.remove('hidden');
                document.getElementById('musicResult').classList.remove('hidden');
                document.getElementById('musicResultText').textContent = musicAudioFile.name;
            }
        }
        if (cutAudio?.file && audioSelectionVersion === 0) {
            const restoredAudio = await fileToDataUrl(cutAudio.file);
            if (audioSelectionVersion !== 0) return;
            cutAudioFile = cutAudio.file;
            cutDataUrl = restoredAudio;
            cutFileName = cutAudioFile.name.replace(/\.mp3$/i, '');
            const preview = document.getElementById('cutPreview');
            preview.src = cutDataUrl;
            preview.classList.remove('hidden');
            document.getElementById('cutResult').classList.remove('hidden');
            document.getElementById('cutResultText').textContent = cutAudioFile.name;
        }
        updateRenderAudioStatus();
    } catch (error) {
        document.getElementById('studioSaveStatus').textContent = 'Saved files could not be restored. You can upload them again.';
    }
}

async function prepareGeneratedMap(file) {
    if (file.size <= MAX_MATERIAL_BYTES) return file;
    // Full-resolution generated PNGs can exceed the video upload limit.
    // Keep the original in Map Studio and resize only the video copy.
    const url = URL.createObjectURL(file);
    try {
        const image = new Image();
        image.src = url;
        await image.decode();
        const canvas = document.createElement('canvas');
        let scale = Math.min(1, 2160 / Math.max(image.naturalWidth, image.naturalHeight));
        let blob;
        do {
            canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
            canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
            canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
            blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
            if (!blob) throw new Error('Could not prepare generated map.');
            scale *= .75;
        } while (blob.size > MAX_MATERIAL_BYTES);
        return new File([blob], file.name, {type: 'image/png', lastModified: file.lastModified});
    } finally {
        URL.revokeObjectURL(url);
    }
}
