/* Normalize map form values and keep CLI preview, compatibility hints, badges, model/font choices in sync. Debounces topic typing; flushes pending input on pagehide. */
// --- Get Current Form Values ---
function buildFillingDictionaries() {
    const filling_txt = {};
    const filling_num = {};
    fillingTxtRules.forEach(r => {
        if (r.key && r.key.trim()) {
            filling_txt[r.key.trim()] = hexToRgbArray(r.hex, r.alpha);
        }
    });
    fillingNumRules.forEach(r => {
        filling_num[`(${Number(r.low) || 0}, ${Number(r.high) || 0})`] = hexToRgbArray(r.hex, r.alpha);
    });
    return { filling_txt, filling_num };
}

function getSelectedExtraOption() {
    const parts = [];
    if (document.getElementById('showMostLeastToggle')?.checked) parts.push('show_most_least');
    if (document.getElementById('shortFormToggle')?.checked) parts.push('short_form');
    return parts.length ? parts.join(',') : 'none';
}

function setSelectedExtraOption(value) {
    const raw = String(value || 'none').toLowerCase();
    const showMostLeast = raw.includes('show_most_least') || raw === 'both';
    const shortForm = raw.includes('short_form') || raw === 'both';
    const mlEl = document.getElementById('showMostLeastToggle');
    const sfEl = document.getElementById('shortFormToggle');
    if (mlEl) mlEl.checked = showMostLeast;
    if (sfEl) sfEl.checked = shortForm;
}

function getFormState() {
    const mode = document.querySelector('input[name="mode"]:checked')?.value || 'text';
    const mapType = document.querySelector('input[name="map_type"]:checked')?.value || 'default';
    const gsm = parseInt(document.getElementById('gsmNumber').value, 10) || 15;
    const extraOption = getSelectedExtraOption();
    const model = document.getElementById('modelSelect').value;
    const font = document.getElementById('fontSelect').value;
    const topic = document.getElementById('topicInput').value.trim();
    const apiKey = savedApiKey(activeAiProvider);
    const autoStyleEl = document.getElementById('autoStyleFromMapToggle');
    const auto_style = autoStyleEl ? !!autoStyleEl.checked : true;
    const palette_theme = document.getElementById('fillingPaletteTheme')?.value || 'auto';
    const { filling_txt, filling_num } = buildFillingDictionaries();

    return {
        provider: activeAiProvider,
        web_search: activeAiProvider === 'deepseek' && !!document.getElementById('deepseekWebSearchToggle')?.checked,
        ...(activeAiProvider === 'deepseek' && document.getElementById('deepseekWebSearchToggle')?.checked && savedApiKey('tavily') ? {tavily_api_key: savedApiKey('tavily')} : {}),
        max_search_requests: parseInt(document.getElementById('deepseekMaxSearchInput')?.value || '5', 10) || 5,
        mode,
        map_type: mapType,
        gsm,
        extra_option: extraOption,
        show_legend: !!document.getElementById('showLegendToggle')?.checked,
        highlight_extrema: !!document.getElementById('highlightExtremaToggle')?.checked,
        highlight_high_color: document.getElementById('highlightHighColor')?.value || '#00e5ff',
        highlight_low_color: document.getElementById('highlightLowColor')?.value || '#ff2daa',
        label_suffix: (document.getElementById('labelSuffixInput')?.value || '').slice(0, 16),
        model,
        font,
        topic,
        api_key: apiKey,
        temperature: Number(document.getElementById('aiTemperature').value),
        auto_style,
        palette_theme,
        // Automatic previews are not manual overrides for the next map.
        ...(!auto_style ? { filling_txt, filling_num } : {}),
        label_style: { ...labelStyleConfig, offset_x: Number(document.getElementById('labelOffsetX').value) }
    };
}

// --- Sync Global size modifier Slider & Number Input ---
function updateAiTemperature(value, persist = true) {
    const parsed = Number(value);
    const temperature = Number.isFinite(parsed) ? Math.max(0, Math.min(2, parsed)) : DEFAULT_AI_TEMPERATURE;
    const input = document.getElementById('aiTemperature');
    input.value = temperature;
    document.getElementById('aiTemperatureValue').textContent = Number(input.value).toFixed(2);
    const progress = Number(input.value) / 2 * 100;
    input.style.background = `linear-gradient(to right, #8fb69a ${progress}%, #39393e ${progress}%)`;
    if (persist) {
        try { localStorage.setItem(AI_TEMPERATURE_STORAGE_KEY, input.value); } catch {}
        updateCommandPreview();
    }
}

function syncGsm(val) {
    const parsed = parseInt(val, 10);
    const safeVal = Math.max(-50, Math.min(100, isNaN(parsed) ? 15 : parsed));
    document.getElementById('gsmRange').value = safeVal;
    document.getElementById('gsmNumber').value = safeVal;
    updateCommandPreview();
}

function adjustGsm(delta) {
    const current = parseInt(document.getElementById('gsmNumber').value, 10);
    const base = isNaN(current) ? 15 : current;
    syncGsm(base + delta);
}

// Save the topic immediately; debounce the more expensive preview work.
let topicPreviewTimer;
function scheduleTopicPreview(event) {
    saveBrowserValue(MAP_TOPIC_STORAGE_KEY, document.getElementById('topicInput').value);
    clearTimeout(topicPreviewTimer);
    if (event && event.isComposing) return;
    topicPreviewTimer = setTimeout(flushTopicPreview, 250);
}

function flushTopicPreview() {
    clearTimeout(topicPreviewTimer);
    updateCommandPreview();
}

// Flush a pending draft when switching away before the debounce expires.
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushTopicPreview();
});
window.addEventListener('pagehide', flushTopicPreview);

// --- Build & Update Live CLI Command ---
function updateCommandPreview() {
    const state = getFormState();
    saveBrowserValue(MAP_TOPIC_STORAGE_KEY, document.getElementById('topicInput').value);
    saveBrowserValue(LABEL_SUFFIX_STORAGE_KEY, state.label_suffix || '');

    // Match cli.py's signature: `cli.py make "<prompt>" -m ... -mp ... -gsm ... -eop ... --model ... --font ...`
    const escapedTopic = (state.topic || '').replace(/"/g, '\\"');
    let cmd = `python cli.py make "${escapedTopic}" -m ${state.mode} -mp ${state.map_type} -gsm ${state.gsm} -eop ${state.extra_option} --model ${state.model} --font "${state.font}" --temperature ${state.temperature}`;
    const isFlagMap = state.map_type === 'flag' || state.map_type === 'world_countries';
    if (state.show_legend && !isFlagMap) cmd += ' --legend';
    if (state.highlight_extrema) cmd += ' --highlight-extrema';
    if (state.label_suffix) cmd += ` --suffix "${state.label_suffix.replace(/"/g, '\\"')}"`;

    document.getElementById('cliCommandDisplay').textContent = cmd;
    const hlColorsRow = document.getElementById('highlightColorsRow');
    if (hlColorsRow) hlColorsRow.classList.toggle('hidden', !state.highlight_extrema);
    const legendEl = document.getElementById('showLegendToggle');
    if (legendEl) {
        legendEl.disabled = isFlagMap;
        const legendLabel = legendEl.closest('label');
        if (legendLabel) legendLabel.classList.toggle('opacity-50', isFlagMap);
    }
    updateActiveBadges(state);
    updateCompatibilityHint(state);
    updateHudCoords(state);
    if (typeof renderFillingStudio === 'function') renderFillingStudio();
}

function updateCompatibilityHint(state) {
    const hint = document.getElementById('extraOptionHint');
    if (!hint) return;
    const hasMostLeast = String(state.extra_option || '').includes('show_most_least');
    if ((hasMostLeast || state.highlight_extrema) && state.mode === 'text') {
        hint.textContent = 'Note: Most/Least and Min/Max highlighting only apply in numeric mode.';
        hint.className = 'text-xs text-amber-400/90 mt-2 block';
    } else if (hasMostLeast && state.map_type === 'world') {
        hint.textContent = 'Note: show_most_least overlay is supported on default, flag, and world_countries maps.';
        hint.className = 'text-xs text-amber-400/90 mt-2 block';
    } else {
        hint.className = 'hidden';
    }
}

function updateHudCoords(state) {
    const el = document.getElementById('hudCoordsText');
    if (!el) return;
    if (state.map_type === 'world') {
        el.textContent = 'World · Continents';
    } else if (state.map_type === 'world_countries') {
        el.textContent = 'World · Countries';
    } else {
        el.textContent = 'Europe';
    }
}

// --- Update Watermark Badges on Map ---
let activeBadgesMarkup = null;
function updateActiveBadges(state) {
    const container = document.getElementById('activeFlagsBadges');
    const badges = [
        { label: `mode: ${state.mode}`, color: 'bg-slate-900/90 text-slate-400 border-slate-700' },
        { label: `map: ${state.map_type}`, color: 'bg-slate-900/90 text-slate-400 border-slate-700' },
        { label: `gsm: ${state.gsm}`, color: 'bg-slate-900/90 text-slate-400 border-slate-700' },
        { label: `eop: ${state.extra_option}`, color: 'bg-slate-900/90 text-slate-400 border-slate-700' },
        ...(state.show_legend ? [{ label: 'legend', color: 'bg-slate-900/90 text-slate-400 border-slate-700' }] : []),
        ...(state.highlight_extrema ? [{ label: 'highlight min/max', color: 'bg-slate-900/90 text-slate-400 border-slate-700' }] : []),
        { label: state.model, color: 'bg-slate-900/90 text-slate-400 border-slate-700' },
        { label: state.font, color: 'bg-slate-900/90 text-slate-400 border-slate-700' }
    ];

    const markup = badges.map(b =>
        `<span class="text-xs font-mono px-3 py-1 rounded-lg border ${b.color}">${escapeHtml(b.label)}</span>`
    ).join('');
    if (markup !== activeBadgesMarkup) {
        container.innerHTML = markup;
        activeBadgesMarkup = markup;
    }
}

// --- Custom Model Addition ---
function toggleCustomModelInput() {
    const box = document.getElementById('customModelBox');
    box.classList.toggle('hidden');
    if (!box.classList.contains('hidden')) {
        document.getElementById('newModelInput').focus();
    }
}

function addCustomModel() {
    const input = document.getElementById('newModelInput');
    const val = input.value.trim();
    if (!val) {
        showToast('Enter a model ID first', 'warn');
        input.focus();
        return;
    }

    if (modelProvider(val) !== activeAiProvider) changeAiProvider(modelProvider(val), val);
    const select = document.getElementById('modelSelect');
    const existing = Array.from(select.options).find(opt => opt.value === val);
    if (!existing) {
        const option = document.createElement('option');
        option.value = val;
        option.textContent = val;
        document.getElementById(`${activeAiProvider}ModelsGroup`).appendChild(option);
    }
    select.value = val;

    input.value = '';
    document.getElementById('customModelBox').classList.add('hidden');
    updateCommandPreview();
    showToast(`Model "${val}" selected`, 'success');
}

// --- Auto-discover Fonts from Flask `/api/fonts` ---
async function fetchFontsFromServer(silent = false) {
    const icon = document.getElementById('fontRefreshIcon');
    icon.classList.add('fa-spin');

    try {
        const res = await fetch('/api/fonts');
        if (!res.ok) throw new Error('Flask server did not respond');
        const data = await res.json();
        if (Array.isArray(data.fonts) && data.fonts.length > 0) {
            const select = document.getElementById('fontSelect');
            const previous = select.value;
            select.replaceChildren(...data.fonts.map(font => new Option(String(font), String(font))));
            // Keep the previously selected font if it still exists.
            if (data.fonts.includes(previous)) select.value = previous;
            updateCommandPreview();
            if (!silent) showToast(`Discovered ${data.fonts.length} fonts in resources/fonts`, 'success');
        }
    } catch (err) {
        logToConsole(`[Warning] Could not fetch fonts from /api/fonts (${err.message}). Using default font list.`, 'warn');
        if (!silent) {
            showToast("Could not connect to /api/fonts", "warn");
        }
    } finally {
        setTimeout(() => icon.classList.remove('fa-spin'), 400);
    }
}
