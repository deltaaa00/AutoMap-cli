'use strict';
const managerKeys = {gemini: 'automap-gemini-api-key', deepseek: 'automap-deepseek-api-key', tavily: 'automap-tavily-api-key'};
const managerElement = id => document.getElementById(id);
function managerMessage(text, error = false) {
    const el = managerElement('managerMessage');
    el.textContent = text;
    el.dataset.error = String(error);
}
function managerStore(key, value) {
    try {
        if (value) localStorage.setItem(key, value);
        else localStorage.removeItem(key);
        return true;
    } catch {
        managerMessage('Browser storage is unavailable. Your changes could not be saved.', true);
        return false;
    }
}
function managerKeyStatus(provider, value) {
    const server = document.querySelector(`[data-provider="${provider}"]`).dataset.server === 'true';
    managerElement(`${provider}Status`).textContent = value ? 'Saved in browser' : server ? 'Server configured' : 'Not configured';
}
function managerLoadKey(provider) {
    try {
        const value = localStorage.getItem(managerKeys[provider]) || '';
        managerElement(`${provider}Key`).value = value;
        managerKeyStatus(provider, value);
    } catch {
        managerElement(`${provider}Status`).textContent = 'Storage unavailable';
        managerMessage('Browser storage is unavailable. Enable storage to save API keys.', true);
    }
}
let managerYoutubeCsrf = '';
async function managerYoutubeStatus() {
    const response = await fetch('/api/youtube/status', {cache: 'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not check YouTube connection.');
    managerYoutubeCsrf = result.csrf_token;
    managerElement('managerYoutubeStatus').textContent = result.connected ? 'Connected' : 'Not connected';
    managerElement('managerYoutubeDisconnect').disabled = !result.connected;
    managerElement('managerYoutubeRedirect').textContent = result.redirect_uri;
}
async function managerYoutubeRequest(path, body) {
    await managerYoutubeStatus();
    const send = () => fetch(path, {method: 'POST', body, headers: {'X-YouTube-CSRF': managerYoutubeCsrf}, cache: 'no-store'});
    let response = await send();
    if (response.status === 403) {
        const result = await response.clone().json().catch(() => ({}));
        if (result.code === 'youtube_csrf_invalid') {
            await managerYoutubeStatus();
            response = await send();
        }
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'YouTube request failed.');
    return result;
}
document.addEventListener('DOMContentLoaded', () => {
    Object.keys(managerKeys).forEach(managerLoadKey);
    for (const button of document.querySelectorAll('[data-save], [data-remove]')) button.addEventListener('click', () => {
        const provider = button.dataset.save || button.dataset.remove;
        const value = button.dataset.remove ? '' : managerElement(`${provider}Key`).value.trim();
        if (managerStore(managerKeys[provider], value)) {
            managerElement(`${provider}Key`).value = value;
            managerKeyStatus(provider, value);
            managerMessage(value ? 'API key saved in this browser.' : 'Browser API key removed.');
        }
    });
    for (const button of document.querySelectorAll('[data-reveal]')) button.addEventListener('click', () => {
        const input = managerElement(button.dataset.reveal);
        const reveal = input.type === 'password';
        input.type = reveal ? 'text' : 'password';
        button.textContent = reveal ? 'Hide' : 'Show';
        button.setAttribute('aria-pressed', String(reveal));
        button.setAttribute('aria-label', `${reveal ? 'Hide' : 'Show'} API key`);
    });
    const toggle = managerElement('managerWebSearch');
    const limit = managerElement('managerSearchLimit');
    try {
        toggle.checked = localStorage.getItem('automap-deepseek-web-search') === '1';
        limit.value = Math.max(1, Math.min(10, Number(localStorage.getItem('automap-deepseek-max-search')) || 5));
    } catch {}
    managerElement('managerSearchValue').value = limit.value;
    toggle.addEventListener('change', () => managerStore('automap-deepseek-web-search', toggle.checked ? '1' : '0'));
    limit.addEventListener('input', () => {
        managerElement('managerSearchValue').value = limit.value;
        managerStore('automap-deepseek-max-search', limit.value);
    });
    managerElement('managerYoutubeRefresh').addEventListener('click', () => managerYoutubeStatus().catch(error => managerMessage(error.message, true)));
    managerElement('managerYoutubeConnect').addEventListener('click', async () => {
        const file = managerElement('managerYoutubeSecret').files[0];
        if (!file) { managerMessage('Choose your Google OAuth client JSON file first.', true); return; }
        const button = managerElement('managerYoutubeConnect');
        button.disabled = true;
        try {
            const form = new FormData();
            form.append('client_secret', file);
            form.append('return_to', '/api-manager');
            const result = await managerYoutubeRequest('/api/youtube/connect', form);
            location.assign(result.authorization_url);
        } catch (error) { managerMessage(error.message, true); }
        finally { button.disabled = false; }
    });
    managerElement('managerYoutubeDisconnect').addEventListener('click', async () => {
        const button = managerElement('managerYoutubeDisconnect');
        button.disabled = true;
        try {
            await managerYoutubeRequest('/api/youtube/disconnect');
            // Keep Video Studio from trying automatic uploads after disconnecting.
            try {
                const settings = JSON.parse(localStorage.getItem('automap-youtube-settings') || '{}');
                settings.youtubeAuto = false;
                localStorage.setItem('automap-youtube-settings', JSON.stringify(settings));
            } catch {}
            await managerYoutubeStatus();
            managerMessage('YouTube disconnected.');
        } catch (error) {
            managerMessage(error.message, true);
            await managerYoutubeStatus().catch(() => {});
        }
    });
    const outcome = new URLSearchParams(location.search).get('youtube');
    if (outcome) {
        managerMessage(outcome === 'connected' ? 'YouTube connected.' : outcome === 'denied' ? 'Google authorization cancelled.' : 'Google authorization failed. Check your OAuth client and redirect URI.', outcome !== 'connected');
        history.replaceState(null, '', location.pathname);
    }
    managerYoutubeStatus().catch(error => {
        managerElement('managerYoutubeStatus').textContent = 'Status unavailable';
        managerMessage(error.message, true);
    });
});
