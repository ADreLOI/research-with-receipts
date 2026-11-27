"""Record a captioned walkthrough of the deployed app: a demo video with no voice-over needed.

It follows docs/DEMO_SCRIPT.md: a research run with Python, hovering claims,
opening a highlighted receipt and verifying its SHA-256, the hostile page,
and the containment test. Captions are drawn on the page and also written
to an .srt file, and key moments are saved as PNG screenshots for the README.

Needs Playwright + Chromium. The sandbox image already has both, so on the VM:

  cd /opt/rwr && mkdir -p video && chmod 777 video
  docker run --rm -v "$PWD:/w:ro" -v "$PWD/video:/out" -e APP_ACCESS_CODE \\
      rwr-sandbox:latest python /w/scripts/record_video.py https://<your-host> --out /out

(APP_ACCESS_CODE must be exported in the shell if the app uses one.) On a
laptop: pip install playwright && playwright install chromium, then run it
directly. Add --replay to use the recorded runs instead of live ones.

Output: demo.webm (1440x900), demo.srt, and shot-*.png.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# The caption is a manual popover so it sits in the browser's top layer and
# stays visible above open <dialog>s (receipt and containment modals).
CAPTION_JS = """(text) => {
  let el = document.getElementById('rwr-caption');
  if (!el) {
    el = document.createElement('div');
    el.id = 'rwr-caption';
    el.setAttribute('popover', 'manual');
    el.style.cssText = 'position:fixed;inset:auto auto 28px 50%;margin:0;border:0;transform:translateX(-50%);' +
      'max-width:1100px;padding:14px 22px;border-radius:12px;background:rgba(17,24,39,.92);color:#fff;' +
      'font:600 22px/1.35 system-ui,sans-serif;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.35);' +
      'pointer-events:none';
    document.body.appendChild(el);
  }
  el.textContent = text;
  if (el.matches(':popover-open')) el.hidePopover();
  if (text) el.showPopover();
}"""

RUN_DONE_JS = """() => [...document.querySelectorAll('#log li')].some(li => /run finished/i.test(li.textContent))"""


class Director:
    def __init__(self, page: Page, out: Path) -> None:
        self.page, self.out = page, out
        self.t0 = time.monotonic()
        self.cues: list[tuple[float, float, str]] = []
        self._open: tuple[float, str] | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    def say(self, text: str, hold: float = 0.0) -> None:
        self._close_cue()
        self.page.evaluate(CAPTION_JS, text)
        self._open = (self.now(), text)
        print(f"[{self.now():6.1f}s] {text}", flush=True)
        if hold:
            self.page.wait_for_timeout(int(hold * 1000))

    def _close_cue(self) -> None:
        if self._open:
            start, text = self._open
            self.cues.append((start, self.now(), text))
            self._open = None

    def shot(self, name: str) -> None:
        self.page.screenshot(path=str(self.out / f"shot-{name}.png"))

    def write_srt(self) -> None:
        self._close_cue()

        def ts(t: float) -> str:
            ms = int(t * 1000)
            return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

        lines = [f"{i}\n{ts(a)} --> {ts(b)}\n{text}\n" for i, (a, b, text) in enumerate(self.cues, 1)]
        (self.out / "demo.srt").write_text("\n".join(lines))


def start_run(d: Director, example: int, replay: bool, code: str) -> None:
    page = d.page
    before = page.evaluate("location.hash")
    page.click(f'#examples .example[data-i="{example}"]')
    if code and page.is_visible("#access"):
        page.fill("#access", code)
    if page.is_visible("#replay-row"):
        page.set_checked("#replay", replay)
    page.click("#go")
    page.wait_for_function("(h) => location.hash && location.hash !== h", arg=before, timeout=30_000)
    page.wait_for_timeout(800)


def wait_done(d: Director, timeout_s: int) -> None:
    d.page.wait_for_function(RUN_DONE_JS, timeout=timeout_s * 1000, polling=1000)
    d.page.wait_for_timeout(1500)


def show_receipt(d: Director) -> bool:
    """Open the first highlighted screenshot receipt and verify its hash. False if the answer has none."""
    page = d.page
    thumb = page.locator("#answer .thumb-btn").first
    if not thumb.count():
        return False
    thumb.click()
    page.wait_for_selector("#verify-btn", timeout=15_000)
    page.wait_for_timeout(800)
    d.say("The receipt: the exact quote highlighted on the real page, timestamped inside the sandbox.", 4)
    page.click("#verify-btn")
    page.wait_for_selector("#verify-out.good, #verify-out.bad", timeout=20_000)
    d.say("Verify SHA-256 recomputes the hash from the captured content. Change one byte of evidence and it stops matching.")
    page.wait_for_timeout(1500)
    d.shot("receipt")
    page.wait_for_timeout(2500)
    page.click("#modal [data-close]")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="public URL of the app, e.g. https://1-2-3-4.sslip.io")
    ap.add_argument("--out", default="video")
    ap.add_argument("--replay", action="store_true", help="replay the recorded runs instead of running live")
    ap.add_argument("--timeout", type=int, default=420, help="max seconds to wait for each run")
    ap.add_argument("--skip-hostile", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    code = os.environ.get("APP_ACCESS_CODE", "")
    size = {"width": 1440, "height": 900}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=size, record_video_dir=str(out / "raw"), record_video_size=size,
                                  color_scheme="light")
        page = ctx.new_page()
        d = Director(page, out)
        page.goto(args.url.rstrip("/") + "/", wait_until="networkidle")
        page.wait_for_timeout(1200)

        d.say("Research with Receipts: an AI research agent where every claim links to evidence "
              "captured inside a Vultr sandbox.", 4.5)
        page.locator("#status").evaluate("el => el.style.outline = '3px solid #f5b301'")
        d.say("Control plane on a Vultr VM. Reasoning on Vultr Serverless Inference. "
              "Every page visit and every line of code runs in a throwaway container.", 5)
        page.locator("#status").evaluate("el => el.style.outline = ''")

        # 1. A question with numbers: browser + python.
        start_run(d, 1, args.replay, code)
        d.say("We ask a question with numbers in it. The model plans one step at a time.", 4)
        d.say("Search results are only leads. Every page the agent reads becomes a receipt: "
              "URL, captured text, screenshot, SHA-256.")
        # Wait until the model runs python or the run ends, whichever comes first.
        page.wait_for_function(f"() => /step \\d+: python/.test(document.querySelector('#log').textContent) "
                               f"|| ({RUN_DONE_JS})()", timeout=args.timeout * 1000, polling=1000)
        if "python" in (page.text_content("#log") or ""):
            d.say("Arithmetic runs as Python in a fresh container with no network. "
                  "If the code fails, the error goes back and the model fixes it.")
        wait_done(d, args.timeout)
        d.shot("answer")
        claims = page.locator("#answer li.claim")
        d.say("Each claim is checked mechanically against its receipts. Hover a claim to see what backs it.")
        for i in range(min(claims.count(), 3)):
            claims.nth(i).scroll_into_view_if_needed()
            claims.nth(i).hover()
            page.wait_for_timeout(1800)

        shown = show_receipt(d)

        # 2. The hostile page.
        if not args.skip_hostile:
            start_run(d, 2, args.replay, code)
            d.say("Now a hostile product page: hidden prompt injection, requests to the cloud metadata "
                  "service and localhost, and a blocking alert().")
            wait_done(d, args.timeout)
            guard = page.locator("#log li.guard").first
            if guard.count():
                guard.scroll_into_view_if_needed()
                guard.evaluate("el => el.style.outline = '3px solid #f5b301'")
            d.say("The injection is flagged and fenced as untrusted data, the metadata and localhost requests "
                  "are blocked, and the answer just reports the price and colours.", 6)
            d.shot("hostile")
            if not shown:
                show_receipt(d)

        # 3. Containment test.
        page.click("#btn-contain")
        d.say("Containment test: eight hostile actions against a fresh sandbox. Steal keys, rm -rf, "
              "infinite loop, fork bomb, 4 GB of RAM, exfiltration, metadata SSRF, localhost.")
        page.wait_for_selector("#contain-done .done-banner, #contain-done .error-text", timeout=180_000)
        page.locator("#contain-done").scroll_into_view_if_needed()
        d.say("All contained, and the container is already gone. One sandbox per run, destroyed at the end.", 5)
        d.shot("containment")
        page.click("#contain [data-close]")
        d.say("Research with Receipts: an agent that executes safely and shows its work. Built on Vultr.", 5)
        d.say("", 0.5)
        d.write_srt()
        video = page.video
        ctx.close()
        browser.close()
        raw = Path(video.path()) if video else None
    if raw and raw.exists():
        shutil.move(str(raw), out / "demo.webm")
        shutil.rmtree(out / "raw", ignore_errors=True)
        print(f"\nvideo: {out / 'demo.webm'}  captions: {out / 'demo.srt'}  screenshots: {out}/shot-*.png")
        return 0
    print("no video was written", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
