/**
 * Start the standalone dashboard after all classic controllers have loaded.
 * Shows the OAuth return outcome, restores legacy saved secret fields, loads the
 * first overview, then polls every 15 seconds. Keep this script last and blocking
 * at the end of the body so required DOM nodes and controller globals exist.
 */
const ytOutcome = new URLSearchParams(location.search).get('youtube');
if (ytOutcome) {
    showAgentToast(
        ytOutcome === 'connected' ? 'Agent YouTube OAuth connected!' : 'Failed to connect Agent YouTube OAuth.',
        ytOutcome !== 'connected'
    );
    history.replaceState(null, '', location.pathname);
}

initLocalSecrets();
loadOverview();
setInterval(loadOverview, 15000);
