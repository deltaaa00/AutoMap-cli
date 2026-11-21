/**
 * Video Studio: Title resets, sticker catalog/theme matching, variant picker, and Shorts
 * presets. Uses stage settings; preview updates call the later overlay-images.js
 * controller.
 * Classic script: shared top-level bindings and public functions support inline HTML
 * handlers.
 * Load order is declared in templates/video/scripts.html.
 */
function updateTitleStyle() {
    document.getElementById('titleFontSizeValue').textContent = `${titleSettings().title_font_size} px`;
    updateStageTitlePreview(document.getElementById('videoTitleInput').value);
    updateAutoMontagePreview();
    updateWatermarkPreview();
    saveVideoSettings();
}

function resetTitleStyle() {
    document.getElementById('titleFontSelect').value = 'DIN Condensed.ttf';
    document.getElementById('titleFontSize').value = '100';
    document.getElementById('titleFirstLineColor').value = '#ffffff';
    document.getElementById('titleOtherLinesColor').value = '#ffe735';
    document.getElementById('titleUppercase').checked = true;
    document.getElementById('subscribeTextInput').value = 'Subscribe!';
    document.getElementById('subscribeDelayInput').value = '2.5';
    clearAutoMontageOverlays(false);
    updateTitleStyle();
}

function clearAutoMontageOverlays(notify = true) {
    document.getElementById('topLeftStickerSelect').value = 'none';
    document.getElementById('topRightStickerSelect').value = 'none';
    document.getElementById('subscribeBadgeToggle').checked = false;
    document.getElementById('watermarkStarsToggle').checked = false;
    document.getElementById('introFadeToggle').checked = false;
    document.getElementById('autoStickerVariantsBox').classList.add('hidden');
    clearLeftStickerImage(true);
    clearRightStickerImage(true);
    clearSubscribeImage(true);
    clearWatermarkStarsImage(true);
    showLiveComposite();
    updateAutoMontagePreview();
    updateWatermarkPreview();
    saveVideoSettings();
    if (notify) {
        showToast('Cleared optional auto-montage overlays.', 'info');
    }
}

const STICKER_CATALOG = {
    auto: 'Auto',
    santa: 'Santa',
    gift: 'Gift',
    snowflake: 'Snow',
    pumpkin: 'Pumpkin',
    globe: 'Globe',
    compass: 'Compass',
    pin: 'Pin',
    plane: 'Plane',
    money: 'Money',
    chart: 'Chart',
    diamond: 'Diamond',
    crown: 'Crown',
    shield: 'Shield',
    swords: 'Swords',
    rocket: 'Rocket',
    bolt: 'Bolt',
    trophy: 'Trophy',
    fire: 'Fire',
    star: '⭐ Star',
    heart: 'Heart',
    skull: 'Skull',
    bear: 'Bear',
    paw: 'Paw',
    tree: 'Tree',
    wolf: 'Wolf',
    beer: 'Beer',
    coffee: 'Coffee',
    wine: 'Wine',
    pizza: 'Pizza',
    soccer: 'Football',
    car: 'Car',
    brain: 'Brain',
    book: 'Book',
    sun: 'Sun',
    mountain: 'Mountain',
    volcano: 'Volcano',
    island: 'Island',
    rainbow: 'Rainbow',
    moon: 'Moon',
    rain: 'Rain',
    robot: 'Robot',
    satellite: 'Satellite',
    laptop: 'Laptop',
    graduation: 'Graduation',
    medal: 'Medal',
    cat: 'Cat',
    dog: 'Dog',
    fish: 'Fish',
    burger: 'Burger',
    train: 'Train',
    bank: 'Bank',
    coin: 'Coin',
    hospital: 'Hospital',
    pill: 'Medicine',
    family: 'Family',
    city: 'City',
    castle: 'Castle',
    museum: 'Museum',
    gamepad: 'Gaming',
    camera: 'Camera',
    oil: 'Oil',
    leaf: 'Ecology',
    scales: 'Law',
    ship: 'Ship',

};

const AUTO_STICKER_THEME_VARIANTS = [
    {
        name: 'Wildlife & Nature',
        keywords: ['bear', 'bears', 'grizzly', 'panda', 'wolf', 'wolves', 'fox', 'deer', 'moose', 'boar', 'lion', 'tiger', 'dog', 'dogs', 'cat', 'cats', 'pet', 'pets', 'animal', 'animals', 'wildlife', 'wild', 'forest', 'tree', 'trees', 'woods', 'nature', 'park', 'hunt', 'bird', 'fish', 'shark', 'snake', 'horse', 'cow'],
        left: ['auto', 'bear', 'paw', 'tree', 'wolf', 'cat', 'dog', 'fish'],
        right: ['auto', 'tree', 'paw', 'bear', 'wolf', 'cat', 'dog', 'fish'],
    },
    {
        name: 'Food, Drinks & Lifestyle',
        keywords: ['beer', 'beers', 'wine', 'alcohol', 'vodka', 'whiskey', 'drink', 'drinks', 'pub', 'bar', 'coffee', 'tea', 'cafe', 'pizza', 'burger', 'food', 'cuisine', 'cheese', 'meat', 'bread', 'milk', 'water'],
        left: ['auto', 'beer', 'coffee', 'wine', 'pizza', 'burger'],
        right: ['auto', 'coffee', 'beer', 'pizza', 'wine', 'burger'],
    },
    {
        name: 'People, IQ & Education',
        keywords: ['iq', 'smart', 'intelligent', 'brain', 'genius', 'education', 'school', 'university', 'student', 'literacy', 'book', 'books', 'read', 'language', 'languages', 'speak', 'word', 'words', 'alphabet', 'name', 'names', 'population', 'people', 'human'],
        left: ['auto', 'brain', 'book', 'crown', 'star', 'graduation', 'laptop'],
        right: ['auto', 'book', 'brain', 'trophy', 'globe', 'graduation', 'laptop'],
    },
    {
        name: 'Cars, Transport & Energy',
        keywords: ['car', 'cars', 'auto', 'vehicle', 'drive', 'driving', 'highway', 'road', 'roads', 'train', 'rail', 'railway', 'metro', 'bus', 'traffic', 'fuel', 'gas', 'petrol', 'oil', 'tesla', 'bmw'],
        left: ['auto', 'car', 'bolt', 'compass', 'fire', 'train'],
        right: ['auto', 'bolt', 'car', 'pin', 'globe', 'train'],
    },
    {
        name: 'Sports, Football & Games',
        keywords: ['sport', 'sports', 'football', 'soccer', 'fifa', 'uefa', 'world cup', 'basketball', 'hockey', 'tennis', 'olympic', 'olympics', 'medal', 'athlete', 'gym', 'game', 'games', 'chess'],
        left: ['auto', 'soccer', 'trophy', 'fire', 'crown', 'medal'],
        right: ['auto', 'trophy', 'soccer', 'star', 'fire', 'medal'],
    },
    {
        name: 'Cold, Ice & Winter Weather',
        keywords: ['cold', 'coldest', 'freeze', 'freezing', 'frost', 'ice', 'icy', 'snow', 'snowy', 'blizzard', 'minus', 'subzero', 'arctic', 'glacier', 'chill'],
        left: ['auto', 'snowflake', 'compass', 'globe', 'star'],
        right: ['auto', 'globe', 'snowflake', 'pin', 'compass'],
    },
    {
        name: 'Weather, Climate & Heat',
        keywords: ['weather', 'temperature', 'climate', 'sun', 'sunny', 'hot', 'hottest', 'heat', 'warm', 'warmest', 'summer', 'rain', 'wind', 'celsius', 'desert'],
        left: ['auto', 'sun', 'compass', 'fire', 'globe'],
        right: ['auto', 'compass', 'sun', 'pin', 'fire'],
    },
    {
        name: 'Oceans, Rivers & Geography',
        keywords: ['sea', 'ocean', 'river', 'lake', 'coast', 'island', 'water', 'mountain', 'mountains'],
        left: ['auto', 'compass', 'globe', 'pin', 'sun'],
        right: ['auto', 'globe', 'pin', 'compass', 'star'],
    },
    {
        name: 'Religion & Culture',
        keywords: ['religion', 'christian', 'catholic', 'orthodox', 'islam', 'muslim', 'church', 'mosque', 'god', 'faith'],
        left: ['auto', 'star', 'globe', 'crown', 'book'],
        right: ['auto', 'globe', 'star', 'pin', 'book'],
    },
    {
        name: 'Holiday & Winter',
        keywords: ['nicholas', 'santa', 'christmas', 'xmas', 'winter', 'holiday', 'new year', 'snow'],
        left: ['auto', 'santa', 'snowflake', 'gift', 'star'],
        right: ['auto', 'gift', 'snowflake', 'santa', 'crown'],
    },
    {
        name: 'Halloween & Spooky',
        keywords: ['halloween', 'spooky', 'horror', 'ghost', 'scary'],
        left: ['auto', 'pumpkin', 'skull', 'fire', 'star'],
        right: ['auto', 'skull', 'pumpkin', 'fire', 'crown'],
    },
    {
        name: 'Economy & Wealth',
        keywords: ['gdp', 'rich', 'poor', 'money', 'salary', 'wage', 'economy', 'tax', 'price', 'cost', 'billion', 'wealth'],
        left: ['auto', 'money', 'diamond', 'chart', 'crown'],
        right: ['auto', 'chart', 'money', 'diamond', 'trophy'],
    },
    {
        name: 'History, Military & Empires',
        keywords: ['war', 'army', 'military', 'empire', 'history', 'battle', 'nato', 'soldier'],
        left: ['auto', 'shield', 'swords', 'crown', 'skull'],
        right: ['auto', 'swords', 'shield', 'fire', 'trophy'],
    },
    {
        name: 'Royalty & Power',
        keywords: ['queen', 'monarch', 'royal', 'king', 'president', 'power', 'ruler'],
        left: ['auto', 'crown', 'diamond', 'shield', 'star'],
        right: ['auto', 'star', 'crown', 'trophy', 'globe'],
    },
    {
        name: 'Science, Space & Energy',
        keywords: ['space', 'nasa', 'tech', 'ai', 'internet', 'speed', 'energy', 'nuclear', 'science'],
        left: ['auto', 'rocket', 'bolt', 'globe', 'star'],
        right: ['auto', 'bolt', 'rocket', 'chart', 'fire'],
    },
    {
        name: 'Travel & Geography',
        keywords: ['travel', 'tourist', 'visa', 'passport', 'flight', 'airport', 'visit'],
        left: ['auto', 'plane', 'globe', 'compass', 'pin'],
        right: ['auto', 'compass', 'pin', 'plane', 'star'],
    },
    {
        name: 'Rankings & Top Charts',
        keywords: ['top', 'best', 'most', 'highest', 'winner', 'champion', 'rank'],
        left: ['auto', 'trophy', 'crown', 'star', 'fire'],
        right: ['auto', 'fire', 'trophy', 'chart', 'diamond'],
    },
    {
        name: 'Love, Health & Happiness',
        keywords: ['happy', 'happiness', 'love', 'health', 'life', 'safe'],
        left: ['auto', 'heart', 'star', 'globe', 'trophy'],
        right: ['auto', 'star', 'heart', 'crown', 'globe'],
    },
    {
        name: 'Danger & Risk',
        keywords: ['danger', 'crime', 'dead', 'worst', 'risk'],
        left: ['auto', 'skull', 'fire', 'shield', 'bolt'],
        right: ['auto', 'fire', 'skull', 'chart', 'pin'],
    },
];

const EXACT_STICKER_WORDS = new Set([
    'ai', 'gdp', 'nato', 'top', 'iq', 'car', 'sun', 'pet', 'dog', 'cat',
    'bar', 'pub', 'tea', 'oil', 'gas', 'bus', 'gym', 'ice', 'hot', 'god',
    'sea',
]);

function detectTitleStickerVariants(title) {
    const lower = (title || '').toLowerCase();
    let best = null;
    let bestScore = 0;
    for (const theme of AUTO_STICKER_THEME_VARIANTS) {
        let score = 0;
        for (const kw of theme.keywords) {
            const escaped = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const pattern = EXACT_STICKER_WORDS.has(kw)
                ? new RegExp(`(^|[^a-zа-яіїєґ0-9_])${escaped}($|[^a-zа-яіїєґ0-9_])`, 'i')
                : new RegExp(`(^|[^a-zа-яіїєґ0-9_])${escaped}`, 'i');
            if (pattern.test(lower)) score += 1;
        }
        if (score > bestScore) {
            bestScore = score;
            best = theme;
        }
    }
    return best || {
        name: 'World & Maps',
        left: ['auto', 'globe', 'compass', 'trophy', 'fire'],
        right: ['auto', 'pin', 'star', 'chart', 'crown'],
    };
}

function normalizeDynamicStickerValue(val) {
    if (!val) return 'none';
    if (val.startsWith('gen_diecut:')) return 'gen_diecut:auto';
    if (val.startsWith('gen_neon:')) return 'gen_neon:auto';
    if (val.startsWith('gen_stamp:')) return 'gen_stamp:auto';
    return val;
}

function isDynamicStickerValue(val) {
    return val === 'auto' || (typeof val === 'string' && val.startsWith('gen_'));
}

function pickStickerVariant(side, value) {
    const selectId = side === 'left' ? 'topLeftStickerSelect' : 'topRightStickerSelect';
    const select = document.getElementById(selectId);
    if (side === 'left' && leftStickerFile) clearLeftStickerImage(true);
    if (side === 'right' && rightStickerFile) clearRightStickerImage(true);
    if (![...select.options].some(opt => opt.value === value)) {
        const opt = document.createElement('option');
        opt.value = value;
        const [prefix, base] = value.split(':');
        const styleLabel = prefix === 'gen_diecut' ? 'Die-Cut' : prefix === 'gen_neon' ? 'Neon' : 'Stamp';
        opt.textContent = `Generated ${styleLabel} (${base})`;
        select.appendChild(opt);
    }
    select.value = value;
    showLiveComposite();
    updateAutoMontagePreview();
}

let lastAutoStickerTheme = null;

function renderAutoStickerVariants() {
    const box = document.getElementById('autoStickerVariantsBox');
    if (!box || box.classList.contains('hidden')) return;
    const title = document.getElementById('videoTitleInput').value.trim();
    const theme = detectTitleStickerVariants(title);
    let mapTheme = '';
    try {
        mapTheme = localStorage.getItem('automap-generated-map-theme') || localStorage.getItem('automap-map-palette-theme') || '';
    } catch {}
    const mapThemeSuffix = mapTheme && mapTheme !== 'auto' ? ` (Map: ${mapTheme})` : '';
    document.getElementById('autoStickerThemeLabel').textContent = `Theme: ${theme.name}${mapThemeSuffix} — library & generated variants`;

    const leftSelect = document.getElementById('topLeftStickerSelect');
    const rightSelect = document.getElementById('topRightStickerSelect');
    if (lastAutoStickerTheme && lastAutoStickerTheme.name !== theme.name) {
        if (lastAutoStickerTheme.left.includes(leftSelect.value) && leftSelect.value !== 'auto') {
            leftSelect.value = 'auto';
        }
        if (lastAutoStickerTheme.right.includes(rightSelect.value) && rightSelect.value !== 'auto') {
            rightSelect.value = 'auto';
        }
    }
    lastAutoStickerTheme = theme;
    if (leftSelect.value.startsWith('gen_') && !leftSelect.value.endsWith(':auto')) {
        leftSelect.value = normalizeDynamicStickerValue(leftSelect.value);
    }
    if (rightSelect.value.startsWith('gen_') && !rightSelect.value.endsWith(':auto')) {
        rightSelect.value = normalizeDynamicStickerValue(rightSelect.value);
    }
    const currentLeft = leftSelect.value;
    const currentRight = rightSelect.value;
    const bestLeftBase = theme.left[1] || 'globe';
    const bestRightBase = theme.right[1] || 'pin';

    const buildChips = (containerId, side, libraryItems, baseKind, currentValue) => {
        const container = document.getElementById(containerId);
        container.replaceChildren();

        const addGroup = (heading, entries) => {
            const label = document.createElement('div');
            label.className = 'w-full text-xs font-mono   text-slate-500 mt-1';
            label.textContent = heading;
            container.appendChild(label);

            entries.forEach(({ val, text, prefixMatch }) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                const active = currentValue === val || (prefixMatch && currentValue.startsWith(prefixMatch));
                btn.className = active
                    ? 'px-2 py-0.5 rounded-md bg-accent-500/30 text-slate-100 border border-accent-400 text-xs font-medium transition'
                    : 'px-2 py-0.5 rounded-md bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-700 text-xs transition';
                btn.textContent = text;
                btn.onclick = () => pickStickerVariant(side, val);
                container.appendChild(btn);
            });
        };

        addGroup('Library (Twemoji)', libraryItems.map(val => ({
            val,
            text: STICKER_CATALOG[val] || val,
        })));

        const baseLabel = STICKER_CATALOG[baseKind] || baseKind;
        addGroup('Generated (Custom Art)', [
            { val: 'gen_diecut:auto', prefixMatch: 'gen_diecut:', text: `Die-Cut (${baseLabel})` },
            { val: 'gen_neon:auto', prefixMatch: 'gen_neon:', text: `Neon Badge (${baseLabel})` },
            { val: 'gen_stamp:auto', prefixMatch: 'gen_stamp:', text: `Stamp (${baseLabel})` },
        ]);
    };

    buildChips('autoLeftVariantsList', 'left', theme.left, bestLeftBase, currentLeft);
    buildChips('autoRightVariantsList', 'right', theme.right, bestRightBase, currentRight);
}

function setAutoStickers() {
    const leftSelect = document.getElementById('topLeftStickerSelect');
    const rightSelect = document.getElementById('topRightStickerSelect');
    leftSelect.value = leftSelect.value.startsWith('gen_')
        ? normalizeDynamicStickerValue(leftSelect.value)
        : 'auto';
    rightSelect.value = rightSelect.value.startsWith('gen_')
        ? normalizeDynamicStickerValue(rightSelect.value)
        : 'auto';
    document.getElementById('autoStickerVariantsBox').classList.remove('hidden');
    renderAutoStickerVariants();
    showLiveComposite();
    updateAutoMontagePreview();
    showToast('Auto-matched stickers! Choose any left or right variant below.', 'info');
}

function applyShortsViralPreset() {
    const titleEl = document.getElementById('videoTitleInput');
    if (!titleEl.value.trim()) {
        titleEl.value = 'When is Saint Nicholas\nDay in your country?';
        manualVideoTitle = true;
    }
    const wmEl = document.getElementById('watermarkTextInput');
    if (!wmEl.value.trim()) {
        wmEl.value = 'TERRAMAPPER';
    }
    document.getElementById('titleUppercase').checked = false;
    document.getElementById('titleFirstLineColor').value = '#ffffff';
    document.getElementById('titleOtherLinesColor').value = '#ffffff';
    document.getElementById('topLeftStickerSelect').value = 'santa';
    document.getElementById('topRightStickerSelect').value = 'gift';
    document.getElementById('subscribeBadgeToggle').checked = true;
    document.getElementById('subscribeTextInput').value = 'Subscribe!';
    document.getElementById('subscribeDelayInput').value = '2.5';
    document.getElementById('watermarkColor').value = '#ffffff';
    document.getElementById('watermarkStarsToggle').checked = true;
    document.getElementById('introFadeToggle').checked = true;
    document.getElementById('visualEffects').checked = true;
    updateVisualEffects();
    showToast('Applied Reference Video Auto-Montage Preset', 'success');
}
