import pytest
import json
from pathlib import Path
from map.functions import ai_request

TEST_DIR = Path(__file__).resolve().parent


def test_ai_request():
    ai_answer_path = TEST_DIR / "_ai_request.txt"
    expected_output_path = TEST_DIR / "ai_request.txt"

  
    with open(ai_answer_path, 'r', encoding='utf-8') as f:
        _ai_answer = f.read()
        _ai_answer = _ai_answer

    with open(expected_output_path, 'r', encoding='utf-8') as f:
        expected_output = f.read()  
        expected_output = dict(json.loads(expected_output))

    result = ai_request(
        _ai_answer=_ai_answer,
        prompt="test",
        mode="test",
        ai_model="test"
    )

    assert result == expected_output