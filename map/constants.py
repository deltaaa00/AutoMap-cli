# Copyright 2025-2026 deltaaa00
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Pixel coordinates in the bundled map artwork, rather than geographic coordinates.
# The first point anchors the label; additional points fill disconnected land areas.
from pathlib import Path


europe_coords = {
    "ukraine": [(3144, 2440), (3565, 2805)],
    "poland": [(2370, 2338)],
    "moldova": [(3100, 2740)],
    "belarus": [(2916, 2101)],
    "russia": [(3149, 1470), (2508, 2094)],
    "lithuania": [(2628, 1998)],
    "latvia": [(2647, 1838)],
    "estonia": [(2641, 1618), (2511, 1718), (2515, 1663)],
    "finland": [(2550, 1222)],
    "norway": [(1733, 1506), (2101, 717)],
    "sweden": [(2076, 1596)],
    "denmark": [(1797, 1984), (1873, 2085), (1942, 2140), (1967, 2060), (1785, 1921)],
    "germany": [(1812, 2384)],
    "czechia": [(2177, 2627)],
    "slovakia": [(2432, 2700)],
    "austria": [(2129, 2835)],
    "hungary": [(2443, 2865)],
    "switzerland": [(1696, 2935)],
    "slovenia": [(2168, 3006)],
    "romania": [(2833, 2916)],
    "bulgaria": [(2916, 3248)],
    "croatia": [(2325, 3045), (2189, 3126), (2178, 3153), (2228, 3231), (2344, 3290), (2214, 3216), (2373, 3302)],
    "bosnia_and_herzegovina": [(2400, 3178)],
    "montenegro": [(2546, 3320)],
    "north_macedonia": [(2717, 3410)],
    "kosovo": [(2669, 3324)],
    "albania": [(2606, 3482)],
    "greece": [(2750, 3616), (2853, 3800), (2974, 3694), (3133, 4010), (3268, 3813), (2941, 3542), (2957, 3514)],
    "turkey": [(3462, 3471), (3181, 3349), (3154, 3586), (3144, 3468), (3348, 3798)],
    "cyprus": [(3773, 3884), (3839, 3820)],
    "portugal": [(383, 3496)],
    "spain": [(724, 3505), ],
    "italy": [(2021, 3392), (2200, 3881), (1777, 3631)],
    "france": [(1265, 2781), (1787, 3426), (1814, 3363)],
    "netherlands": [(1537, 2418)],
    "united_kingdom": [(1089, 2233), (879, 2040), ],
    "ireland": [(748, 2168), (813, 1991)],
    "iceland": [(602, 837)],
    "serbia": [(2609, 3147)],
    "belgium": [(1491, 2539)],
    "usa": [(1398, 629)],
}
# These seeds use the world map's own pixel dimensions.
world_coords = {
    "asia": [(2240,842), (2273,1370), (2250,1270), (2617,1230), (2459,311), (2412,272), (2396,214), (2795,491),
             (2776,434), (2814,429), (2854,446), (2806,944), (2801,983), (2803,1025), (2784,1036), (2773,1101),
             (2745,1120), (2704,1142), (2726,1115 ), (2619,1289 ),(2653,1371),(2622,1313),(2435,1365),(2457,1399),(2453,1439),(2555,1430),(2504,1495),(2539,1498),(2521,1270),(2611,1455),(2618,1428),(2626,1466),(2677,1430),(2722,1453),(2792,1483),(2879,1480),(2870,1484)],
    "europe": [(1674, 889), (1569, 904), (1516, 893), (1552, 934), (1426, 701), (1395, 685), (1738, 303), (1778, 347),
               (1783, 258),(2069, 477), (1663, 1038), (1663, 1062), (1708, 1092), (1698, 1089), (1776, 1091),(1663,853),(1875,1003),(1971,635),(1718,622)],
    "australia": [(2683, 1633), (2838,1829), (3033,1859), (3086,1794)],
    "africa": [(1637,1255), (1979, 1602)],
    "south_america": [(1017,1521),(1000, 1986)],
    "north_america": [(640,993), (613,1189), (632,1215), (893,1366), (892,1239), (929,1257), (975,1271), (1042,1003),
                      (1028,1016), (1057,1001), (1106,960), (864,711), (991,657), (787,497), (740,517), (653,566),
                      (545,502), (610,425), (735,414), (818,438), (634,328), (715,310), (762,323), (807,254),
                      (922,238), (1237,386), (1128,597), (227,853), (273,836),(273,836),(203,864),(185,876),(302,788)]
}

# Prompt templates intentionally leave values blank for the AI to supply.
# These strings are instructions, not JSON documents to parse directly.
europe = """{

        "ukraine":,
        "poland":,
        "moldova":,
        "belarus":,
        "russia":,
        "lithuania":,
        "latvia":,
        "estonia":,
        "finland":,
        "norway":,
        "sweden":,
        "denmark":,
        "germany":,
        "czechia":,
        "slovakia":,
        "austria":,
        "hungary":,
        "switzerland":,
        "slovenia":,
        "romania":,
        "bulgaria":,
        "croatia":,
        "bosnia_and_herzegovina":,
        "montenegro":,
        "north_macedonia":,
        "kosovo":,
        "albania":,
        "greece":,
        "turkey":,
        "cyprus":,
        "portugal":,
        "spain":,
        "italy":,
        "france":,
        "netherlands":,
        "united_kingdom":,
        "ireland":,
        "iceland":,
        "serbia":,
        "belgium":,
        "usa":
    }"""
# Continents dictionary
world = """{

        "asia":,
        "europe":,
        "australia":,
        "north_america":,
        "south_america":,
        "africa":
    }"""

FONTS_DIR = Path(__file__).resolve().parent.parent / "resources" / "fonts"

_AUTO_PALETTE: tuple[tuple[int, int, int, int], ...] = (
    (56, 189, 248, 255),   # #38bdf8 Sky
    (99, 102, 241, 255),   # #6366f1 Indigo
    (16, 185, 129, 255),   # #10b981 Emerald
    (245, 158, 11, 255),   # #f59e0b Amber
    (244, 63, 94, 255),    # #f43f5e Rose
    (168, 85, 247, 255),   # #a855f7 Purple
    (20, 184, 166, 255),   # #14b8a6 Teal
    (236, 72, 153, 255),   # #ec4899 Pink
    (234, 179, 8, 255),    # #eab308 Gold
    (249, 115, 22, 255),   # #f97316 Orange
)

_PALETTE_THEMES: dict[str, tuple[tuple[int, int, int, int], ...]] = {
    "auto": _AUTO_PALETTE,
    "viridis": (
        (68, 1, 84, 255), (59, 82, 139, 255), (33, 145, 140, 255),
        (94, 201, 98, 255), (253, 231, 37, 255),
    ),
    "sunset": (
        (124, 45, 18, 255), (194, 65, 12, 255), (234, 88, 12, 255),
        (245, 158, 11, 255), (253, 224, 71, 255),
    ),
    "ocean": (
        (12, 74, 110, 255), (3, 105, 161, 255), (2, 132, 199, 255),
        (56, 189, 248, 255), (125, 211, 252, 255),
    ),
    "festive": (
        (220, 38, 38, 255), (22, 163, 74, 255), (234, 179, 8, 255),
        (37, 99, 235, 255), (147, 51, 234, 255),
    ),
    "emerald": (
        (6, 78, 59, 255), (4, 120, 87, 255), (16, 185, 129, 255),
        (52, 211, 153, 255), (167, 243, 208, 255),
    ),
    "neon": (
        (236, 72, 153, 255), (139, 92, 246, 255), (6, 182, 212, 255),
        (16, 185, 129, 255), (250, 204, 21, 255),
    ),
    "pastel": (
        (147, 197, 253, 255), (196, 181, 253, 255), (167, 243, 208, 255),
        (253, 230, 138, 255), (253, 164, 175, 255),
    ),
    "monochrome": (
        (51, 65, 85, 255), (71, 85, 105, 255), (100, 116, 139, 255),
        (148, 163, 184, 255), (203, 213, 225, 255),
    ),
    "heatmap": (
        (37, 99, 235, 255), (16, 185, 129, 255), (245, 158, 11, 255),
        (239, 68, 68, 255), (185, 28, 28, 255),
    ),
    "emerald_gold": (
        (244, 63, 94, 255), (245, 158, 11, 255), (234, 179, 8, 255),
        (16, 185, 129, 255), (5, 150, 105, 255),
    ),
    "oceanic": (
        (56, 189, 248, 255), (14, 165, 233, 255), (37, 99, 235, 255),
        (99, 102, 241, 255), (139, 92, 246, 255),
    ),
    "categorical": _AUTO_PALETTE + (
        (14, 165, 233, 255), (132, 204, 22, 255),
    ),
    "greens": (
        (187, 247, 208, 255), (134, 239, 172, 255), (74, 222, 128, 255),
        (22, 163, 74, 255), (20, 83, 45, 255),
    ),
    "blues": (
        (191, 219, 254, 255), (147, 197, 253, 255), (96, 165, 250, 255),
        (37, 99, 235, 255), (30, 58, 138, 255),
    ),
    "reds": (
        (254, 202, 202, 255), (252, 165, 165, 255), (248, 113, 113, 255),
        (220, 38, 38, 255), (127, 29, 29, 255),
    ),
    "oranges": (
        (254, 215, 170, 255), (253, 186, 116, 255), (251, 146, 60, 255),
        (234, 88, 12, 255), (124, 45, 18, 255),
    ),
    "yellows": (
        (254, 240, 138, 255), (253, 224, 71, 255), (250, 204, 21, 255),
        (202, 138, 4, 255), (133, 77, 14, 255),
    ),
    "purples": (
        (233, 213, 255, 255), (216, 180, 254, 255), (192, 132, 252, 255),
        (147, 51, 234, 255), (88, 28, 135, 255),
    ),
    "teals": (
        (153, 246, 228, 255), (94, 234, 212, 255), (45, 212, 191, 255),
        (13, 148, 136, 255), (19, 78, 74, 255),
    ),
    "pinks": (
        (251, 207, 232, 255), (249, 168, 212, 255), (244, 114, 182, 255),
        (219, 39, 119, 255), (131, 24, 67, 255),
    ),
    "earth_tones": (
        (231, 229, 176, 255), (194, 178, 128, 255), (163, 137, 87, 255),
        (120, 113, 62, 255), (75, 85, 45, 255),
    ),
}

_SEMANTIC_COLORS: dict[str, tuple[int, int, int, int]] = {
    "yes": (46, 204, 113, 255), "так": (46, 204, 113, 255), "true": (46, 204, 113, 255),
    "no": (231, 76, 60, 255), "ні": (231, 76, 60, 255), "false": (231, 76, 60, 255),
    "tea": (16, 185, 129, 255), "coffee": (245, 158, 11, 255), "coffe": (245, 158, 11, 255),
    "beer": (234, 179, 8, 255), "wine": (225, 29, 72, 255),
}

_DEFAULT_UI_TXT_KEYS = {"yes", "no", "tea", "coffee"}
_DEFAULT_UI_NUM_BOUNDS = {(0, 100), (100, 1000), (1000, 10000), (10000, 100000000)}
_RELIEF_ALPHA_LUT: list[int] = [int(p * 0.3) for p in range(256)]

_EUROPE_LABEL_SIZES: dict[str, int] = {
    **{k: 70 for k in (
        "cyprus", "kosovo", "montenegro", "slovenia", "latvia", "lithuania",
        "estonia", "switzerland", "austria", "slovakia", "hungary", "croatia",
        "netherlands", "belgium", "czechia", "moldova", "bosnia_and_herzegovina",
        "serbia", "bulgaria", "albania", "greece", "ireland", "iceland",
        "norway", "finland", "sweden", "denmark", "portugal",
    )},
    **{k: 100 for k in ("sweden", "norway", "finland")},
    **{k: 125 for k in ("north_macedonia", "bosnia")},
    **{k: 175 for k in (
        "ukraine", "poland", "france", "spain", "turkey",
        "romania", "italy", "belarus", "united_kingdom", "germany",
    )},
    **{k: 250 for k in ("russia", "usa")},
}

_WORLD_LABEL_SIZES: dict[str, int] = {
    "asia": 200,
    "africa": 100,
    "europe": 110,
    "australia": 50,
    "south_america": 100,
    "north_america": 120,
}

# Map types whose regions are painted from the cached region-ID map.
# Flag-filled maps (``flag``, ``world_countries``) keep their underlying flag artwork.
_PAINTED_MAP_TYPES: frozenset[str] = frozenset({"default", "world"})

# Smallest base label size (before the global size modifier) still drawn on the
# world countries map; smaller countries stay unlabeled to keep the map readable.
_WORLD_COUNTRIES_MIN_LABEL = 1

# Common alternative AI spellings mapped to canonical snake_case country keys.
# Applied only when the alias target exists in the coordinates dictionary.
_WORLD_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "usa": ("united_states", "united_states_of_america"),
    "us": ("united_states", "united_states_of_america"),
    "united_states_of_america": ("united_states",),
    "america": ("united_states", "united_states_of_america"),
    "uk": ("united_kingdom",),
    "great_britain": ("united_kingdom",),
    "britain": ("united_kingdom",),
    "russian_federation": ("russia",),
    "czech_republic": ("czechia",),
    "turkiye": ("turkey",),
    "ivory_coast": ("cote_d_ivoire", "cote_divoire"),
    "cote_d_ivoire": ("ivory_coast",),
    "cote_divoire": ("ivory_coast", "cote_d_ivoire"),
    "drc": ("democratic_republic_of_the_congo", "dr_congo"),
    "dr_congo": ("democratic_republic_of_the_congo",),
    "congo_kinshasa": ("democratic_republic_of_the_congo", "dr_congo"),
    "democratic_republic_of_congo": ("democratic_republic_of_the_congo", "dr_congo"),
    "congo_brazzaville": ("republic_of_the_congo", "congo"),
    "republic_of_congo": ("republic_of_the_congo", "congo"),
    "south_korea": ("korea_south", "republic_of_korea"),
    "korea": ("south_korea",),
    "korea_south": ("south_korea",),
    "republic_of_korea": ("south_korea",),
    "north_korea": ("korea_north", "dprk"),
    "korea_north": ("north_korea",),
    "dprk": ("north_korea",),
    "burma": ("myanmar",),
    "swaziland": ("eswatini",),
    "eswatini": ("swaziland",),
    "macedonia": ("north_macedonia",),
    "holland": ("netherlands",),
    "the_netherlands": ("netherlands",),
    "uae": ("united_arab_emirates",),
    "east_timor": ("timor_leste",),
    "timor_leste": ("east_timor",),
    "cabo_verde": ("cape_verde",),
    "cape_verde": ("cabo_verde",),
    "vatican_city": ("vatican",),
    "bosnia": ("bosnia_and_herzegovina",),
    "the_gambia": ("gambia",),
    "the_bahamas": ("bahamas",),
    "united_republic_of_tanzania": ("tanzania",),
    "republic_of_serbia": ("serbia",),
    "state_of_palestine": ("palestine",),
    "brunei_darussalam": ("brunei",),
    "viet_nam": ("vietnam",),
    "lao_pdr": ("laos",),
}