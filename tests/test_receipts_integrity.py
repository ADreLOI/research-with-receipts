import base64
import hashlib
import json

from app.receipts import HASH_FORMULA, Receipt, ReceiptStore, match_quote, receipt_hash
from app.runs import RunRegistry

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n fake image bytes").decode()


def _page(store, **kw):
    base = dict(id=store.next_id(), kind="page", tool="screenshot", step=1, captured_at="t", sandbox="s",
                url="https://example.org/a", title="A", text="Alpha beta gamma.")
    base.update(kw)
    return store.add(Receipt(**base), PNG, "image/png")


def test_hash_formula_is_what_the_browser_recomputes(tmp_path):
    store = ReceiptStore(tmp_path)
    r = _page(store)
    expected = hashlib.sha256(f"{r.url}\n{r.text}\n{r.code}\n{r.stdout}".encode() + base64.b64decode(PNG)).hexdigest()
    assert r.sha256 == expected == receipt_hash(r, base64.b64decode(PNG))
    assert "image_bytes" in HASH_FORMULA


def test_verify_detects_tampered_text_and_image(tmp_path):
    store = ReceiptStore(tmp_path)
    r = _page(store)
    assert store.verify_hash(r)["match"]
    r.text += " (edited)"
    assert not store.verify_hash(r)["match"]
    r.text = r.text.replace(" (edited)", "")
    assert store.verify_hash(r)["match"]
    (store.dir / r.image_file).write_bytes(b"swapped screenshot")
    out = store.verify_hash(r)
    assert not out["match"] and out["image_bytes"] == len(b"swapped screenshot")


def test_receipt_ids_are_sequential_and_lookup_is_forgiving(tmp_path):
    store = ReceiptStore(tmp_path)
    a, b = _page(store), _page(store, url="https://example.org/b")
    assert (a.id, b.id) == ("R1", "R2")
    assert store.get("r2") is b and store.get("[R1]") is a and store.get("R3") is None
    assert store.find_page("https://example.org/b/") is b


def test_code_receipt_without_image(tmp_path):
    store = ReceiptStore(tmp_path)
    r = store.add(Receipt(id="R1", kind="code", tool="python", step=2, captured_at="t", sandbox="s",
                          code="print(42)", stdout="42\n", exit_code=0))
    assert r.image_file == "" and store.verify_hash(r)["match"]


def test_quote_matching_edge_cases():
    text = "The “quick” brown fox — jumps over\nthe lazy dog."
    assert match_quote('the "quick" brown fox - jumps over the lazy dog.', text)[0] == 1.0
    assert match_quote("", text)[0] == 0.0
    partial = match_quote("the quick brown fox leaps over the lazy cat", text)[0]
    assert 0.6 <= partial < 1.0


def test_run_persistence_roundtrip(tmp_path):
    reg = RunRegistry()
    reg.root = tmp_path
    run = reg.create("Persist me?")
    assert run.dir.parent == tmp_path
    rec = _page(run.receipts)
    run.answer = {"summary": "ok [R1]", "claims": [], "counts": {"verified": 0, "partial": 0, "unverified": 0}}
    run.status = "done"
    run.emit("final", counts=run.answer["counts"])
    run.save()
    data = json.loads((run.dir / "run.json").read_text())
    assert data["receipts"][0]["text"] == rec.text  # full text persisted

    fresh = RunRegistry()
    fresh.root = tmp_path
    fresh.runs = {}
    fresh.load_saved()
    loaded = fresh.get(run.id)
    assert loaded.answer == run.answer and loaded.status == "done"
    again = loaded.receipts.get("R1")
    assert again.sha256 == rec.sha256 and loaded.receipts.verify_hash(again)["match"]
