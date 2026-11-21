/* Inspect uploaded/generated map images and repaint using existing values. Request versions and cancellation keep stale palette analyses from replacing newer choices. */
function applyAnalysisToFillingStudio(analysis, mapValues = null, notifyToast = true) {
    if (!analysis) return;
    lastMapAnalysis = analysis;
    if (analysis.inspection_only || !mapValues || typeof mapValues !== 'object') {
        lastMapValues = null;
    } else {
        lastMapValues = mapValues;
    }
    if (Array.isArray(analysis.ui_txt_rules)) {
        fillingTxtRules = analysis.ui_txt_rules.map(r => ({
            key: String(r.key ?? ''),
            hex: safePaletteHex(r.hex),
            alpha: typeof r.alpha === 'number' ? r.alpha : 255
        }));
    }
    if (Array.isArray(analysis.ui_num_rules)) {
        fillingNumRules = analysis.ui_num_rules.map(r => ({
            low: Number(r.low) || 0,
            high: Number(r.high) || 0,
            hex: safePaletteHex(r.hex, '#38bdf8'),
            alpha: typeof r.alpha === 'number' ? r.alpha : 255
        }));
    }
    if (analysis.label_style) {
        labelStyleConfig = {
            ...labelStyleConfig,
            ...analysis.label_style
        };
        if (analysis.label_style.offset_x !== undefined) updateLabelOffset(analysis.label_style.offset_x);
    }

    const statsBar = document.getElementById('detectedMapStatsBar');
    if (statsBar) {
        const st = analysis.stats || {};
        const samples = Array.isArray(st.sample_values) ? st.sample_values.slice(0, 6).join(', ') : '';
        const rangeBadge = (st.min_value !== null && st.min_value !== undefined)
            ? `<span class="px-2 py-1 rounded bg-slate-800 text-slate-400 border border-slate-700">Range: ${escapeHtml(st.min_value)} .. ${escapeHtml(st.max_value)}</span>`
            : '';
        statsBar.innerHTML = `
            <span class="px-2 py-1 rounded bg-accent-500/20 text-accent-300 border border-accent-500/30 font-semibold">Type: ${escapeHtml(analysis.detected_type || 'auto')}</span>
            <span class="px-2 py-1 rounded bg-slate-800 text-slate-400 border border-slate-700">Theme: ${escapeHtml(analysis.resolved_theme || 'auto')}</span>
            <span class="px-2 py-1 rounded bg-slate-900 text-slate-300 border border-slate-700">${escapeHtml(st.total_regions || 0)} regions (${escapeHtml(st.unique_values || 0)} unique)</span>
            ${rangeBadge}
            ${samples ? `<span class="text-slate-400 truncate max-w-full">On Map: <strong class="text-slate-200">${escapeHtml(samples)}</strong></span>` : ''}
        `;
    }

    renderFillingStudio();
    if (notifyToast) {
        if (analysis.inspection_only) {
            showToast(`Extracted ${analysis.ui_txt_rules?.length || 0} palette colors from uploaded map (palette-only inspection).`, 'success');
        } else {
            showToast(`Palette ready: ${analysis.resolved_theme}. Use Apply & Re-Paint to update the map.`, 'success');
        }
    }
}

async function autoDetectMapColorsAndStyle(insideModal = false) {
    cancelPaletteAnalysis();
    document.getElementById('autoStyleFromMapToggle').checked = true;
    const version = paletteAnalysisVersion;
    const controller = new AbortController();
    paletteAnalysisRequest = controller;
    paletteAnalysisPending = true;
    paletteAnalysisReady = false;
    document.getElementById('restyleCurrentMapBtn').disabled = true;
    const state = getFormState();
    const btnIcon = document.getElementById('autoDetectMapIcon');
    if (btnIcon) btnIcon.className = 'fa-solid fa-circle-notch fa-spin text-xs';

    try {
        const serverImg = document.getElementById('serverMapImage');
        const hasMapImg = serverImg && !serverImg.classList.contains('hidden') && serverImg.src && serverImg.src.startsWith('data:image/');
        const payload = {
            topic: state.topic,
            mode: state.mode,
            map_type: state.map_type,
            palette_theme: document.getElementById('fillingPaletteTheme')?.value || 'auto',
            api_key: state.api_key,
            model: state.model,
            prefer_cached: true,
            map_values: lastMapValues,
            image_data: (!lastMapValues && hasMapImg) ? serverImg.src : undefined
        };

        const res = await fetch('/api/analyze_map', {
            method: 'POST',
            signal: controller.signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (version !== paletteAnalysisVersion) return;
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.error || 'Could not analyze map');
        }
        applyAnalysisToFillingStudio(data.analysis, data.map_values, true);
        paletteAnalysisReady = true;
        if (!insideModal) {
            openFillingStudio();
        }
    } catch (err) {
        if (version !== paletteAnalysisVersion || err.name === 'AbortError') return;
        logToConsole(`[Auto-Style] ${err.message}`, 'warn');
        showToast(err.message || 'Auto-detect failed', 'warn');
    } finally {
        if (version === paletteAnalysisVersion) {
            paletteAnalysisRequest = null;
            paletteAnalysisPending = false;
            document.getElementById('restyleCurrentMapBtn').disabled = mapRepaintPending || !paletteAnalysisReady;
            if (btnIcon) btnIcon.className = 'fa-solid fa-magnifying-glass-chart text-xs';
        }
    }
}

async function handleInspectUploadedMapImage(event) {
    const file = event.target?.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
        const dataUrl = reader.result;
        // Show the inspected map on the stage as well
        const imgEl = document.getElementById('serverMapImage');
        const emptyEl = document.getElementById('emptyState');
        if (imgEl) {
            imgEl.src = dataUrl;
            imgEl.classList.remove('hidden');
        }
        if (emptyEl) emptyEl.classList.add('hidden');

        showToast(`Extracting color palette from "${file.name}"...`, 'info');
        const state = getFormState();
        try {
            const res = await fetch('/api/analyze_map', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    image_data: dataUrl,
                    topic: state.topic,
                    mode: 'auto',
                    map_type: state.map_type,
                    palette_theme: document.getElementById('fillingPaletteTheme')?.value || 'auto',
                    api_key: state.api_key,
                    model: state.model,
                    prefer_cached: false
                })
            });
            const data = await res.json();
            if (!res.ok || data.status !== 'success') {
                throw new Error(data.error || 'Map image inspection failed');
            }
            applyAnalysisToFillingStudio(data.analysis, data.map_values, true);
            logToConsole(`[Map Inspector] Extracted ${data.analysis?.ui_txt_rules?.length || 0} palette colors from "${file.name}" (palette-only)`, 'success');
        } catch (err) {
            showToast(err.message || 'Could not inspect map image', 'error');
        } finally {
            event.target.value = '';
        }
    };
    reader.readAsDataURL(file);
}

function setMapRepaintBusy(busy) {
    mapRepaintPending = busy;
    const btn = document.getElementById('restyleCurrentMapBtn');
    btn.disabled = busy || paletteAnalysisPending || !paletteAnalysisReady;
    btn.setAttribute('aria-busy', String(busy));
    document.getElementById('fillingPaletteTheme').disabled = busy;
    document.getElementById('labelOffsetX').disabled = busy;
    document.getElementById('openFillingStudioBtn').disabled = busy;
    document.getElementById('mapRepaintButtonLabel').textContent = busy
        ? 'Re-painting…' : 'Apply & Re-Paint Current Map';
    document.getElementById('mapRepaintIcon').className = busy
        ? 'fa-solid fa-circle-notch animate-spin' : 'fa-solid fa-paintbrush';
    document.getElementById('mapRepaintStatus').classList.toggle('hidden', !busy);
    document.getElementById('mapRepaintOverlay').classList.toggle('hidden', !busy);
    document.getElementById('mapViewport').setAttribute('aria-busy', String(busy));
}

async function restyleCurrentMapFromStudio() {
    if (mapRepaintPending) return;
    if (paletteAnalysisPending || !paletteAnalysisReady) {
        showToast('Wait for palette analysis to finish, or retry Detect if it failed.', 'warn');
        return;
    }
    if (!lastMapValues || lastMapAnalysis?.inspection_only) {
        showToast('Generate a map first to re-paint it live (uploaded images only extract palette colors).', 'warn');
        return;
    }
    const state = getFormState();
    setMapRepaintBusy(true);
    try {
        const res = await fetch('/api/restyle_map', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...state,
                ...buildFillingDictionaries(),
                auto_style: false,
                map_values: lastMapValues
            })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') {
            throw new Error(data.error || 'Generate a map first to re-paint it live.');
        }
        const imgEl = document.getElementById('serverMapImage');
        const emptyEl = document.getElementById('emptyState');
        if (imgEl) {
            imgEl.src = data.image_url;
            imgEl.classList.remove('hidden');
            await imgEl.decode();
        }
        if (emptyEl) emptyEl.classList.add('hidden');
        logToConsole('[Auto-Style] Re-painted active map with updated Color Filling Studio rules & typography style.', 'success');
        try {
            const blob = await (await fetch(data.image_url)).blob();
            await StudioStorage.setAsset('latest-map', new File([blob], 'generated-map.png', {type: 'image/png'}), {topic: state.topic});
        } catch {
            logToConsole('[Storage] Map was re-painted, but could not be saved for Video Studio.', 'warn');
        }
        showToast('Map re-painted with new colors & label style', 'success');
        closeFillingStudio(false);
    } catch (err) {
        showToast(err.message || 'Could not re-paint map', 'warn');
    } finally {
        setMapRepaintBusy(false);
    }
}
