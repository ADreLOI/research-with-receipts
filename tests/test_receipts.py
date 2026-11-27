from pathlib import Path

from app.receipts import Receipt, ReceiptStore, match_quote, verify_claim

TEXT = "gVisor is an application kernel, written in Go.\nIt implements a large part of the Linux system call interface."


def test_exact_quote_scores_one():
    score, _ = match_quote("It implements a large part of the Linux  system call interface.", TEXT)
    assert score == 1.0


def test_smart_quotes_and_case_are_normalized():
    score, _ = match_quote("GVISOR IS AN APPLICATION KERNEL, WRITTEN IN GO.", TEXT)
    assert score == 1.0


def test_fabricated_quote_scores_low():
    score, _ = match_quote("gVisor was written in Rust by Microsoft in 1999.", TEXT)
    assert score < 0.6


def test_verify_claim_statuses(tmp_path: Path):
    store = ReceiptStore(tmp_path)
    page = store.add(Receipt(id="R1", kind="page", tool="fetch", step=1, captured_at="", sandbox="t", text=TEXT))
    shot = store.add(Receipt(id="R2", kind="page", tool="evidence", step=1, captured_at="", sandbox="t", text=TEXT,
                             highlight_found=True))
    good = {"text": "x", "receipt_ids": [page.id, shot.id], "quote": "an application kernel, written in Go"}
    assert verify_claim(good, store)["status"] == "verified"
    text_only = {"text": "x", "receipt_ids": [page.id], "quote": "an application kernel, written in Go"}
    assert verify_claim(text_only, store)["status"] == "partial"
    bogus = {"text": "x", "receipt_ids": ["R9"], "quote": "anything"}
    assert verify_claim(bogus, store)["status"] == "unverified"
    # Located in the live page but hidden from view: text-only evidence still counts.
    hidden = store.add(Receipt(id="R3", kind="page", tool="evidence", step=1, captured_at="", sandbox="t", text=TEXT,
                               highlight_hidden="not rendered on the page (display:none or collapsed)"))
    located = {"text": "x", "receipt_ids": [page.id, hidden.id], "quote": "an application kernel, written in Go"}
    assert verify_claim(located, store)["status"] == "verified"
