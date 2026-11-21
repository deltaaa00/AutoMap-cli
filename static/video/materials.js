/**
 * Video Studio: Render material file ownership, drag/drop controls, live media URLs, and
 * File-to-data-URL conversion. Depends on earlier settings, preview, overlay, and codec
 * controllers.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
function setupWatermarkDropzone() {
    const zone = document.getElementById('watermarkDropzone');
    const input = document.getElementById('watermarkFileInput');
    zone.addEventListener('click', () => input.click());
    zone.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            input.click();
        }
    });
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(name => {
        zone.addEventListener(name, event => {
            event.preventDefault();
            event.stopPropagation();
            zone.classList.toggle('drag-active', name === 'dragenter' || name === 'dragover');
            if (name === 'drop') selectWatermark(event.dataTransfer?.files[0]);
        });
    });
}

// Selected files for the render, keyed by input id.
const renderFiles = { mapFileInput: null, bgFileInput: null };
// Last successful render result: { status, file_path, download_url }
let renderResultData = null;

function humanSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Attach click / drag-drop handlers to a dropzone element.
function setupDropzone(zoneId) {
    const zone = document.getElementById(zoneId);
    if (!zone) return;
    const input = document.getElementById(zone.dataset.input);

    // Click anywhere on the zone opens the file picker (but not on the clear button).
    zone.addEventListener('click', (e) => {
        if (e.target.closest('.dropzone-clear')) return;
        input.click();
    });

    input.addEventListener('change', () => {
        if (input.files && input.files[0]) acceptFile(input.id, input.files[0]);
    });

    // Drag & drop
    ['dragenter', 'dragover'].forEach((ev) =>
        zone.addEventListener(ev, (e) => {
            e.preventDefault();
            e.stopPropagation();
            zone.classList.add('border-accent-500', 'bg-slate-900');
        })
    );
    ['dragleave', 'drop'].forEach((ev) =>
        zone.addEventListener(ev, (e) => {
            e.preventDefault();
            e.stopPropagation();
            zone.classList.remove('border-accent-500', 'bg-slate-900');
        })
    );
    zone.addEventListener('drop', (e) => {
        const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (file) acceptFile(input.id, file);
    });

    // Clear button resets the zone.
    const clearBtn = zone.querySelector('.dropzone-clear');
    if (clearBtn) {
        clearBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            resetDropzone(input.id);
        });
    }
}

function acceptFile(inputId, file, persist = true, topic = '') {
    materialVersions[inputId]++;
    // Size guard: reject material files larger than 15 MB.
    if (file.size > MAX_MATERIAL_BYTES) {
        showToast(`${file.name} is ${humanSize(file.size)} — max allowed is 15 MB.`, 'error');
        logToConsole(`[Reject] ${file.name} (${humanSize(file.size)}) exceeds 15 MB limit.`, 'error');
        resetDropzone(inputId);
        return;
    }
    // Basic type guard (map = image, background = video).
    const isMap = inputId === 'mapFileInput';
    const wantType = isMap ? 'image/' : 'video/';
    if (file.type && !file.type.startsWith(wantType)) {
        showToast(`Wrong file type for ${isMap ? 'map' : 'background video'}.`, 'error');
        resetDropzone(inputId);
        return;
    }

    renderFiles[inputId] = file;
    const zone = document.querySelector(`.dropzone[data-input="${inputId}"]`);
    if (!zone) return;
    zone.querySelector('.dropzone-empty').classList.add('hidden');
    const filled = zone.querySelector('.dropzone-filled');
    filled.classList.remove('hidden');
    filled.classList.add('flex');
    zone.querySelector('.dropzone-name').textContent = `${file.name} (${humanSize(file.size)})`;

    // Update live Cinema Stage preview
    updateCinemaStagePreview(inputId, file);
    if (persist) rememberVideoAsset(inputId === 'mapFileInput' ? 'video-map' : 'video-background', file, {topic});

    logToConsole(`[File] Selected ${file.name} (${humanSize(file.size)})`, 'info');
}

function resetDropzone(inputId) {
    materialVersions[inputId]++;
    renderFiles[inputId] = null;
    const input = document.getElementById(inputId);
    if (input) input.value = '';
    const zone = document.querySelector(`.dropzone[data-input="${inputId}"]`);
    if (!zone) return;
    zone.querySelector('.dropzone-empty').classList.remove('hidden');
    const filled = zone.querySelector('.dropzone-filled');
    filled.classList.add('hidden');
    filled.classList.remove('flex');

    updateCinemaStagePreview(inputId, null);
    rememberVideoAsset(inputId === 'mapFileInput' ? 'video-map' : 'video-background', null);
}

function updateCinemaStagePreview(inputId, file) {
    const stageMap = document.getElementById('stageMapPreview');
    const stageBg = document.getElementById('stageBgPreview');
    const emptyHint = document.getElementById('stageEmptyPlaceholder');
    const mapThumb = document.getElementById('mapThumbPreview');
    const mapThumbIcon = document.getElementById('mapThumbIcon');
    showLiveComposite();
    if (materialPreviewUrls[inputId]) URL.revokeObjectURL(materialPreviewUrls[inputId]);
    materialPreviewUrls[inputId] = file ? URL.createObjectURL(file) : null;

    if (inputId === 'mapFileInput') {
        const zoomImg = document.getElementById('zoomPickerImg');
        const zoomPlaceholder = document.getElementById('zoomPickerPlaceholder');
        if (file) {
            const url = materialPreviewUrls[inputId];
            if (zoomImg) {
                zoomImg.src = url;
                zoomImg.classList.remove('hidden');
            }
            if (zoomPlaceholder) zoomPlaceholder.classList.add('hidden');
            if (stageMap) {
                stageMap.onload = fitStageMap;
                stageMap.src = url;
                stageMap.classList.remove('hidden');
            }
            if (mapThumb && mapThumbIcon) {
                mapThumb.src = url;
                mapThumb.classList.remove('hidden');
                mapThumbIcon.classList.add('hidden');
            }
        } else {
            if (zoomImg) {
                zoomImg.removeAttribute('src');
                zoomImg.classList.add('hidden');
            }
            if (zoomPlaceholder) zoomPlaceholder.classList.remove('hidden');
            if (stageMap) {
                stageMap.src = '';
                stageMap.classList.add('hidden');
            }
            if (mapThumb && mapThumbIcon) {
                mapThumb.src = '';
                mapThumb.classList.add('hidden');
                mapThumbIcon.classList.remove('hidden');
            }
        }
    } else if (inputId === 'bgFileInput') {
        if (file && stageBg) {
            const url = materialPreviewUrls[inputId];
            stageBg.src = url;
            stageBg.classList.remove('hidden');
            updatePreviewPlayback();
        } else if (stageBg) {
            stageBg.pause();
            stageBg.removeAttribute('src');
            stageBg.load();
            stageBg.classList.add('hidden');
        }
    }

    if (emptyHint) {
        const hasAny = Boolean(renderFiles.mapFileInput || renderFiles.bgFileInput);
        if (hasAny) emptyHint.classList.add('hidden');
        else emptyHint.classList.remove('hidden');
    }
}

// Read a File as a base64 data URL string (e.g. "data:image/png;base64,....").
function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error || new Error('Failed to read file'));
        reader.readAsDataURL(file);
    });
}
