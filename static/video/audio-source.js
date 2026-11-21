/**
 * Video Studio: Audio download/upload, saved track identity, cookies, and source preview.
 * Registers DOMContentLoaded work; audio-cut.js and console.js are loaded before it
 * executes.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
// --- Music source ---
let musicDataUrl = null;   // populated with data:audio/mpeg;base64,... from the API
let musicFileName = 'music_automap';  // filename stem taken from the API "title" field
let musicAudioFile = null;

async function selectMusicResult(result) {
    const raw = result.file || result.audio || result.data;
    if (!raw) throw new Error('Server did not return audio.');
    const base64 = raw.startsWith('data:') ? raw.split(',')[1] : raw;
    const trackTitle = (result.title || '').trim();
    musicDataUrl = `data:audio/mpeg;base64,${base64}`;
    musicFileName = sanitizeFileName(trackTitle) || 'music_automap';
    const blob = await (await fetch(musicDataUrl)).blob();
    musicAudioFile = new File([blob], `${trackTitle || musicFileName}.mp3`, {type:'audio/mpeg'});
    saveMusicTitle(trackTitle);
    updateCutTitleHint(trackTitle);
    audioSelectionVersion++;
    cutAudioFile = null;
    cutDataUrl = null;
    document.getElementById('cutResult').classList.add('hidden');
    const oldCutPreview = document.getElementById('cutPreview');
    oldCutPreview.pause();
    oldCutPreview.removeAttribute('src');
    oldCutPreview.load();
    await rememberVideoAsset('video-cut-audio', null);
    await rememberVideoAsset('video-source-audio', musicAudioFile);
    document.getElementById('musicResultText').textContent = `${trackTitle} — ${describeDataUrl(musicDataUrl)}`;
    const preview = document.getElementById('musicPreview');
    preview.src = musicDataUrl;
    preview.classList.remove('hidden');
    document.getElementById('musicResult').classList.remove('hidden');
    updateRenderAudioStatus();
}

async function handleMusicUpload(file) {
    if (!file) return;
    const input = document.getElementById('musicFileInput');
    const status = document.getElementById('musicUploadStatus');
    if (document.getElementById('cutBtn').disabled) {
        showToast('Wait for the current audio cut to finish.', 'warn');
        input.value = '';
        return;
    }
    if (!file.size || file.size > MAX_MATERIAL_BYTES) {
        status.textContent = 'Choose a non-empty audio file up to 15 MB.';
        input.value = '';
        showToast(status.textContent, 'warn');
        return;
    }
    if (!file.type.startsWith('audio/') && !/\.(mp3|wav|m4a|aac|ogg|flac|webm)$/i.test(file.name)) {
        status.textContent = 'Choose a supported audio file.';
        input.value = '';
        showToast(status.textContent, 'warn');
        return;
    }
    input.disabled = true;
    document.getElementById('musicBtn').disabled = true;
    status.textContent = 'Uploading audio...';
    setEqualizerActive(true, 'Preparing Uploaded Audio...');
    audioSelectionVersion++;
    try {
        const response = await fetch('/api/music_upload', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({filename:file.name, audio_file:await fileToDataUrl(file)})
        });
        const result = await response.json();
        if (!response.ok || result.status === 'error') throw new Error(result.error || 'Audio upload failed.');
        await selectMusicResult(result);
        status.textContent = `Ready: ${file.name}`;
        showToast('Music uploaded. Preview, cut or render your video.', 'success');
    } catch (error) {
        status.textContent = error.message;
        showToast(error.message, 'error');
    } finally {
        input.disabled = false;
        input.value = '';
        document.getElementById('musicBtn').disabled = false;
        setEqualizerActive(false, 'Audio source');
    }
}

// localStorage key holding the last downloaded track's title (cross-tab / reload persistence).
const MUSIC_TITLE_KEY = 'automap_music_title';
let currentMusicTitle = '';

function saveMusicTitle(title) {
    currentMusicTitle = title || '';
    try {
        if (title) localStorage.setItem(MUSIC_TITLE_KEY, title);
        else localStorage.removeItem(MUSIC_TITLE_KEY);
    } catch (e) {
        // localStorage can be unavailable (private mode / quota) - fail silently.
    }
}

function loadMusicTitle() {
    if (currentMusicTitle) return currentMusicTitle;
    try {
        return localStorage.getItem(MUSIC_TITLE_KEY) || '';
    } catch (e) {
        return '';
    }
}

// Reflect the currently-saved track title under the Music Cut input.
function updateCutTitleHint(title) {
    const hint = document.getElementById('cutTitleHint');
    if (!hint) return;
    const t = (title || '').trim();
    if (t) {
        hint.textContent = `Using track title: ${t}`;
        hint.classList.remove('hidden');
    } else {
        hint.textContent = '';
        hint.classList.add('hidden');
    }
}

// Toggle equalizer animation when audio previews play/pause or during API work
function setEqualizerActive(active, labelText = null) {
    const eqBox = document.getElementById('musicEqBox');
    const eqStatus = document.getElementById('musicEqStatus');
    if (eqBox) {
        if (active) eqBox.classList.add('eq-active');
        else eqBox.classList.remove('eq-active');
    }
    if (labelText && eqStatus) {
        eqStatus.textContent = labelText;
    }
}

window.addEventListener('DOMContentLoaded', () => {
    // Restore focus to the URL field for quick use.
    const urlInput = document.getElementById('musicUrlInput');
    if (urlInput) urlInput.focus();

    // If a previous session stored a title, surface it so the user knows cuts will use it.
    const saved = loadMusicTitle();
    const cutTitleHint = document.getElementById('cutTitleHint');
    if (saved && cutTitleHint) {
        cutTitleHint.textContent = `Using saved title: ${saved}`;
        cutTitleHint.classList.remove('hidden');
    }

    // Hook audio elements to animate the studio equalizer when playing
    ['musicPreview', 'cutPreview'].forEach((id) => {
        const audioEl = document.getElementById(id);
        if (!audioEl) return;
        audioEl.addEventListener('play', () => setEqualizerActive(true, 'Playing Audio Preview...'));
        audioEl.addEventListener('pause', () => setEqualizerActive(false, 'Audio source'));
        audioEl.addEventListener('ended', () => setEqualizerActive(false, 'Audio source'));
    });

    // Initialize timeline slice preview and duration badges
    updateCutRangePreview();
});

// Make the API-provided title safe to use as a download filename.
function sanitizeFileName(name) {
    if (!name || typeof name !== 'string') return '';
    return name
        .replace(/[/\\?%*:|"<>]/g, '_')  // characters illegal in filenames
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 120);
}

async function pasteMusicUrl() {
    const urlInput = document.getElementById('musicUrlInput');
    try {
        const text = await navigator.clipboard.readText();
        if (text && text.trim()) {
            if (urlInput) {
                urlInput.value = text.trim();
                urlInput.focus();
            }
            showToast("Link pasted from clipboard", "info");
        } else {
            if (urlInput) urlInput.focus();
            showToast("Clipboard is empty — paste link manually", "warn");
        }
    } catch (err) {
        if (urlInput) urlInput.focus();
        showToast("Clipboard access blocked — press Ctrl+V to paste", "warn");
        logToConsole(`[Warning] Clipboard read failed (${err.message}). Paste manually.`, 'warn');
    }
}

function normalizeMusicCookies(contents) {
    const lines = contents.replace(/^\uFEFF/, '').split(/\r\n|\r|\n/);
    const rows = lines.filter(line => line.trim() && (!line.startsWith('#') || line.startsWith('#HttpOnly_')));
    if (!rows.length || rows.some(line => {
        const fields = line.split('\t');
        return fields.length !== 7 || !fields[0] || !/^(TRUE|FALSE)$/.test(fields[1])
            || !/^(TRUE|FALSE)$/.test(fields[3]) || !/^\d*$/.test(fields[4]);
    })) {
        throw new Error('Please export cookies in Netscape cookies.txt format (not JSON or a Cookie request header).');
    }
    // yt-dlp requires this header. Preserve tabs, including empty cookie values.
    return '# Netscape HTTP Cookie File\n' + rows.join('\n') + '\n';
}

async function handleMusicCookiesUpload(input) {
    const file = input.files[0];
    if (!file) return;
    const button = document.getElementById('musicBtn');
    const status = document.getElementById('musicCookiesFileStatus');
    const wasDisabled = button.disabled;
    button.disabled = true;
    input.disabled = true;
    status.textContent = 'Reading cookies file...';
    try {
        if (file.size > 1024 * 1024) throw new Error('Cookies file must be smaller than 1 MB.');
        const contents = normalizeMusicCookies(await file.text());
        const textarea = document.getElementById('musicCookiesInput');
        textarea.value = contents;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        saveVideoSettings();
        status.textContent = `Loaded ${file.name}. File format checked; YouTube will verify the session during download.`;
    } catch (error) {
        status.textContent = error.message || 'Could not read the cookies file.';
        showToast(status.textContent, 'warn');
    } finally {
        input.value = '';
        input.disabled = false;
        button.disabled = wasDisabled;
    }
}

async function handleMusicDownload(e) {
    e.preventDefault();
    if (document.getElementById('cutBtn').disabled) {
        showToast('Wait for the current audio cut to finish.', 'warn');
        return;
    }
    const url = document.getElementById('musicUrlInput').value.trim();
    const btn = document.getElementById('musicBtn');
    const btnText = document.getElementById('musicBtnText');
    const btnIcon = document.getElementById('musicBtnIcon');
    const metaBadge = document.getElementById('studioMetaBadge');

    if (!url) {
        showToast("Please paste a YouTube Music link first.", "warn");
        return;
    }

    btn.disabled = true;
    document.getElementById('musicFileInput').disabled = true;
    document.getElementById('musicCookiesFileInput').disabled = true;
    audioSelectionVersion++;
    btn.classList.add('opacity-70');
    btnText.textContent = "Downloading...";
    btnIcon.className = "fa-solid fa-circle-notch animate-spin";
    metaBadge.textContent = "Downloading...";
    setEqualizerActive(true, 'Fetching YouTube Audio...');

    logToConsole(`[Music] POST /api/music_download ${url}`, 'cmd');

    try {
        const rawCookies = document.getElementById('musicCookiesInput').value;
        const cookies = rawCookies.trim() ? normalizeMusicCookies(rawCookies) : '';
        // Single request: backend downloads + converts and returns the audio directly.
        const response = await fetch('/api/music_download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cookies ? { url, cookies } : { url })
        });

        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error(`Server returned ${response.status} ${response.statusText}`);
        }

        if (!response.ok || result.status === 'error') {
            throw new Error(result.error || 'Music download failed');
        }

        await selectMusicResult(result);
        const trackTitle = (result.title || '').trim();
        logToConsole(`[Success] Music file received "${musicFileName}.mp3" (${describeDataUrl(musicDataUrl)})`, 'success');

        metaBadge.textContent = `Done (${new Date().toLocaleTimeString()})`;
        setEqualizerActive(false, trackTitle || 'Track Ready');
        showToast("Music downloaded! Click Save to store the MP3.", "success");
    } catch (error) {
        logToConsole(`[API Error] ${error.message}`, 'error');
        const cookieStatus = document.getElementById('musicCookiesFileStatus');
        cookieStatus.textContent = error.message;
        cookieStatus.closest('details').open = true;
        showToast("Music download failed. Check console logs.", "error");
        metaBadge.textContent = "Error";
        setEqualizerActive(false, 'Download Failed');
    } finally {
        btn.disabled = false;
        document.getElementById('musicFileInput').disabled = false;
        document.getElementById('musicCookiesFileInput').disabled = false;
        btn.classList.remove('opacity-70');
        btnText.textContent = "Download MP3";
        btnIcon.className = "fa-solid fa-download";
    }
}

function downloadMusicFile() {
    if (!musicDataUrl) {
        showToast("Download a track first.", "warn");
        return;
    }
    const a = document.createElement('a');
    a.href = musicDataUrl;
    a.download = `${musicFileName || 'music_automap'}.mp3`;
    a.click();
    showToast("Saving MP3 file...", "success");
}
