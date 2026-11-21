/* Shared map workspace state and defaults. Load after StudioStorage and before all feature scripts; classic globals are shared with inline HTML handlers. */
// --- State ---
let currentZoom = 1;
let panX = 0;
let panY = 0;
let isDraggingMap = false;
let dragStartX = 0;
let dragStartY = 0;
let runHistory = [];
let historyWrites = Promise.resolve();
let historyRevision = 0;
const HISTORY_RETENTION_MS = StudioStorage.HISTORY_RETENTION_MS;
let apiKeyRequired = false;
let loadingTimerInterval = null;

// Default Color Filling Studio Rules (`filling_txt` & `filling_num`)
const DEFAULT_FILLING_TXT = [
    { key: "yes", hex: "#2ecc71", alpha: 255 },
    { key: "no", hex: "#e74c3c", alpha: 255 },
    { key: "tea", hex: "#10b981", alpha: 255 },
    { key: "coffee", hex: "#f59e0b", alpha: 255 }
];
const DEFAULT_FILLING_NUM = [
    { low: 0, high: 100, hex: "#38bdf8", alpha: 255 },
    { low: 100, high: 1000, hex: "#6366f1", alpha: 255 },
    { low: 1000, high: 10000, hex: "#a855f7", alpha: 255 },
    { low: 10000, high: 100000000, hex: "#f43f5e", alpha: 255 }
];

let fillingTxtRules = JSON.parse(JSON.stringify(DEFAULT_FILLING_TXT));
let fillingNumRules = JSON.parse(JSON.stringify(DEFAULT_FILLING_NUM));
let labelStyleConfig = {
    recommended_font: "Bips.ttf",
    recommended_gsm: 15,
    text_color: "#ffffff",
    stroke_color: "#090d16",
    stroke_width: 15,
    auto_fit: true
};
let lastMapValues = null;
let lastMapAnalysis = null;
let paletteAnalysisVersion = 0;
let paletteAnalysisRequest = null;
let paletteAnalysisPending = false;
let paletteAnalysisReady = true;
let mapRepaintPending = false;
const MAP_PALETTE_STORAGE_KEY = 'automap-map-palette-theme';
const AI_TEMPERATURE_STORAGE_KEY = 'automap-ai-temperature';
const API_KEY_STORAGE_KEY = 'automap-gemini-api-key';
const PROVIDER_STORAGE_KEY = 'automap-ai-provider';
const PROVIDER_KEY_STORAGE = {gemini: API_KEY_STORAGE_KEY, deepseek: 'automap-deepseek-api-key'};
const PROVIDER_LABELS = {gemini: 'Gemini', deepseek: 'DeepSeek'};
let activeAiProvider = 'gemini';
let modelListVersion = 0;
const fallbackModels = Array.from(document.getElementById('modelSelect').options, option => option.value);
const providerKeys = {gemini: '', deepseek: ''};
const MAP_TOPIC_STORAGE_KEY = 'automap-map-topic';
const DEFAULT_AI_TEMPERATURE = 0.75;
const LABEL_OFFSET_STORAGE_KEY = 'automap-label-offset-x';
const LABEL_SUFFIX_STORAGE_KEY = 'automap-label-suffix';
