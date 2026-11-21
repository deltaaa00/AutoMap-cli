/* Topic presets and AI idea suggestions, including recent-idea avoidance and applying returned parameters. Uses provider/form helpers and /api/generate_idea. */
function applyPromptPreset(promptText, mode, mapType, extraOption = 'none') {
    const topicInput = document.getElementById('topicInput');
    topicInput.value = promptText;

    const modeRadio = document.querySelector(`input[name="mode"][value="${mode}"]`);
    if (modeRadio) modeRadio.checked = true;

    const mapRadio = document.querySelector(`input[name="map_type"][value="${mapType}"]`);
    if (mapRadio) mapRadio.checked = true;

    setSelectedExtraOption(extraOption);

    updateCommandPreview();
    showToast(`Preset loaded: "${promptText}"`, 'info');
}

function clearTopicInput() {
    const topicInput = document.getElementById('topicInput');
    topicInput.value = '';
    topicInput.focus();
    updateCommandPreview();
}

const RECENT_IDEAS_STORAGE_KEY = 'automap-recent-ideas';

function loadRecentIdeas() {
    try {
        const parsed = JSON.parse(localStorage.getItem(RECENT_IDEAS_STORAGE_KEY) || '[]');
        return Array.isArray(parsed) ? parsed.map(x => String(x || '').trim()).filter(Boolean).slice(-30) : [];
    } catch {
        return [];
    }
}

function rememberGeneratedIdea(ideaText) {
    const cleaned = String(ideaText || '').trim();
    if (!cleaned) return;
    const current = loadRecentIdeas().filter(item => item.toLowerCase() !== cleaned.toLowerCase());
    current.push(cleaned);
    try {
        localStorage.setItem(RECENT_IDEAS_STORAGE_KEY, JSON.stringify(current.slice(-30)));
    } catch {}
}

async function generateIdea() {
    const btn = document.getElementById('generateIdeaBtn');
    const icon = document.getElementById('generateIdeaIcon');
    const textEl = document.getElementById('generateIdeaText');
    const withParams = document.getElementById('ideaModeSelect')?.value === 'idea_and_params';
    const state = getFormState();
    const avoidIdeas = loadRecentIdeas();
    const currentTopic = document.getElementById('topicInput')?.value?.trim();
    if (currentTopic && !avoidIdeas.some(item => item.toLowerCase() === currentTopic.toLowerCase())) {
        avoidIdeas.push(currentTopic);
    }

    if (btn) btn.disabled = true;
    if (icon) icon.className = 'fa-solid fa-spinner fa-spin text-xs';
    if (textEl) textEl.textContent = 'Thinking...';

    try {
        const response = await fetch('/api/generate_idea', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                provider: state.provider,
                model: state.model,
                api_key: state.api_key,
                with_params: withParams,
                count: Number(document.getElementById('ideaCount').value),
                temperature: state.temperature,
                avoid_ideas: avoidIdeas.slice(-30),
            }),
        });
        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error(`Server returned ${response.status} ${response.statusText}`);
        }
        if (!response.ok || result.status !== 'success' || (!result.idea && !result.ideas?.length)) {
            throw new Error(result.error || 'Failed to generate idea');
        }

        const choices = result.ideas || [result];
        const container = document.getElementById('ideaChoices');
        container.replaceChildren();
        choices.forEach((choice, index) => {
            rememberGeneratedIdea(choice.idea);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'w-full text-left p-3 rounded-xl border border-slate-700 bg-slate-900 hover:bg-slate-800 text-sm text-slate-200';
            button.setAttribute('aria-pressed', 'false');
            const title = document.createElement('div');
            title.textContent = `${index + 1}. ${choice.idea}`;
            button.append(title);
            if (withParams && choice.params) {
                const detail = document.createElement('div');
                detail.className = 'mt-1 text-xs text-slate-400';
                detail.textContent = `${choice.params.mode} · ${choice.params.map_type} · ${choice.params.palette_theme} · ${choice.params.font}`;
                button.append(detail);
            }
            button.onclick = () => {
                container.querySelectorAll('button').forEach(item => {
                    item.setAttribute('aria-pressed', String(item === button));
                    item.classList.toggle('ring-2', item === button);
                });
                applyGeneratedIdea(choice, withParams);
            };
            container.append(button);
        });
        showToast('Choose an idea to apply it', 'success');
    } catch (error) {
        logToConsole(`[Idea Error] ${error.message}`, 'error');
        showToast(error.message || 'Failed to generate idea', 'error');
    } finally {
        if (btn) btn.disabled = false;
        if (icon) icon.className = 'fa-solid fa-lightbulb text-xs';
        if (textEl) textEl.textContent = 'Generate idea';
    }
}

function applyGeneratedIdea(result, withParams) {
    try {
        localStorage.setItem('automap-selected-idea', JSON.stringify({
            topic: result.idea, id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, updatedAt: Date.now(),
        }));
    } catch {}

    const topicInput = document.getElementById('topicInput');
    if (topicInput) {
        topicInput.value = result.idea;
        scheduleTopicPreview();
        flushTopicPreview();
    }
    rememberGeneratedIdea(result.idea);

    if (withParams && result.params) {
        const p = result.params;
        const modeRadio = document.querySelector(`input[name="mode"][value="${p.mode}"]`);
        if (modeRadio) modeRadio.checked = true;
        const mapRadio = document.querySelector(`input[name="map_type"][value="${p.map_type}"]`);
        if (mapRadio) mapRadio.checked = true;
        const paletteEl = document.getElementById('fillingPaletteTheme');
        if (paletteEl && p.palette_theme && Array.from(paletteEl.options).some(o => o.value === p.palette_theme)) {
            paletteEl.value = p.palette_theme;
            if (typeof onPaletteThemeChange === 'function') {
                onPaletteThemeChange();
            }
        }
        const fontEl = document.getElementById('fontSelect');
        if (fontEl && p.font && Array.from(fontEl.options).some(o => o.value === p.font)) {
            fontEl.value = p.font;
        }
        if (typeof setSelectedExtraOption === 'function') {
            setSelectedExtraOption(p.extra_option || 'none');
        }
        const legendEl = document.getElementById('showLegendToggle');
        if (legendEl) legendEl.checked = !!p.show_legend;

        updateCommandPreview();
        logToConsole(`[Idea] "${result.idea}" (${p.mode}, ${p.map_type}, theme=${p.palette_theme}, font=${p.font})`, 'info');
        showToast(`Generated idea + params (${p.palette_theme}, ${p.map_type})`, 'success');
    } else {
        updateCommandPreview();
        logToConsole(`[Idea] "${result.idea}"`, 'info');
        showToast('Generated new map idea', 'success');
    }
}
