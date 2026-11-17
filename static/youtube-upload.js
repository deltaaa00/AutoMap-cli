/* Google credentials and OAuth tokens are never stored in browser storage. */
let youtubeCsrf = '';
let youtubeConnected = false;
let youtubePolling = 0;
const youtubeFields = ['youtubeTitle', 'youtubeDescription', 'youtubeTags', 'youtubePrivacy', 'youtubeKids'];
const ytElement = id => document.getElementById(id);

function youtubeSettings() {
    const settings = {
        title: ytElement('youtubeTitle').value.trim() || ytElement('videoTitleInput').value.trim(),
        description: ytElement('youtubeDescription').value,
        tags: ytElement('youtubeTags').value,
        privacy: ytElement('youtubePrivacy').value,
        made_for_kids: ytElement('youtubeKids').checked,
    };
    if (!settings.title || settings.title.length > 100 || /[<>]/.test(settings.title)) throw new Error('YouTube title must contain 1–100 characters and no < or >.');
    if (new TextEncoder().encode(settings.description).length > 5000 || /[<>]/.test(settings.description)) throw new Error('YouTube description must be at most 5000 UTF-8 bytes and contain no < or >.');
    return settings;
}

async function youtubeRequest(url, options = {}) {
    const response = await fetch(url, {...options, headers: {...options.headers, 'X-YouTube-CSRF': youtubeCsrf}, cache: 'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'YouTube request failed.');
    return result;
}

async function loadYouTubeConnection() {
    try {
        const result = await youtubeRequest('/api/youtube/status');
        youtubeCsrf = result.csrf_token;
        youtubeConnected = result.connected;
        ytElement('youtubeConnection').textContent = result.connected ? 'YouTube account connected.' : 'YouTube account not connected.';
        ytElement('youtubeRedirect').textContent = result.redirect_uri;
    } catch (error) {
        ytElement('youtubeConnection').textContent = error.message;
    }
}

async function connectYouTube() {
    const file = ytElement('youtubeSecret').files[0];
    if (!file) { showToast('Choose the client secret JSON file first.', 'warn'); return; }
    ytElement('youtubeConnect').disabled = true;
    try {
        const form = new FormData();
        form.append('client_secret', file);
        const result = await youtubeRequest('/api/youtube/connect', {method: 'POST', body: form});
        window.location.assign(result.authorization_url);
    } catch (error) {
        ytElement('youtubeConnection').textContent = error.message;
        showToast(error.message, 'error');
    } finally { ytElement('youtubeConnect').disabled = false; }
}

async function disconnectYouTube() {
    try {
        await youtubeRequest('/api/youtube/disconnect', {method: 'POST'});
        ytElement('youtubeAuto').checked = false;
        await loadYouTubeConnection();
    } catch (error) { showToast(error.message, 'error'); }
}

function watchYouTubeUpload(jobId) {
    const generation = ++youtubePolling;
    ytElement('youtubeUpload').disabled = true;
    ytElement('youtubeVideoLink').classList.add('hidden');
    try { sessionStorage.setItem('automap-youtube-job', jobId); } catch {}
    const poll = async () => {
        if (generation !== youtubePolling) return;
        try {
            const job = await youtubeRequest(`/api/youtube/jobs/${encodeURIComponent(jobId)}`);
            if (generation !== youtubePolling) return;
            ytElement('youtubeUploadStatus').textContent = job.error || `${job.phase} (${job.progress}%)`;
            if (job.status === 'complete') {
                const link = ytElement('youtubeVideoLink');
                link.href = job.url;
                link.classList.remove('hidden');
                showToast('Video uploaded to YouTube.', 'success');
            }
            if (job.status !== 'uploading') {
                try { sessionStorage.removeItem('automap-youtube-job'); } catch {}
                return;
            }
        } catch (error) {
            ytElement('youtubeUploadStatus').textContent = `${error.message} Check YouTube Studio before starting another upload.`;
            return;
        }
        setTimeout(poll, 1500);
    };
    poll();
}

async function uploadToYouTube() {
    if (!renderResultData?.upload_token) { showToast('Render a video first.', 'warn'); return; }
    ytElement('youtubeUpload').disabled = true;
    try {
        const result = await youtubeRequest('/api/youtube/upload', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({upload_token: renderResultData.upload_token, youtube: youtubeSettings()})});
        watchYouTubeUpload(result.job_id);
    } catch (error) {
        ytElement('youtubeUpload').disabled = false;
        ytElement('youtubeUploadStatus').textContent = error.message;
        showToast(error.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    try {
        const saved = JSON.parse(localStorage.getItem('automap-youtube-settings') || '{}');
        for (const id of youtubeFields) {
            if (id === 'youtubeKids') ytElement(id).checked = saved[id] === true;
            else if (typeof saved[id] === 'string') ytElement(id).value = saved[id];
        }
        const job = sessionStorage.getItem('automap-youtube-job');
        if (job) watchYouTubeUpload(job);
    } catch {}
    for (const id of youtubeFields) ytElement(id).addEventListener('input', () => {
        const settings = Object.fromEntries(youtubeFields.map(key => [key, key === 'youtubeKids' ? ytElement(key).checked : ytElement(key).value]));
        try { localStorage.setItem('automap-youtube-settings', JSON.stringify(settings)); } catch {}
    });
    // Automatic upload is an explicit choice for each visit.
    const outcome = new URLSearchParams(location.search).get('youtube');
    if (outcome) {
        ytElement('youtubePanel').open = true;
        if (outcome !== 'connected') showToast(outcome === 'denied' ? 'Google authorization cancelled.' : 'Google authorization failed. Check the redirect URI and try connecting again.', 'error');
        history.replaceState(null, '', location.pathname);
    }
    loadYouTubeConnection();
});
