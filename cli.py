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

"""Command-line interface (`automap make` and `automap batch`) for AutoMap."""

import os
from pathlib import Path

import click

FONTS_DIR = Path(__file__).parent / "resources" / "fonts"

# General text-generation models verified against Google's model catalog and
# deprecations on 2026-11-15: https://ai.google.dev/gemini-api/docs/models
AVAILABLE_MODELS = [
    "gemini-3.8-flash",
    "gemini-4-hydrogen",
    "gemini-4-carbon",
    "gemini-4-argon",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
    # Current DeepSeek API model identifiers: https://api-docs.deepseek.com/
    "deepseek-flash",
    "deepseek-v4-pro",
]


def _available_fonts():
    """Return the list of usable font files from resources/fonts."""
    if not FONTS_DIR.is_dir():
        return []
    # Sort filenames so help output and font choices stay stable across systems.
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
              type=click.Choice(["default", "flag", "world", "world_countries"], case_sensitive=False),
              default="default",
              show_default=True,
              help="Map type: default (default), flag, world or world_countries.")
@click.option("-gsm", "--global-size-modifier",
              "size_mod",
              type=int,
              default=15,
              show_default=True,
              help="Global font size modifier (default 15).")
@click.option("-eop", "--extra-option",
              "optional_feature",
              type=click.Choice(["none", "show_most_least", "short_form", "show_most_least,short_form", "short_form,show_most_least"], case_sensitive=False),
              default="none",
              show_default=True,
              help="Extra options: none, show_most_least, short_form, or both separated by a comma.")
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
              help="API key for the selected model provider (overrides its environment key).")
@click.option("--temperature", type=click.FloatRange(0, 2), default=None,
              help="AI sampling randomness, from 0 to 2. Omit to use the model default.")
@click.option("--legend", "show_legend", is_flag=True, help="Draw a color legend on the map.")
@click.option("--highlight-extrema", is_flag=True, help="Highlight the highest and lowest map values.")
@click.option("--suffix", "label_suffix", default="", help="Optional suffix appended to each country label (e.g. m, %, km).")
def make(prompt, mode, map_type, size_mod, optional_feature, model, font_name, show_result, verbose, api_key, temperature, show_legend, highlight_extrema, label_suffix):
    """Generate a map from PROMPT and save it as result.png.

    Example:

        automap make "Population of European countries" -m number -mp default
    """
    # Lazy import so `automap --help` stays fast and doesn't require API keys.
    from map import log
    from map.functions import AIServiceError, provider_for_model, provider_key_name
    from map.auto_map import auto_map

    ai_options = {}
    if show_legend:
        ai_options["show_legend"] = True
    if highlight_extrema:
        ai_options["highlight_extrema"] = True
    if label_suffix:
        ai_options["label_suffix"] = label_suffix
    if temperature is not None:
        from map.functions import validate_temperature
        try:
            ai_options['temperature'] = validate_temperature(temperature)
        except ValueError as error:
            raise click.BadParameter(str(error), param_hint='--temperature') from error

    # Wire up verbose logging before doing any work.
    log.set_verbose(verbose)

    # Normalise the "none" extra option to a real None.
    feature = None if optional_feature.lower() == "none" else optional_feature.lower()

    # A key passed on the command line overrides the env var for this run only.
    if api_key:
        os.environ[provider_key_name(provider_for_model(model))] = api_key

    # Click accepts mixed-case choices; the renderer compares lowercase names.
    try:
        out_path = auto_map(
            prompt=prompt,
            mode=mode.lower(),
            map_type=map_type.lower(),
            size_mod=size_mod,
            optional_feature=feature,
            model=model,
            font_name=font_name,
            show_result=show_result,
            **ai_options,
        )
    except AIServiceError as error:
        raise click.ClickException(str(error)) from error

    if out_path is None:
        # Convert renderer failures into a nonzero exit status for shell scripts.
        raise click.ClickException("Map generation failed. See errors above.")


@main.command()
@click.argument("ideas_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output-dir",
              "output_dir",
              type=click.Path(file_okay=False, path_type=Path),
              default=Path("output_maps"),
              show_default=True,
              help="Directory where generated PNG maps will be saved.")
@click.option("-m", "--mode",
              type=click.Choice(["text", "number"], case_sensitive=False),
              default="text",
              show_default=True,
              help="Default data mode: text (default) or number.")
@click.option("-mp", "--map-type",
              "map_type",
              type=click.Choice(["default", "flag", "world", "world_countries"], case_sensitive=False),
              default="default",
              show_default=True,
              help="Default map type: default, flag, world or world_countries.")
@click.option("-gsm", "--global-size-modifier",
              "size_mod",
              type=int,
              default=15,
              show_default=True,
              help="Default global font size modifier (default 15).")
@click.option("-eop", "--extra-option",
              "optional_feature",
              type=click.Choice(["none", "show_most_least", "short_form"], case_sensitive=False),
              default="none",
              show_default=True,
              help="Default extra option: none, show_most_least or short_form.")
@click.option("--model",
              type=click.Choice(AVAILABLE_MODELS, case_sensitive=False),
              default=AVAILABLE_MODELS[0],
              show_default=True,
              help="Default AI model to use.")
@click.option("--font",
              "font_name",
              type=click.Choice(_available_fonts(), case_sensitive=True),
              default="Bips.ttf",
              show_default=True,
              help="Default font file name from resources/fonts.")
@click.option("-v", "--verbose",
              is_flag=True,
              default=False,
              help="Print detailed progress output.")
@click.option("--key",
              "api_key",
              default=None,
              help="API key for the selected model provider (overrides its environment key).")
def batch(ideas_file, output_dir, mode, map_type, size_mod, optional_feature, model, font_name, verbose, api_key):
    """Generate multiple maps from ideas listed in a .csv IDEAS_FILE."""
    from map import log
    from map.functions import AIServiceError, provider_for_model, provider_key_name
    from map.auto_map import auto_map
    from map.functions import load_ideas_from_file, slugify_prompt

    log.set_verbose(verbose)
    if api_key:
        os.environ[provider_key_name(provider_for_model(model))] = api_key

    try:
        ideas = load_ideas_from_file(ideas_file)
    except ValueError as err:
        raise click.ClickException(str(err)) from err

    if not ideas:
        raise click.ClickException(f"No valid map ideas found in {ideas_file}.")

    output_dir.mkdir(parents=True, exist_ok=True)
    available_fonts = set(_available_fonts())
    succeeded = 0

    for idx, idea in enumerate(ideas, start=1):
        prompt = str(idea["prompt"])
        item_mode = str(idea.get("mode") or mode).lower()
        item_map_type = str(idea.get("map_type") or map_type).lower()
        item_size_mod = int(idea.get("gsm", size_mod))
        raw_eop = str(idea.get("extra_option") or optional_feature).lower()
        item_feature = None if raw_eop == "none" else raw_eop
        item_model = str(idea.get("model") or model)
        item_font = str(idea.get("font") or font_name)
        if item_font not in available_fonts:
            item_font = font_name

        log.step(f"[{idx}/{len(ideas)}] Generating map: {prompt}")
        style_options = {}
        if "palette_theme" in idea:
            style_options["palette_theme"] = idea["palette_theme"]
            style_options["auto_style"] = True
        try:
            buffer = auto_map(
                prompt=prompt,
                mode=item_mode,
                map_type=item_map_type,
                size_mod=item_size_mod,
                optional_feature=item_feature,
                model=item_model,
                font_name=item_font,
                show_result=False,
                io_res=True,
                api_key=api_key if provider_for_model(item_model) == provider_for_model(model) else None,
                **style_options,
            )
        except AIServiceError as error:
            log.warning(str(error))
            continue
        if buffer is None:
            log.warning(f"[{idx}/{len(ideas)}] Failed to generate map for: {prompt}")
            continue

        custom_out = str(idea.get("output") or "").strip()
        filename = Path(custom_out).name if custom_out else f"{idx:02d}_{slugify_prompt(prompt)}.png"
        if not filename.lower().endswith(".png"):
            filename += ".png"
        target = output_dir / filename
        target.write_bytes(buffer.getvalue())
        succeeded += 1
        log.success(f"Saved {target}")

    if succeeded == 0:
        raise click.ClickException("All batch map generations failed.")

    log.finished(f"Generated {succeeded}/{len(ideas)} maps in {output_dir}")


if __name__ == "__main__":
    main()

