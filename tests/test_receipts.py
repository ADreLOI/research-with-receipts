from pathlib import Path

from app.receipts import Receipt, ReceiptStore, match_quote, table_cells, verify_claim

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


# Page text as the sandbox captures a wide table: cells on separate lines.
POP_PAGE = "\n".join([
    "#", "Country", "Population (2025)", "Yearly Change", "Net Change", "Density", "Land Area", "World Share",
    "1", "India", "1,463,865,525", "+0.89%", "12,929,734", "492", "2,973,190", "17.8%",
    "2", "China", "1 416 096 094", "\u22120.23%", "\u22123,225,184", "151", "9,388,211", "17.2%",
    "3", "United States", "347,275,807", "+0.54%", "1,849,236", "38", "9,147,420", "4.2%",
])


def test_markdown_table_row_quotes_match_page_cells():
    score, span = match_quote("| India | 1,463,865,525 | 17.8% |", POP_PAGE)  # skips 5 columns
    assert score == 1.0 and span.startswith("india 1463865525") and span.endswith("17.8%")
    assert match_quote("|China|1,416,096,094|17.2%|", POP_PAGE)[0] == 1.0
    # Cells taken from different rows are not one row.
    assert match_quote("| India | 347,275,807 | 4.2% |", POP_PAGE)[0] < 0.9
    assert table_cells("| :--- | ---: |") == []


def test_table_row_quote_is_verified_against_its_receipt(tmp_path: Path):
    store = ReceiptStore(tmp_path)
    page = store.add(Receipt(id="R1", kind="page", tool="evidence", step=1, captured_at="", sandbox="t",
                             text=POP_PAGE, highlight_found=True))
    row = {"text": "x", "receipt_ids": [page.id], "quote": "| United States | 347,275,807 | 4.2% |"}
    assert verify_claim(row, store)["status"] == "verified"
