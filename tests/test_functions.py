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

"""Regression fixture for the original saved AI response."""
import json
import pytest
from pathlib import Path

from map.functions import ai_request, parse_ideas_content

TEST_DIR = Path(__file__).resolve().parent


@pytest.mark.parametrize('headers', ['prompt,prompt', 'prompt, Prompt ', 'prompt,mode,MODE'])
def test_csv_duplicate_headers_are_rejected(headers):
    with pytest.raises(ValueError, match='duplicate column'):
        parse_ideas_content(headers + '\nPopulation,text,number\n', 'ideas.csv')


def test_saved_ai_response():
    answer = (TEST_DIR / '_ai_request.txt').read_text(encoding='utf-8')
    expected = json.loads((TEST_DIR / 'ai_request.txt').read_text(encoding='utf-8'))
    assert ai_request(prompt='test', mode='test', ai_model='test', _ai_answer=answer) == expected
