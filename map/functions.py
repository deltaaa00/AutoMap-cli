import json
import sys
from dotenv import load_dotenv
from google import genai
from pathlib import Path

from . import constants, log
from ast import literal_eval

load_dotenv(".env")  # loading .env file


def _error(title: str, message: str = ""):
    """Print an error message to stderr (CLI-safe replacement for tkinter showerror)."""
    log.error(f"{title}: {message}" if message else title)
def _warning(title: str, message: str = ""):
    """Print a warning message (CLI-safe replacement for tkinter showwarning)."""
    log.warning(f"{title}: {message}" if message else title)
# mode selecting

def _ai_request(prompt, mode, ai_model, api_key=None) -> str | None:
    """Send a request to the AI model and return the response as a string."""

    if mode == 'default' or mode == 'flag':
        dict_ = constants.europe
    elif mode == 'world':
        dict_ = constants.world

    # request data
    prompt_to_ai = f"""Fill in a dictionary where the key """ + str(prompt) + f"""
    Search the internet and return the result as JSON. Dont write 'json' in start

    If there is no exact data for a country or continent, use the approximate value.
                
    Provide the answer only as a dictionary, without explanations.

    Dont include luxembourg
                        
    {dict_}
    """

    try:
        with log.spinner("Waiting for AI response..."):
            # If a key was supplied (e.g. from the web UI when .env is missing),
            # use it for this request only; otherwise fall back to the env var.
            client = genai.Client(api_key=api_key) if api_key else genai.Client()
            request = client.models.generate_content(
                model=ai_model,
                contents=prompt_to_ai,
            )


    except Exception as e:
        _error(f"AI request failed: {e}")
        return None

    return request.text


def ai_request(prompt: str, mode: str, ai_model: str, api_key: str | None = None, _ai_answer = None) -> dict | None:
    "handle the AI request and parse the response into a dictionary."
    if _ai_answer is not None:
        request = _ai_answer
    else:
        request = _ai_request(prompt, mode, ai_model, api_key)

    # The AI request can fail and return None (the error is already logged).
    # Guard against that to avoid "AttributeError: 'NoneType' object has no attribute 'strip'".
    if not request:
        _error("AI request failed", "empty response from the AI model")
        return None

    try:
        json_style = request.strip().replace("```json", "").replace("```", "").strip()
        country_dict = json.loads(json_style)

        return country_dict
    except json.JSONDecodeError:
        try:
            json_style = request.strip().replace("```python", "").replace("```", "").strip()
            country_dict = json.loads(json_style)


            return country_dict

        except json.JSONDecodeError:
            _error(f"Failed to convert into dictionary. Result {json_style}")
            print("Result:", json_style)
            return None
    except (AttributeError, TypeError) as e:
        _error("AI response parsing error", str(e))
        return None





def load_filling():

    path = Path(__file__).parent / "filling.json"

    filling_txt = {}
    filling_num = {}

    log.info(f"Loading filling config from {path}")

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)


        raw_txt = data.get("filling_txt", {})
        filling_txt = {k: tuple(v) for k, v in raw_txt.items()}


        raw_num = data.get("filling_num", {})
        for key_str, color_list in raw_num.items():
            try:
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



