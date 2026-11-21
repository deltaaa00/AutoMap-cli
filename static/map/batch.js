/* CSV selection, bounded parallel map generation, result selection/download, and latest-map storage sync. Also discovers live model choices used by startup. */
let loadedIdeasFile = { filename: '', text: '' };
let lastBatchResults = [];

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

async function loadAvailableModels() {
    const provider = activeAiProvider;
    const version = ++modelListVersion;
    const select = document.getElementById('modelSelect');
    if (!select) return;
    const apiKey = savedApiKey(activeAiProvider);
    try {
        const res = await fetch('/api/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({provider, ...(apiKey ? {api_key: apiKey} : {})})
        });
        const data = await res.json();
        if (provider !== activeAiProvider || version !== modelListVersion) return;
        if (!res.ok || data.status !== 'success' || !Array.isArray(data.models) || data.models.length === 0) {
            return;
        }
        const prev = select.value;
        const models = data.models.filter(model => typeof model === 'string' && modelProvider(model) === provider);
        if (!models.length) { populateProviderModels(provider); return; }
        const group = document.getElementById(`${provider}ModelsGroup`);
        group.replaceChildren();
        models.forEach(modelId => {
            const opt = document.createElement('option');
            opt.value = modelId;
            opt.textContent = modelId;
            group.appendChild(opt);
        });
        if (models.includes(prev)) {
            select.value = prev;
        } else {
            const preferred = models.find(m => m.includes('flash')) || models[0];
            select.value = preferred;
        }
        updateCommandPreview();
    } catch (_) {
        // Keep static fallback options if /api/models is unreachable
    }
}

async function syncLatestMapToStudioStorage(imageUrl, topic = '') {
    if (!imageUrl || typeof StudioStorage === 'undefined' || !StudioStorage.setAsset) return;
    try {
        const blob = await (await fetch(imageUrl)).blob();
        await StudioStorage.setAsset('latest-map', new File([blob], 'generated-map.png', { type: 'image/png' }), {topic});
    } catch (_) {
        // Ignore storage quota or fetch errors during preview sync
    }
}

function renderBatchResultsPanel(results) {
    lastBatchResults = Array.isArray(results) ? results : [];
    const panel = document.getElementById('batchResultsPanel');
    const summary = document.getElementById('batchResultsSummary');
    const list = document.getElementById('batchResultsList');
    if (!panel || !list) return;
    list.replaceChildren();
    if (lastBatchResults.length === 0) {
        panel.classList.add('hidden');
        return;
    }
    panel.classList.remove('hidden');
    if (summary) {
        summary.textContent = `${lastBatchResults.length} map${lastBatchResults.length === 1 ? '' : 's'} ready`;
    }
    lastBatchResults.forEach((item, idx) => {
        const row = document.createElement('div');
        row.className = 'flex items-center justify-between gap-2 px-2 py-1 rounded-lg bg-slate-900/90 border border-slate-800 hover:border-accent-500/50 transition';

        const selectBtn = document.createElement('button');
        selectBtn.type = 'button';
        selectBtn.className = 'flex-1 text-left text-xs font-mono text-slate-200 hover:text-accent-300 truncate';
        selectBtn.title = String(item.prompt || '');
        selectBtn.textContent = `${item.index}. ${item.prompt || ''}`;
        selectBtn.addEventListener('click', () => selectBatchResult(idx));

        const dlBtn = document.createElement('button');
        dlBtn.type = 'button';
        dlBtn.title = `Download ${item.filename || `map_${idx + 1}.png`}`;
        dlBtn.className = 'text-xs px-2 py-1 rounded bg-slate-800 hover:bg-emerald-600/30 text-slate-300 hover:text-emerald-300 transition';
        const icon = document.createElement('i');
        icon.className = 'fa-solid fa-download';
        dlBtn.appendChild(icon);
        dlBtn.addEventListener('click', () => downloadBatchResult(idx));

        row.appendChild(selectBtn);
        if (item.duration_s !== undefined && item.duration_s !== null) {
            const timeBadge = document.createElement('span');
            timeBadge.className = 'text-xs font-mono text-slate-400 shrink-0';
            timeBadge.textContent = `${item.duration_s}s`;
            row.appendChild(timeBadge);
        }
        row.appendChild(dlBtn);
        list.appendChild(row);
    });
}

function restoreBatchResultParameters(item) {
    changeAiProvider(item.provider || modelProvider(item.model), item.model);
    for (const name of ['mode', 'map_type']) {
        const input = Array.from(document.getElementsByName(name))
            .find(input => input.value === item[name]);
        if (input) input.checked = true;
    }
    if (item.extra_option !== undefined) setSelectedExtraOption(item.extra_option);
    document.getElementById('topicInput').value = item.prompt || '';
    document.getElementById('labelSuffixInput').value = item.label_suffix || '';
    document.getElementById('showLegendToggle').checked = !!item.show_legend;
    document.getElementById('highlightExtremaToggle').checked = !!item.highlight_extrema;
    document.getElementById('highlightHighColor').value = item.highlight_high_color || '#00e5ff';
    document.getElementById('highlightLowColor').value = item.highlight_low_color || '#ff2daa';
    if (item.gsm !== undefined) syncGsm(item.gsm);
    for (const [field, id] of [['model', 'modelSelect'], ['font', 'fontSelect']]) {
        if (!item[field]) continue;
        const select = document.getElementById(id);
        if (!Array.from(select.options).some(option => option.value === item[field])) {
            select.add(new Option(item[field], item[field]));
        }
        select.value = item[field];
    }
    if (item.temperature !== undefined) updateAiTemperature(item.temperature);
    if (item.palette_theme) {
        const palette = document.getElementById('fillingPaletteTheme');
        palette.value = item.palette_theme;
        saveBrowserValue(MAP_PALETTE_STORAGE_KEY, palette.value);
    }
    updateCommandPreview();
}

function selectBatchResult(idx) {
    const item = lastBatchResults[idx];
    if (!item || !item.image_url) return;
    // Repainting must use this result's geography and generation settings.
    cancelPaletteAnalysis();
    restoreBatchResultParameters(item);
    const imgEl = document.getElementById('serverMapImage');
    const emptyEl = document.getElementById('emptyState');
    if (imgEl) {
        imgEl.src = item.image_url;
        imgEl.classList.remove('hidden');
    }
    if (emptyEl) emptyEl.classList.add('hidden');
    if (item.analysis) {
        applyAnalysisToFillingStudio(item.analysis, item.map_values || null, false);
    }
    syncLatestMapToStudioStorage(item.image_url, item.prompt);
    showToast(`Viewing #${item.index}: ${item.prompt}`, 'info');
}

function downloadBatchResult(idx) {
    const item = lastBatchResults[idx];
    if (!item || !item.image_url) return;
    const a = document.createElement('a');
    a.href = item.image_url;
    a.download = item.filename || `map_${idx + 1}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
}

async function downloadAllBatchResults() {
    if (!lastBatchResults.length) return;
    try {
        showToast(`Creating ZIP for ${lastBatchResults.length} maps...`, 'info');
        const res = await fetch('/api/download_batch_zip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                items: lastBatchResults.map(item => ({
                    image_url: item.image_url,
                    prompt: item.prompt,
                    filename: item.filename,
                })),
            }),
        });
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.error || 'Failed to create ZIP archive');
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'automap_batch.zip';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast(`Downloaded ${lastBatchResults.length} maps as ZIP`, 'success');
    } catch (err) {
        showToast(`ZIP download failed: ${err.message}`, 'error');
    }
}

async function loadIdeasFile(file) {
    const badge = document.getElementById('ideasFileBadge');
    const labelText = document.getElementById('ideasFileLabelText');
    const btn = document.getElementById('generateBatchBtn');
    if (!file) {
        loadedIdeasFile = { filename: '', text: '' };
        if (badge) {
            badge.textContent = 'No file';
            badge.className = 'text-xs font-mono text-slate-500 truncate max-w-[110px]';
            badge.removeAttribute('title');
        }
        if (labelText) labelText.textContent = 'Choose CSV';
        if (btn) btn.disabled = true;
        return;
    }
    if (!file.name.toLowerCase().endsWith('.csv')) {
        showToast('Please drop a .csv file', 'warn');
        return;
    }
    try {
        const text = await file.text();
        loadedIdeasFile = { filename: file.name, text };
        if (badge) {
            badge.textContent = file.name;
            badge.title = file.name;
            badge.className = 'text-xs font-mono text-emerald-400 truncate max-w-[140px]';
        }
        if (labelText) labelText.textContent = `Loaded: ${file.name}`;
        if (btn) btn.disabled = !text.trim();
        logToConsole(`[Batch] Loaded ideas file "${file.name}" (${text.length} chars). Click "Generate all" to render.`, 'info');
        showToast(`Loaded "${file.name}" — ready to generate maps`, 'info');
    } catch (err) {
        showToast(`Failed to read file: ${err.message}`, 'error');
    }
}

async function handleIdeasFileSelected(event) {
    await loadIdeasFile(event.target?.files?.[0]);
}

function handleCsvDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
    const zone = document.getElementById('csvDropZone');
    if (zone) zone.classList.add('border-accent-500', 'bg-accent-950/30');
}

function handleCsvDragLeave(event) {
    event.preventDefault();
    event.stopPropagation();
    const zone = document.getElementById('csvDropZone');
    if (zone && !zone.contains(event.relatedTarget)) {
        zone.classList.remove('border-accent-500', 'bg-accent-950/30');
    }
}

async function handleCsvDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    const zone = document.getElementById('csvDropZone');
    if (zone) zone.classList.remove('border-accent-500', 'bg-accent-950/30');
    const file = event.dataTransfer?.files?.[0];
    await loadIdeasFile(file);
}

async function generateMapsFromFile() {
    if (!loadedIdeasFile.text.trim()) {
        showToast('Choose a .csv ideas file first', 'warn');
        return;
    }
    const state = getFormState();
    const btn = document.getElementById('generateBatchBtn');
    const loadingEl = document.getElementById('loadingOverlay');
    const stageEl = document.getElementById('loadingStage');
    if (btn) btn.disabled = true;
    if (loadingEl) loadingEl.classList.remove('hidden');

    let progressWrap = document.getElementById('batchProgressWrap');
    if (!progressWrap && loadingEl) {
        progressWrap = document.createElement('div');
        progressWrap.id = 'batchProgressWrap';
        progressWrap.className = 'w-64 mt-3';
        progressWrap.innerHTML = `
            <div class="flex justify-between text-xs font-mono text-slate-300 mb-1">
                <span id="batchProgressLabel">Parsing CSV...</span>
                <span id="batchProgressPct">0%</span>
            </div>
            <div class="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                <div id="batchProgressFill" class="h-full bg-accent-500 transition-colors duration-200" style="width: 0%"></div>
            </div>
        `;
        loadingEl.appendChild(progressWrap);
    }
    const updateProgress = (done, total, label) => {
        const pct = total > 0 ? Math.round((done / total) * 100) : 0;
        const fill = document.getElementById('batchProgressFill');
        const lbl = document.getElementById('batchProgressLabel');
        const pctEl = document.getElementById('batchProgressPct');
        if (fill) fill.style.width = `${pct}%`;
        if (lbl) lbl.textContent = label || `${done}/${total} maps`;
        if (pctEl) pctEl.textContent = `${pct}%`;
        if (stageEl) stageEl.textContent = label || `Generating ${done}/${total} maps...`;
    };

    try {
        const useParallel = document.getElementById('batchParallelToggle')?.checked !== false;
        if (!useParallel) {
            updateProgress(0, 1, 'Running single batch request...');
            const res = await fetch('/api/generate_batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ...state,
                    filename: loadedIdeasFile.filename,
                    ideas_text: loadedIdeasFile.text
                })
            });
            const data = await res.json();
            if (!res.ok || data.status !== 'success') {
                throw new Error(data.error || 'Batch generation failed');
            }
            const okItems = (data.results || []).filter(r => r.status === 'success' && r.image_url);
            okItems.forEach(item => {
                addToHistory(
                    {
                        ...state,
                        topic: item.prompt,
                        mode: item.mode || state.mode,
                        map_type: item.map_type || state.map_type,
                        gsm: item.gsm ?? state.gsm,
                        extra_option: item.extra_option || state.extra_option,
                        model: item.model || state.model,
                        provider: item.provider || modelProvider(item.model || state.model),
                        font: item.font || state.font,
                        palette_theme: item.palette_theme || state.palette_theme,
                        temperature: item.temperature ?? state.temperature,
                    },
                    `[batch] ${item.filename}`,
                    {
                        image_url: item.image_url,
                        analysis: item.analysis || null,
                        map_values: item.map_values || null,
                    }
                );
                logToConsole(`[Batch ${item.index}/${data.total}] Generated "${item.prompt}" -> ${item.filename}`, 'success');
            });
            (data.results || []).filter(r => r.status !== 'success').forEach(item => {
                logToConsole(`[Batch ${item.index}/${data.total}] Failed "${item.prompt}": ${item.error}`, 'warn');
            });
            updateProgress(data.total || okItems.length, data.total || okItems.length, `Done (${data.succeeded}/${data.total})`);
            renderBatchResultsPanel(okItems);
            if (okItems.length > 0) {
                selectBatchResult(okItems.length - 1);
            }
            showToast(`Generated ${data.succeeded}/${data.total} maps from "${loadedIdeasFile.filename}"`, 'success');
            return;
        }

        updateProgress(0, 1, 'Parsing CSV...');
        const parseRes = await fetch('/api/parse_batch_csv', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename: loadedIdeasFile.filename,
                ideas_text: loadedIdeasFile.text
            })
        });
        const parseData = await parseRes.json();
        if (!parseRes.ok || parseData.status !== 'success') {
            throw new Error(parseData.error || 'Failed to parse CSV');
        }

        const ideas = parseData.ideas || [];
        const total = ideas.length;
        let completed = 0;
        let succeeded = 0;
        const results = new Array(total);
        updateProgress(0, total, `Starting ${total} maps (3 parallel)...`);

        let nextIdx = 0;
        const workerCount = Math.min(3, total);
        const workers = Array.from({ length: workerCount }, async () => {
            while (nextIdx < total) {
                const idx = nextIdx++;
                const idea = ideas[idx];
                const itemPayload = {
                    ...state,
                    topic: idea.prompt,
                    mode: idea.mode || state.mode,
                    map_type: idea.map_type || state.map_type,
                    gsm: idea.gsm ?? state.gsm,
                    extra_option: idea.extra_option || state.extra_option,
                    model: idea.model || state.model,
                    provider: modelProvider(idea.model || state.model),
                    font: idea.font || state.font,
                    palette_theme: idea.palette_theme || state.palette_theme,
                };
                const t0 = performance.now();
                try {
                    const res = await fetch('/api/generate', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(itemPayload)
                    });
                    const data = await res.json();
                    if (!res.ok || data.status !== 'success') {
                        throw new Error(data.error || 'Generation failed');
                    }
                    succeeded++;
                    const duration_s = ((performance.now() - t0) / 1000).toFixed(1);
                    const filename = idea.output || `${String(idx + 1).padStart(2, '0')}.png`;
                    const itemResult = {
                        index: idx + 1,
                        status: 'success',
                        prompt: idea.prompt,
                        filename,
                        duration_s,
                        image_url: data.image_url,
                        analysis: data.analysis || null,
                        map_values: data.map_values || null,
                        ...itemPayload,
                    };
                    results[idx] = itemResult;
                    addToHistory(itemPayload, `[batch] ${filename}`, {
                        image_url: data.image_url,
                        analysis: data.analysis || null,
                        map_values: data.map_values || null,
                    });
                    logToConsole(`[Batch ${idx + 1}/${total}] Generated "${idea.prompt}" -> ${filename}`, 'success');
                } catch (err) {
                    results[idx] = { index: idx + 1, status: 'error', prompt: idea.prompt, error: err.message };
                    logToConsole(`[Batch ${idx + 1}/${total}] Failed "${idea.prompt}": ${err.message}`, 'warn');
                } finally {
                    completed++;
                    updateProgress(completed, total, `${completed}/${total} maps (${succeeded} ok)`);
                }
            }
        });

        await Promise.all(workers);

        const okItems = results.filter(r => r && r.status === 'success' && r.image_url);
        renderBatchResultsPanel(okItems);
        if (okItems.length > 0) {
            selectBatchResult(okItems.length - 1);
        }
        showToast(`Generated ${succeeded}/${total} maps from "${loadedIdeasFile.filename}"`, 'success');
    } catch (err) {
        logToConsole(`[Batch Error] ${err.message}`, 'error');
        showToast(err.message || 'Batch generation failed', 'error');
    } finally {
        if (btn) btn.disabled = false;
        if (loadingEl) loadingEl.classList.add('hidden');
        clearInterval(loadingTimerInterval);
    }
}
