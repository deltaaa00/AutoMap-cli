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

import io
import base64
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from main import app
from map.functions import load_ideas_from_file, parse_ideas_content


@pytest.fixture(autouse=True)
def authenticated_batch_client(monkeypatch):
    from conftest import AuthenticatedWebClient
    monkeypatch.setattr(app, 'test_client_class', AuthenticatedWebClient)


@pytest.mark.parametrize("ext", [".txt", ".md", ".json", ".yaml"])
def test_parse_ideas_rejects_non_csv_extensions(tmp_path: Path, ext: str):
    with pytest.raises(ValueError, match="Only .csv files"):
        parse_ideas_content("prompt\nEurope population\n", filename=f"ideas{ext}")

    file_path = tmp_path / f"ideas{ext}"
    file_path.write_text("prompt\nEurope population\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Only .csv files"):
        load_ideas_from_file(file_path)


@pytest.mark.parametrize(
    "csv_text,match",
    [
        ("prompt,unknown_col\nTest,1\n", "Unknown CSV column"),
        ("mode,map_type\ntext,default\n", "Missing required CSV column"),
        ("prompt,mode\n,text\n", "cannot be empty"),
        ("prompt,mode\nTest,invalid\n", "invalid mode"),
        ("prompt,map_type\nTest,mars\n", "invalid map_type"),
        ("prompt,extra_option\nTest,bad_opt\n", "invalid extra_option"),
        ("prompt,gsm\nTest,abc\n", "invalid gsm"),
        ("prompt,gsm\nTest,101\n", "invalid gsm"),
    ],
)
def test_parse_ideas_validates_schema_and_values(csv_text: str, match: str):
    with pytest.raises(ValueError, match=match):
        parse_ideas_content(csv_text, filename="ideas.csv")


def test_batch_template_endpoint_and_parsing():
    client = app.test_client()
    res = client.get("/api/batch-template")
    assert res.status_code == 200
    assert "text/csv" in res.content_type
    assert "batch_template.csv" in res.headers.get("Content-Disposition", "")

    parsed = parse_ideas_content(res.get_data(as_text=True), filename="batch_template.csv")
    assert len(parsed) == 1
    assert parsed[0]["prompt"]


def test_generate_batch_rejects_invalid_csv_before_ai_call():
    client = app.test_client()
    with patch("main.auto_map") as mock_auto_map:
        res = client.post(
            "/api/generate_batch",
            json={
                "filename": "ideas.txt",
                "ideas_text": "Population of Europe\n",
                "api_key": "test-key",
            },
        )
    assert res.status_code == 400
    assert "Only .csv" in res.get_json()["error"]
    mock_auto_map.assert_not_called()


def test_generate_batch_forwards_temperature_zero_and_row_overrides_and_partial_failure():
    client = app.test_client()
    csv_text = (
        "prompt,mode,map_type,gsm,extra_option,model,font,palette_theme\n"
        "Row One,number,world,22,show_most_least,gemini-3.8-flash,Bips.ttf,ocean\n"
        "Row Two,text,default,15,none,gemini-3.8-flash,Bips.ttf,auto\n"
    )

    def fake_auto_map(**kwargs):
        if kwargs["prompt"] == "Row Two":
            raise RuntimeError("Simulated AI failure")
        buf = io.BytesIO(b"PNGDATA")
        buf.map_meta = {"ai_answer": {"Europe": "10"}}
        return buf

    with patch("main.auto_map", side_effect=fake_auto_map) as mock_auto_map:
        res = client.post(
            "/api/generate_batch",
            json={
                "filename": "ideas.csv",
                "ideas_text": csv_text,
                "temperature": 0,
                "api_key": "test-key",
                "label_suffix": "%",
                "show_legend": True,
                "highlight_extrema": True,
            },
        )

    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert data["total"] == 2
    assert data["succeeded"] == 1

    first_call = mock_auto_map.call_args_list[0].kwargs
    assert first_call["temperature"] == 0.0
    assert first_call["mode"] == "number"
    assert first_call["map_type"] == "world"
    assert first_call["size_mod"] == 22
    assert first_call["optional_feature"] == "show_most_least"
    assert first_call["palette_theme"] == "ocean"
    assert first_call["label_suffix"] == "%"
    assert first_call["show_legend"] and first_call["highlight_extrema"]

    ok_item = data["results"][0]
    assert ok_item["status"] == "success"
    assert ok_item["mode"] == "number"
    assert ok_item["map_type"] == "world"
    assert ok_item["gsm"] == 22
    assert ok_item["temperature"] == 0.0
    assert ok_item["label_suffix"] == "%"
    assert ok_item["show_legend"] and ok_item["highlight_extrema"]

    err_item = data["results"][1]
    assert err_item["status"] == "error"
    assert "Simulated AI failure" in err_item["error"]


def test_index_html_batch_safety_and_studio_sync():
    html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'accept=".csv"' in html
    assert "/api/batch-template" in html
    assert "syncLatestMapToStudioStorage" in html
    assert "selectBtn.textContent" in html
    assert "title=\"${item.prompt" not in html


def test_batch_web_search_reaches_renderer():
    with patch('main.auto_map', return_value=io.BytesIO(b'PNGDATA')) as render:
        response = app.test_client().post('/api/generate_batch', json={
            'filename': 'ideas.csv', 'ideas_text': 'prompt\nSearch regression\n',
            'api_key': 'test-key', 'model': 'deepseek-flash',
            'web_search': True, 'max_search_requests': 3, 'refresh_ai': True,
        })
    assert response.status_code == 200
    assert response.get_json()['succeeded'] == 1
    assert render.call_args.kwargs['web_search'] is True
    assert render.call_args.kwargs['max_search_requests'] == 3


def test_batch_zip_archives_data_urls_with_unique_topic_names():
    image = io.BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(image, format='PNG')
    image_url = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
    response = app.test_client().post('/api/download_batch_zip', json={'items': [
        {'prompt': topic, 'image_url': image_url}
        for topic in ('My topic', 'My topic', 'My topic_2')
    ]})
    assert response.status_code == 200
    assert response.mimetype == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.namelist() == ['My_topic.png', 'My_topic_2.png', 'My_topic_2_2.png']
        assert all(archive.read(name) == image.getvalue() for name in archive.namelist())


@pytest.mark.parametrize('image_url', [
    '/etc/passwd', 'https://example.com/map.png',
    'data:image/png;base64,!!!', 'data:image/png;base64,bm90IGEgcG5n',
])
def test_batch_zip_rejects_invalid_images(image_url):
    response = app.test_client().post('/api/download_batch_zip', json={
        'items': [{'prompt': 'Invalid', 'image_url': image_url}],
    })
    assert response.status_code == 404
