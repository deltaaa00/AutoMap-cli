/**
 * Fetch and render the complete dashboard overview snapshot.
 * Updates status, form defaults, media lists, approvals, and analytics/history.
 * Depends on common.js helpers and connections.js secret persistence at call time.
 * The bootstrap script performs initial polling after every controller has loaded.
 */
async function loadOverview() {
    const res = await fetch('/api/agent/overview', {cache: 'no-store'});
    const data = await res.json();
    if (!res.ok) { showAgentToast(data.error || 'Failed to load overview', true); return; }

    const cfg = data.config || {};
    const pool = data.media_pool || {};
    document.getElementById('statPending').textContent = (data.pending_queue || []).length;
    document.getElementById('statHours').textContent = (data.recommended_hours || [14, 20]).map(h => `${String(h).padStart(2,'0')}:00`).join(', ');
    document.getElementById('statTz').textContent = cfg.timezone || 'Europe/Kyiv';
    document.getElementById('statSeries').textContent = pool.current_background
        ? `#${pool.series_index} (${pool.slot_in_series}/${pool.series_size}): ${pool.current_background}`
        : 'Upload backgrounds to Media Pool';
    document.getElementById('statPoolCounts').textContent = `Backgrounds: ${(pool.backgrounds || []).length} • Music: ${(pool.music || []).length}`;
    document.getElementById('tgStatusPill').textContent = `Telegram: ${data.telegram_configured ? 'OK' : 'not set'}`;
    document.getElementById('ytApiPill').textContent = `YT API: ${data.youtube_api_configured ? 'OK' : 'missing'}`;
    document.getElementById('ytOauthBadge').textContent = data.youtube_oauth_connected ? 'OAuth: Connected' : 'OAuth: Not connected';
    document.getElementById('ytOauthBadge').className = `text-[11px] font-mono px-2 py-0.5 rounded bg-slate-900 border border-slate-800 ${data.youtube_oauth_connected ? 'text-emerald-300' : 'text-amber-300'}`;
    document.getElementById('agentYtDisconnectBtn').classList.toggle('hidden', !data.youtube_oauth_connected);
    document.getElementById('agentRedirectUri').textContent = data.agent_redirect_uri || '';
    document.getElementById('secTgTokenBadge').classList.toggle('hidden', !data.telegram_token_set);
    document.getElementById('secTgChatIdBadge').classList.toggle('hidden', !data.telegram_chat_id);
    document.getElementById('secYtApiKeyBadge').classList.toggle('hidden', !data.youtube_api_configured);
    document.getElementById('secGeminiApiKeyBadge').classList.toggle('hidden', !data.gemini_api_configured);
    document.getElementById('secDeepseekApiKeyBadge').classList.toggle('hidden', !data.deepseek_api_configured);
    document.getElementById('secTavilyApiKeyBadge').classList.toggle('hidden', !data.tavily_api_configured);
    if (document.activeElement?.id !== 'secTgChatId' && data.telegram_chat_id && !document.getElementById('secTgChatId').value) {
        document.getElementById('secTgChatId').value = data.telegram_chat_id;
        saveLocalSecret('secTgChatId', data.telegram_chat_id);
    }
    const renderFileList = (elId, kind, list) => {
        const el = document.getElementById(elId);
        if (!el) return;
        el.innerHTML = (list && list.length)
            ? list.map(name => `<li class="flex items-center justify-between gap-1 bg-slate-900/70 px-2 py-1 rounded"><span class="truncate">${esc(name)}</span><span class="flex items-center gap-0.5 shrink-0">${kind === 'background' ? `<button type="button" onclick="splitMedia('${esc(name)}')" class="text-sky-400 hover:text-sky-200 px-1" title="Split long background into 15s clips"><i class="fa-solid fa-scissors text-[10px]"></i></button>` : ''}<button type="button" onclick="deleteMedia('${kind}', '${esc(name)}')" class="text-rose-400 hover:text-rose-200 px-1">&times;</button></span></li>`).join('')
            : '<li class="text-slate-500">Empty</li>';
    };
    renderFileList('bgFilesList', 'background', pool.backgrounds || []);
    renderFileList('musicFilesList', 'music', pool.music || []);
    document.getElementById('agentHeartbeatBadge').textContent = cfg.last_heartbeat_at
        ? `Agent Active (${cfg.last_heartbeat_at.slice(11, 19)} UTC)`
        : 'Agent not reported yet';

    if (document.activeElement?.tagName !== 'TEXTAREA' && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'SELECT') {
        document.getElementById('cfgCompetitors').value = (cfg.competitor_channels || []).join(', ');
        document.getElementById('cfgPostsPerDay').value = cfg.posts_per_day || 2;
        document.getElementById('cfgSeriesSize').value = cfg.series_size || 4;
        document.getElementById('cfgTimezone').value = cfg.timezone || 'Europe/Kyiv';
        const modelSel = document.getElementById('cfgModel');
        const models = Array.isArray(data.available_models) && data.available_models.length
            ? [...data.available_models]
            : ['gemini-3.8-flash'];
        const currentModel = cfg.ai_model || models[0];
        if (currentModel && !models.includes(currentModel)) {
            models.unshift(currentModel);
        }
        modelSel.innerHTML = models
            .map(m => `<option value="${esc(m)}">${esc(m)}</option>`)
            .join('');
        modelSel.value = currentModel;
        document.getElementById('cfgWatermark').value = cfg.watermark_text || '';
        document.getElementById('cfgWebSearch').checked = cfg.web_search_enabled !== false;
        document.getElementById('cfgMaxSearches').value = cfg.max_search_requests || 5;
        document.getElementById('cfgAutomapUrl').value = cfg.automap_url || '';
        document.getElementById('cfgDescTemplate').value = cfg.description_template || '{topic}\n\n{hashtags}';
    }
    if (data.automap_public_url) {
        document.getElementById('mapStudioLink').href = `${data.automap_public_url}/`;
        document.getElementById('videoStudioLink').href = `${data.automap_public_url}/studio`;
    }

    // Pending queue
    const qBox = document.getElementById('pendingQueueContainer');
    const queue = data.pending_queue || [];
    if (!queue.length) {
        qBox.innerHTML = '<p class="text-xs text-slate-500">Queue is empty — click "Generate Video" or wait for the next scheduled slot.</p>';
    } else {
        qBox.innerHTML = queue.map(exp => `
            <div class="rounded-xl bg-slate-950/80 border border-slate-800 p-4 flex flex-col sm:flex-row gap-4">
                <video controls playsinline class="w-full sm:w-36 aspect-[9/16] rounded-lg bg-black object-contain shrink-0" src="/api/agent/video/${exp.id}"></video>
                <div class="flex-1 min-w-0 flex flex-col justify-between gap-3">
                    <div class="space-y-1">
                        <div class="text-[11px] font-mono text-amber-300">Short #${exp.id} • ${esc(exp.arm)}</div>
                        <div class="text-sm font-semibold text-white">${esc(exp.topic)}</div>
                        <p class="text-[11px] text-slate-400">${esc(exp.rationale)}</p>
                        <div class="text-[10px] font-mono text-slate-500">
                            BG: ${esc(exp.params?.background_file || '—')} • Music: ${esc(exp.params?.music_file || 'none')}
                        </div>
                    </div>
                    <div class="flex flex-wrap gap-1.5">
                        <button onclick="expAction(${exp.id}, 'publish')" class="px-2.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold">✅ Publish</button>
                        <button onclick="expAction(${exp.id}, 'schedule')" class="px-2.5 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-[11px] font-semibold">🕒 Schedule</button>
                        <button onclick="expAction(${exp.id}, 'private')" class="px-2.5 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white text-[11px] font-semibold">🔒 Private</button>
                        <button onclick="expAction(${exp.id}, 'regenerate')" class="px-2.5 py-1.5 rounded-lg bg-amber-600/80 hover:bg-amber-500 text-white text-[11px] font-semibold">🔄 Regenerate</button>
                        <button onclick="expAction(${exp.id}, 'reject')" class="px-2.5 py-1.5 rounded-lg bg-rose-900/70 hover:bg-rose-800 text-rose-200 text-[11px] font-semibold">❌ Reject</button>
                    </div>
                </div>
            </div>
        `).join('');
    }

    // Leaderboard
    const lb = data.leaderboard || [];
    document.getElementById('leaderboardBody').innerHTML = lb.length
        ? lb.map(r => {
            const retParts = [];
            if (r.avg_retention != null) retParts.push(`${r.avg_retention.toFixed(1)}%`);
            if (r.avg_watch_sec != null) retParts.push(`${r.avg_watch_sec.toFixed(1)}s`);
            if (r.avg_hook_retention != null) retParts.push(`hook ${r.avg_hook_retention.toFixed(0)}%`);
            const engParts = [];
            if (r.avg_likes != null) engParts.push(`👍${Math.round(r.avg_likes)}`);
            if (r.avg_comments != null) engParts.push(`💬${Math.round(r.avg_comments)}`);
            if (r.avg_shares != null) engParts.push(`🔁${Math.round(r.avg_shares)}`);
            if (r.avg_subs_gained != null) engParts.push(`+${Math.round(r.avg_subs_gained)}`);
            return `
            <tr>
                <td class="py-2 pr-3 font-mono text-slate-200">${esc(r.arm)}</td>
                <td class="py-2 px-2 text-slate-300">${r.total_runs} (${r.measured_runs || 0} measured)</td>
                <td class="py-2 px-2 text-slate-300">${r.avg_views != null ? Math.round(r.avg_views).toLocaleString() : '—'}</td>
                <td class="py-2 px-2 font-mono text-slate-300">${retParts.length ? retParts.join(' • ') : '—'}</td>
                <td class="py-2 px-2 font-mono text-slate-300">${engParts.length ? engParts.join(' ') : '—'}</td>
                <td class="py-2 pl-2 font-mono text-emerald-300">${r.avg_score != null ? r.avg_score.toFixed(1) : '—'}</td>
            </tr>`;
        }).join('')
        : '<tr><td colspan="6" class="py-4 text-slate-500">No completed experiments yet.</td></tr>';

    // Own Channel Video Analytics
    const ownSynced = cfg.own_analytics_synced_at;
    document.getElementById('ownAnalyticsSyncedAt').textContent = ownSynced
        ? `Synced: ${ownSynced.replace('T', ' ').slice(0, 16)} UTC`
        : 'Not synced yet';
    const ownVideos = Array.isArray(cfg.own_top_topics) ? cfg.own_top_topics : [];
    document.getElementById('ownVideosBody').innerHTML = ownVideos.length
        ? ownVideos.map(v => {
            const retStr = v.avg_view_pct != null ? `${Number(v.avg_view_pct).toFixed(1)}%` : '—';
            const watchStr = v.avg_view_duration_sec != null
                ? `${Number(v.avg_view_duration_sec).toFixed(1)}s${v.duration_sec ? '/' + v.duration_sec + 's' : ''}`
                : (v.duration_sec ? `${v.duration_sec}s` : '');
            const hookEnd = (v.hook_retention_pct != null || v.end_retention_pct != null)
                ? `${v.hook_retention_pct ?? '—'}% &rarr; ${v.end_retention_pct ?? '—'}%${v.relative_retention != null ? ` (${v.relative_retention}x)` : ''}`
                : '—';
            const netSubs = (v.subscribers_gained || 0) - (v.subscribers_lost || 0);
            const engStr = [
                `👍 ${Number(v.likes || 0).toLocaleString()}`,
                `💬 ${Number(v.comments || 0).toLocaleString()}`,
                `🔁 ${Number(v.shares || 0).toLocaleString()}`,
                `${netSubs >= 0 ? '+' : ''}${netSubs} subs`,
                v.engagement_rate != null ? `${v.engagement_rate}% ER` : '',
            ].filter(Boolean).join(' &bull; ');
            return `
            <tr>
                <td class="py-2 pr-3 text-white max-w-xs truncate"><a class="text-sky-300 hover:underline" target="_blank" rel="noopener noreferrer" href="https://youtube.com/shorts/${esc(v.video_id)}">${esc(v.title)}</a></td>
                <td class="py-2 px-2 font-mono text-slate-200">${Number(v.views || 0).toLocaleString()}</td>
                <td class="py-2 px-2 font-mono text-slate-300">${retStr}${watchStr ? ` <span class="text-slate-500">(${watchStr})</span>` : ''}</td>
                <td class="py-2 px-2 font-mono text-slate-300">${hookEnd}</td>
                <td class="py-2 px-2 font-mono text-slate-300 text-[11px]">${engStr}</td>
                <td class="py-2 pl-2 text-right font-mono text-emerald-300">${v.score != null ? Number(v.score).toFixed(0) : '—'}</td>
            </tr>`;
        }).join('')
        : '<tr><td colspan="6" class="py-4 text-slate-500">Connect YouTube OAuth and click Sync to load your channel video analytics.</td></tr>';

    // Competitors
    const comps = data.competitors || [];
    document.getElementById('competitorsBody').innerHTML = comps.length
        ? comps.map(c => `
            <tr>
                <td class="py-2 pr-3 text-slate-300">${esc(c.channel_title || c.channel_id)}</td>
                <td class="py-2 px-2 text-white"><a class="hover:underline" target="_blank" rel="noopener noreferrer" href="https://youtube.com/shorts/${esc(c.video_id)}">${esc(c.title)}</a></td>
                <td class="py-2 px-2 font-mono text-slate-300">${Number(c.views || 0).toLocaleString()}</td>
                <td class="py-2 px-2 font-mono ${c.outlier_ratio >= 2 ? 'text-amber-300 font-bold' : 'text-slate-400'}">${c.outlier_ratio}x</td>
                <td class="py-2 px-2 text-slate-400">${esc(c.music_hint || '—')}</td>
                <td class="py-2 pl-2 text-right">
                    ${c.music_imported
                        ? '<span class="text-emerald-400 font-mono">In pool</span>'
                        : `<button onclick="importMusic(${c.id})" class="px-2.5 py-1 rounded-lg bg-indigo-600/80 hover:bg-indigo-500 text-white text-[11px] font-semibold">🎵 Add Track</button>`}
                </td>
            </tr>`).join('')
        : '<tr><td colspan="6" class="py-4 text-slate-500">Add competitor channels on the right and click refresh.</td></tr>';

    // History
    const hist = data.experiments || [];
    document.getElementById('historyBody').innerHTML = hist.length
        ? hist.map(e => {
            const retParts = [];
            if (e.avg_view_pct != null) retParts.push(`${e.avg_view_pct}%`);
            if (e.avg_view_duration_sec != null) retParts.push(`${e.avg_view_duration_sec}s${e.duration_sec ? '/' + e.duration_sec + 's' : ''}`);
            if (e.hook_retention_pct != null || e.end_retention_pct != null) {
                retParts.push(`hook ${e.hook_retention_pct ?? '—'}%&rarr;${e.end_retention_pct ?? '—'}%`);
            }
            const engParts = [];
            if (e.likes_72h != null) engParts.push(`👍${e.likes_72h}`);
            if (e.comments_72h != null) engParts.push(`💬${e.comments_72h}`);
            if (e.shares_72h != null) engParts.push(`🔁${e.shares_72h}`);
            if (e.subscribers_gained != null || e.subscribers_lost != null) {
                const net = (e.subscribers_gained || 0) - (e.subscribers_lost || 0);
                engParts.push(`${net >= 0 ? '+' : ''}${net} subs`);
            }
            return `
            <tr>
                <td class="py-2 pr-2 font-mono text-slate-400">#${e.id}</td>
                <td class="py-2 px-2 text-white">${e.youtube_url ? `<a class="text-sky-300 hover:underline" target="_blank" rel="noopener noreferrer" href="${esc(e.youtube_url)}">${esc(e.topic)}</a>` : esc(e.topic)}</td>
                <td class="py-2 px-2 font-mono text-slate-300">${esc(e.arm)}</td>
                <td class="py-2 px-2 font-mono text-slate-300">${esc(e.status)}</td>
                <td class="py-2 px-2 font-mono text-slate-300">${e.views_24h ?? '—'} / ${e.views_72h ?? '—'}</td>
                <td class="py-2 px-2 font-mono text-slate-300">${retParts.length ? retParts.join(' • ') : '—'}</td>
                <td class="py-2 px-2 font-mono text-slate-300 text-[11px]">${engParts.length ? engParts.join(' ') : '—'}</td>
                <td class="py-2 pl-2 text-right font-mono text-emerald-300">${e.score != null ? Number(e.score).toFixed(0) : '—'}</td>
            </tr>`;
        }).join('')
        : '<tr><td colspan="8" class="py-4 text-slate-500">History is empty.</td></tr>';
}
