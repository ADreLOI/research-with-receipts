import asyncio

import pytest

import app.agent as agent_mod
from app.agent import execute_run
from app.runs import registry
from app.sandbox.base import ToolResult

from .helpers import GVISOR, GVISOR_QUOTE, CountingSandbox, ScriptedProvider, act, override


def run_with(monkeypatch, replies, sandbox=None, question="What is gVisor?"):
    provider = ScriptedProvider(replies)
    sandbox = sandbox or CountingSandbox()

    async def make():
        return sandbox

    monkeypatch.setattr(agent_mod, "make_live_provider", lambda: provider)
    monkeypatch.setattr(agent_mod, "make_sandbox", make)
    run = registry.create(question)
    asyncio.run(execute_run(run))
    return run, provider, sandbox


def final(*claims, summary="s [R1]"):
    return act("final", summary=summary, claims=list(claims))


def claim(text, rids, quote):
    return {"text": text, "receipt_ids": rids, "quote": quote}


def events(run, type_):
    return [e for e in run.events if e["type"] == type_]


def test_happy_path_produces_verified_claim_with_highlighted_evidence(monkeypatch):
    run, _, sb = run_with(monkeypatch, [
        act("screenshot", url=GVISOR),
        final(claim("gVisor ships runsc.", ["R1"], GVISOR_QUOTE)),
    ])
    assert run.status == "done", run.error
    c = run.answer["claims"][0]
    assert c["verification"]["status"] == "verified"
    ev = run.receipts.get(c["evidence_id"])
    assert ev.tool == "evidence" and ev.highlight_found and ev.highlight == GVISOR_QUOTE
    assert sb.destroyed


def test_invalid_json_is_repaired(monkeypatch):
    run, provider, _ = run_with(monkeypatch, [
        "I think I should search first.",
        act("screenshot", url=GVISOR),
        final(claim("x", ["R1"], GVISOR_QUOTE)),
    ])
    assert run.status == "done", run.error
    repair_prompts = [m for m in provider.seen[1] if m["role"] == "user" and "not a valid JSON action" in m["content"]]
    assert repair_prompts


def test_three_invalid_replies_fail_the_run_cleanly(monkeypatch):
    run, _, sb = run_with(monkeypatch, ["nope", "still nope", "no json here"])
    assert run.status == "failed"
    assert "valid action" in run.error
    assert sb.destroyed, "sandbox must be destroyed even when the run fails"
    assert events(run, "run_finished")[0]["status"] == "failed"


def test_unknown_action_is_reported_to_the_model(monkeypatch):
    run, provider, _ = run_with(monkeypatch, [
        act("teleport", where="mars"),
        act("screenshot", url=GVISOR),
        final(claim("x", ["R1"], GVISOR_QUOTE)),
    ])
    assert run.status == "done"
    obs = [m["content"] for m in provider.seen[1] if m["content"].startswith("Observation for step 1")]
    assert obs and "unknown action 'teleport'" in obs[0]


def test_step_budget_forces_a_final_answer(monkeypatch):
    with override(max_steps=2):
        run, provider, _ = run_with(monkeypatch, [
            act("search", query="gvisor"),
            act("search", query="gvisor again"),
            final(),
        ])
    assert run.status == "done"
    assert any("used all your tool calls" in m["content"] for m in provider.seen[-1] if m["role"] == "user")


def test_python_error_triggers_visible_self_heal(monkeypatch):
    broken = ToolResult(True, "python", data={"exit_code": 1, "stdout": "", "stderr": "NameError: name 'x' is not defined",
                                               "timed_out": False, "files": [], "captured_at": "now"})
    sb = CountingSandbox(fail_python=[broken])
    run, provider, _ = run_with(monkeypatch, [
        act("python", code="print(x)"),
        act("python", code="print(6 * 7)"),
        final(claim("6 times 7 is 42.", ["R2"], "42")),
    ], sandbox=sb)
    assert run.status == "done", run.error
    assert len(events(run, "retry")) == 1 and run.answer["retries"] == 1
    obs = [m["content"] for m in provider.seen[1] if m["content"].startswith("Observation for step 1")][0]
    assert "NameError" in obs
    assert run.answer["claims"][0]["verification"]["status"] == "verified"


def test_contained_tool_result_emits_containment_event(monkeypatch):
    killed = ToolResult(True, "python", data={"exit_code": -9, "stdout": "", "stderr": "killed", "timed_out": True,
                                               "files": [], "captured_at": "now"}, contained="timeout")
    run, _, _ = run_with(monkeypatch, [act("python", code="while True: pass"), final()],
                         sandbox=CountingSandbox(fail_python=[killed]))
    assert events(run, "containment") and "timeout" in events(run, "containment")[0]["message"]


def test_hallucinated_receipt_and_fabricated_quote_are_unverified(monkeypatch):
    run, _, _ = run_with(monkeypatch, [
        act("screenshot", url=GVISOR),
        final(claim("Made-up receipt.", ["R9"], GVISOR_QUOTE),
              claim("Made-up quote.", ["R1"], "gVisor was written in COBOL by a team of three wizards in 1971.")),
    ])
    statuses = [c["verification"]["status"] for c in run.answer["claims"]]
    assert statuses == ["unverified", "unverified"]
    assert run.answer["counts"] == {"verified": 0, "partial": 0, "unverified": 2}


def test_evidence_capture_dedupes_and_respects_budget(monkeypatch):
    q2 = "It provides an additional layer of isolation between running applications and the host operating system."
    sb = CountingSandbox()
    with override(max_evidence_shots=1):
        run, _, _ = run_with(monkeypatch, [
            act("screenshot", url=GVISOR),
            final(claim("a", ["R1"], GVISOR_QUOTE), claim("b", ["R1"], GVISOR_QUOTE), claim("c", ["R1"], q2)),
        ], sandbox=sb)
    highlighted = [c for c in sb.calls if c[0] == "screenshot" and c[1]["highlight"]]
    assert len(highlighted) == 1
    a, b, c = run.answer["claims"]
    assert a["evidence_id"] == b["evidence_id"]
    assert "evidence_id" not in c and c["verification"]["status"] == "partial"


def test_sandbox_crash_fails_run_and_still_tears_down(monkeypatch):
    sb = CountingSandbox(explode_on="screenshot")
    run, _, _ = run_with(monkeypatch, [act("screenshot", url=GVISOR)], sandbox=sb)
    assert run.status == "failed" and "sandbox crashed" in run.error
    assert sb.destroyed and events(run, "sandbox_destroyed")


def test_page_content_is_fenced_as_untrusted(monkeypatch):
    run, provider, _ = run_with(monkeypatch, [act("screenshot", url="https://mock.local/hostile"), final()],
                                question="Summarize https://mock.local/hostile")
    obs = [m["content"] for m in provider.seen[1] if m["content"].startswith("Observation for step 1")][0]
    assert "<untrusted_content>" in obs and "</untrusted_content>" in obs
    assert events(run, "guard")


@pytest.mark.parametrize("bad_args", [None, "oops", ["list"]])
def test_non_dict_args_do_not_crash(monkeypatch, bad_args):
    run, _, _ = run_with(monkeypatch, [{"thought": "t", "action": "search", "args": bad_args}, final()])
    assert run.status == "done", run.error
