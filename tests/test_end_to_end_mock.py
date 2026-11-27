import asyncio

from app.agent import execute_run
from app.containment import run_battery
from app.runs import registry


def test_mock_run_produces_verified_claims():
    run = registry.create("How does gVisor isolate containers compared to runc?")
    asyncio.run(execute_run(run))
    assert run.status == "done", run.error
    types = [e["type"] for e in run.events]
    assert types[0] == "run_started" and "sandbox_created" in types and "sandbox_destroyed" in types
    assert run.answer and run.answer["counts"]["verified"] >= 2
    for claim in run.answer["claims"]:
        for rid in claim["receipt_ids"]:
            assert run.receipts.get(rid) is not None


def test_hostile_page_is_flagged():
    run = registry.create("Summarize the product page at http://localhost:8000/demo/hostile")
    asyncio.run(execute_run(run))
    assert run.status == "done", run.error
    assert any(e["type"] == "guard" for e in run.events)


def test_containment_battery_in_mock_mode():
    async def collect():
        return [item async for item in run_battery()]

    items = asyncio.run(collect())
    attacks = [i for i in items if i["type"] == "attack"]
    assert attacks and all(a["contained"] for a in attacks), [a for a in attacks if not a["contained"]]
    assert items[-1]["type"] == "done" and items[-1]["host_ok"]
