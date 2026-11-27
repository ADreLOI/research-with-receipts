import asyncio
import os

from app.agent import execute_run
from app.cassettes import Cassette, CassetteLibrary, normalize_question, slugify
from app.runs import registry

Q = "How does gVisor isolate containers compared to runc?"


def _record_and_load(tmp_path):
    run = registry.create(Q)
    asyncio.run(execute_run(run))
    assert run.status == "done", run.error
    cassette = Cassette.load(run.dir / "cassette")
    return run, cassette


def test_live_run_is_recorded(tmp_path):
    run, cassette = _record_and_load(tmp_path)
    assert cassette.question == Q
    assert len(cassette.llm_turns) >= 3
    tools = [c["tool"] for c in cassette.tool_calls]
    assert tools[0] == "search" and "python" in tools and "screenshot" in tools
    # Screenshots are stored as files, not inline base64.
    shots = [c for c in cassette.tool_calls if c["tool"] == "screenshot"]
    assert all("image_b64" not in c["data"] and c["data"]["image_file"] for c in shots)
    assert all((cassette.dir / c["data"]["image_file"]).is_file() for c in shots)


def test_replay_reproduces_the_answer(tmp_path):
    os.environ["REPLAY_SPEED"] = "0"
    live, cassette = _record_and_load(tmp_path)
    replay = registry.create(Q)
    asyncio.run(execute_run(replay, cassette))
    assert replay.status == "done", replay.error
    assert replay.mode == "replay" and replay.replay_of["question"] == Q
    assert replay.answer["counts"] == live.answer["counts"]
    assert [c["text"] for c in replay.answer["claims"]] == [c["text"] for c in live.answer["claims"]]
    # Receipts are recomputed from the recorded data, so hashes match the live run.
    assert sorted(r.sha256 for r in replay.receipts.items.values()) == \
        sorted(r.sha256 for r in live.receipts.items.values())
    assert not (replay.dir / "cassette").exists(), "replays must not be re-recorded"


def test_replay_reports_missing_calls(tmp_path):
    _, cassette = _record_and_load(tmp_path)
    cassette.tool_calls = cassette.tool_calls[:1]  # truncate the recording
    run = registry.create(Q)
    asyncio.run(execute_run(run, cassette))
    errors = [e for e in run.events if e["type"] == "tool_result" and not e["ok"]]
    assert errors and "not in the recording" in errors[0]["error"]


def test_question_matching_ignores_case_punctuation_and_demo_host(tmp_path):
    a = "Summarize the product page at http://1.2.3.4/demo/hostile and list its price."
    b = "summarize the product page at https://rwr.example.com/demo/hostile, and list its price"
    assert normalize_question(a) == normalize_question(b)
    assert slugify(a) == slugify(b)


def test_library_lists_shipped_cassettes():
    lib = CassetteLibrary()
    found = lib.find("summarize the product page at https://anything.example/demo/hostile and list its price and "
                     "colours")
    assert found is not None and found.tool_calls
