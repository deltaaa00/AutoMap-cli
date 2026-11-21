/**
 * Video Studio controller for YouTube connection, metadata and upload progress.
 *
 * Loaded after the feature controllers listed in templates/video/scripts.html;
 * it relies on that
 * page's element IDs and render-completion hooks. Calls /api/youtube/* served by
 * webapp/youtube.py. Only upload preferences belong in browser storage; Google
 * OAuth credentials stay on the server. Status requests share session setup, and
 * CSRF refresh retries only the explicitly identified token-rejection response.
 * Keep rendered-video download usable when upload fails.
 */
/* Google credentials and OAuth tokens are never stored in browser storage. */
let youtubeCsrf = '';
let youtubeConnected = false;
let youtubePolling = 0;
const youtubeFields = ['youtubeTitle', 'youtubeDescription', 'youtubeTags', 'youtubePrivacy', 'youtubeKids', 'youtubeAuto', 'youtubeUseTopic'];
let youtubeCustomTitle = '';
const ytElement = id => document.getElementById(id);

function syncYouTubeTitle() {
    const input = ytElement('youtubeTitle');
    const useTopic = ytElement('youtubeUseTopic').checked;
    input.readOnly = useTopic;
    input.value = useTopic ? ytElement('videoTitleInput').value.replace(/\s+/g, ' ').trim().slice(0, 100) : youtubeCustomTitle;
}

function saveYouTubeSettings() {
    const settings = Object.fromEntries(youtubeFields.map(id => {
        const input = ytElement(id);
        return [id, id === 'youtubeTitle' ? youtubeCustomTitle : input.type === 'checkbox' ? input.checked : input.value];
    }));
    try { localStorage.setItem('automap-youtube-settings', JSON.stringify(settings)); } catch {}
}

function youtubeSettings() {
    syncYouTubeTitle();
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

let youtubeConnectionRequest = null;

async function loadYouTubeConnection() {
    // Share one request so simultaneous actions cannot race session setup.
    if (youtubeConnectionRequest) return youtubeConnectionRequest;
    youtubeConnectionRequest = (async () => {
        const response = await fetch('/api/youtube/status', {cache: 'no-store'});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not check YouTube connection.');
        youtubeCsrf = result.csrf_token;
        youtubeConnected = result.connected;
        const label = result.connected ? 'Signed in to YouTube' : 'Not signed in to YouTube';
        ytElement('youtubeConnection').textContent = label;
        const badge = ytElement('youtubeConnectionBadge');
        badge.textContent = label;
        badge.className = `ml-2 text-xs font-normal ${result.connected ? 'text-emerald-400' : 'text-amber-300'}`;
        return result;
    })();
    try { return await youtubeConnectionRequest; }
    catch (error) {
        ytElement('youtubeConnection').textContent = error.message;
        ytElement('youtubeConnectionBadge').textContent = 'YouTube sign-in status unavailable';
        ytElement('youtubeConnectionBadge').className = 'ml-2 text-xs font-normal text-slate-400';
        throw error;
    } finally { youtubeConnectionRequest = null; }
}

async function youtubeFetch(url, options = {}) {
    const mutating = (options.method || 'GET').toUpperCase() === 'POST';
    // Refresh before every mutation, including actions from older open tabs.
    if (mutating) await loadYouTubeConnection();
    const send = () => fetch(url, {...options, headers: {...options.headers, 'X-YouTube-CSRF': youtubeCsrf}, cache: 'no-store'});
    let response = await send();
    if (mutating && response.status === 403) {
        const rejection = await response.clone().json().catch(() => ({}));
        // This response is issued before any upload/render/OAuth side effect.
        if (rejection.code === 'youtube_csrf_invalid') {
            await loadYouTubeConnection();
            response = await send();
        }
    }
    return response;
}

async function youtubeRequest(url, options = {}) {
    const response = await youtubeFetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'YouTube request failed.');
    return result;
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
            const input = ytElement(id);
            if (input.type === 'checkbox') {
                if (typeof saved[id] === 'boolean') input.checked = saved[id];
            } else if (typeof saved[id] === 'string') input.value = saved[id];
        }
        const job = sessionStorage.getItem('automap-youtube-job');
        if (job) watchYouTubeUpload(job);
    } catch {}
    youtubeCustomTitle = ytElement('youtubeTitle').value;
    syncYouTubeTitle();
    document.addEventListener('automap:video-topic-changed', syncYouTubeTitle);
    for (const id of youtubeFields) {
        for (const event of ['input', 'change']) ytElement(id).addEventListener(event, () => {
            if (id === 'youtubeTitle') youtubeCustomTitle = ytElement(id).value;
            if (id === 'youtubeUseTopic') syncYouTubeTitle();
            saveYouTubeSettings();
        });
    }
    const outcome = new URLSearchParams(location.search).get('youtube');
    if (outcome) {
        ytElement('youtubePanel').open = true;
        if (outcome !== 'connected') showToast(outcome === 'denied' ? 'Google authorization cancelled.' : 'Google authorization failed. Check the redirect URI and try connecting again.', 'error');
        history.replaceState(null, '', location.pathname);
    }
    loadYouTubeConnection().catch(() => {});
});
