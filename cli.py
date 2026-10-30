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

import os
from pathlib import Path

import click

FONTS_DIR = Path(__file__).parent / "resources" / "fonts"

# Supported AI models. Extend this list when new models are provided.
AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


def _available_fonts():
    """Return the list of usable font files from resources/fonts."""
    if not FONTS_DIR.is_dir():
        return []
    return sorted(p.name for p in FONTS_DIR.iterdir() if p.suffix.lower() in (".ttf", ".otf"))


@click.group()
def main():
    """AutoMap — create maps from an AI query."""
    pass


@main.command()
@click.argument("prompt")
@click.option("-m", "--mode",
              type=click.Choice(["text", "number"], case_sensitive=False),
              default="text",
              show_default=True,
              help="Data mode: text (default) or number.")
@click.option("-mp", "--map-type",
              "map_type",
              type=click.Choice(["default", "flag", "world"], case_sensitive=False),
              default="default",
              show_default=True,
              help="Map type: default (default), flag or world.")
@click.option("-gsm", "--global-size-modifier",
              "size_mod",
              type=int,
              default=15,
              show_default=True,
              help="Global font size modifier (default 15).")
@click.option("-eop", "--extra-option",
              "optional_feature",
              type=click.Choice(["none", "show_most_least", "short_form"], case_sensitive=False),
              default="none",
              show_default=True,
              help="One extra option: none (default), show_most_least or short_form.")
@click.option("--model",
              type=click.Choice(AVAILABLE_MODELS, case_sensitive=False),
              default=AVAILABLE_MODELS[0],
              show_default=True,
              help="AI model to use.")
@click.option("--font",
              "font_name",
              type=click.Choice(_available_fonts(), case_sensitive=True),
              default="Bips.ttf",
              show_default=True,
              help="Font file name from resources/fonts.")
@click.option("--show/--no-show",
              "show_result",
              default=False,
              show_default=True,
              help="Open the result image after generating.")
@click.option("-v", "--verbose",
              is_flag=True,
              default=False,
              help="Print detailed progress output.")
@click.option("--key",
              "api_key",
              default=None,
              help="Gemini API key for this run (overrides GEMINI_API_KEY env var).")
def make(prompt, mode, map_type, size_mod, optional_feature, model, font_name, show_result, verbose, api_key):
    """Generate a map from PROMPT and save it as result.png.

    Example:

        automap make "Population of European countries" -m number -mp default
    """
    # Lazy import so `automap --help` stays fast and doesn't require API keys.
    from map import log
    from map.auto_map import auto_map

    # Wire up verbose logging before doing any work.
    log.set_verbose(verbose)

    # Normalise the "none" extra option to a real None.
    feature = None if optional_feature.lower() == "none" else optional_feature.lower()

    # A key passed on the command line overrides the env var for this run only.
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key

    out_path = auto_map(
        prompt=prompt,
        mode=mode.lower(),
        map_type=map_type.lower(),
        size_mod=size_mod,
        optional_feature=feature,
        model=model,
        font_name=font_name,
        show_result=show_result,
    )

    if out_path is None:
        raise click.ClickException("Map generation failed. See errors above.")


if __name__ == "__main__":
    main()

