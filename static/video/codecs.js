/**
 * Video Studio: Encoder availability and selection, plus shared material byte/codec
 * limits. Load before materials.js, render.js, and final initialization.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
function selectCodecCard(codec) {
    const select = document.getElementById('codecSelect');
    if (select && availableCodecs.includes(codec)) {
        select.value = codec;
        syncCodecCards(codec);
    }
}

function syncCodecCards(codec) {
    const select = document.getElementById('codecSelect');
    if (!availableCodecs.includes(codec)) codec = availableCodecs[0] || '';
    select.value = codec;
    const badge = document.getElementById('stageCodecBadge');
    if (badge) badge.textContent = codec || 'Unavailable';
    document.querySelectorAll('.codec-card').forEach((card) => {
        const isMatch = card.dataset.codec === codec;
        card.setAttribute('aria-pressed', String(isMatch));
        if (isMatch) {
            card.className = 'codec-card p-2 rounded-xl border border-slate-700 bg-slate-800 text-left transition';
        } else {
            card.className = 'codec-card p-2 rounded-xl border border-slate-800 bg-slate-900/60 hover:border-slate-700 text-left transition';
        }
    });
    saveVideoSettings();
}

// --- Render Video ---
const MAX_MATERIAL_BYTES = 15 * 1024 * 1024;  // 15 MB per input file
const ALLOWED_CODECS = ['libx264', 'h264_nvenc', 'h264_videotoolbox'];
let availableCodecs = ['libx264'];

async function loadCodecs() {
    const status = document.getElementById('codecStatus');
    try {
        const response = await fetch('/api/codecs');
        const result = await response.json();
        if (!response.ok || !Array.isArray(result.codecs)) throw new Error('Codec check failed.');
        availableCodecs = ALLOWED_CODECS.filter(codec => result.codecs.includes(codec));
        status.textContent = availableCodecs.length
            ? `Available on render worker: ${availableCodecs.join(', ')}. Disabled encoders are unavailable here.`
            : 'No usable encoders found on the render worker.';
    } catch (error) {
        availableCodecs = ['libx264'];
        status.textContent = 'Could not check encoders. Using libx264 (CPU).';
    }
    document.querySelectorAll('#codecSelect option, .codec-card').forEach(element => {
        const codec = element.dataset.codec || element.value;
        element.disabled = !availableCodecs.includes(codec);
        element.title = element.disabled ? 'Unavailable on the render worker' : '';
    });
    syncCodecCards(document.getElementById('codecSelect').value);
}
