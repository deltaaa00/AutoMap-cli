/* Render and edit text/numeric filling rules, label-style controls, and JSON export. Consumes shared rules and palette helpers without requesting new AI map values. */
function renderFillingStudio() {
    const txtList = document.getElementById('fillingTxtList');
    const numList = document.getElementById('fillingNumList');
    const jsonPreview = document.getElementById('fillingJsonPreview');
    const miniSwatches = document.getElementById('miniSwatchPreview');
    const summaryBadge = document.getElementById('fillingSummaryBadge');
    const sidebarSub = document.getElementById('fillingSidebarSubtext');
    const isAuto = !!document.getElementById('autoStyleFromMapToggle')?.checked;

    // Sync Label Style UI controls
    const txtColorEl = document.getElementById('labelStyleTextColor');
    const txtHexEl = document.getElementById('labelStyleTextHex');
    const strColorEl = document.getElementById('labelStyleStrokeColor');
    const strHexEl = document.getElementById('labelStyleStrokeHex');
    const strWidthEl = document.getElementById('labelStyleStrokeWidth');
    const strWidthValEl = document.getElementById('labelStyleStrokeWidthVal');
    const autoFitEl = document.getElementById('labelStyleAutoFit');
    const recFontBadge = document.getElementById('recommendedFontBadge');

    if (txtColorEl) txtColorEl.value = labelStyleConfig.text_color || '#ffffff';
    if (txtHexEl) txtHexEl.textContent = labelStyleConfig.text_color || '#ffffff';
    if (strColorEl) strColorEl.value = labelStyleConfig.stroke_color || '#090d16';
    if (strHexEl) strHexEl.textContent = labelStyleConfig.stroke_color || '#090d16';
    if (strWidthEl) strWidthEl.value = labelStyleConfig.stroke_width || 15;
    if (strWidthValEl) strWidthValEl.textContent = `${labelStyleConfig.stroke_width || 15}px`;
    if (autoFitEl) autoFitEl.checked = labelStyleConfig.auto_fit !== false;
    if (recFontBadge) recFontBadge.textContent = `Font: ${labelStyleConfig.recommended_font || 'Bips.ttf'}`;

    if (txtList) {
        txtList.innerHTML = fillingTxtRules.map((item, idx) => `
            <div class="flex items-center gap-3 p-2 rounded-xl bg-slate-900/90 border border-slate-800">
                <input type="color" value="${item.hex}" onchange="updateTxtRule(${idx}, 'hex', this.value)"
                    class="w-7 h-7 rounded-lg cursor-pointer shrink-0">
                <input type="text" value="${escapeHtml(item.key)}" oninput="updateTxtRule(${idx}, 'key', this.value)"
                    placeholder="Label (e.g. yes)"
                    class="flex-1 min-w-0 px-3 py-1 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono text-slate-100 focus:outline-none focus:border-accent-500">
                <span class="text-xs font-mono text-slate-400 hidden sm:inline">[${hexToRgbArray(item.hex, item.alpha).join(', ')}]</span>
                <button type="button" onclick="removeTxtRule(${idx})" class="text-slate-500 hover:text-slate-400 p-1 transition" title="Remove">
                    <i class="fa-solid fa-trash-can text-xs"></i>
                </button>
            </div>
        `).join('');
    }

    if (numList) {
        numList.innerHTML = fillingNumRules.map((item, idx) => `
            <div class="flex items-center gap-2 p-2 rounded-xl bg-slate-900/90 border border-slate-800">
                <input type="color" value="${item.hex}" onchange="updateNumRule(${idx}, 'hex', this.value)"
                    class="w-7 h-7 rounded-lg cursor-pointer shrink-0">
                <div class="flex items-center gap-2 flex-1 min-w-0">
                    <input type="number" step="any" value="${item.low}" oninput="updateNumRule(${idx}, 'low', this.value)"
                        class="w-20 px-2 py-1 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono text-slate-100 focus:outline-none focus:border-slate-700">
                    <span class="text-xs text-slate-500 font-mono">to</span>
                    <input type="number" step="any" value="${item.high}" oninput="updateNumRule(${idx}, 'high', this.value)"
                        class="w-24 px-2 py-1 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono text-slate-100 focus:outline-none focus:border-slate-700">
                </div>
                <button type="button" onclick="removeNumRule(${idx})" class="text-slate-500 hover:text-slate-400 p-1 transition" title="Remove">
                    <i class="fa-solid fa-trash-can text-xs"></i>
                </button>
            </div>
        `).join('');
    }

    // Build JSON structure matching map/filling.json + label_style metadata
    const { filling_txt, filling_num } = buildFillingDictionaries();
    const fillingObj = {
        filling_txt,
        filling_num,
        label_style: {
            text_color: labelStyleConfig.text_color,
            stroke_color: labelStyleConfig.stroke_color,
            stroke_width: labelStyleConfig.stroke_width,
            auto_fit: labelStyleConfig.auto_fit,
            offset_x: Number(document.getElementById('labelOffsetX').value),
            recommended_font: labelStyleConfig.recommended_font
        }
    };

    if (jsonPreview) {
        jsonPreview.textContent = JSON.stringify(fillingObj, null, 2);
    }

    const currentMapType = document.querySelector('input[name="map_type"]:checked')?.value || 'default';
    const isFlagMap = currentMapType === 'flag' || currentMapType === 'world_countries';
    const allColors = [...fillingTxtRules.map(r => r.hex), ...fillingNumRules.map(r => r.hex)].slice(0, 6);
    if (miniSwatches) {
        miniSwatches.innerHTML = isFlagMap ? '' : allColors.map(hex =>
            `<span class="w-4 h-4 rounded-full border border-slate-950 " style="background:${hex}"></span>`
        ).join('');
    }
    const totalRules = fillingTxtRules.length + fillingNumRules.length;
    if (summaryBadge) {
        summaryBadge.textContent = isFlagMap
            ? 'Flags (no fill)'
            : (isAuto ? `Auto • ${totalRules} rules` : `Custom • ${totalRules} rules`);
    }
    if (sidebarSub) {
        if (isFlagMap) {
            sidebarSub.textContent = `Flag artwork active • Font: ${labelStyleConfig.recommended_font}`;
        } else if (lastMapAnalysis) {
            sidebarSub.textContent = `Detected ${lastMapAnalysis.detected_type} (${lastMapAnalysis.resolved_theme}) • Font: ${labelStyleConfig.recommended_font}`;
        }
    }
}

function updateTxtRule(idx, field, val) {
    if (!fillingTxtRules[idx]) return;
    useManualFillingRules();
    fillingTxtRules[idx][field] = val;
    renderFillingStudio();
}

function addTextFillingRule() {
    useManualFillingRules();
    fillingTxtRules.push({ key: "custom", hex: "#8b5cf6", alpha: 255 });
    renderFillingStudio();
}

function removeTxtRule(idx) {
    useManualFillingRules();
    fillingTxtRules.splice(idx, 1);
    renderFillingStudio();
}

function updateNumRule(idx, field, val) {
    if (!fillingNumRules[idx]) return;
    useManualFillingRules();
    fillingNumRules[idx][field] = field === 'hex' ? val : Number(val);
    renderFillingStudio();
}

function addNumFillingRule() {
    useManualFillingRules();
    fillingNumRules.push({ low: 1000, high: 50000, hex: "#14b8a6", alpha: 255 });
    renderFillingStudio();
}

function removeNumRule(idx) {
    useManualFillingRules();
    fillingNumRules.splice(idx, 1);
    renderFillingStudio();
}

function resetFillingDefaults() {
    useManualFillingRules();
    fillingTxtRules = JSON.parse(JSON.stringify(DEFAULT_FILLING_TXT));
    fillingNumRules = JSON.parse(JSON.stringify(DEFAULT_FILLING_NUM));
    labelStyleConfig = {
        recommended_font: "Bips.ttf",
        recommended_gsm: 15,
        text_color: "#ffffff",
        stroke_color: "#090d16",
        stroke_width: 15,
        auto_fit: true
    };
    renderFillingStudio();
    showToast("Default filling palette & style restored", "info");
}

function copyFillingJson() {
    const text = document.getElementById('fillingJsonPreview').textContent;
    copyTextToClipboard(text, () => {
        showToast("filling.json copied to clipboard", "success");
    });
}

function downloadFillingJson() {
    const text = document.getElementById('fillingJsonPreview').textContent;
    const blob = new Blob([text], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'filling.json';
    a.click();
    URL.revokeObjectURL(url);
    showToast("Downloaded filling.json", "success");
}

// --- Console & History Management ---
