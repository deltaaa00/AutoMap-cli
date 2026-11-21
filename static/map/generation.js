/* Single-map generation and loading-stage polling. Applies returned image/analysis, updates shared assets/history, and releases UI busy state after failures. */
let loadingProgressPollInterval = null;
let lastRenderedLoadingStage = 0;

function renderLoadingStages(activeStage, labelText) {
    const stepText = document.getElementById('loadingStepText');
    if (stepText && labelText) stepText.textContent = labelText;
    if (activeStage === lastRenderedLoadingStage) return;
    lastRenderedLoadingStage = activeStage;

    const stageNames = [
        '1. AI Geospatial Query',
        '2. Painting Regions & Labels',
        '3. Adding Relief, Borders & Overlays',
        '4. Encoding Final PNG'
    ];

    for (let i = 1; i <= 4; i++) {
        const el = document.getElementById(`loadStage${i}`);
        if (!el) continue;
        const title = stageNames[i - 1];
        const nextState = i < activeStage ? 'done' : (i === activeStage ? 'running' : 'pending');
        if (el.dataset.state === nextState) continue;
        el.dataset.state = nextState;
        if (nextState === 'done') {
            el.className = 'flex items-center justify-between text-emerald-400';
            el.innerHTML = `<span class="flex items-center gap-2"><i class="fa-solid fa-check text-xs check-pop"></i> ${title}</span><span class="text-xs opacity-75">Done</span>`;
        } else if (nextState === 'running') {
            el.className = 'flex items-center justify-between text-accent-300';
            el.innerHTML = `<span class="flex items-center gap-2"><i class="fa-solid fa-circle-notch fa-spin text-xs"></i> ${title}</span><span class="text-xs opacity-75">Running</span>`;
        } else {
            el.className = 'flex items-center justify-between text-slate-500';
            el.innerHTML = `<span class="flex items-center gap-2"><i class="fa-regular fa-circle text-xs"></i> ${title}</span><span class="text-xs opacity-75">Pending</span>`;
        }
    }
}

// --- Multi-Stage Loading Animation Helper ---
function startLoadingStages(progressId = '') {
    const stepText = document.getElementById('loadingStepText');
    const timerEl = document.getElementById('loadingElapsedTimer');
    const startTime = performance.now();
    clearInterval(loadingTimerInterval);
    clearInterval(loadingProgressPollInterval);
    lastRenderedLoadingStage = 0;
    for (let i = 1; i <= 4; i++) {
        const el = document.getElementById(`loadStage${i}`);
        if (el) delete el.dataset.state;
    }

    renderLoadingStages(1, 'Querying AI model...');

    loadingTimerInterval = setInterval(() => {
        const elapsed = (performance.now() - startTime) / 1000;
        if (timerEl) timerEl.textContent = `${elapsed.toFixed(1)}s`;
        if (elapsed >= 15 && stepText && stepText.textContent === 'Querying AI model...') {
            stepText.textContent = 'Still waiting for the AI model response...';
        }
    }, 150);

    if (progressId) {
        let polling = false;
        loadingProgressPollInterval = setInterval(async () => {
            if (polling) return;
            polling = true;
            try {
                const resp = await fetch(`/api/generate_progress/${encodeURIComponent(progressId)}`, { cache: 'no-store' });
                if (resp.ok) {
                    const prog = await resp.json();
                    if (prog && typeof prog.stage === 'number') {
                        renderLoadingStages(prog.stage, prog.label);
                    }
                    if (prog && (prog.status === 'done' || prog.status === 'error')) {
                        clearInterval(loadingProgressPollInterval);
                    }
                }
            } catch {
                // Ignore transient polling errors while map is generating.
            } finally {
                polling = false;
            }
        }, 250);
    }
}

function stopLoadingStages() {
    clearInterval(loadingTimerInterval);
    clearInterval(loadingProgressPollInterval);
}

// --- Handle Map Generation (Form Submit) ---
async function handleGenerate(e) {
    e.preventDefault();
    cancelPaletteAnalysis();
    const state = getFormState();
    const cmd = document.getElementById('cliCommandDisplay').textContent;

    const overlay = document.getElementById('loadingOverlay');
    const btn = document.getElementById('generateBtn');
    const metaBadge = document.getElementById('mapMetaBadge');

    // If the server has no .env key, a key must be entered in the form.
    if (apiKeyRequired && !state.api_key) {
        logToConsole(`[Error] Enter your ${PROVIDER_LABELS[activeAiProvider]} API key in API Manager.`, 'error');
        showToast(`Configure your ${PROVIDER_LABELS[activeAiProvider]} API key in API Manager.`, "error");
        document.getElementById('apiManagerLink').focus();
        return;
    }

    const progressId = (window.crypto && typeof crypto.randomUUID === 'function')
        ? crypto.randomUUID()
        : `map_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;

    document.getElementById('generationError').classList.add('hidden');
    overlay.classList.remove('hidden');
    btn.disabled = true;
    btn.classList.add('opacity-70');
    metaBadge.textContent = "Synthesizing...";
    startLoadingStages(progressId);

    logToConsole(`$ ${cmd}`, 'cmd');

    try {
        const response = await fetch('/api/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...state, progress_id: progressId })
        });

        // The backend may return JSON on error; guard against non-JSON bodies.
        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error(`Server returned ${response.status} ${response.statusText}`);
        }

        if (response.ok && result.status === 'success') {
            if (result.command) logToConsole(`[Server] ${result.command}`, 'info');
            if (result.warning) {
                logToConsole(`[Warning] ${result.warning}`, 'warn');
                showToast(result.warning, 'warn');
            }
            logToConsole(`[Success] Map generated and loaded (${describeDataUrl(result.image_url)})`, 'success');

            if (result.map_values) {
                lastMapValues = result.map_values;
            }
            if (result.analysis) {
                applyAnalysisToFillingStudio(result.analysis, result.map_values, false);
                const st = result.analysis.stats || {};
                const ls = result.analysis.label_style || {};
                logToConsole(`[Auto-Style] Detected ${result.analysis.detected_type} map (${st.total_regions || 0} regions, ${st.unique_values || 0} unique values) -> theme "${result.analysis.resolved_theme}", recommended font "${ls.recommended_font || 'Bips.ttf'}"`, 'info');
            }

            // Display actual image returned by Flask (data URL)
            const imgEl = document.getElementById('serverMapImage');
            const emptyEl = document.getElementById('emptyState');
            imgEl.onload = () => resetZoom();
            imgEl.src = result.image_url;
            imgEl.classList.remove('hidden');
            if (emptyEl) emptyEl.classList.add('hidden');

            metaBadge.textContent = `Generated (${new Date().toLocaleTimeString()})`;
            addToHistory(state, cmd, {
                image_url: result.image_url,
                analysis: result.analysis || null,
                map_values: result.map_values || null,
            });
            try {
                const blob = await (await fetch(result.image_url)).blob();
                await StudioStorage.setAsset('latest-map', new File([blob], 'generated-map.png', {type: 'image/png'}), {topic: state.topic});
                showToast("Map generated & auto-styled! Ready in Video Studio.", "success");
            } catch (error) {
                showToast("Map generated, but automatic transfer could not be saved. Download it to use in Video Studio.", "warn");
            }
        } else {
            // Backend errors use {status:'error', error:'...'}
            throw new Error(result.error || result.stderr || 'Generation failed');
        }
    } catch (error) {
        logToConsole(`[API Error] ${error.message}`, 'error');
        const errorPanel = document.getElementById('generationError');
        errorPanel.textContent = error.message;
        errorPanel.classList.remove('hidden');
        showToast(error.message, 'error');
        metaBadge.textContent = "Execution Error";
    } finally {
        stopLoadingStages();
        overlay.classList.add('hidden');
        btn.disabled = false;
        btn.classList.remove('opacity-70');
    }
}

// --- Zoom, Pan & Fullscreen Controls ---
