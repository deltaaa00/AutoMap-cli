/**
 * Manage saved credential inputs, Telegram testing, and YouTube OAuth buttons.
 * Preserves legacy browser localStorage keys and the server request contracts.
 * Uses common.js/overview.js globals; bootstrap initializes saved input values.
 * Changing secret persistence policy belongs in a separate behavior change.
 */
const SECRET_STORAGE_KEYS = {
    secTgToken: 'automap-agent-tg-token',
    secTgChatId: 'automap-agent-tg-chat-id',
    secYtApiKey: 'automap-agent-yt-api-key',
    secGeminiApiKey: 'automap-gemini-api-key',
    secDeepseekApiKey: 'automap-deepseek-api-key',
    secTavilyApiKey: 'automap-tavily-api-key',
};

function saveLocalSecret(id, val) {
    try {
        const key = SECRET_STORAGE_KEYS[id];
        if (!key) return;
        if (val) localStorage.setItem(key, val);
        else localStorage.removeItem(key);
    } catch (_) {}
}

function initLocalSecrets() {
    Object.entries(SECRET_STORAGE_KEYS).forEach(([id, storageKey]) => {
        const el = document.getElementById(id);
        if (!el) return;
        try {
            const saved = localStorage.getItem(storageKey);
            if (saved) el.value = saved;
        } catch (_) {}
        el.addEventListener('input', () => saveLocalSecret(id, el.value.trim()));
        el.addEventListener('change', () => saveLocalSecret(id, el.value.trim()));
    });
}

function toggleSecretVisibility(inputId, iconId) {
    const input = document.getElementById(inputId);
    const icon = document.getElementById(iconId);
    if (!input || !icon) return;
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    icon.className = show ? 'fa-solid fa-eye-slash text-xs' : 'fa-solid fa-eye text-xs';
}

async function saveSecrets() {
    const payload = {};
    const tok = document.getElementById('secTgToken').value.trim();
    const cid = document.getElementById('secTgChatId').value.trim();
    const ytk = document.getElementById('secYtApiKey').value.trim();
    const gmk = document.getElementById('secGeminiApiKey').value.trim();
    const dsk = document.getElementById('secDeepseekApiKey').value.trim();
    const tvk = document.getElementById('secTavilyApiKey').value.trim();
    if (tok) payload.telegram_bot_token = tok;
    if (cid) payload.telegram_chat_id = cid;
    if (ytk) payload.youtube_api_key = ytk;
    if (gmk) payload.gemini_api_key = gmk;
    if (dsk) payload.deepseek_api_key = dsk;
    if (tvk) payload.tavily_api_key = tvk;
    if (!Object.keys(payload).length) { showAgentToast('Enter at least one key or Chat ID.', true); return; }
    const res = await fetch('/api/agent/secrets', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
        if (tok) saveLocalSecret('secTgToken', tok);
        if (cid) saveLocalSecret('secTgChatId', cid);
        if (ytk) saveLocalSecret('secYtApiKey', ytk);
        if (gmk) saveLocalSecret('secGeminiApiKey', gmk);
        if (dsk) saveLocalSecret('secDeepseekApiKey', dsk);
        if (tvk) saveLocalSecret('secTavilyApiKey', tvk);
        showAgentToast('Keys saved! Agent will pick them up automatically.');
    } else {
        showAgentToast(data.error || 'Failed to save keys', true);
    }
    loadOverview();
}

async function testTelegram() {
    const res = await fetch('/api/agent/telegram/test', {method: 'POST'});
    const data = await res.json();
    showAgentToast(data.message || data.error, !res.ok);
}

async function connectAgentYouTube() {
    const file = document.getElementById('agentYtSecretFile').files[0];
    if (!file) { showAgentToast('Choose a client_secret.json file first.', true); return; }
    const form = new FormData();
    form.append('client_secret', file);
    try {
        const res = await fetch('/api/agent/youtube/connect', {method: 'POST', body: form});
        const text = await res.text();
        let data = {};
        try { data = JSON.parse(text); } catch (_) {}
        if (res.ok && data.authorization_url) {
            window.location.assign(data.authorization_url);
        } else {
            showAgentToast(data.error || `Could not start OAuth flow (HTTP ${res.status}).`, true);
        }
    } catch (err) {
        showAgentToast(err?.message || 'Network error starting OAuth flow.', true);
    }
}

async function disconnectAgentYouTube() {
    const res = await fetch('/api/agent/youtube/disconnect', {method: 'POST'});
    const data = await res.json();
    showAgentToast(data.message || data.error, !res.ok);
    loadOverview();
}
