/**
 * Video Studio: Shared title settings, country targeting, map placement, and preview
 * playback visibility. Event handlers call stage-animation.js after all scripts load.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
// --- Interactive Stage & Codec Helpers ---
let titlePreviewTimer;
let titlePreviewRequest;
let titlePreviewVersion = 0;
let titlePreviewUrl;
const materialPreviewUrls = {};
let previewVisible = true;

function titleSettings() {
    return {
        title_font: document.getElementById('titleFontSelect').value,
        title_font_size: Number(document.getElementById('titleFontSize').value),
        title_first_line_color: document.getElementById('titleFirstLineColor').value,
        title_other_lines_color: document.getElementById('titleOtherLinesColor').value,
        title_uppercase: document.getElementById('titleUppercase').checked,
        visual_effects: document.getElementById('visualEffects').checked,
        intro_fade: document.getElementById('introFadeToggle').checked,
        map_scale: Number(document.getElementById('mapSize').value) / 100,
        map_offset_x: Number(document.getElementById('mapOffsetX').value) / 100,
        zoom_enabled: document.getElementById('mapZoomToggle').checked,
        zoom_x: Number(document.getElementById('mapZoomX').value || 0.80),
        zoom_y: Number(document.getElementById('mapZoomY').value || 0.35),
        zoom_country_key: (document.getElementById('mapZoomCountryKey')?.value || '').trim(),
        zoom_scale: Number(document.getElementById('mapZoomScale').value || 2.0),
        zoom_start: Number(document.getElementById('mapZoomStart').value || 1.5),
        zoom_duration: Number(document.getElementById('mapZoomDuration').value || 2.5),
        zoom_out_enabled: !!document.getElementById('mapZoomOutToggle')?.checked,
        zoom_out_duration: Number(document.getElementById('mapZoomOutDuration')?.value || 2.0),
        reveal_enabled: !!document.getElementById('mapZoomToggle')?.checked && !!document.getElementById('revealCountryToggle')?.checked,
        reveal_time: Number(document.getElementById('revealTimeInput')?.value || 5.5),
        reveal_color: document.getElementById('revealColorInput')?.value || '#000000',
        top_left_sticker: document.getElementById('topLeftStickerSelect').value,
        top_right_sticker: document.getElementById('topRightStickerSelect').value,
        subscribe_badge: document.getElementById('subscribeBadgeToggle').checked,
        subscribe_text: document.getElementById('subscribeTextInput').value.trim() || 'Subscribe!',
        subscribe_delay: Number(document.getElementById('subscribeDelayInput').value || 2.5),
    };
}

let zoomCountriesCache = null;

async function loadZoomCountries() {
    if (zoomCountriesCache) return zoomCountriesCache;
    try {
        const resp = await fetch('/api/zoom_countries');
        if (resp.ok) zoomCountriesCache = await resp.json();
    } catch {
        // Fall back to direct click coordinates if country index fails to load.
    }
    return zoomCountriesCache;
}

function updateZoomOutUI() {
    const enabled = !!document.getElementById('mapZoomOutToggle')?.checked;
    const input = document.getElementById('mapZoomOutDuration');
    const wrap = document.getElementById('mapZoomOutDurationWrap');
    if (input) input.disabled = !enabled;
    if (wrap) wrap.classList.toggle('opacity-50', !enabled);
}

function updateRevealUI(restart = false) {
    const zoomEnabled = !!document.getElementById('mapZoomToggle')?.checked;
    const enabled = zoomEnabled && !!document.getElementById('revealCountryToggle')?.checked;
    const panel = document.getElementById('revealControlsPanel');
    if (panel) panel.classList.toggle('hidden', !enabled);
    if (restart && zoomEnabled) {
        previewAnimTime = 0;
        previewLastTs = null;
    }
    if (typeof syncPreviewAnimationLoop === 'function') {
        syncPreviewAnimationLoop();
    } else if (typeof renderPreviewFrame === 'function') {
        renderPreviewFrame(previewAnimTime);
    }
}

function updateMapZoomUI(restart = false) {
    const enabled = !!document.getElementById('mapZoomToggle')?.checked;
    const panel = document.getElementById('mapZoomPanel');
    if (panel) panel.classList.toggle('hidden', !enabled);
    if (enabled) loadZoomCountries();
    updateZoomOutUI();
    updateRevealUI(restart);
    const pin = document.getElementById('zoomPickerPin');
    const rawX = parseFloat(document.getElementById('mapZoomX')?.value);
    const rawY = parseFloat(document.getElementById('mapZoomY')?.value);
    const zx = Math.max(0, Math.min(1, Number.isFinite(rawX) ? rawX : 0.807));
    const zy = Math.max(0, Math.min(1, Number.isFinite(rawY) ? rawY : 0.358));
    if (pin) {
        pin.style.left = `${(zx * 100).toFixed(1)}%`;
        pin.style.top = `${(zy * 100).toFixed(1)}%`;
    }
    const countryName = (document.getElementById('mapZoomCountry')?.value || '').trim();
    const labelEl = document.getElementById('zoomTargetCountry');
    if (labelEl) {
        const coordsText = `(${Math.round(zx * 100)}%, ${Math.round(zy * 100)}%)`;
        labelEl.textContent = countryName ? `${countryName} ${coordsText}` : coordsText;
    }
}

function setZoomTarget(x, y, countryName = '', countryKey = '') {
    document.getElementById('mapZoomX').value = Number(x).toFixed(4);
    document.getElementById('mapZoomY').value = Number(y).toFixed(4);
    const countryEl = document.getElementById('mapZoomCountry');
    if (countryEl) countryEl.value = countryName;
    const keyEl = document.getElementById('mapZoomCountryKey');
    if (keyEl) {
        const normalizedName = (countryName || '').trim().toLowerCase();
        keyEl.value = countryKey || (normalizedName === 'russia' ? 'russia' : '');
    }
    updateMapZoomUI(true);
    saveVideoSettings();
}

let lastZoomPointerPos = null;

function resolveZoomTargetFromEvent(event) {
    const img = document.getElementById('zoomPickerImg');
    if (!img || img.classList.contains('hidden')) return null;
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    const aspect = (img.naturalWidth && img.naturalHeight) ? (img.naturalWidth / img.naturalHeight) : 1.0;
    const mapKind = aspect < 1.25 ? 'europe' : 'world';

    if (event.ctrlKey) {
        return { x, y, name: 'Custom point', key: '', mapKind };
    }

    const data = zoomCountriesCache;
    const list = data ? data[mapKind] : null;
    if (Array.isArray(list) && list.length) {
        let best = null;
        let bestDist = Infinity;
        for (const c of list) {
            const dx = c.x - x;
            const dy = c.y - y;
            const dist = dx * dx + dy * dy;
            if (dist < bestDist) {
                bestDist = dist;
                best = c;
            }
        }
        if (best && bestDist <= 0.18 * 0.18) {
            const resolvedKey = best.key || ((best.name || '').trim().toLowerCase() === 'russia' ? 'russia' : '');
            return { x: best.x, y: best.y, name: best.name, key: resolvedKey, mapKind };
        }
    }
    return { x, y, name: '', key: '', mapKind };
}

let lastHoverOutlineKey = '';
function hoverZoomPoint(event) {
    lastZoomPointerPos = { clientX: event.clientX, clientY: event.clientY };
    if (!zoomCountriesCache) {
        loadZoomCountries().then(data => {
            if (data && lastZoomPointerPos) hoverZoomPoint({ ...lastZoomPointerPos, ctrlKey: !!event.ctrlKey });
        });
    }
    const target = resolveZoomTargetFromEvent(event);
    const hoverPin = document.getElementById('zoomHoverPin');
    const hoverLabel = document.getElementById('zoomHoverLabel');
    const hoverOutline = document.getElementById('zoomHoverOutline');
    if (!target || !hoverPin || !hoverLabel) return;
    const leftPct = `${(target.x * 100).toFixed(1)}%`;
    const topPct = `${(target.y * 100).toFixed(1)}%`;
    hoverPin.classList.toggle('transition-colors', !event.ctrlKey);
    hoverPin.classList.toggle('duration-75', !event.ctrlKey);
    hoverPin.style.left = leftPct;
    hoverPin.style.top = topPct;
    hoverPin.classList.remove('hidden');

    if (hoverOutline) {
        if (target.key) {
            const outlineKey = `${target.mapKind}:${target.key}`;
            if (outlineKey !== lastHoverOutlineKey) {
                lastHoverOutlineKey = outlineKey;
                hoverOutline.src = `/api/zoom_country_outline?map=${encodeURIComponent(target.mapKind)}&key=${encodeURIComponent(target.key)}`;
            }
            hoverOutline.classList.remove('hidden');
        } else {
            lastHoverOutlineKey = '';
            hoverOutline.classList.add('hidden');
        }
    }

    if (target.name) {
        hoverLabel.textContent = target.name;
        hoverLabel.style.left = `${Math.max(14, Math.min(86, target.x * 100)).toFixed(1)}%`;
        hoverLabel.style.top = `${Math.max(14, target.y * 100).toFixed(1)}%`;
        hoverLabel.classList.remove('hidden');
    } else {
        hoverLabel.classList.add('hidden');
    }
}

function clearZoomHover() {
    lastZoomPointerPos = null;
    lastHoverOutlineKey = '';
    document.getElementById('zoomHoverPin')?.classList.add('hidden');
    document.getElementById('zoomHoverLabel')?.classList.add('hidden');
    document.getElementById('zoomHoverOutline')?.classList.add('hidden');
}

function pickZoomPoint(event) {
    const target = resolveZoomTargetFromEvent(event);
    if (!target) return;
    setZoomTarget(target.x, target.y, target.name, target.key || '');
}

for (const evtName of ['keydown', 'keyup']) {
    window.addEventListener(evtName, e => {
        if (e.key === 'Control' && lastZoomPointerPos) {
            hoverZoomPoint({ ...lastZoomPointerPos, ctrlKey: e.ctrlKey });
        }
    });
}

function updateMapLayout() {
    document.getElementById('mapSizeValue').textContent = `${document.getElementById('mapSize').value}%`;
    document.getElementById('mapOffsetXValue').textContent = `${document.getElementById('mapOffsetX').value}%`;
    fitStageMap();
    renderPreviewFrame(previewAnimTime);
    saveVideoSettings();
}

function resetMapLayout() {
    document.getElementById('mapSize').value = '100';
    document.getElementById('mapOffsetX').value = '0';
    updateMapLayout();
}

function fitStageMap() {
    const map = document.getElementById('stageMapPreview');
    const area = document.getElementById('stageMapArea');
    if (!map.naturalWidth || !map.naturalHeight) return;
    area.style.justifyContent = map.naturalWidth < map.naturalHeight ? 'flex-end' : 'center';
    const scale = Math.min(area.clientWidth / map.naturalWidth, area.clientHeight / map.naturalHeight)
        * Number(document.getElementById('mapSize').value) / 100;
    map.style.left = `${area.clientWidth * Number(document.getElementById('mapOffsetX').value) / 100}px`;
    map.style.width = `${map.naturalWidth * scale}px`;
    map.style.height = `${map.naturalHeight * scale}px`;
}

function updatePreviewPlayback() {
    const bg = document.getElementById('stageBgPreview');
    const player = document.getElementById('renderedVideoPlayer');
    const liveActive = previewVisible && !document.hidden && player && player.classList.contains('hidden');
    if (liveActive && bg.getAttribute('src')) {
        if (previewAnimPlaying) bg.play().catch(() => {});
        else bg.pause();
    } else {
        bg.pause();
    }
    syncPreviewAnimationLoop();
}

function showLiveComposite() {
    const player = document.getElementById('renderedVideoPlayer');
    player.pause();
    player.classList.add('hidden');
    document.getElementById('stageModeLabel').textContent = 'Live Frame Composite';
    updatePreviewPlayback();
}
