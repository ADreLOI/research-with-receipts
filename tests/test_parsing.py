from app.llm import extract_json, strip_reasoning


def test_extract_plain_json():
    assert extract_json('{"action": "search", "args": {"query": "x"}}')["action"] == "search"


def test_extract_from_fence_and_chatter():
    raw = 'Sure!\n```json\n{"thought": "a {brace} in text", "action": "final", "args": {}}\n```\nDone.'
    assert extract_json(raw)["thought"] == "a {brace} in text"


def test_strip_think_block():
    assert strip_reasoning("<think>secret plan</think>{\"a\": 1}") == '{"a": 1}'
    assert extract_json("<think>hm {not json</think> {\"action\": \"search\"}")["action"] == "search"
