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

"""English-only sticker catalog and keyword dictionary for automatic video sticker selection."""

# Valid sticker identifiers accepted by the API; 'none' and 'auto' are control modes.
ALLOWED_STICKERS: tuple[str, ...] = (
    'none', 'auto', 'santa', 'gift', 'snowflake', 'pumpkin',
    'globe', 'compass', 'pin', 'plane',
    'money', 'chart', 'diamond', 'crown',
    'shield', 'swords', 'rocket', 'bolt',
    'fire', 'trophy', 'star', 'heart', 'skull',
    'bear', 'paw', 'tree', 'wolf',
    'beer', 'coffee', 'wine', 'pizza',
    'soccer', 'car', 'brain', 'book', 'sun',
    'mountain', 'volcano', 'island', 'rainbow', 'moon', 'rain',
    'robot', 'satellite', 'laptop', 'graduation', 'medal',
    'cat', 'dog', 'fish', 'burger', 'train',
)

# Mapping from sticker name to lowercase hex Twemoji codepoint for SVG/PNG asset lookup.
TWEMOJI_CODEPOINTS: dict[str, str] = {
    'santa': '1f385',
    'gift': '1f381',
    'snowflake': '2744',
    'pumpkin': '1f383',
    'globe': '1f30d',
    'compass': '1f9ed',
    'pin': '1f4cd',
    'plane': '2708',
    'money': '1f4b0',
    'chart': '1f4c8',
    'diamond': '1f48e',
    'crown': '1f451',
    'shield': '1f6e1',
    'swords': '2694',
    'rocket': '1f680',
    'bolt': '26a1',
    'fire': '1f525',
    'trophy': '1f3c6',
    'star': '2b50',
    'heart': '2764',
    'skull': '1f480',
    'bear': '1f43b',
    'paw': '1f43e',
    'tree': '1f332',
    'wolf': '1f43a',
    'beer': '1f37a',
    'coffee': '2615',
    'wine': '1f377',
    'pizza': '1f355',
    'soccer': '26bd',
    'car': '1f697',
    'brain': '1f9e0',
    'book': '1f4da',
    'sun': '2600',
    'mountain': '26f0', 'volcano': '1f30b', 'island': '1f3dd', 'rainbow': '1f308',
    'moon': '1f319', 'rain': '1f327', 'robot': '1f916', 'satellite': '1f6f0',
    'laptop': '1f4bb', 'graduation': '1f393', 'medal': '1f3c5', 'cat': '1f431',
    'dog': '1f436', 'fish': '1f41f', 'burger': '1f354', 'train': '1f686',
}

# Ordered priority rules: ((keywords...), (left_sticker, right_sticker)).
# First matching theme wins when resolving 'auto' stickers from a video title.
AUTO_STICKER_THEMES: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (
        (
            'bear', 'bears', 'grizzly', 'panda', 'wolf', 'wolves', 'fox', 'deer', 'moose',
            'boar', 'lion', 'tiger', 'dog', 'dogs', 'cat', 'cats', 'pet', 'pets',
            'animal', 'animals', 'wildlife', 'wild', 'forest', 'tree', 'trees', 'woods',
            'nature', 'park', 'hunt', 'bird', 'fish', 'shark', 'snake', 'horse', 'cow',
        ),
        ('bear', 'tree'),
    ),
    (
        (
            'beer', 'beers', 'wine', 'alcohol', 'vodka', 'whiskey', 'drink', 'drinks',
            'pub', 'bar', 'coffee', 'tea', 'cafe', 'pizza', 'burger', 'food', 'cuisine',
            'cheese', 'meat', 'bread', 'milk', 'water',
        ),
        ('beer', 'coffee'),
    ),
    (
        (
            'iq', 'smart', 'intelligent', 'brain', 'genius', 'education', 'school',
            'university', 'student', 'literacy', 'book', 'books', 'read', 'language',
            'languages', 'speak', 'word', 'words', 'alphabet', 'name', 'names',
            'population', 'people', 'human',
        ),
        ('brain', 'book'),
    ),
    (
        (
            'car', 'cars', 'auto', 'vehicle', 'drive', 'driving', 'highway', 'road',
            'roads', 'train', 'rail', 'railway', 'metro', 'bus', 'traffic', 'fuel',
            'gas', 'petrol', 'oil', 'tesla', 'bmw',
        ),
        ('car', 'bolt'),
    ),
    (
        (
            'sport', 'sports', 'football', 'soccer', 'fifa', 'uefa', 'world cup',
            'basketball', 'hockey', 'tennis', 'olympic', 'olympics', 'medal', 'athlete',
            'gym', 'game', 'games', 'chess',
        ),
        ('soccer', 'trophy'),
    ),
    (
        (
            'cold', 'coldest', 'freeze', 'freezing', 'frost', 'ice', 'icy', 'snow',
            'snowy', 'blizzard', 'minus', 'subzero', 'arctic', 'glacier', 'chill',
        ),
        ('snowflake', 'globe'),
    ),
    (
        (
            'weather', 'temperature', 'climate', 'sun', 'sunny', 'hot', 'hottest',
            'heat', 'warm', 'warmest', 'summer', 'rain', 'wind', 'celsius', 'desert',
        ),
        ('sun', 'compass'),
    ),
    (
        (
            'sea', 'ocean', 'river', 'lake', 'coast', 'island', 'water', 'mountain', 'mountains',
        ),
        ('compass', 'globe'),
    ),
    (
        (
            'religion', 'christian', 'catholic', 'orthodox', 'islam', 'muslim', 'church',
            'mosque', 'god', 'faith',
        ),
        ('star', 'globe'),
    ),
    (
        (
            'nicholas', 'santa', 'christmas', 'xmas', 'winter', 'holiday', 'new year',
        ),
        ('santa', 'gift'),
    ),
    (
        (
            'halloween', 'spooky', 'horror',
        ),
        ('pumpkin', 'skull'),
    ),
    (
        (
            'gdp', 'rich', 'poor', 'money', 'salary', 'wage', 'economy', 'tax',
            'price', 'cost', 'billion', 'wealth',
        ),
        ('money', 'chart'),
    ),
    (
        (
            'war', 'army', 'military', 'empire', 'king', 'history', 'battle', 'nato',
        ),
        ('shield', 'swords'),
    ),
    (
        (
            'queen', 'monarch', 'royal', 'president', 'power', 'ruler',
        ),
        ('crown', 'star'),
    ),
    (
        (
            'space', 'nasa', 'tech', 'ai', 'internet', 'speed', 'energy', 'nuclear', 'science',
        ),
        ('rocket', 'bolt'),
    ),
    (
        (
            'travel', 'tourist', 'visa', 'passport', 'flight', 'airport', 'visit',
        ),
        ('plane', 'compass'),
    ),
    (
        (
            'danger', 'crime', 'dead', 'worst', 'risk',
        ),
        ('skull', 'fire'),
    ),
    (
        (
            'top', 'best', 'most', 'highest', 'winner', 'champion', 'rank',
        ),
        ('trophy', 'fire'),
    ),
    (
        (
            'happy', 'happi', 'happiness', 'love', 'health', 'life', 'safe',
        ),
        ('heart', 'star'),
    ),
)

# Short keywords that must match whole words only (e.g. 'ai', 'car', 'top')
# to avoid false substring matches inside unrelated words.
EXACT_WORD_KEYWORDS: set[str] = {
    'ai', 'gdp', 'nato', 'top', 'iq', 'car', 'sun', 'pet', 'dog', 'cat',
    'bar', 'pub', 'tea', 'oil', 'gas', 'bus', 'gym', 'ice', 'hot', 'god',
    'sea',
}

_AUTO_STICKER_THEMES = AUTO_STICKER_THEMES
_EXACT_WORD_KEYWORDS = EXACT_WORD_KEYWORDS
