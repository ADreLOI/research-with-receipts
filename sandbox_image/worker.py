"""Tool worker that runs INSIDE the sandbox container.

Protocol: one JSON request on stdin, one JSON response on stdout.
The control plane starts this with `docker exec` (browser tools) or
`docker run --network none` (python tool). It never holds credentials:
the container is started with an empty environment apart from a few
non-secret knobs (RWR_*).
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone

MAX_TEXT = 20000
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 ResearchWithReceipts/1.0")
ALLOW_HOSTS = {h.strip().lower() for h in os.environ.get("RWR_ALLOW_HOSTS", "").split(",") if h.strip()}
_ip_cache: dict[str, bool] = {}


# ---------------------------------------------------------------- safety

def host_is_safe(host: str) -> bool:
    """Block loopback, private, link-local (cloud metadata) and reserved IPs.

    This stops a hostile page or a prompt-injected model from using the
    sandbox to reach 169.254.169.254 or services on the VM's private net.
    """
    host = (host or "").lower().strip("[]")
    if not host:
        return False
    if host in ALLOW_HOSTS:
        return True
    if host in _ip_cache:
        return _ip_cache[host]
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        _ip_cache[host] = False
        return False
    safe = True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            safe = False
            break
    _ip_cache[host] = safe
    return safe


def check_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"scheme not allowed: {parsed.scheme!r}")
    if not host_is_safe(parsed.hostname or ""):
        raise ValueError(f"blocked host (private, link-local or unresolvable): {parsed.hostname}")
    return url


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out, blank = [], 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip()[:MAX_TEXT]


# ---------------------------------------------------------------- search

_STOP = set("a an and are as at be by did do does for from how in into is it its of on or that the their them "
            "these this to was were what when where which who whom why will with today now currently".split())


def keywords(query: str, limit: int = 6) -> str:
    """'What share of the world do the 3 biggest countries hold?' -> 'share world 3 biggest countries hold'."""
    words = [w.strip("?!.,;:'\"()") for w in query.split()]
    return " ".join([w for w in words if w and w.lower() not in _STOP][:limit])


def _ddg_html(httpx, BeautifulSoup, query: str, n: int) -> list[dict]:
    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query},
                   headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
    out: list[dict] = []
    for res in BeautifulSoup(r.text, "html.parser").select(".result"):
        a = res.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        if "uddg=" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        snip = res.select_one(".result__snippet")
        out.append({"title": a.get_text(" ", strip=True), "url": href,
                    "snippet": snip.get_text(" ", strip=True) if snip else ""})
        if len(out) >= n:
            break
    return out


def _ddg_lite(httpx, BeautifulSoup, query: str, n: int) -> list[dict]:
    r = httpx.post("https://lite.duckduckgo.com/lite/", data={"q": query},
                   headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
    soup = BeautifulSoup(r.text, "html.parser")
    snippets = [td.get_text(" ", strip=True) for td in soup.select("td.result-snippet")]
    out: list[dict] = []
    for i, a in enumerate(soup.select("a.result-link")):
        href = a.get("href", "")
        if "uddg=" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        out.append({"title": a.get_text(" ", strip=True), "url": href,
                    "snippet": snippets[i] if i < len(snippets) else ""})
        if len(out) >= n:
            break
    return out


def _wikipedia(httpx, BeautifulSoup, query: str, n: int) -> list[dict]:
    r = httpx.get("https://en.wikipedia.org/w/api.php",
                  params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": n},
                  headers={"User-Agent": UA}, timeout=15)
    out = []
    for item in r.json().get("query", {}).get("search", []):
        title = item["title"]
        out.append({"title": title, "snippet": BeautifulSoup(item.get("snippet", ""), "html.parser").get_text(),
                    "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))})
    return out


def search(req: dict) -> dict:
    """Keyless search from inside the sandbox. Datacenter IPs often get an
    empty page from DuckDuckGo, so try its two endpoints, then Wikipedia
    (with a keyword-only retry, since long questions match nothing there)."""
    import httpx
    from bs4 import BeautifulSoup

    query = str(req.get("query", ""))[:300]
    n = int(req.get("max_results", 6))
    attempts = [("duckduckgo", _ddg_html, query), ("duckduckgo-lite", _ddg_lite, query),
                ("wikipedia", _wikipedia, query)]
    short = keywords(query)
    if short and short.lower() != query.lower():
        attempts.append(("wikipedia (keywords)", _wikipedia, short))
    tried: list[str] = []
    for name, fn, q in attempts:
        try:
            results = fn(httpx, BeautifulSoup, q, n)
        except Exception as exc:  # noqa: BLE001 - try the next engine
            tried.append(f"{name}: {type(exc).__name__}")
            continue
        if results:
            provider = name if not tried else f"{name} (after {', '.join(tried)})"
            return {"provider": provider, "query": query, "results": results}
        tried.append(f"{name}: no results")
    return {"provider": "none (" + ", ".join(tried) + ")", "query": query, "results": []}


# ---------------------------------------------------------------- fetch

def fetch(req: dict) -> dict:
    import httpx
    from bs4 import BeautifulSoup

    url = check_url(str(req["url"]))
    # Follow redirects manually so every hop is checked against the blocklist.
    for _ in range(5):
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=20, follow_redirects=False)
        if r.is_redirect and r.headers.get("location"):
            url = check_url(urllib.parse.urljoin(url, r.headers["location"]))
            continue
        break
    ctype = r.headers.get("content-type", "")
    if "html" in ctype or not ctype:
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(strip=True) if soup.title else url
        text = clean_text(soup.get_text("\n"))
    elif "text" in ctype or "json" in ctype:
        title, text = url, clean_text(r.text)
    else:
        title, text = url, f"[non-text content: {ctype}, {len(r.content)} bytes]"
    return {"url": str(r.url), "status": r.status_code, "title": title, "text": text,
            "content_type": ctype, "captured_at": now_iso()}


# ---------------------------------------------------------------- browser

HIGHLIGHT_JS = r"""
(args) => {
  const [quote, stamp] = args;
  // Compare text with all whitespace removed so line breaks, inline tags and
  // indentation in the DOM do not defeat the match.
  const fold = (c) => ({'‘': "'", '’': "'", '“': '"', '”': '"', '–': '-', '—': '-'}[c] || c);
  const squash = (s) => Array.from(s || '').filter((c) => !/\s/.test(c)).map((c) => fold(c).toLowerCase()).join('');
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement && !skip.has(n.parentElement.tagName)) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
  });
  let flat = '';
  const map = [];
  while (walker.nextNode()) {
    const n = walker.currentNode, v = n.nodeValue;
    for (let i = 0; i < v.length; i++) {
      if (/\s/.test(v[i])) continue;
      flat += fold(v[i]).toLowerCase();
      map.push([n, i]);
    }
  }
  const words = (quote || '').trim().split(/\s+/).filter(Boolean);
  const cands = [];
  if (words.length) {
    cands.push(words.join(' '));
    for (const k of [20, 14, 10, 7, 5]) if (words.length > k) cands.push(words.slice(0, k).join(' '));
    for (const k of [14, 10, 7]) if (words.length > k) cands.push(words.slice(-k).join(' '));
  }
  let found = '', rects = [];
  for (const c of cands) {
    const q = squash(c);
    if (q.length < 12) continue;
    const idx = flat.indexOf(q);
    if (idx < 0) continue;
    const [sn, so] = map[idx];
    const [en, eo] = map[idx + q.length - 1];
    const range = document.createRange();
    range.setStart(sn, so);
    range.setEnd(en, eo + 1);
    const el = sn.parentElement;
    if (el) el.scrollIntoView({block: 'center'});
    rects = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
    found = c;
    break;
  }
  for (const r of rects) {
    const d = document.createElement('div');
    Object.assign(d.style, {position: 'absolute', left: (r.left + window.scrollX - 2) + 'px',
      top: (r.top + window.scrollY - 2) + 'px', width: (r.width + 4) + 'px', height: (r.height + 4) + 'px',
      background: 'rgba(255, 221, 0, 0.45)', outline: '2px solid #e0a800', zIndex: 2147483646,
      pointerEvents: 'none', borderRadius: '2px'});
    document.body.appendChild(d);
  }
  const b = document.createElement('div');
  b.textContent = stamp;
  Object.assign(b.style, {position: 'fixed', left: '0', right: '0', bottom: '0', padding: '6px 10px',
    font: '12px/1.3 monospace', color: '#fff', background: 'rgba(20, 20, 30, 0.85)', zIndex: 2147483647});
  document.body.appendChild(b);
  return {found: !!found && rects.length > 0, matched: found};
}
"""


def screenshot(req: dict) -> dict:
    from playwright.sync_api import sync_playwright

    url = check_url(str(req["url"]))
    highlight = str(req.get("highlight", ""))[:600]
    blocked: list[str] = []

    def guard(route):
        host = urllib.parse.urlparse(route.request.url).hostname or ""
        if route.request.url.startswith(("data:", "blob:")) or host_is_safe(host):
            return route.continue_()
        blocked.append(route.request.url[:200])
        return route.abort()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=UA,
                                  accept_downloads=False, java_script_enabled=True)
        ctx.route("**/*", guard)
        page = ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())
        resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:  # noqa: BLE001 - many pages never go idle
            pass
        final_url = page.url
        check_url(final_url)
        title = page.title() or final_url
        try:
            text = clean_text(page.inner_text("body", timeout=5000))
        except Exception:  # noqa: BLE001
            text = ""
        captured_at = now_iso()
        stamp = f"receipt · {captured_at} · {final_url[:120]}"
        hl = page.evaluate(HIGHLIGHT_JS, [highlight, stamp])
        png = page.screenshot(type="jpeg", quality=72, full_page=False)
        browser.close()
    return {"url": final_url, "status": resp.status if resp else 0, "title": title, "text": text,
            "captured_at": captured_at, "highlight_found": bool(hl.get("found")),
            "highlight_matched": hl.get("matched", ""), "blocked_requests": blocked[:20],
            "image_b64": base64.b64encode(png).decode(), "image_mime": "image/jpeg"}


# ---------------------------------------------------------------- python

def run_python(req: dict) -> dict:
    code = str(req.get("code", ""))
    timeout = float(req.get("timeout_s", 10))
    work = "/tmp/work"
    os.makedirs(f"{work}/out", exist_ok=True)
    path = f"{work}/main.py"
    with open(path, "w") as fh:
        fh.write(code)
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "MPLBACKEND": "Agg",
           "MPLCONFIGDIR": "/tmp/mpl", "PYTHONDONTWRITEBYTECODE": "1"}
    t0 = time.time()
    try:
        proc = subprocess.run([sys.executable, "-I", path], cwd=work, env=env, capture_output=True,
                              text=True, timeout=timeout)
        exit_code, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        exit_code, timed_out = -9, True
        stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = f"TimeoutExpired: killed after {timeout}s"
    files = []
    # The code may have deleted its own work dir (e.g. rm -rf /), so be tolerant.
    try:
        names = sorted(os.listdir(f"{work}/out"))[:3]
    except OSError:
        names = []
    for name in names:
        fp = f"{work}/out/{name}"
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".svg")) and os.path.getsize(fp) < 2_000_000:
            with open(fp, "rb") as fh:
                files.append({"name": name, "b64": base64.b64encode(fh.read()).decode()})
    return {"exit_code": exit_code, "stdout": stdout[-8000:], "stderr": stderr[-4000:],
            "timed_out": timed_out, "duration_s": round(time.time() - t0, 3), "files": files,
            "captured_at": now_iso()}


TOOLS = {"search": search, "fetch": fetch, "screenshot": screenshot, "python": run_python}


def main() -> None:
    try:
        req = json.loads(sys.stdin.read() or "{}")
        tool = TOOLS[req.get("tool", "")]
        out = {"ok": True, "data": tool(req)}
    except Exception as exc:  # noqa: BLE001 - report every failure as data
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000]}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
