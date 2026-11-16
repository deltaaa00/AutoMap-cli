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

"""Tests for the ``world_countries`` map type across renderer, CLI, CSV, and web API."""
import importlib
import io
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from PIL import Image, ImageDraw

import cli
from map import functions
from map.functions import parse_ideas_content

renderer = importlib.import_module("map.auto_map")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world_countries_scene(monkeypatch, tmp_path):
    """Stub ``world_countries`` artwork and coordinates for fast deterministic tests."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(renderer, "__file__", str(tmp_path / "map" / "auto_map.py"))

    base = Image.new("RGBA", (640, 480), "white")
    draw = ImageDraw.Draw(base)
    draw.rectangle((0, 0, 209, 479), fill=(10, 80, 180, 255))
    draw.rectangle((211, 0, 419, 479), fill=(220, 40, 60, 255))
    draw.rectangle((421, 0, 639, 479), fill=(20, 160, 90, 255))
    draw.line((210, 0, 210, 479), fill="black")
    draw.line((420, 0, 420, 479), fill="black")

    relief = Image.new("RGBA", base.size, (120, 120, 120, 200))
    borders = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(borders).line((210, 0, 210, 479), fill=(0, 0, 0, 255))

    opened_names: list[str] = []
    real_open = Image.open

    def open_image(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and str(path).endswith(".png"):
            name = Path(path).name
            opened_names.append(name)
            if name == "world_countries_map.png":
                return base.copy()
            if name == "world_map_relief.png":
                return relief.copy()
            if name == "world_countries_map_blured.png":
                return borders.copy()
            return Image.new("RGBA", base.size, (0, 0, 0, 0))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Image, "open", open_image)

    stub_mod = types.ModuleType("map.world_countries_coords")
    stub_mod.world_countries_coords = {
        "united_states": [(80, 120)],
        "france": [(300, 120)],
        "monaco": [(500, 120)],
    }
    stub_mod.world_countries_label_sizes = {
        "united_states": 48,
        "france": 32,
        "monaco": 0,
    }
    stub_mod.world_countries_prompt = {
        "united_states": "",
        "france": "",
        "monaco": "",
    }
    monkeypatch.setitem(sys.modules, "map.world_countries_coords", stub_mod)

    monkeypatch.setattr(
        renderer,
        "load_filling",
        lambda: (
            {"yes": (255, 0, 0, 255), "no": (0, 0, 255, 255)},
            {(0, 100): (0, 255, 0, 255), (100, 1000): (255, 255, 0, 255)},
        ),
    )
    return real_open, opened_names, stub_mod


def test_world_countries_renders_text_mode_with_case_space_and_small_label_skip(
    world_countries_scene, monkeypatch, tmp_path
):
    real_open, opened_names, _ = world_countries_scene
    monkeypatch.setenv("AUTOMAP_REGION_CACHE_DIR", str(tmp_path / "rcache"))

    drawn_texts: list[tuple[tuple[int, int], str, dict]] = []
    orig_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, text, *args, **kwargs):
        drawn_texts.append((xy, text, dict(kwargs)))
        return orig_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)

    ai_mock = Mock(return_value={"United States": "Yes", " france ": "No", "Monaco": "Yes"})
    monkeypatch.setattr(renderer, "ai_request", ai_mock)

    output = renderer.auto_map(
        prompt="Coffee or tea",
        mode="text",
        map_type="world_countries",
        size_mod=0,
        optional_feature=None,
        model="mock-model",
        font_name="Bips.ttf",
        show_result=False,
        io_res=True,
        api_key="test-key",
    )

    assert isinstance(output, io.BytesIO)
    image = real_open(output)
    assert image.format == "PNG"
    assert image.mode == "RGBA"
    assert image.size == (640, 480)

    # Relief and blurred border assets were loaded and composited.
    assert "world_countries_map.png" in opened_names
    assert "world_map_relief.png" in opened_names
    assert "world_countries_map_blured.png" in opened_names

    # Flag artwork is preserved (not overwritten by filling_txt red/blue).
    us_px = image.getpixel((80, 400))
    fr_px = image.getpixel((300, 400))
    mc_px = image.getpixel((500, 400))
    assert us_px[2] > us_px[0] and us_px[3] == 255
    assert fr_px[0] > fr_px[2] and fr_px[3] == 255
    assert mc_px[1] > mc_px[0] and mc_px[3] == 255

    # Small country with label size 0 ("monaco") received no label.
    labels = [t for _, t, _ in drawn_texts]
    assert labels == ["Yes", "No"]
    assert all(kw.get("anchor") == "mm" for _, _, kw in drawn_texts)
    ai_mock.assert_called_once_with("Coffee or tea", "world_countries", "mock-model", "test-key")


def test_world_countries_number_mode_and_show_most_least(world_countries_scene, monkeypatch, tmp_path):
    real_open, _, _ = world_countries_scene
    monkeypatch.setenv("AUTOMAP_REGION_CACHE_DIR", str(tmp_path / "rcache"))

    drawn_texts: list[str] = []
    orig_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, text, *args, **kwargs):
        drawn_texts.append(text)
        return orig_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)

    output = renderer.auto_map(
        prompt="Score",
        mode="number",
        map_type="world_countries",
        size_mod=0,
        optional_feature="show_most_least",
        model="mock-model",
        font_name="Bips.ttf",
        show_result=False,
        io_res=True,
        ai_answer_override={"USA": 500, "France": 50, "monaco": 10},
    )

    assert isinstance(output, io.BytesIO)
    image = real_open(output)
    assert image.mode == "RGBA"
    assert image.size == (640, 480)
    # show_most_least draws the extrema caption in addition to country labels.
    assert any("Most:" in text and "Least:" in text for text in drawn_texts)
    assert "500" in drawn_texts and "50" in drawn_texts


def test_world_countries_prompt_template_used_in_ai_request(world_countries_scene, monkeypatch):
    captured: dict[str, object] = {}

    class FakeModels:
        def generate_content(self, *, model, contents, **kwargs):
            captured["contents"] = contents
            return types.SimpleNamespace(text='{"united_states": "Yes"}')

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(functions.genai, "Client", FakeClient)
    res = functions.ai_request("Test prompt", "world_countries", "gemini-4-hydrogen", api_key="k")
    assert res == {"united_states": "Yes"}
    assert '"united_states":' in str(captured["contents"])
    assert '"monaco":' in str(captured["contents"])


def test_csv_cli_api_and_ui_accept_world_countries(web, monkeypatch):
    parsed = parse_ideas_content("prompt,map_type\nGDP,world_countries\n", filename="ideas.csv")
    assert parsed == [{"prompt": "GDP", "map_type": "world_countries"}]

    generate = Mock(return_value=Path("result.png"))
    monkeypatch.setattr(renderer, "auto_map", generate)
    cli_res = CliRunner().invoke(cli.main, ["make", "GDP", "-mp", "world_countries"])
    assert cli_res.exit_code == 0, cli_res.output
    assert generate.call_args.kwargs["map_type"] == "world_countries"

    web_mock = Mock(return_value=io.BytesIO(b"png-data"))
    monkeypatch.setattr(web, "auto_map", web_mock)
    resp = web.app.test_client().post(
        "/api/generate",
        json={
            "topic": "GDP",
            "mode": "number",
            "map_type": "world_countries",
            "model": "mock-model",
            "api_key": "k",
        },
    )
    assert resp.status_code == 200
    assert web_mock.call_args.kwargs["map_type"] == "world_countries"
    assert "-mp world_countries" in resp.json["command"]

    bad_resp = web.app.test_client().post(
        "/api/generate",
        json={
            "topic": "GDP",
            "mode": "number",
            "map_type": "atlantis",
            "model": "mock-model",
            "api_key": "k",
        },
    )
    assert bad_resp.status_code == 400

    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'value="world_countries"' in html
    assert "World Countries" in html


def test_world_countries_real_assets_render_if_present():
    """Verify full 3424x2027 RGBA render against real mapart assets when present."""
    map_img = ROOT / "resources" / "maps" / "world_countries_map.png"
    blured_img = ROOT / "resources" / "maps" / "world_countries_map_blured.png"
    coords_file = ROOT / "map" / "world_countries_coords.py"
    if not (map_img.is_file() and blured_img.is_file() and coords_file.is_file()):
        pytest.skip("world_countries assets not yet created by @mapart")

    from map.world_countries_coords import (
        world_countries_coords,
        world_countries_label_sizes,
        world_countries_prompt,
    )

    assert len(world_countries_coords) > 100
    assert set(world_countries_label_sizes) >= set(world_countries_coords)
    assert set(world_countries_prompt) >= set(world_countries_coords)

    sample = {k: str((i % 5) + 1) for i, k in enumerate(world_countries_coords)}
    output = renderer.auto_map(
        prompt="Test",
        mode="number",
        map_type="world_countries",
        size_mod=0,
        optional_feature=None,
        model="mock-model",
        font_name="Bips.ttf",
        show_result=False,
        io_res=True,
        ai_answer_override=sample,
    )
    assert isinstance(output, io.BytesIO)
    img = Image.open(output)
    assert img.mode == "RGBA"
    assert img.size == (3424, 2027)
