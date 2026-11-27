import pytest

from app.normalize import normalize_action, normalize_claims


@pytest.mark.parametrize("raw, expected", [
    ({"action": "search", "args": {"query": "x"}}, ("search", {"query": "x"})),
    ({"tool": "web_search", "query": "x"}, ("search", {"query": "x"})),
    ({"action": "Open Page", "args": {"link": "https://a.b"}}, ("screenshot", {"url": "https://a.b"})),
    ({"name": "fetch", "arguments": '{"url": "<https://a.b>"}'}, ("fetch", {"url": "https://a.b"})),
    ({"function": {"name": "run_python", "arguments": {"code": "```python\nprint(1)\n```"}}},
     ("python", {"code": "print(1)"})),
    ({"action": "search", "args": "plain query string"}, ("search", {"query": "plain query string"})),
    ({"summary": "s", "claims": []}, ("final", {"summary": "s", "claims": []})),
    ({"action": "final_answer", "answer": "short"}, ("final", {"summary": "short"})),
])
def test_normalize_action_shapes(raw, expected):
    out = normalize_action(raw)
    assert out["action"] == expected[0]
    for k, v in expected[1].items():
        assert out["args"][k] == v


def test_missing_action_raises():
    with pytest.raises(ValueError):
        normalize_action({"thought": "hmm"})


def test_normalize_claims_variants():
    claims = normalize_claims({"claims": [
        {"claim": "A", "receipts": "R1, R3", "evidence": " q1 "},
        {"text": "B", "receipt_ids": [2, "[R4]"], "quote": "q2"},
        {"text": "C", "sources": "R2"},
        "D is true [R5][R6]",
        {"text": ""},
        42,
    ]})
    assert claims == [
        {"text": "A", "receipt_ids": ["R1", "R3"], "quote": "q1"},
        {"text": "B", "receipt_ids": ["R2", "R4"], "quote": "q2"},
        {"text": "C", "receipt_ids": ["R2"], "quote": ""},
        {"text": "D is true", "receipt_ids": ["R5", "R6"], "quote": ""},
    ]


def test_claims_as_json_string():
    assert normalize_claims({"claims": '[{"text": "x", "receipt_ids": ["R1"], "quote": "y"}]'})[0]["quote"] == "y"
