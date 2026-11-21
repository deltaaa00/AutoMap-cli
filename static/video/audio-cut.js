/**
 * Video Studio: Trim timeline, selected audio state, and cut requests. Uses audio-
 * source.js state plus later console/material helpers only when handlers run.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
// --- Music Cut ---
let cutDuration = 8;  // configurable segment length: 1..30s (default 8s)
let cutDataUrl = null;
let cutFileName = 'music_cut';
let cutAudioFile = null;
let audioSelectionVersion = 0;

function updateRenderAudioStatus() {
    const status = document.getElementById('renderAudioStatus');
    if (cutAudioFile) {
        status.textContent = `Audio: cut fragment · ${cutAudioFile.name}`;
    } else if (loadMusicTitle()) {
        status.textContent = `Audio: selected track · ${loadMusicTitle()}`;
    } else {
        status.textContent = 'No music uploaded · video will be silent.';
    }
}

function getCutDuration() {
    const numInput = document.getElementById('cutDurationInput');
    const raw = numInput ? parseInt(numInput.value, 10) : cutDuration;
    if (Number.isNaN(raw)) return cutDuration || 8;
    return Math.max(1, Math.min(30, raw));
}

function setCutDuration(val, clampInput = true) {
    const parsed = parseInt(val, 10);
    const clamped = Number.isNaN(parsed) ? (cutDuration || 8) : Math.max(1, Math.min(30, parsed));
    cutDuration = clamped;

    const rangeEl = document.getElementById('cutDurationRange');
    const numEl = document.getElementById('cutDurationInput');
    if (rangeEl) rangeEl.value = String(clamped);
    if (numEl && (clampInput || document.activeElement !== numEl)) {
        numEl.value = String(clamped);
    }

    // Sync active styling on quick duration preset pills
    document.querySelectorAll('.duration-preset-btn').forEach((btn) => {
        const btnDur = parseInt(btn.dataset.duration, 10);
        if (btnDur === clamped) {
            btn.className = 'duration-preset-btn px-3 py-1 rounded-md bg-slate-800 text-accent-300 border border-accent-400 text-xs font-mono font-semibold transition';
        } else {
            btn.className = 'duration-preset-btn px-3 py-1 rounded-md bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-100 border border-slate-800 hover:border-slate-600 text-xs font-mono transition';
        }
    });

    updateCutRangePreview();
    saveVideoSettings();
}

// Parse "MM:SS" (or a bare number of seconds) into total seconds. Returns null if invalid.
function parseTimeToSeconds(value) {
    if (!value) return null;
    const trimmed = value.trim();
    const m = /^(\d{1,3}):([0-5]?\d)$/.exec(trimmed);
    if (m) {
        return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
    }
    // Bare seconds, e.g. "90"
    if (/^\d{1,5}$/.test(trimmed)) {
        return parseInt(trimmed, 10);
    }
    return null;
}

// Format total seconds as "MM:SS".
function formatSecondsToTime(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds));
    const mm = String(Math.floor(s / 60)).padStart(2, '0');
    const ss = String(s % 60).padStart(2, '0');
    return `${mm}:${ss}`;
}

// Quick preset helper for Music Cut start time
function setCutPreset(timecode) {
    const input = document.getElementById('cutStartInput');
    if (!input) return;
    input.value = timecode;
    onCutStartInput(input);
    saveVideoSettings();
}

// Lightweight input mask: keep digits and a single colon, auto-insert colon.
function onCutStartInput(input) {
    let v = input.value.replace(/[^\d:]/g, '');
    const firstColon = v.indexOf(':');
    if (firstColon !== -1) {
        v = v.slice(0, firstColon + 1) + v.slice(firstColon + 1).replace(/:/g, '');
    }
    // "130" -> "01:30" convenience when the user types 3+ digits with no colon.
    if (firstColon === -1 && v.length >= 3) {
        v = v.slice(0, 2) + ':' + v.slice(2, 4);
    }
    input.value = v.slice(0, 5);

    // Sync active styling on quick timecode preset buttons
    const currentVal = input.value.trim();
    document.querySelectorAll('.cut-preset-btn').forEach((btn) => {
        if (btn.dataset.timecode === currentVal) {
            btn.className = 'cut-preset-btn px-2 py-1 rounded-md bg-slate-800 text-accent-300 border border-accent-400 text-xs font-mono font-semibold transition';
        } else {
            btn.className = 'cut-preset-btn px-2 py-1 rounded-md bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-100 border border-slate-800 hover:border-slate-600 text-xs font-mono transition';
        }
    });

    updateCutRangePreview();
}

function updateCutRangePreview() {
    const dur = getCutDuration();
    const startEl = document.getElementById('cutRangeStart');
    const endEl = document.getElementById('cutRangeEnd');
    const durBadge = document.getElementById('cutDurationBadge');
    const headerBadge = document.getElementById('cutWindowHeaderBadge');
    const sliceStepBadge = document.getElementById('headerSliceStepBadge');
    const stageDurBadge = document.getElementById('stageDurationBadge');
    const visualBar = document.getElementById('cutVisualBar');
    const btn = document.getElementById('cutBtn');
    const btnText = document.getElementById('cutBtnText');

    if (durBadge) durBadge.textContent = `${dur}.0s`;
    if (headerBadge) headerBadge.innerHTML = `MM:SS &bull; +${dur}s Window`;
    if (sliceStepBadge) sliceStepBadge.textContent = `02 Slice ${dur}s`;
    if (stageDurBadge) stageDurBadge.textContent = `00:${String(dur).padStart(2, '0')}.00`;
    if (btn && !btn.disabled && btnText) {
        btnText.textContent = `Cut ${dur} Second${dur === 1 ? '' : 's'}`;
    }

    const widthPct = Math.max(8, Math.min(85, Math.round((dur / 30) * 75)));
    if (visualBar) {
        visualBar.style.width = `${widthPct}%`;
    }

    const startInputEl = document.getElementById('cutStartInput');
    const secs = parseTimeToSeconds(startInputEl ? startInputEl.value : '');
    if (secs === null) {
        if (startEl) startEl.textContent = '--:--';
        if (endEl) endEl.textContent = `+${dur}s`;
        if (visualBar) visualBar.style.marginLeft = '10%';
        return;
    }
    if (startEl) startEl.textContent = formatSecondsToTime(secs);
    if (endEl) endEl.textContent = formatSecondsToTime(secs + dur);
    if (visualBar) {
        const maxOffset = Math.max(5, 95 - widthPct);
        const offsetPct = Math.min(maxOffset, Math.max(5, Math.floor((secs % 180) / 180 * maxOffset)));
        visualBar.style.marginLeft = `${offsetPct}%`;
    }
}

async function handleMusicCut(e) {
    e.preventDefault();
    if (document.getElementById('musicBtn').disabled) {
        showToast('Wait for the selected audio to finish loading.', 'warn');
        return;
    }
    const startInput = document.getElementById('cutStartInput');
    const startTime = startInput.value.trim();
    const durationSecs = getCutDuration();
    const btn = document.getElementById('cutBtn');
    const btnText = document.getElementById('cutBtnText');
    const btnIcon = document.getElementById('cutBtnIcon');
    const metaBadge = document.getElementById('studioMetaBadge');

    const startSecs = parseTimeToSeconds(startTime);
    if (startSecs === null) {
        showToast("Enter start time as MM:SS (e.g. 01:30).", "warn");
        startInput.focus();
        return;
    }

    cutDataUrl = null;
    cutFileName = 'music_cut';
    btn.disabled = true;
    btn.classList.add('opacity-70');
    btnText.textContent = "Cutting...";
    btnIcon.className = "fa-solid fa-circle-notch animate-spin";
    metaBadge.textContent = "Cutting...";
    setEqualizerActive(true, `Slicing ${durationSecs}s Audio Hook...`);

    const endLabel = formatSecondsToTime(startSecs + durationSecs);
    const trackTitle = loadMusicTitle();
    logToConsole(`[Cut] POST /api/music_cut start=${startTime} duration=${durationSecs}s (${formatSecondsToTime(startSecs)} -> ${endLabel})${trackTitle ? ` title="${trackTitle}"` : ''}`, 'cmd');

    try {
        // Send the start time, selected duration (1-30s), and (if previously downloaded) the track title.
        const payload = {
            start_time: startTime,
            duration: parseInt(durationSecs, 10)
        };
        if (trackTitle) payload.title = trackTitle;
        if (musicAudioFile && musicDataUrl) payload.audio_file = musicDataUrl;

        const response = await fetch('/api/music_cut', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error(`Server returned ${response.status} ${response.statusText}`);
        }

        if (!response.ok || result.status === 'error') {
            throw new Error(result.error || 'Music cut failed');
        }

        // Backend returns base64 audio (raw base64 or a data: URL).
        const raw = result.file || result.audio || result.data;
        if (!raw) throw new Error('Server did not return cut audio.');
        const base64 = raw.startsWith('data:') ? raw.split(',')[1] : raw;
        cutDataUrl = `data:audio/mpeg;base64,${base64}`;

        // Filename hint: prefer the server's title, else the saved track title.
        const titleHint = sanitizeFileName(result.title || trackTitle || '');
        const stamp = `${formatSecondsToTime(startSecs).replace(':', '-')}_${durationSecs}s`;
        cutFileName = titleHint ? `${titleHint}_${stamp}` : `music_cut_${stamp}`;
        const cutBlob = await (await fetch(cutDataUrl)).blob();
        cutAudioFile = new File([cutBlob], `${cutFileName}.mp3`, {type: 'audio/mpeg'});
        audioSelectionVersion++;
        await rememberVideoAsset('video-cut-audio', cutAudioFile);
        updateRenderAudioStatus();

        logToConsole(`[Success] Cut ${durationSecs}s segment ${formatSecondsToTime(startSecs)} -> ${endLabel} (${describeDataUrl(cutDataUrl)})`, 'success');

        const resultBox = document.getElementById('cutResult');
        const resultText = document.getElementById('cutResultText');
        const preview = document.getElementById('cutPreview');

        resultText.textContent = `${formatSecondsToTime(startSecs)} -> ${endLabel} (${durationSecs}s) — ${describeDataUrl(cutDataUrl)}`;
        preview.src = cutDataUrl;
        preview.classList.remove('hidden');
        resultBox.classList.remove('hidden');

        metaBadge.textContent = `Cut done (${new Date().toLocaleTimeString()})`;
        setEqualizerActive(false, `${durationSecs}s Slice (${formatSecondsToTime(startSecs)}-${endLabel})`);
        showToast(`Cut ${durationSecs}s audio segment! Click Save to store the MP3.`, "success");
    } catch (error) {
        logToConsole(`[API Error] ${error.message}`, 'error');
        showToast("Music cut failed. Check console logs.", "error");
        metaBadge.textContent = "Error";
        setEqualizerActive(false, 'Audio source');
    } finally {
        const finalDur = getCutDuration();
        btn.disabled = false;
        btn.classList.remove('opacity-70');
        btnText.textContent = `Cut ${finalDur} Second${finalDur === 1 ? '' : 's'}`;
        btnIcon.className = "fa-solid fa-scissors";
    }
}

function downloadCutFile() {
    if (!cutDataUrl) {
        showToast("Cut a segment first.", "warn");
        return;
    }
    const a = document.createElement('a');
    a.href = cutDataUrl;
    a.download = `${cutFileName || 'music_cut'}.mp3`;
    a.click();
    showToast("Saving cut MP3...", "success");
}
