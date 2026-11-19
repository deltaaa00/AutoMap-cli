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

import csv
import io
import json
import math
import os
import random
import re
import sys
import requests
try:
    import httpx2 as httpx
except ImportError:  # Older google-genai releases use the original transport.
    import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pathlib import Path

from . import constants, log
from ast import literal_eval

load_dotenv(".env")  # loading .env file

AI_TIMEOUT_MS = 45_000
DEEPSEEK_API_URL = 'https://api.deepseek.com'


def provider_for_model(model):
    """DeepSeek models use their own endpoint and credentials."""
    return 'deepseek' if str(model).lower().startswith('deepseek-') else 'gemini'


def provider_key_name(provider):
    """Return the environment/settings key name for the given AI provider."""
    return 'DEEPSEEK_API_KEY' if provider == 'deepseek' else 'GEMINI_API_KEY'


def configured_api_key(provider, settings=None):
    """Return the configured API key for ``provider``, ignoring empty or ``your_*`` placeholders."""
    name = provider_key_name(provider)
    value = (os.environ.get(name) or (settings or {}).get(name) or '').strip()
    return value if value and not value.lower().startswith('your_') else None


class AIServiceError(RuntimeError):
    """An actionable upstream failure safe to display in the web interface."""

    def __init__(self, message, status_code=503):
        super().__init__(message)
        self.status_code = status_code


def _deepseek_json(path, api_key, *, payload=None, timeout=45):
    """Call only the fixed DeepSeek origin; never expose upstream credential errors."""
    if not api_key:
        raise AIServiceError('DeepSeek API key required. Set DEEPSEEK_API_KEY or enter it in the web form.', 400)
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    try:
        if payload is None:
            response = requests.get(f'{DEEPSEEK_API_URL}/{path}', headers=headers,
                                    timeout=(10, timeout), allow_redirects=False)
        else:
            response = requests.post(f'{DEEPSEEK_API_URL}/{path}', headers=headers, json=payload,
                                     timeout=(10, timeout), allow_redirects=False)
    except requests.Timeout as error:
        raise AIServiceError(f'DeepSeek did not respond within {timeout} seconds. Try again or select another model.', 504) from error
    except requests.RequestException as error:
        raise AIServiceError('Could not connect to DeepSeek. Please try again later.', 503) from error
    try:
        status = response.status_code
        if status in (401, 403):
            raise AIServiceError('DeepSeek rejected the API key. Check your key and account access.', 400)
        if status == 402:
            raise AIServiceError('Your DeepSeek account has insufficient balance. Top up your DeepSeek account.', 402)
        if status == 429:
            raise AIServiceError('DeepSeek request limit reached. Please try again later.', 429)
        if status >= 500:
            raise AIServiceError('DeepSeek is overloaded or temporarily unavailable. Try again shortly or select another model.', 503)
        if status != 200:
            raise AIServiceError('DeepSeek could not process the request. Check the selected model and API access.', 400)
        try:
            result = response.json()
        except ValueError as error:
            raise AIServiceError('DeepSeek returned an invalid response. Please try again.', 502) from error
        if not isinstance(result, dict) or result.get('error'):
            raise AIServiceError('DeepSeek could not complete the request. Please try again.', 502)
        return result
    finally:
        response.close()


def list_deepseek_models(api_key):
    """Return sorted ``deepseek-*`` model IDs available for ``api_key``."""
    result = _deepseek_json('models', api_key, timeout=15)
    models = result.get('data')
    if not isinstance(models, list):
        raise AIServiceError('DeepSeek returned an invalid model list.', 502)
    return sorted({model['id'] for model in models if isinstance(model, dict)
                   and isinstance(model.get('id'), str) and model['id'].startswith('deepseek-')})


def _web_search_ddg(query, max_results=5):
    """Fetch top web search snippets from Wikipedia's public search API without external dependencies."""
    # Use MediaWiki's JSON search API instead of scraping HTML search pages that return HTTP 202 bot challenges.
    try:
        resp = requests.get(
            'https://en.wikipedia.org/w/api.php',
            params={
                'action': 'query',
                'list': 'search',
                'srsearch': str(query),
                'srlimit': int(max_results),
                'format': 'json',
                'utf8': 1,
            },
            headers={'User-Agent': 'AutoMap-cli/1.0 (https://github.com/deltaaa00/AutoMap-cli)'},
            timeout=(5, 10),
        )
        if resp.status_code != 200:
            return f'Search failed (HTTP {resp.status_code}).'
        data = resp.json()
    except (requests.RequestException, ValueError) as err:
        return f'Search error: {err}'

    results = data.get('query', {}).get('search') or []
    lines = []
    for item in results[:max_results]:
        title = str(item.get('title') or '').strip()
        snippet = re.sub(r'<[^>]+>', '', str(item.get('snippet') or '')).replace('&quot;', '"').replace('&amp;', '&').strip()
        if title or snippet:
            lines.append(f'- {title}: {snippet}' if title and snippet else f'- {title or snippet}')
    return '\n'.join(lines) if lines else 'No search results found.'


def _deepseek_request(prompt, model, api_key=None, *, temperature=None, web_search=False, max_search_requests=5):
    """Send a chat-completion request to DeepSeek, optionally running a bounded web_search tool loop."""
    sys_msg = 'Return only a JSON object mapping region names to values. Example: {"ukraine": 42}.'
    if web_search:
        sys_msg += ' You have access to the web_search tool—use it to look up accurate, up-to-date facts and statistics before answering.'
    messages = [
        {'role': 'system', 'content': sys_msg},
        {'role': 'user', 'content': prompt},
    ]
    payload = {
        'model': model,
        'messages': messages,
        'response_format': {'type': 'json_object'},
        'thinking': {'type': 'disabled'},
        'max_tokens': 4096,
        'stream': False,
    }
    if web_search:
        payload['tools'] = [{
            'type': 'function',
            'function': {
                'name': 'web_search',
                'description': 'Search the web for up-to-date facts, rankings, and statistics.',
                'parameters': {
                    'type': 'object',
                    'properties': {'query': {'type': 'string', 'description': 'Search query'}},
                    'required': ['query'],
                },
            },
        }]
    if temperature is not None:
        payload['temperature'] = validate_temperature(temperature)

    # Track remaining search calls so the model cannot loop indefinitely on tool_calls.
    budget = max(1, min(10, int(max_search_requests or 5)))
    key = api_key or configured_api_key('deepseek')
    result = _deepseek_json('chat/completions', key, payload=payload)

    while web_search and budget > 0:
        try:
            choice = result['choices'][0]
            msg = choice['message']
        except (KeyError, IndexError, TypeError) as error:
            raise AIServiceError('DeepSeek returned no map data. Please try again.', 502) from error
        tool_calls = msg.get('tool_calls') or []
        if choice.get('finish_reason') != 'tool_calls' or not tool_calls:
            break

        # Append the assistant's tool-call turn, then answer each tool call in order.
        messages.append(msg)
        for tc in tool_calls:
            if budget <= 0:
                messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': 'Search budget reached. Return final JSON now.'})
                continue
            budget -= 1
            try:
                args = json.loads(tc.get('function', {}).get('arguments') or '{}')
                q = args.get('query') or prompt
            except ValueError:
                q = prompt
            messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': _web_search_ddg(q)})

        # Once the search budget is spent, strip `tools` so DeepSeek must return the final JSON.
        if budget <= 0:
            payload.pop('tools', None)
        result = _deepseek_json('chat/completions', key, payload=payload)
    try:
        choice = result['choices'][0]
        content = choice['message']['content']
    except (KeyError, IndexError, TypeError) as error:
        raise AIServiceError('DeepSeek returned no map data. Please try again.', 502) from error
    if choice.get('finish_reason') == 'length':
        raise AIServiceError('DeepSeek returned an incomplete map. Try a shorter prompt.', 502)
    if not isinstance(content, str) or not content.strip():
        raise AIServiceError('DeepSeek returned no map data. Please try again.', 502)
    return content


def _error(title: str, message: str = ""):
    """Print an error message to stderr (CLI-safe replacement for tkinter showerror)."""
    log.error(f"{title}: {message}" if message else title)
def _warning(title: str, message: str = ""):
    """Print a warning message (CLI-safe replacement for tkinter showwarning)."""
    log.warning(f"{title}: {message}" if message else title)
# mode selecting

def validate_temperature(value: object) -> float:
    """Accept finite Gemini sampling temperatures in the supported 0–2 range."""
    message = "Temperature must be a finite number between 0 and 2."
    if isinstance(value, bool):
        raise ValueError(message)
    try:
        temperature = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(message) from exc
    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise ValueError(message)
    return temperature


def world_countries_prompt_template() -> str:
    """Return the AI dictionary template for the ``world_countries`` map.

    ``world_countries_prompt`` may be a ready template string or a mapping of
    country keys to (empty) placeholder values; both render the same style as
    ``constants.world``.
    """
    from .world_countries_coords import world_countries_prompt

    if isinstance(world_countries_prompt, str):
        return world_countries_prompt
    body = ",\n".join(f'        "{key}":' for key in world_countries_prompt)
    return "{\n" + body + "\n    }"


def _ai_request(prompt, mode, ai_model, api_key=None, *, temperature=None, web_search=False, max_search_requests=5) -> str | None:
    """Send a request to the AI model and return the response as a string."""

    exclusions = "Dont include luxembourg"
    if mode == 'default' or mode == 'flag':
        dict_ = constants.europe
    elif mode == 'world':
        dict_ = constants.world
    elif mode == 'world_countries':
        dict_ = world_countries_prompt_template()
        exclusions = "Use exactly the keys of the dictionary below."
    else:
        raise ValueError(f"Unknown map type: {mode}")

    # request data
    prompt_to_ai = f"""Fill in a dictionary where the key """ + str(prompt) + f"""
    Search the internet and return the result as JSON. Dont write 'json' in start

    If there is no exact data for a country or continent, use the approximate value.
                
    Provide the answer only as a dictionary, without explanations.

    {exclusions}
                        
    {dict_}
    """

    config = {} if temperature is None else {
        'config': types.GenerateContentConfig(temperature=validate_temperature(temperature)),
    }
    if provider_for_model(ai_model) == 'deepseek':
        with log.spinner('Waiting for DeepSeek response...'):
            return _deepseek_request(
                prompt_to_ai, ai_model, api_key,
                temperature=temperature, web_search=web_search, max_search_requests=max_search_requests,
            )
    try:
        with log.spinner("Waiting for AI response..."):
            # If a key was supplied (e.g. from the web UI when .env is missing),
            # use it for this request only; otherwise fall back to the env var.
            options = {'http_options': types.HttpOptions(
                timeout=AI_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1),
            )}
            if api_key:
                options['api_key'] = api_key
            client = genai.Client(**options)
            request = client.models.generate_content(
                model=ai_model,
                contents=prompt_to_ai,
                **config,
            )


    except genai_errors.APIError as error:
        if error.code in (500, 502, 503, 504):
            raise AIServiceError(
                f'The selected model ({ai_model}) is overloaded or temporarily unavailable. '
                'Please try again shortly or select another model.', 503,
            ) from error
        if error.code == 429:
            raise AIServiceError(
                'The AI request limit or quota has been reached. Please try again later '
                'or check the quota for your API key.', 429,
            ) from error
        if error.code in (400, 401, 403, 404):
            raise AIServiceError(
                f'The selected model ({ai_model}) could not be used. '
                'Refresh the model list and check your API key and model access.', 400,
            ) from error
        raise AIServiceError('The AI service could not complete this request. Please try again.') from error
    except (httpx.TimeoutException, TimeoutError) as error:
        raise AIServiceError(
            'The AI model did not respond within 45 seconds. '
            'Please try again or select a faster model.', 504,
        ) from error
    except Exception as e:
        _error(f"AI request failed: {e}")
        return None

    return request.text


def ai_request(prompt: str, mode: str, ai_model: str, api_key: str | None = None, _ai_answer = None,
               *, temperature: float | None = None, web_search: bool = False, max_search_requests: int = 5) -> dict | None:
    "handle the AI request and parse the response into a dictionary."
    if _ai_answer is not None:
        request = _ai_answer
    else:
        options = {} if temperature is None else {'temperature': temperature}
        if web_search:
            options['web_search'] = True
            options['max_search_requests'] = max_search_requests
        request = _ai_request(prompt, mode, ai_model, api_key, **options)

    # The AI request can fail and return None (the error is already logged).
    # Guard against that to avoid "AttributeError: 'NoneType' object has no attribute 'strip'".
    if not request:
        _error("AI request failed", "empty response from the AI model")
        return None

    try:
        # Models may wrap otherwise valid JSON in a Markdown code fence.
        json_style = (
            request.strip()
            .removeprefix("```json")
            .removeprefix("```python")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        country_dict = json.loads(json_style)
    except json.JSONDecodeError:
        _error(f"Failed to convert into dictionary. Result {json_style}")
        print("Result:", json_style)
        return None
    except (AttributeError, TypeError) as e:
        _error("AI response parsing error", str(e))
        return None

    if not isinstance(country_dict, dict):
        _error("AI response parsing error", "expected a JSON object mapping regions to values")
        return None

    return country_dict


IDEA_EXAMPLES = (
    "How many bears are there in your country?",
    "How many school years children must study in your country?",
    "What is the most popular drink in your country?",
    "What is the biggest city in your country?",
    "How many McDonald's restaurants are there in your country?",
    "What is the most popular car brand in your country?",
    "How many UNESCO World Heritage sites are there in your country?",
    "What is the minimum driving age in your country?",
    "What is the most popular sport in your country?",
    "How many hours do people work per week in your country?",
)


def _call_model_text(
    system_msg: str,
    user_prompt: str,
    ai_model: str,
    api_key: str | None = None,
    *,
    temperature: float | None = None,
    json_mode: bool = False,
) -> str:
    """Send a lightweight prompt to the selected Gemini or DeepSeek model and return raw text."""
    if provider_for_model(ai_model) == 'deepseek':
        payload: dict[str, object] = {
            'model': ai_model,
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_prompt},
            ],
            'thinking': {'type': 'disabled'},
            'max_tokens': 512,
            'stream': False,
        }
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        if temperature is not None:
            payload['temperature'] = validate_temperature(temperature)
        key = api_key or configured_api_key('deepseek')
        result = _deepseek_json('chat/completions', key, payload=payload)
        try:
            content = result['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as error:
            raise AIServiceError('DeepSeek returned no idea. Please try again.', 502) from error
        if not isinstance(content, str) or not content.strip():
            raise AIServiceError('DeepSeek returned an empty idea. Please try again.', 502)
        return content.strip()

    try:
        options: dict[str, object] = {
            'http_options': types.HttpOptions(
                timeout=AI_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        }
        if api_key:
            options['api_key'] = api_key
        client = genai.Client(**options)
        cfg_kwargs: dict[str, object] = {}
        if temperature is not None:
            cfg_kwargs['temperature'] = validate_temperature(temperature)
        if json_mode:
            cfg_kwargs['response_mime_type'] = 'application/json'
        config = {'config': types.GenerateContentConfig(**cfg_kwargs)} if cfg_kwargs else {}
        response = client.models.generate_content(
            model=ai_model,
            contents=f"{system_msg}\n\n{user_prompt}",
            **config,
        )
    except genai_errors.APIError as error:
        if error.code in (500, 502, 503, 504):
            raise AIServiceError(
                f'The selected model ({ai_model}) is overloaded or temporarily unavailable. '
                'Please try again shortly or select another model.', 503,
            ) from error
        if error.code == 429:
            raise AIServiceError(
                'The AI request limit or quota has been reached. Please try again later '
                'or check the quota for your API key.', 429,
            ) from error
        if error.code in (400, 401, 403, 404):
            raise AIServiceError(
                f'The selected model ({ai_model}) could not be used. '
                'Refresh the model list and check your API key and model access.', 400,
            ) from error
        raise AIServiceError('The AI service could not complete this request. Please try again.') from error
    except (httpx.TimeoutException, TimeoutError) as error:
        raise AIServiceError(
            'The AI model did not respond within 45 seconds. '
            'Please try again or select a faster model.', 504,
        ) from error

    text = getattr(response, 'text', None)
    if not isinstance(text, str) or not text.strip():
        raise AIServiceError('The AI model returned an empty idea. Please try again.', 502)
    return text.strip()


IDEA_CATEGORIES = (
    "food, drinks, and dining habits",
    "driving, traffic, and public transport",
    "work, salaries, holidays, and retirement",
    "language, slang, names, and alphabets",
    "housing, everyday prices, and cost of living",
    "climate, weather, nature, and wildlife",
    "school, education, and university life",
    "sports, hobbies, and pop culture",
    "internet, technology, and gaming",
    "health, sleep, and daily routines",
    "unusual laws, rules, and traditions",
    "cities, borders, and surprising geography",
)


def _normalize_idea_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def generate_map_idea(
    ai_model: str,
    api_key: str | None = None,
    *,
    with_params: bool = False,
    available_themes: list[str] | tuple[str, ...] | None = None,
    available_fonts: list[str] | tuple[str, ...] | None = None,
    temperature: float | None = None,
    avoid_ideas: list[str] | tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Generate a single map idea (and optionally matching map parameters) via the selected AI model."""
    sampled_examples = random.sample(IDEA_EXAMPLES, min(6, len(IDEA_EXAMPLES)))
    examples_block = "\n".join(f'- "{ex}"' for ex in sampled_examples)
    cleaned_avoid = [str(item).strip() for item in (avoid_ideas or ()) if str(item).strip()][:30]
    avoid_keys = {_normalize_idea_key(item) for item in cleaned_avoid}
    avoid_block = ""
    if cleaned_avoid:
        avoid_lines = "\n".join(f'- "{item}"' for item in cleaned_avoid[-20:])
        avoid_block = (
            "\n\nIMPORTANT — Do NOT repeat or closely rephrase any of these already-used ideas:\n"
            f"{avoid_lines}"
        )
    idea_temp = 1.0 if temperature is None else validate_temperature(temperature)

    if not with_params:
        system_msg = (
            "You generate short, engaging, curiosity-sparking map topic questions in English. "
            "Always pick a fresh, surprising angle and never repeat recent topics. "
            "Return ONLY the idea itself as a single plain-text sentence/question, with no quotes, "
            "no numbering, no markdown, and no explanation."
        )
        idea = ""
        for attempt in range(2):
            focus_category = random.choice(IDEA_CATEGORIES)
            nonce = random.randint(1000, 9999)
            retry_note = "\nPrevious candidate was a duplicate — choose a completely different subject." if attempt else ""
            user_prompt = (
                f"Generate 1 fresh, creative map topic idea focused on: {focus_category} (variation #{nonce}).\n"
                "Match the concise style of these examples without copying them:\n"
                f"{examples_block}"
                f"{avoid_block}"
                f"{retry_note}\n\n"
                "Return only the idea text."
            )
            raw = _call_model_text(system_msg, user_prompt, ai_model, api_key, temperature=idea_temp, json_mode=False)
            candidate = next(iter(raw.strip().splitlines()), '').strip().strip('"\'` ')
            if not candidate:
                raise AIServiceError('The AI model returned an empty idea. Please try again.', 502)
            idea = candidate
            if _normalize_idea_key(candidate) not in avoid_keys:
                break
        return {"idea": idea}

    raw_themes = [str(t) for t in (available_themes or list(constants._PALETTE_THEMES.keys()))]
    themes = [t for t in raw_themes if t not in ("auto", "ocean", "emerald")] or raw_themes
    fonts = [str(f) for f in (available_fonts or ["Bips.ttf", "DIN Condensed.ttf", "kodchasan.ttf", "arialroundedmtbold.ttf"])]
    allowed_map_types = ("default", "flag", "world", "world_countries")
    allowed_modes = ("text", "number")
    allowed_extras = ("none", "show_most_least", "show_short_form")

    system_msg = (
        "You generate short, engaging map topic questions in English along with the best map rendering parameters. "
        "Always pick a fresh, surprising angle and never repeat recent topics. "
        "Return ONLY a valid JSON object (no markdown fences, no commentary)."
    )
    parsed = None
    idea = ""
    for attempt in range(2):
        focus_category = random.choice(IDEA_CATEGORIES)
        nonce = random.randint(1000, 9999)
        retry_note = "\nPrevious candidate was a duplicate — choose a completely different subject." if attempt else ""
        user_prompt = (
            f"Generate 1 fresh, creative map topic idea focused on: {focus_category} (variation #{nonce}).\n"
            "Match the concise style of these examples without copying them:\n"
            f"{examples_block}"
            f"{avoid_block}"
            f"{retry_note}\n\n"
            "Return a JSON object with these exact keys:\n"
            '- "idea": string (the single map question/topic, no surrounding quotes)\n'
            f'- "mode": one of {list(allowed_modes)} ("number" for numeric counts/amounts/years, "text" for names/words/categories)\n'
            f'- "map_type": one of {list(allowed_map_types)} ("default" = Europe painted, "flag" = Europe flags, "world" = continents, "world_countries" = all world countries)\n'
            f'- "palette_theme": one of {themes}\n'
            f'- "font": one of {fonts}\n'
            f'- "extra_option": one of {list(allowed_extras)} ("show_most_least" or "show_short_form" only when mode is "number" and map_type is not "world", otherwise "none")\n'
            '- "show_legend": boolean (true if a legend helps explain the map, false otherwise)'
        )
        raw = _call_model_text(system_msg, user_prompt, ai_model, api_key, temperature=idea_temp, json_mode=True)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            candidate_obj = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise AIServiceError('AI returned invalid JSON for the map idea. Please try again.', 502) from error
        if not isinstance(candidate_obj, dict):
            raise AIServiceError('AI returned an unexpected format for the map idea.', 502)
        candidate_idea = str(candidate_obj.get("idea") or "").strip().strip('"\'` ')
        if not candidate_idea:
            raise AIServiceError('The AI model returned an empty idea. Please try again.', 502)
        parsed = candidate_obj
        idea = candidate_idea
        if _normalize_idea_key(candidate_idea) not in avoid_keys:
            break

    mode = str(parsed.get("mode") or "text").strip().lower()
    if mode not in allowed_modes:
        mode = "text"
    map_type = str(parsed.get("map_type") or "default").strip().lower()
    if map_type not in allowed_map_types:
        map_type = "default"
    palette_theme = str(parsed.get("palette_theme") or "auto").strip()
    if palette_theme not in themes:
        palette_theme = "auto" if "auto" in themes else themes[0]
    font = str(parsed.get("font") or fonts[0]).strip()
    if font not in fonts:
        font = fonts[0]
    extra_option = str(parsed.get("extra_option") or "none").strip().lower()
    if extra_option not in allowed_extras or mode != "number" or map_type == "world":
        extra_option = "none"
    show_legend = bool(parsed.get("show_legend", False))

    return {
        "idea": idea,
        "params": {
            "mode": mode,
            "map_type": map_type,
            "palette_theme": palette_theme,
            "font": font,
            "extra_option": extra_option,
            "show_legend": show_legend,
        },
    }





def load_filling():
    """Load text and numeric color-filling rules from ``filling.json`` (or ``filling.template.json``).

    Returns ``(filling_txt, filling_num)`` where ``filling_txt`` maps label strings
    to RGBA tuples and ``filling_num`` maps ``(low, high)`` numeric bounds to RGBA tuples.
    """

    path = Path(__file__).parent / "filling.json"
    if not path.is_file():
        path = path.with_name("filling.template.json")

    filling_txt = {}
    filling_num = {}

    log.info(f"Loading filling config from {path}")

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)


        raw_txt = data.get("filling_txt", {})
        # JSON stores colors as arrays; Pillow also accepts these tuple values.
        filling_txt = {k: tuple(v) for k, v in raw_txt.items()}


        raw_num = data.get("filling_num", {})
        for key_str, color_list in raw_num.items():
            try:
                # JSON object keys are strings; recover the (low, high) range
                # with literal_eval, which parses literals without executing code.
                tuple_key = literal_eval(key_str)
                filling_num[tuple_key] = tuple(color_list)
            except (ValueError, SyntaxError):
                log.warning(f"Invalid JSON Key (see docs): {key_str}")

        log.success(
            f"Filling config loaded ({len(filling_txt)} text, {len(filling_num)} number rules)"
        )

    except Exception as e:
        _error("JSON reading error", str(e))

    

    return filling_txt, filling_num


_SLUG_RE = re.compile(r'[^a-zA-Z0-9]+')

BATCH_CSV_REQUIRED_COLUMNS: tuple[str, ...] = ("prompt",)
BATCH_CSV_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "mode",
    "map_type",
    "gsm",
    "extra_option",
    "model",
    "font",
    "palette_theme",
    "output",
)
BATCH_CSV_ALL_COLUMNS: tuple[str, ...] = BATCH_CSV_REQUIRED_COLUMNS + BATCH_CSV_OPTIONAL_COLUMNS
_ALLOWED_MODES = frozenset({"text", "number"})
_ALLOWED_MAP_TYPES = frozenset({"default", "flag", "world", "world_countries"})
_ALLOWED_EXTRA_OPTIONS = frozenset({"none", "show_most_least", "short_form", "show_most_least,short_form", "short_form,show_most_least"})


def slugify_prompt(prompt: str, max_len: int = 48) -> str:
    """Convert a map prompt into a safe, concise filename stem."""
    slug = _SLUG_RE.sub('_', prompt.strip().lower()).strip('_')
    return (slug[:max_len].strip('_') or 'map')


def parse_ideas_content(content: str, filename: str = "") -> list[dict[str, object]]:
    """Parse and validate map ideas from a .csv file."""
    if filename and Path(filename).suffix.lower() != ".csv":
        raise ValueError("Only .csv files are supported for batch ideas.")

    text = (content or "").lstrip("\ufeff").strip()
    if not text:
        raise ValueError("CSV file is empty.")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file is missing a header row.")

    headers = [str(h).strip().lower() for h in reader.fieldnames]
    if any(not h for h in headers):
        raise ValueError("CSV header contains an empty column name.")
    if len(set(headers)) != len(headers):
        raise ValueError("CSV header contains duplicate column names.")

    unknown_cols = [h for h in headers if h not in BATCH_CSV_ALL_COLUMNS]
    if unknown_cols:
        raise ValueError(
            f"Unknown CSV column(s): {', '.join(unknown_cols)}. "
            f"Allowed columns: {', '.join(BATCH_CSV_ALL_COLUMNS)}."
        )

    missing_cols = [c for c in BATCH_CSV_REQUIRED_COLUMNS if c not in headers]
    if missing_cols:
        raise ValueError(f"Missing required CSV column(s): {', '.join(missing_cols)}.")

    reader.fieldnames = headers
    ideas: list[dict[str, object]] = []
    for row_num, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"Row {row_num}: malformed CSV row (unexpected extra columns).")

        prompt = str(row.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Row {row_num}: 'prompt' is required and cannot be empty.")

        entry: dict[str, object] = {"prompt": prompt}

        mode_val = str(row.get("mode") or "").strip().lower()
        if mode_val:
            if mode_val not in _ALLOWED_MODES:
                raise ValueError(
                    f"Row {row_num}: invalid mode '{mode_val}'. Allowed: {', '.join(sorted(_ALLOWED_MODES))}."
                )
            entry["mode"] = mode_val

        map_type_val = str(row.get("map_type") or "").strip().lower()
        if map_type_val:
            if map_type_val not in _ALLOWED_MAP_TYPES:
                raise ValueError(
                    f"Row {row_num}: invalid map_type '{map_type_val}'. Allowed: {', '.join(sorted(_ALLOWED_MAP_TYPES))}."
                )
            entry["map_type"] = map_type_val

        extra_val = str(row.get("extra_option") or "").strip().lower()
        if extra_val:
            if extra_val not in _ALLOWED_EXTRA_OPTIONS:
                raise ValueError(
                    f"Row {row_num}: invalid extra_option '{extra_val}'. Allowed: {', '.join(sorted(_ALLOWED_EXTRA_OPTIONS))}."
                )
            entry["extra_option"] = extra_val

        gsm_raw = str(row.get("gsm") or "").strip()
        if gsm_raw:
            try:
                gsm_val = int(gsm_raw)
            except ValueError as exc:
                raise ValueError(
                    f"Row {row_num}: invalid gsm '{gsm_raw}'. Must be an integer between 1 and 100."
                ) from exc
            if gsm_val < 1 or gsm_val > 100:
                raise ValueError(
                    f"Row {row_num}: invalid gsm '{gsm_raw}'. Must be an integer between 1 and 100."
                )
            entry["gsm"] = gsm_val

        for key in ("model", "font", "palette_theme", "output"):
            val = str(row.get(key) or "").strip()
            if val:
                entry[key] = val

        ideas.append(entry)

    if not ideas:
        raise ValueError("CSV file contains no data rows.")

    return ideas


def load_ideas_from_file(file_path: str | Path) -> list[dict[str, object]]:
    """Read and parse map ideas from a .csv file."""
    path = Path(file_path)
    if path.suffix.lower() != ".csv":
        raise ValueError("Only .csv files are supported for batch ideas.")
    content = path.read_text(encoding="utf-8")
    return parse_ideas_content(content, filename=path.name)




