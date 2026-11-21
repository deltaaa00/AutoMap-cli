/**
 * Video Studio: Render progress polling, notification audio, multipart submission, and
 * completed video downloads. Uses all preceding material/audio/preview state and publishes
 * events consumed by youtube-upload.js.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
function setRenderProgress(progress, phase) {
    const value = Math.max(0, Math.min(100, Number(progress) || 0));
    document.getElementById('renderProgressBar').value = value;
    document.getElementById('renderProgressPercent').textContent = `${Math.round(value)}%`;
    document.getElementById('renderProgressPhase').textContent = phase;
}

function startRenderProgress(renderId) {
    let stopped = false;
    let timer;
    let controller;
    const poll = async () => {
        controller = new AbortController();
        const deadline = setTimeout(() => controller.abort(), 6000);
        try {
            const response = await fetch(`/api/render_progress/${renderId}`, {signal: controller.signal, cache: 'no-store'});
            if (stopped) return;
            if (response.ok) {
                const status = await response.json();
                if (stopped) return;
                setRenderProgress(status.progress, status.phase || 'Rendering');
                document.getElementById('renderProgressHint').textContent = status.error || 'Progress is reported by the render worker.';
            } else if (response.status !== 404) {
                document.getElementById('renderProgressHint').textContent = 'Progress connection interrupted. Rendering may still be running.';
            }
        } catch {
            if (!stopped) document.getElementById('renderProgressHint').textContent = 'Reconnecting to render progress…';
        } finally {
            clearTimeout(deadline);
            if (!stopped) timer = setTimeout(poll, 750);
        }
    };
    timer = setTimeout(poll, 250);
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); };
}

let renderNotificationAudio = null;

function prepareRenderNotification() {
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return;
        if (!renderNotificationAudio || renderNotificationAudio.state === 'closed') {
            renderNotificationAudio = new AudioContext();
        }
        // Unlock audio during the Render button gesture, before the request finishes.
        renderNotificationAudio.resume().catch(() => {});
    } catch {}
}

function playRenderNotification() {
    const context = renderNotificationAudio;
    if (!context) return;
    const play = () => {
        try {
            const start = context.currentTime;
            [660, 880, 1100].forEach((frequency, index) => {
                const oscillator = context.createOscillator();
                const gain = context.createGain();
                const at = start + index * 0.16;
                oscillator.frequency.value = frequency;
                gain.gain.setValueAtTime(0, at);
                gain.gain.linearRampToValueAtTime(0.12, at + 0.015);
                gain.gain.exponentialRampToValueAtTime(0.001, at + 0.28);
                oscillator.connect(gain);
                gain.connect(context.destination);
                oscillator.start(at);
                oscillator.stop(at + 0.3);
                oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
            });
        } catch {}
    };
    if (context.state === 'running') play();
    else context.resume().then(play).catch(() => {});
}

async function handleRenderVideo(e) {
    e.preventDefault();
    const btn = document.getElementById('renderBtn');
    const btnText = document.getElementById('renderBtnText');
    const btnIcon = document.getElementById('renderBtnIcon');
    const overlay = document.getElementById('renderOverlay');
    const metaBadge = document.getElementById('studioMetaBadge');

    const mapFile = renderFiles.mapFileInput;
    const bgFile = renderFiles.bgFileInput;
    const title = document.getElementById('videoTitleInput').value.trim();
    const codec = document.getElementById('codecSelect').value;
    const durationSecs = getCutDuration();
    const includeAudio = Boolean(cutAudioFile || musicAudioFile || loadMusicTitle());

    if (!mapFile) {
        showToast("Please upload a map file first.", 'warn');
        return;
    }
    if (!bgFile) {
        showToast("Please upload a background video first.", 'warn');
        return;
    }
    if (!title) {
        showToast("Please enter a video title.", 'warn');
        document.getElementById('videoTitleInput').focus();
        return;
    }
    if (!availableCodecs.includes(codec)) {
        showToast("Select an available render worker codec.", 'error');
        return;
    }
    // Final size double-check before uploading.
    if (mapFile.size > MAX_MATERIAL_BYTES || bgFile.size > MAX_MATERIAL_BYTES) {
        showToast("A file exceeds the 15 MB limit.", 'error');
        return;
    }

    let youtube = null;
    if (document.getElementById('youtubeAuto').checked) {
        try {
            if (!youtubeConnected) throw new Error('Connect your YouTube account in API Manager first.');
            youtube = youtubeSettings();
        } catch (error) { showToast(error.message, 'error'); return; }
    }
    prepareRenderNotification();
    document.getElementById('renderResult').classList.add('hidden');
    renderResultData = null;
    document.getElementById('youtubeUpload').disabled = true;
    btn.disabled = true;
    btn.classList.add('opacity-70');
    btnText.textContent = "Rendering...";
    btnIcon.className = "fa-solid fa-circle-notch animate-spin";
    overlay.classList.remove('hidden');
    metaBadge.textContent = "Rendering...";
    setRenderProgress(0, 'Preparing materials');
    document.getElementById('renderProgressHint').textContent = 'Preparing and uploading selected materials…';
    const renderId = typeof crypto.randomUUID === 'function' ? crypto.randomUUID()
        : Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
            .replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/, '$1-$2-$3-$4-$5');
    let stopProgress = () => {};

    logToConsole(`[Render] POST /api/render_video title="${title}" codec=${codec} duration=${durationSecs}s map=${mapFile.name} bg=${bgFile.name}`, 'cmd');

    try {
        // Encode materials and any custom overlay images as base64 data URLs.
        const overlaySettings = {...titleSettings(), ...watermarkSettings()};
        // Send files as binary multipart parts (no base64 in the browser,
        // ~25% smaller upload); settings travel as one JSON "payload" field.
        const payload = {
            video_title: title,
            audio_title: includeAudio ? loadMusicTitle() : '',
            include_audio: includeAudio,
            codec: codec,
            duration: parseInt(durationSecs, 10),
            ...overlaySettings,
            render_id: renderId,
            ...(youtube ? {youtube} : {}),
        };
        const audioSource = includeAudio ? (cutAudioFile || musicAudioFile) : null;
        const formData = new FormData();
        const files = {
            map_file: mapFile,
            background_video: bgFile,
            watermark_image: watermarkFile,
            audio_file: audioSource,
            subscribe_image: overlaySettings.subscribe_badge ? subscribeImageFile : null,
            watermark_stars_image: overlaySettings.watermark_stars ? watermarkStarsFile : null,
            top_left_sticker_image: leftStickerFile,
            top_right_sticker_image: rightStickerFile,
        };
        for (const [field, file] of Object.entries(files)) {
            if (file) formData.append(field, file, file.name || field);
            else payload[field] = '';
        }
        formData.append('payload', JSON.stringify(payload));

        stopProgress = startRenderProgress(renderId);
        const response = await (youtube ? youtubeFetch : fetch)('/api/render_video', {
            method: 'POST',
            headers: youtube ? {'X-YouTube-CSRF': youtubeCsrf} : {},
            body: formData,
        });

        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error(`Server returned ${response.status} ${response.statusText}`);
        }

        // Expected response: { status: "success"|"error", file_path, error? }
        if (!response.ok || result.status === 'error') {
            throw new Error(result.error || 'Video render failed');
        }

        stopProgress();
        setRenderProgress(100, 'Complete');
        renderResultData = result;
        document.getElementById('youtubeUpload').disabled = !result.upload_token;
        if (result.youtube_job_id) watchYouTubeUpload(result.youtube_job_id);
        if (result.youtube_error) {
            document.getElementById('youtubeUploadStatus').textContent = result.youtube_error;
            showToast(result.youtube_error, 'warn');
        }
        const filePath = result.file_path || result.path || result.file || '(unknown path)';

        const resultBox = document.getElementById('renderResult');
        const resultText = document.getElementById('renderResultText');
        resultText.textContent = filePath;
        resultText.title = filePath;
        document.body.append(resultBox);
        resultBox.classList.remove('hidden', 'render-result-docked');
        playRenderNotification();

        // Load the rendered video into the Cinema Stage Player for immediate preview
        const player = document.getElementById('renderedVideoPlayer');
        const stageModeLabel = document.getElementById('stageModeLabel');
        if (player && filePath && filePath !== '(unknown path)') {
            const streamUrl = result.download_url || `/api/download_video?path=${encodeURIComponent(filePath)}`;
            player.src = streamUrl;
            player.muted = false;
            player.volume = 1;
            player.load();
            player.classList.remove('hidden');
            updatePreviewPlayback();
            if (stageModeLabel) stageModeLabel.textContent = 'Master MP4 Output';
        }

        logToConsole(`[Success] Video rendered: ${filePath}`, 'success');
        if (result.warning) {
            logToConsole(`[Warning] ${result.warning}`, 'warn');
            showToast(result.warning, 'warn');
        } else {
            showToast(result.has_audio ? 'Video rendered with music! Click Download video.' : 'Video rendered without music.', 'success');
        }
        metaBadge.textContent = `Done (${new Date().toLocaleTimeString()})`;
    } catch (error) {
        logToConsole(`[API Error] ${error.message}`, 'error');
        showToast(error.message || "Video render failed. Check console logs.", 'error');
        metaBadge.textContent = "Error";
    } finally {
        stopProgress();
        overlay.classList.add('hidden');
        btn.disabled = false;
        btn.classList.remove('opacity-70');
        btnText.textContent = "Render video";
        btnIcon.className = "fa-solid fa-film";
    }
}

function dismissRenderResult() {
    const result = document.getElementById('renderResult');
    result.classList.add('render-result-docked');
    document.getElementById('renderResultDock').append(result);
    document.getElementById('downloadVideoBtn').focus({preventScroll: true});
}

function downloadRenderedVideo() {
    if (!renderResultData) {
        showToast("Render a video first.", 'warn');
        return;
    }
    // Prefer an explicit download URL from the server, otherwise fall back to a
    // download endpoint that streams the rendered file by its server path.
    const filePath = renderResultData.file_path || renderResultData.path || renderResultData.file;
    const url = renderResultData.download_url
        || `/api/download_video?path=${encodeURIComponent(filePath || '')}`;

    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
    showToast("Downloading video...", 'success');
}
