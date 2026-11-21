/* Provider selection, browser preferences, API key status, and model fallback choices. Uses shared state; endpoint contracts live in webapp/pages.py. */
function saveBrowserValue(key, value) {
    try {
        if (value) localStorage.setItem(key, value);
        else localStorage.removeItem(key);
    } catch {
        // Inputs remain usable if browser storage is blocked or full.
    }
}

function restoreSearchPreferences() {
    try {
        document.getElementById('deepseekWebSearchToggle').checked = localStorage.getItem('automap-deepseek-web-search') === '1';
        const limit = Math.max(1, Math.min(10, Number(localStorage.getItem('automap-deepseek-max-search')) || 5));
        document.getElementById('deepseekMaxSearchInput').value = limit;
        document.getElementById('deepseekMaxSearchVal').textContent = limit;
    } catch {}
}

function savedApiKey(provider) {
    try { return (localStorage.getItem(PROVIDER_KEY_STORAGE[provider] || 'automap-tavily-api-key') || '').trim(); }
    catch { return providerKeys[provider] || ''; }
}

window.addEventListener('storage', () => {
    for (const provider of Object.keys(providerKeys)) providerKeys[provider] = savedApiKey(provider);
    restoreSearchPreferences();
    updateApiKeyIndicator();
    loadAvailableModels();
});

function modelProvider(model) {
    return String(model || '').toLowerCase().startsWith('deepseek-') ? 'deepseek' : 'gemini';
}

function populateProviderModels(provider, selectedModel = null) {
    const select = document.getElementById('modelSelect');
    const models = fallbackModels.filter(model => modelProvider(model) === provider);
    if (selectedModel && modelProvider(selectedModel) === provider && !models.includes(selectedModel)) {
        models.push(selectedModel);
    }
    document.getElementById(`${provider}ModelsGroup`).replaceChildren(...models.map(model => new Option(model, model)));
    if (selectedModel && models.includes(selectedModel)) select.value = selectedModel;
    else if (modelProvider(select.value) !== provider) select.value = models[0];
}

function onAiModelChange(model) {
    changeAiProvider(modelProvider(model), model);
}

function changeAiProvider(provider, selectedModel = null) {
    if (selectedModel) provider = modelProvider(selectedModel);
    if (!(provider in PROVIDER_LABELS)) return;
    if (provider !== activeAiProvider) {
        activeAiProvider = provider;
        apiKeyRequired = true;
        populateProviderModels(provider, selectedModel);
        saveBrowserValue(PROVIDER_STORAGE_KEY, provider);
        checkApiKeyStatus();
        loadAvailableModels();
    } else if (selectedModel) {
        const select = document.getElementById('modelSelect');
        if (!Array.from(select.options).some(option => option.value === selectedModel)) {
            document.getElementById(`${provider}ModelsGroup`).appendChild(new Option(selectedModel, selectedModel));
        }
        select.value = selectedModel;
    }
    updateApiKeyIndicator();
    updateCommandPreview();
}

function restoreSavedMapInputs() {
    try {
        activeAiProvider = localStorage.getItem(PROVIDER_STORAGE_KEY) === 'deepseek' ? 'deepseek' : 'gemini';
        for (const provider of Object.keys(providerKeys)) {
            providerKeys[provider] = localStorage.getItem(PROVIDER_KEY_STORAGE[provider]) || '';
        }
        document.getElementById('topicInput').value = localStorage.getItem(MAP_TOPIC_STORAGE_KEY) || '';
    } catch {
        // Use empty fields when browser storage is unavailable.
    }
}

// --- Reactive API Key & System Status Indicator ---
function updateApiKeyIndicator() {
    const statusIndicator = document.getElementById('statusIndicator');
    const headerDot = document.getElementById('headerApiDot');
    const headerText = document.getElementById('headerApiText');
    const stageDot = document.getElementById('stageStatusDot');
    const hasEnteredKey = !!savedApiKey(activeAiProvider);
    document.getElementById('apiConnectionNotice').classList.toggle('hidden', !apiKeyRequired || hasEnteredKey);
    document.getElementById('apiConnectionMessage').textContent = `Configure your ${PROVIDER_LABELS[activeAiProvider]} key in API Manager.`;
    const needsKeyNow = apiKeyRequired && !hasEnteredKey;

    if (needsKeyNow) {
        if (statusIndicator) {
            statusIndicator.className = 'flex items-center gap-2 px-3 py-2 rounded-xl bg-amber-500/10 border border-amber-500/40 text-xs text-amber-300 transition-colors';
        }
        if (headerDot) {
            headerDot.className = 'w-2 h-2 rounded-full bg-amber-400  ';
        }
        if (headerText) {
            headerText.textContent = 'API Key Needed';
        }
        if (stageDot) {
            stageDot.className = 'w-2.5 h-2.5 rounded-full bg-amber-400   transition-colors';
        }
    } else {
        if (statusIndicator) {
            statusIndicator.className = 'flex items-center gap-2 px-3 py-2 rounded-xl bg-slate-900/90 border border-emerald-500/30 text-xs text-emerald-300 transition-colors';
        }
        if (headerDot) {
            headerDot.className = 'w-2 h-2 rounded-full bg-emerald-400 ';
        }
        if (headerText) {
            headerText.textContent = apiKeyRequired ? 'API Key Entered' : 'System Ready';
        }
        if (stageDot) {
            stageDot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-400  transition-colors';
        }
    }
}

// --- API key status / input ---
async function checkApiKeyStatus() {
    const provider = activeAiProvider;
    try {
        const res = await fetch(`/api/status?provider=${provider}`);
        if (!res.ok) throw new Error('status endpoint unavailable');
        const data = await res.json();
        if (provider !== activeAiProvider) return;
        apiKeyRequired = !!data.needs_api_key;
    } catch (err) {
        if (provider !== activeAiProvider) return;
        // If we can't determine status, be safe and assume no .env key.
        apiKeyRequired = true;
        logToConsole(`[Warning] Could not read /api/status (${err.message}). Assuming API key is required.`, 'warn');
    }


    updateApiKeyIndicator();
}

// --- Prompt presets ---
