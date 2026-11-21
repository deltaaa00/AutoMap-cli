/**
 * Video Studio: Activity console and safe summaries of large data URLs. Shared by audio
 * and render request handlers.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
// --- Console Management ---
// Short, human-readable description of a (possibly huge) data URL,
// so we never dump the full base64 payload into the console.
function describeDataUrl(url) {
    if (!url) return 'no audio';
    const m = /^data:([^;]+);base64,(.*)$/.exec(url);
    if (!m) return url.length > 60 ? url.slice(0, 60) + '…' : url;
    const mime = m[1];
    const bytes = Math.floor(m[2].length * 0.75);
    const kb = (bytes / 1024).toFixed(1);
    return `${mime}, ${kb} kB`;
}

function logToConsole(text, type = 'info') {
    const box = document.getElementById('consoleTabContent');
    const line = document.createElement('div');
    const time = new Date().toLocaleTimeString();

    const colorMap = {
        cmd: 'text-emerald-400 font-semibold',
        info: 'text-slate-300',
        success: 'text-accent-300 font-medium',
        warn: 'text-amber-400',
        error: 'text-rose-400 font-semibold'
    };

    line.className = `${colorMap[type] || 'text-slate-300'} break-all`;
    line.textContent = `[${time}] ${text}`;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
}

function clearConsole() {
    document.getElementById('consoleTabContent').innerHTML = '<div class="text-slate-500">[System] Console cleared.</div>';
}
