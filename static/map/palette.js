/* Palette selection, filling modal visibility, manual/automatic style switching, and typography preferences. Analysis requests and rule rendering are separate feature scripts. */
function safePaletteHex(value, fallback = '#6366f1') {
    return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
}

function hexToRgbArray(hex, alpha = 255) {
    const clean = safePaletteHex(hex).slice(1);
    const num = parseInt(clean, 16);
    const r = (num >> 16) & 255;
    const g = (num >> 8) & 255;
    const b = num & 255;
    return [r, g, b, alpha];
}

function openFillingStudio() {
    const modal = document.getElementById('fillingStudioModal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    renderFillingStudio();
}

function closeFillingStudio(notify = false) {
    const modal = document.getElementById('fillingStudioModal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    if (notify) {
        showToast("Color filling & label style saved", "success");
    }
}

function onAutoStyleToggleChange() {
    cancelPaletteAnalysis();
    renderFillingStudio();
    const isAuto = !!document.getElementById('autoStyleFromMapToggle')?.checked;
    showToast(isAuto ? "Smart Auto-Detect from Map enabled" : "Manual custom rules mode active", "info");
    if (isAuto && lastMapValues) return autoDetectMapColorsAndStyle(true);
}

const PALETTE_PREVIEW_SWATCHES = {
    auto: ['#38bdf8', '#34d399', '#fbbf24', '#f87171', '#a78bfa'],
    viridis: ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'],
    heatmap: ['#1d4ed8', '#10b981', '#f59e0b', '#ef4444', '#991b1b'],
    emerald_gold: ['#fb7185', '#fbbf24', '#34d399', '#10b981', '#065f46'],
    festive: ['#2563eb', '#16a34a', '#eab308', '#dc2626', '#9333ea'],
    oceanic: ['#38bdf8', '#0284c7', '#1d4ed8', '#4f46e5', '#7c3aed'],
    categorical: ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6'],
    greens: ['#dcfce7', '#86efac', '#22c55e', '#15803d', '#14532d'],
    blues: ['#dbeafe', '#93c5fd', '#3b82f6', '#1d4ed8', '#1e3a8a'],
    reds: ['#fee2e2', '#fca5a5', '#ef4444', '#b91c1c', '#7f1d1d'],
    oranges: ['#ffedd5', '#fdba74', '#f97316', '#c2410c', '#7c2d12'],
    yellows: ['#fef9c3', '#fde047', '#eab308', '#a16207', '#713f12'],
    purples: ['#f3e8ff', '#d8b4fe', '#a855f7', '#7e22ce', '#581c87'],
    teals: ['#ccfbf1', '#5eead4', '#14b8a6', '#0f766e', '#134e4a'],
    pinks: ['#fce7f3', '#f9a8d4', '#ec4899', '#be185d', '#831843'],
    sunset: ['#7c2d18', '#c2410c', '#ea580c', '#f59e0b', '#fde047'],
    ocean: ['#0c4a6e', '#0369a1', '#0284c7', '#38bdf8', '#7dd3fc'],
    emerald: ['#064e3b', '#047857', '#10b981', '#34d399', '#a7f3d0'],
    neon: ['#ec4899', '#8b5cf6', '#06b6d4', '#10b981', '#facc15'],
    pastel: ['#93c5fd', '#c4b5fd', '#a7f3d0', '#fde68a', '#fda4af'],
    monochrome: ['#334155', '#475569', '#64748b', '#94a3b8', '#cbd5e1'],
};

function applyPalettePreviewToEmptyRules(theme) {
    const swatches = PALETTE_PREVIEW_SWATCHES[theme] || PALETTE_PREVIEW_SWATCHES.auto;
    fillingTxtRules.forEach((rule, idx) => {
        rule.hex = swatches[idx % swatches.length];
    });
    fillingNumRules.forEach((rule, idx) => {
        rule.hex = swatches[idx % swatches.length];
    });
}

function onPaletteThemeChange(theme = document.getElementById('fillingPaletteTheme').value) {
    document.getElementById('fillingPaletteTheme').value = theme;
    try {
        localStorage.setItem(MAP_PALETTE_STORAGE_KEY,
            document.getElementById('fillingPaletteTheme').value);
    } catch {
        // The palette still works when browser storage is unavailable.
    }
    document.getElementById('autoStyleFromMapToggle').checked = true;
    if (lastMapValues && Object.keys(lastMapValues).length > 0) {
        return autoDetectMapColorsAndStyle(true);
    } else {
        cancelPaletteAnalysis();
        applyPalettePreviewToEmptyRules(theme);
        renderFillingStudio();
    }
}

function restoreMapPaletteTheme() {
    try {
        const saved = localStorage.getItem(MAP_PALETTE_STORAGE_KEY);
        const select = document.getElementById('fillingPaletteTheme');
        if (saved && Array.from(select.options).some(option => option.value === saved)) {
            select.value = saved;
            applyPalettePreviewToEmptyRules(saved);
        }
    } catch {
        // Fall back to Auto if storage is blocked.
    }
}

function cancelPaletteAnalysis() {
    paletteAnalysisVersion++;
    if (paletteAnalysisRequest) paletteAnalysisRequest.abort();
    paletteAnalysisRequest = null;
    paletteAnalysisPending = false;
    paletteAnalysisReady = true;
    document.getElementById('restyleCurrentMapBtn').disabled = mapRepaintPending;
    document.getElementById('autoDetectMapIcon').className = 'fa-solid fa-magnifying-glass-chart text-xs';
}

function useManualFillingRules() {
    cancelPaletteAnalysis();
    document.getElementById('autoStyleFromMapToggle').checked = false;
}

function updateLabelOffset(value, persist = true) {
    const parsed = Number(value);
    const offset = Number.isFinite(parsed) ? Math.max(-500, Math.min(500, Math.round(parsed))) : 0;
    document.getElementById('labelOffsetX').value = offset;
    document.getElementById('labelOffsetXValue').textContent = `${offset > 0 ? '+' : ''}${offset} px`;
    labelStyleConfig.offset_x = offset;
    if (persist) saveBrowserValue(LABEL_OFFSET_STORAGE_KEY, String(offset));
}

function updateLabelStyleField(field, val) {
    if (field === 'stroke_width') {
        labelStyleConfig.stroke_width = Math.max(2, Math.min(25, parseInt(val, 10) || 15));
    } else if (field === 'auto_fit') {
        labelStyleConfig.auto_fit = !!val;
    } else {
        labelStyleConfig[field] = val;
    }
    renderFillingStudio();
}

function applyRecommendedFontAndSize() {
    const recFont = labelStyleConfig.recommended_font || 'Bips.ttf';
    const recGsm = typeof labelStyleConfig.recommended_gsm === 'number' ? labelStyleConfig.recommended_gsm : 15;
    const fontSelect = document.getElementById('fontSelect');
    if (fontSelect && Array.from(fontSelect.options).some(o => o.value === recFont)) {
        fontSelect.value = recFont;
    }
    syncGsm(recGsm);
    showToast(`Applied recommended style: ${recFont} (GSM ${recGsm})`, 'success');
}
