"""Regression fixture for the original saved AI response."""
import json
from pathlib import Path

from map.functions import ai_request

TEST_DIR = Path(__file__).resolve().parent


def test_saved_ai_response():
    answer = (TEST_DIR / '_ai_request.txt').read_text(encoding='utf-8')
    expected = json.loads((TEST_DIR / 'ai_request.txt').read_text(encoding='utf-8'))
    assert ai_request(prompt='test', mode='test', ai_model='test', _ai_answer=answer) == expected
