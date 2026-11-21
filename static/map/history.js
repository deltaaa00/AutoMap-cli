/* Console output and persistent map run history. Serializes storage writes, expires old runs, restores previews/parameters, and backfills thumbnails. */
function describeDataUrl(url) {
    if (!url) return 'no image';
    const m = /^data:([^;]+);base64,(.*)$/.exec(url);
    if (!m) return url.length > 60 ? url.slice(0, 60) + '…' : url;
    const mime = m[1];
    const bytes = Math.floor(m[2].length * 0.75);
    const kb = (bytes / 1024).toFixed(1);
    return `${mime}, ${kb} kB`;
}

function logToConsole(text, type = 'info') {
    const box = document.getElementById('consoleTabContent');
    const line = document.createElement('div');
    const time = new Date().toLocaleTimeString();

    const colorMap = {
        cmd: 'text-emerald-400 font-semibold',
        info: 'text-slate-300',
        success: 'text-accent-300 font-medium',
        warn: 'text-amber-400',
        error: 'text-rose-400 font-semibold'
    };

    line.className = `${colorMap[type] || 'text-slate-300'} break-all`;
    line.textContent = `[${time}] ${text}`;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
}

function clearConsole() {
    document.getElementById('consoleTabContent').innerHTML = '<div class="text-slate-500">[System] Console cleared.</div>';
    showToast("Console output cleared", "info");
}

function queueHistoryWrite(operation) {
    historyWrites = historyWrites.then(operation).catch(() => {
        document.getElementById('historyStorageStatus').textContent = 'History could not be saved. Kept for this visit only.';
        logToConsole('[History] Browser storage is unavailable or full. New history is kept for this visit only.', 'warn');
    });
}

async function restoreRunHistory() {
    const revision = historyRevision;
    try {
        const saved = await StudioStorage.getHistory();
        if (revision !== historyRevision) return;
        const entries = new Map(saved.map(entry => [entry.id, entry]));
        for (const entry of runHistory) entries.set(entry.id, entry);
        const cutoff = Date.now() - HISTORY_RETENTION_MS;
        runHistory = [...entries.values()].filter(entry => entry.createdAt >= cutoff)
            .sort((a, b) => b.createdAt - a.createdAt);
        renderRunHistory();
    } catch {
        // History remains usable in memory if IndexedDB is blocked.
    }
}

function pruneRunHistory() {
    const cutoff = Date.now() - HISTORY_RETENTION_MS;
    const previousLength = runHistory.length;
    runHistory = runHistory.filter(entry => entry.createdAt >= cutoff);
    if (runHistory.length !== previousLength) renderRunHistory();
    queueHistoryWrite(() => StudioStorage.pruneHistory());
}

function clearHistory() {
    historyRevision++;
    runHistory = [];
    renderRunHistory();
    queueHistoryWrite(() => StudioStorage.clearHistory());
    showToast("Run history cleared", "info");
}

function createHistoryThumbnail(dataUrl, size = 96) {
    return new Promise(resolve => {
        if (!dataUrl || !dataUrl.startsWith('data:image/')) {
            resolve(null);
            return;
        }
        const img = new Image();
        img.onload = () => {
            try {
                const canvas = document.createElement('canvas');
                canvas.width = size;
                canvas.height = size;
                const ctx = canvas.getContext('2d');
                const scale = Math.max(size / img.naturalWidth, size / img.naturalHeight);
                const drawW = img.naturalWidth * scale;
                const drawH = img.naturalHeight * scale;
                ctx.drawImage(img, (size - drawW) / 2, (size - drawH) / 2, drawW, drawH);
                resolve(canvas.toDataURL('image/jpeg', 0.82));
            } catch {
                resolve(null);
            }
        };
        img.onerror = () => resolve(null);
        img.src = dataUrl;
    });
}

function addToHistory(state, cmd, extra = {}) {
    const {api_key, tavily_api_key, ...savedState} = state;
    const createdAt = Date.now();
    const entry = {
        id: `${createdAt}-${Math.random().toString(36).slice(2)}`,
        createdAt,
        time: new Date(createdAt).toLocaleString(),
        state: savedState,
        cmd,
        image_url: extra.image_url || null,
        thumb_url: null,
        analysis: extra.analysis || null,
        map_values: extra.map_values || null
    };
    runHistory = [entry, ...runHistory.filter(item => createdAt - item.createdAt <= HISTORY_RETENTION_MS)];
    renderRunHistory();
    // Persist the result immediately; thumbnails may finish after navigation.
    queueHistoryWrite(() => StudioStorage.addHistory(entry));
    if (entry.image_url) {
        createHistoryThumbnail(entry.image_url).then(thumb => {
            if (!thumb) return;
            entry.thumb_url = thumb;
            const imgEl = document.querySelector(`[data-history-id="${entry.id}"] img`);
            if (imgEl) imgEl.src = thumb;
            queueHistoryWrite(() => StudioStorage.addHistory(entry));
        });
    }
}

let thumbBackfillRunning = false;
function backfillMissingThumbnails() {
    if (thumbBackfillRunning) return;
    const missing = runHistory.filter(item => item.image_url && !item.thumb_url);
    if (!missing.length) return;
    thumbBackfillRunning = true;
    (async () => {
        for (const item of missing) {
            const thumb = await createHistoryThumbnail(item.image_url);
            if (thumb) {
                item.thumb_url = thumb;
                const imgEl = document.querySelector(`[data-history-id="${item.id}"] img`);
                if (imgEl) imgEl.src = thumb;
                queueHistoryWrite(() => StudioStorage.addHistory(item));
            }
            await new Promise(r => setTimeout(r, 20));
        }
        thumbBackfillRunning = false;
    })();
}

function renderRunHistory() {
    document.getElementById('historyCountBadge').textContent = runHistory.length;
    const list = document.getElementById('historyList');
    list.replaceChildren();
    if (!runHistory.length) {
        const empty = document.createElement('div');
        empty.className = 'text-xs text-slate-500 col-span-full text-center py-8';
        empty.textContent = 'No runs yet. History is saved for 24 hours.';
        list.appendChild(empty);
    }
    runHistory.forEach((item, idx) => {
        const row = document.createElement('div');
        row.dataset.historyId = item.id || '';
        row.style.contain = 'layout style paint';
        row.className = 'p-3 rounded-xl bg-slate-900 border border-slate-800 hover:border-accent-500/60 cursor-pointer transition flex items-center gap-3';
        row.addEventListener('click', () => restoreHistoryItem(idx));

        if (item.image_url || item.thumb_url) {
            const preview = document.createElement('img');
            preview.decoding = 'async';
            preview.loading = 'lazy';
            preview.width = 44;
            preview.height = 44;
            preview.src = item.thumb_url || item.image_url;
            preview.alt = 'Map preview';
            preview.className = 'w-11 h-11 rounded-lg object-cover border border-slate-700/80 shrink-0 bg-slate-950';
            row.appendChild(preview);
        }

        const details = document.createElement('div');
        details.className = 'min-w-0 flex-1 flex flex-col justify-between gap-1';
        const metadata = document.createElement('div');
        metadata.className = 'flex items-center justify-between gap-2 text-xs';
        const topicEl = document.createElement('span');
        topicEl.className = 'text-accent-300 font-semibold truncate';
        topicEl.textContent = (item.state?.topic || '').trim() || 'Untitled map';
        const time = document.createElement('span');
        time.className = 'text-slate-500 shrink-0';
        time.textContent = item.time;
        metadata.append(topicEl, time);

        const modeLine = document.createElement('div');
        modeLine.className = 'text-xs font-mono text-slate-400 truncate';
        const mapLabel = String(item.state?.map_type || 'default').replace(/_/g, ' ');
        modeLine.textContent = `${mapLabel} / ${item.state?.mode || 'text'}`;
        details.append(metadata, modeLine);
        row.appendChild(details);
        list.appendChild(row);
    });
    backfillMissingThumbnails();
}

function restoreHistoryItem(index) {
    const item = runHistory[index];
    if (!item) return;
    if (Date.now() - item.createdAt > HISTORY_RETENTION_MS) {
        pruneRunHistory();
        showToast('This run has expired after 24 hours.', 'info');
        return;
    }

    const s = item.state;
    updateLabelOffset(s.label_style?.offset_x ?? 0);
    changeAiProvider(s.provider || modelProvider(s.model), s.model);
    const modeRadio = document.querySelector(`input[name="mode"][value="${s.mode}"]`);
    if (modeRadio) modeRadio.checked = true;
    const mapRadio = document.querySelector(`input[name="map_type"][value="${s.map_type}"]`);
    if (mapRadio) mapRadio.checked = true;
    syncGsm(s.gsm);
    setSelectedExtraOption(s.extra_option);
    const legendToggle = document.getElementById('showLegendToggle');
    if (legendToggle) legendToggle.checked = !!s.show_legend;
    const hlToggle = document.getElementById('highlightExtremaToggle');
    if (hlToggle) hlToggle.checked = !!s.highlight_extrema;
    const hlHigh = document.getElementById('highlightHighColor');
    if (hlHigh && s.highlight_high_color) hlHigh.value = s.highlight_high_color;
    const hlLow = document.getElementById('highlightLowColor');
    if (hlLow && s.highlight_low_color) hlLow.value = s.highlight_low_color;
    document.getElementById('modelSelect').value = s.model;
    document.getElementById('fontSelect').value = s.font;
    document.getElementById('topicInput').value = s.topic || '';
    document.getElementById('labelSuffixInput').value = s.label_suffix || '';
    updateAiTemperature(s.temperature ?? DEFAULT_AI_TEMPERATURE);

    if (item.image_url) {
        const imgEl = document.getElementById('serverMapImage');
        const emptyEl = document.getElementById('emptyState');
        const metaBadge = document.getElementById('mapMetaBadge');
        if (imgEl) {
            imgEl.src = item.image_url;
            imgEl.classList.remove('hidden');
        }
        if (emptyEl) emptyEl.classList.add('hidden');
        if (metaBadge) metaBadge.textContent = `Restored (${item.time})`;
        if (item.analysis) {
            applyAnalysisToFillingStudio(item.analysis, item.map_values || null, false);
        } else {
            lastMapValues = item.map_values || null;
            lastMapAnalysis = null;
        }
        syncLatestMapToStudioStorage(item.image_url, s.topic);
    }

    updateCommandPreview();
    showToast(item.image_url ? "Map & parameters restored from history" : "Parameters restored from history", "info");
}

function switchBottomTab(tab) {
    const consoleTab = document.getElementById('consoleTabContent');
    const historyTab = document.getElementById('historyTabContent');
    const consoleBtn = document.getElementById('tabConsoleBtn');
    const historyBtn = document.getElementById('tabHistoryBtn');
    const clearConsoleBtn = document.getElementById('clearConsoleBtn');
    const clearHistoryBtn = document.getElementById('clearHistoryBtn');

    if (tab === 'console') {
        consoleTab.classList.remove('hidden');
        historyTab.classList.add('hidden');
        consoleBtn.className = "text-xs font-semibold text-accent-300 border-b-2 border-accent-500 pb-2 -mb-2.5 flex items-center gap-2 transition";
        historyBtn.className = "text-xs font-semibold text-slate-400 hover:text-slate-200 pb-2 -mb-2.5 flex items-center gap-2 transition";
        if (clearConsoleBtn) clearConsoleBtn.classList.remove('hidden');
        if (clearHistoryBtn) clearHistoryBtn.classList.add('hidden');
    } else {
        consoleTab.classList.add('hidden');
        historyTab.classList.remove('hidden');
        historyBtn.className = "text-xs font-semibold text-accent-300 border-b-2 border-accent-500 pb-2 -mb-2.5 flex items-center gap-2 transition";
        consoleBtn.className = "text-xs font-semibold text-slate-400 hover:text-slate-200 pb-2 -mb-2.5 flex items-center gap-2 transition";
        if (clearConsoleBtn) clearConsoleBtn.classList.add('hidden');
        if (clearHistoryBtn) clearHistoryBtn.classList.remove('hidden');
    }
}

// --- Reset Form ---
