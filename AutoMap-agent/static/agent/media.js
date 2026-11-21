/**
 * Upload, delete, split, and queue URL imports for the local agent media pool.
 * Functions are called from templates/agent/media_pool.html and rendered file lists.
 * Requests target agent.web_media; successful actions refresh the shared overview.
 */
async function uploadMedia(kind) {
    const input = document.getElementById(kind === 'background' ? 'bgUploadInput' : 'musicUploadInput');
    if (!input.files || !input.files.length) { showAgentToast('Select files to upload.', true); return; }
    const form = new FormData();
    form.append('kind', kind);
    for (const f of input.files) form.append('files', f);
    const res = await fetch('/api/agent/media/upload', {method: 'POST', body: form});
    const data = await res.json();
    if (res.ok) input.value = '';
    showAgentToast(data.message || data.error, !res.ok);
    loadOverview();
}

async function deleteMedia(kind, filename) {
    const res = await fetch('/api/agent/media/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({kind, filename}),
    });
    const data = await res.json();
    showAgentToast(data.message || data.error, !res.ok);
    loadOverview();
}

async function splitMedia(filename) {
    showAgentToast(`Splitting ${filename} into 15s clips...`);
    const res = await fetch('/api/agent/media/split', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({filename}),
    });
    const data = await res.json();
    showAgentToast(data.message || data.error, !res.ok);
    loadOverview();
}

async function importMusicByUrl() {
    const input = document.getElementById('musicUrlInput');
    const url = input.value.trim();
    if (!url) { showAgentToast('Paste a YouTube URL first.', true); return; }
    const res = await fetch('/api/agent/media/import_url', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url}),
    });
    const data = await res.json();
    if (res.ok) input.value = '';
    showAgentToast(data.message || data.error, !res.ok);
    loadOverview();
}
