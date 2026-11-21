/**
 * Shared HTML escaping and toast feedback for the standalone dashboard.
 * Loaded first as a classic script; feature controllers and inline event handlers
 * use these global helpers. Toast state is stored on window as before.
 */
function esc(str) {
    return String(str ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
}
function showAgentToast(msg, isErr = false) {
    const el = document.getElementById('agentToast');
    el.textContent = msg;
    el.className = `fixed bottom-5 right-5 z-50 px-4 py-2.5 rounded-xl bg-slate-900 text-xs font-medium shadow-2xl border ${isErr ? 'border-rose-500/50 text-rose-200' : 'border-emerald-500/50 text-emerald-200'}`;
    clearTimeout(window._agentToastTimer);
    window._agentToastTimer = setTimeout(() => el.classList.add('hidden'), 4000);
}
