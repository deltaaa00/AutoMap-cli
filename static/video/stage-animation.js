/**
 * Video Studio: Preview clock, country mask cache, reveal effects, and animation
 * lifecycle. Depends on stage-layout.js settings; init.js starts observers after remaining
 * controls load.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
let previewAnimPlaying = true;
let previewAnimTime = 0;
let previewLastTs = null;
let previewRafId = null;
const revealMaskCache = { key: '', silhouette: null, cx: 0.5, cy: 0.5, loadingKey: '' };

function smootherstep(u) {
    const c = Math.max(0, Math.min(1, u));
    return c * c * c * (c * (c * 6 - 15) + 10);
}

function formatPreviewClock(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const mins = Math.floor(s / 60);
    const rem = (s % 60).toFixed(1).padStart(4, '0');
    return `${String(mins).padStart(2, '0')}:${rem}`;
}

function parseHexRgb(hexStr) {
    const s = String(hexStr || '#000000').trim().replace(/^#/, '');
    if (/^[0-9a-fA-F]{6}$/.test(s)) {
        return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
    }
    if (/^[0-9a-fA-F]{3}$/.test(s)) {
        return [parseInt(s[0] + s[0], 16), parseInt(s[1] + s[1], 16), parseInt(s[2] + s[2], 16)];
    }
    return [0, 0, 0];
}

function onRevealColorChange() {
    const colorVal = document.getElementById('revealColorInput')?.value || '#000000';
    const hexLabel = document.getElementById('revealColorHex');
    if (hexLabel) hexLabel.textContent = colorVal.toLowerCase();
    revealMaskCache.key = '';
    revealMaskCache.loadingKey = '';
    updateRevealUI();
    saveVideoSettings();
}

function ensureRevealMaskLoaded() {
    const map = document.getElementById('stageMapPreview');
    if (!map || map.classList.contains('hidden') || !map.naturalWidth || !map.naturalHeight) return null;

    const colorHex = (document.getElementById('revealColorInput')?.value || '#000000').toLowerCase();
    const hexLabel = document.getElementById('revealColorHex');
    if (hexLabel && hexLabel.textContent !== colorHex) hexLabel.textContent = colorHex;

    const customDataUrl = (typeof window.revealMaskDataUrl === 'string' && window.revealMaskDataUrl) ? window.revealMaskDataUrl : '';
    let maskKey = '';
    let maskSrc = '';
    if (customDataUrl) {
        maskKey = `custom:${customDataUrl.slice(0, 64)}:${colorHex}`;
        maskSrc = customDataUrl;
    } else {
        const aspect = map.naturalWidth / map.naturalHeight;
        const mapKind = aspect < 1.25 ? 'europe' : 'world';
        let countryKey = (document.getElementById('mapZoomCountryKey')?.value || '').trim();
        if (!countryKey) {
            if (!zoomCountriesCache) {
                loadZoomCountries().then(() => renderPreviewFrame(previewAnimTime));
                return null;
            }
            const list = zoomCountriesCache[mapKind];
            const zx = parseFloat(document.getElementById('mapZoomX')?.value || '0.807');
            const zy = parseFloat(document.getElementById('mapZoomY')?.value || '0.358');
            if (Array.isArray(list) && list.length) {
                let best = null;
                let bestDist = Infinity;
                for (const c of list) {
                    const d = (c.x - zx) ** 2 + (c.y - zy) ** 2;
                    if (d < bestDist) {
                        bestDist = d;
                        best = c;
                    }
                }
                if (best && bestDist <= 0.18 * 0.18) {
                    countryKey = best.key || '';
                    const keyEl = document.getElementById('mapZoomCountryKey');
                    if (keyEl && countryKey) keyEl.value = countryKey;
                }
            }
        }
        if (!countryKey) return null;
        maskKey = `${mapKind}:${countryKey}:${colorHex}`;
        maskSrc = `/api/zoom_country_outline?map=${encodeURIComponent(mapKind)}&key=${encodeURIComponent(countryKey)}&solid=1`;
    }

    if (revealMaskCache.key === maskKey && revealMaskCache.silhouette) {
        return revealMaskCache;
    }
    if (revealMaskCache.loadingKey === maskKey) {
        return null;
    }

    const [cr, cg, cb] = parseHexRgb(colorHex);
    revealMaskCache.loadingKey = maskKey;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
        if (revealMaskCache.loadingKey !== maskKey) return;
        const w = img.naturalWidth || 1;
        const h = img.naturalHeight || 1;
        const off = document.createElement('canvas');
        off.width = w;
        off.height = h;
        const octx = off.getContext('2d');
        octx.drawImage(img, 0, 0, w, h);
        const imgData = octx.getImageData(0, 0, w, h);
        const data = imgData.data;
        let sumX = 0;
        let sumY = 0;
        let count = 0;
        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                const idx = (y * w + x) * 4;
                const a = data[idx + 3];
                if (a > 20) {
                    data[idx] = cr;
                    data[idx + 1] = cg;
                    data[idx + 2] = cb;
                    data[idx + 3] = 255;
                    sumX += x;
                    sumY += y;
                    count++;
                } else {
                    data[idx + 3] = 0;
                }
            }
        }
        octx.putImageData(imgData, 0, 0);
        revealMaskCache.key = maskKey;
        revealMaskCache.silhouette = off;
        revealMaskCache.cx = count > 0 ? (sumX / count) / w : 0.5;
        revealMaskCache.cy = count > 0 ? (sumY / count) / h : 0.5;
        revealMaskCache.loadingKey = '';
        renderPreviewFrame(previewAnimTime);
    };
    img.onerror = () => {
        if (revealMaskCache.loadingKey === maskKey) revealMaskCache.loadingKey = '';
    };
    img.src = maskSrc;
    return null;
}

function renderPreviewFrame(t) {
    const stage = document.querySelector('.video-preview-stage');
    const composite = document.getElementById('stageCompositeBox');
    const player = document.getElementById('renderedVideoPlayer');
    const veil = document.getElementById('stageIntroVeil');
    const revealCanvas = document.getElementById('stageRevealCanvas');
    const subBox = document.getElementById('stageSubscribeBanner');
    const starsBox = document.getElementById('stageWatermarkStars');
    const timeEl = document.getElementById('previewTimeIndicator');
    const totalDur = getCutDuration();
    const baseScale = stage && stage.clientWidth ? (stage.clientWidth / 1080) : 1;

    if (timeEl) {
        timeEl.textContent = `${formatPreviewClock(t)} / ${formatPreviewClock(totalDur)}`;
    }

    if (!player || !player.classList.contains('hidden')) {
        if (composite) composite.style.transform = `scale(${baseScale})`;
        if (veil) veil.classList.add('hidden');
        if (revealCanvas) revealCanvas.classList.add('hidden');
        if (subBox) subBox.style.opacity = '';
        if (starsBox) starsBox.style.opacity = '';
        return;
    }

    const introFade = !!document.getElementById('introFadeToggle')?.checked;
    if (veil) {
        if (introFade && t < 0.8) {
            veil.classList.remove('hidden');
            veil.style.opacity = String(Math.max(0, 1 - t / 0.8));
        } else {
            veil.classList.add('hidden');
            veil.style.opacity = '0';
        }
    }

    const subEnabled = !!document.getElementById('subscribeBadgeToggle')?.checked;
    const subDelay = Number(document.getElementById('subscribeDelayInput')?.value || 2.5);
    if (subBox) {
        if (subEnabled && !subBox.classList.contains('hidden')) {
            subBox.style.opacity = String(t < subDelay ? 0 : Math.min(1, (t - subDelay) / 0.4));
        } else {
            subBox.style.opacity = '';
        }
    }

    const starsEnabled = !!document.getElementById('watermarkStarsToggle')?.checked;
    if (starsBox) {
        if (starsEnabled && !starsBox.classList.contains('hidden')) {
            starsBox.style.opacity = String(Math.min(1, Math.max(0, (t - 0.3) / 0.5)));
        } else {
            starsBox.style.opacity = '';
        }
    }

    const zoomEnabled = !!document.getElementById('mapZoomToggle')?.checked;
    const revealEnabled = zoomEnabled && !!document.getElementById('revealCountryToggle')?.checked;
    const zStartVal = Number(document.getElementById('mapZoomStart')?.value || 1.5);
    const zDurVal = Math.max(0.1, Number(document.getElementById('mapZoomDuration')?.value || 2.5));
    const rawRTime = Number(document.getElementById('revealTimeInput')?.value || 5.5);
    const rTime = zoomEnabled ? Math.max(zStartVal + zDurVal + 0.5, rawRTime) : Math.max(0.2, rawRTime);
    const map = document.getElementById('stageMapPreview');
    const area = document.getElementById('stageMapArea');

    let s = 1;
    let tx = 0;
    let ty = 0;
    if (zoomEnabled) {
        const rawX = parseFloat(document.getElementById('mapZoomX')?.value);
        const rawY = parseFloat(document.getElementById('mapZoomY')?.value);
        const zx = Math.max(0, Math.min(1, Number.isFinite(rawX) ? rawX : 0.80));
        const zy = Math.max(0, Math.min(1, Number.isFinite(rawY) ? rawY : 0.35));
        const targetScale = Math.max(1, Number(document.getElementById('mapZoomScale')?.value || 2.0));
        const zStart = Number(document.getElementById('mapZoomStart')?.value || 1.5);
        const zDur = Math.max(0.1, Number(document.getElementById('mapZoomDuration')?.value || 2.5));
        const zEnd = zStart + zDur;
        const zOutEnabled = !!document.getElementById('mapZoomOutToggle')?.checked;
        const zOutDur = Math.max(0.1, Number(document.getElementById('mapZoomOutDuration')?.value || 2.0));
        const zOutStart = Math.max(zEnd, totalDur - zOutDur);

        let ease = 0;
        if (t >= zStart && t < zEnd) {
            ease = smootherstep((t - zStart) / zDur);
        } else if (t >= zEnd) {
            ease = (zOutEnabled && t >= zOutStart) ? (1 - smootherstep((t - zOutStart) / zOutDur)) : 1;
        }
        s = 1 + (targetScale - 1) * ease;
        const targetX = (map && map.offsetWidth > 0 && area)
            ? (area.offsetLeft + map.offsetLeft + zx * map.offsetWidth)
            : (zx * 1080);
        const targetY = (map && map.offsetHeight > 0 && area)
            ? (area.offsetTop + map.offsetTop + zy * map.offsetHeight)
            : (zy * 1920);
        tx = Math.min(0, Math.max(1080 * (1 - s), 540 - targetX * s));
        ty = Math.min(0, Math.max(1920 * (1 - s), 960 - targetY * s));
    }

    let shakeX = 0;
    let shakeY = 0;
    if (revealEnabled && t >= rTime && t < rTime + 0.5) {
        const dt = t - rTime;
        const decay = Math.exp(-dt * 9.0) * (1 - dt / 0.5);
        const amp = 18 * decay;
        shakeX = Math.sin(dt * 55) * amp;
        shakeY = Math.cos(dt * 47) * amp * 0.7;
    }

    if (composite) {
        composite.style.transform = `scale(${baseScale}) translate3d(${(tx + shakeX).toFixed(2)}px, ${(ty + shakeY).toFixed(2)}px, 0) scale(${s.toFixed(4)})`;
    }

    const fadeDur = 0.25;
    const mask = revealEnabled ? ensureRevealMaskLoaded() : null;
    if (revealCanvas && revealEnabled && mask && mask.silhouette && map && !map.classList.contains('hidden') && map.offsetWidth > 0 && area && t < rTime + fadeDur) {
        revealCanvas.classList.remove('hidden');
        const aw = Math.max(1, area.clientWidth);
        const ah = Math.max(1, area.clientHeight);
        if (revealCanvas.width !== aw) revealCanvas.width = aw;
        if (revealCanvas.height !== ah) revealCanvas.height = ah;
        const ctx = revealCanvas.getContext('2d');
        ctx.clearRect(0, 0, aw, ah);
        const alpha = t < rTime ? 1.0 : Math.max(0, 1 - (t - rTime) / fadeDur);
        const mx = map.offsetLeft;
        const my = map.offsetTop;
        const mw = map.offsetWidth;
        const mh = map.offsetHeight;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.drawImage(mask.silhouette, mx, my, mw, mh);
        if (t < rTime) {
            const dotCount = t < rTime * 0.30 ? 1 : (t < rTime * 0.60 ? 2 : 3);
            const cx = mx + mask.cx * mw;
            const cy = my + mask.cy * mh;
            const r = Math.max(5, Math.min(11, mw * 0.011));
            const gap = r * 2.8;
            const startX = cx - ((dotCount - 1) * gap) / 2;
            const [cr, cg, cb] = parseHexRgb(document.getElementById('revealColorInput')?.value || '#000000');
            const isLightCover = (0.299 * cr + 0.587 * cg + 0.114 * cb) > 175;
            for (let i = 0; i < dotCount; i++) {
                const dx = startX + i * gap;
                ctx.beginPath();
                ctx.arc(dx, cy, r, 0, Math.PI * 2);
                ctx.fillStyle = isLightCover ? '#0f172a' : '#ffffff';
                ctx.shadowColor = isLightCover ? 'rgba(255,255,255,0.75)' : 'rgba(0,0,0,0.75)';
                ctx.shadowBlur = 8;
                ctx.fill();
                ctx.shadowBlur = 0;
                ctx.lineWidth = 2;
                ctx.strokeStyle = isLightCover ? 'rgba(255,255,255,0.85)' : 'rgba(15,23,42,0.85)';
                ctx.stroke();
            }
        }
        ctx.restore();
    } else if (revealCanvas) {
        revealCanvas.classList.add('hidden');
    }
}

function syncPreviewTransportUI() {
    const icon = document.getElementById('previewPlayPauseIcon');
    const label = document.getElementById('previewPlayPauseLabel');
    const bar = document.getElementById('previewPlaybackBar');
    const player = document.getElementById('renderedVideoPlayer');
    const mp4Active = player && !player.classList.contains('hidden');
    if (icon) icon.className = previewAnimPlaying ? 'fa-solid fa-pause text-xs' : 'fa-solid fa-play text-xs';
    if (label) label.textContent = previewAnimPlaying ? 'Pause' : 'Play';
    if (bar) bar.classList.toggle('opacity-40', !!mp4Active);
}

function stepPreviewAnimation(ts) {
    const player = document.getElementById('renderedVideoPlayer');
    if (!previewAnimPlaying || !previewVisible || document.hidden || !player || !player.classList.contains('hidden')) {
        previewRafId = null;
        previewLastTs = null;
        renderPreviewFrame(previewAnimTime);
        return;
    }
    if (previewLastTs !== null) {
        const dt = Math.min(0.1, Math.max(0, (ts - previewLastTs) / 1000));
        const dur = getCutDuration();
        previewAnimTime = (previewAnimTime + dt) % Math.max(0.5, dur);
    }
    previewLastTs = ts;
    renderPreviewFrame(previewAnimTime);
    previewRafId = requestAnimationFrame(stepPreviewAnimation);
}

function hasActivePreviewAnimations() {
    const zoomEnabled = !!document.getElementById('mapZoomToggle')?.checked;
    return !!(
        document.getElementById('introFadeToggle')?.checked
        || document.getElementById('subscribeBadgeToggle')?.checked
        || document.getElementById('watermarkStarsToggle')?.checked
        || zoomEnabled
    );
}

function syncPreviewAnimationLoop() {
    syncPreviewTransportUI();
    const player = document.getElementById('renderedVideoPlayer');
    const hasAnims = hasActivePreviewAnimations();
    const shouldRun = hasAnims && previewAnimPlaying && previewVisible && !document.hidden && player && player.classList.contains('hidden');
    if (shouldRun && previewRafId === null) {
        previewLastTs = null;
        previewRafId = requestAnimationFrame(stepPreviewAnimation);
    } else if (!shouldRun) {
        if (previewRafId !== null) {
            cancelAnimationFrame(previewRafId);
            previewRafId = null;
        }
        previewLastTs = null;
        if (!hasAnims) previewAnimTime = 0;
    }
    // Apply edits immediately, including while a RAF is already scheduled.
    renderPreviewFrame(previewAnimTime);
}

function togglePreviewAnimation() {
    const player = document.getElementById('renderedVideoPlayer');
    if (player && !player.classList.contains('hidden')) return;
    previewAnimPlaying = !previewAnimPlaying;
    updatePreviewPlayback();
}

function restartPreviewAnimation() {
    const player = document.getElementById('renderedVideoPlayer');
    if (player && !player.classList.contains('hidden')) return;
    previewAnimTime = 0;
    previewLastTs = null;
    const bg = document.getElementById('stageBgPreview');
    if (bg && bg.getAttribute('src')) {
        try { bg.currentTime = 0; } catch {}
    }
    previewAnimPlaying = true;
    updatePreviewPlayback();
    renderPreviewFrame(0);
}
