/**
 * Queue approval/generation/sync actions and save dashboard settings.
 * Uses the existing /api/agent endpoints, shared feedback helpers, and loadOverview.
 * Functions stay global for template onclick handlers; no work runs at load time.
 */
async function expAction(expId, action) {
    const res = await fetch(`/api/agent/action/${expId}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action}),
    });
    const data = await res.json();
    showAgentToast(data.message || data.error || 'Done', !res.ok);
    loadOverview();
}

async function triggerGenerate() {
    const topicEl = document.getElementById('customTopicInput');
    const topic = topicEl ? topicEl.value.trim() : '';
    const res = await fetch('/api/agent/trigger', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(topic ? {topic} : {}),
    });
    const data = await res.json();
    if (res.ok && topicEl) topicEl.value = '';
    showAgentToast(data.message || data.error, !res.ok);
    loadOverview();
}

async function triggerSync() {
    const res = await fetch('/api/agent/sync', {method: 'POST'});
    const data = await res.json();
    showAgentToast(data.message || data.error, !res.ok);
    loadOverview();
}

async function importMusic(compId) {
    const res = await fetch(`/api/agent/competitor/${compId}/import_music`, {method: 'POST'});
    const data = await res.json();
    showAgentToast(data.message || data.error, !res.ok);
    loadOverview();
}

async function saveAgentConfig() {
    const payload = {
        competitor_channels: document.getElementById('cfgCompetitors').value,
        posts_per_day: Number(document.getElementById('cfgPostsPerDay').value || 2),
        series_size: Number(document.getElementById('cfgSeriesSize').value || 4),
        timezone: document.getElementById('cfgTimezone').value.trim() || 'Europe/Kyiv',
        ai_model: document.getElementById('cfgModel').value.trim() || 'gemini-3.8-flash',
        watermark_text: document.getElementById('cfgWatermark').value.trim(),
        web_search_enabled: document.getElementById('cfgWebSearch').checked,
        max_search_requests: Math.max(1, Math.min(5, Number(document.getElementById('cfgMaxSearches').value || 5))),
        automap_url: document.getElementById('cfgAutomapUrl').value.trim(),
        description_template: document.getElementById('cfgDescTemplate').value.trim(),
    };
    const res = await fetch('/api/agent/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    showAgentToast(res.ok ? 'Settings saved!' : (data.error || 'Error'), !res.ok);
    loadOverview();
}
