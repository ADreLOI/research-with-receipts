# 2-minute demo script

Two voices work best: **A** drives the browser, **B** talks. Times are cumulative. Everything on screen is the public URL on the Vultr VM.

## Pre-flight (15 minutes before recording)

- [ ] `python scripts/check_inference.py` on the VM prints `chat: OK`.
- [ ] `/api/health` shows `sandbox: docker` and `llm: vultr ...` in green in the header.
- [ ] `python scripts/record_demo.py` ran on the VM, all three questions finished, and `demo/cassettes/` was committed. This is the safety net.
- [ ] Run each demo question once to warm up the model and the sandbox image.
- [ ] Browser at 1440 px wide, zoom 100%, history cleared or tidy, a terminal with `ssh` to the VM ready for the last shot.
- [ ] If anything is flaky on the day: tick **Replay the recorded run**. The UI labels it a replay, so say so out loud.

## Script

| Time | On screen (A) | Voice-over (B) |
|---|---|---|
| 0:00 | Landing page, empty state with Plan / Execute / Receipt / Verify | "Research agents sound confident and cite links they never opened. And an agent that browses the open web reads hostile input all day. We built one that can't hide anything, and can't hurt anything." |
| 0:12 | Point at the header pills: `sandbox: docker`, `llm: vultr …` | "The control plane runs on a Vultr VM. The model runs on Vultr Serverless Inference. Every browse and every line of code runs in a throwaway container." |
| 0:20 | Click example **Browser + Python** (population shares), press **Research it** | "Let's ask something with numbers in it." |
| 0:24 | Live log fills: step 1 search, step 2 screenshot → R1, R2; phase stepper moves Plan → Browse | "The model plans one step at a time. Search results are only leads. Each page it actually reads becomes a receipt: URL, text, a screenshot, a SHA-256." |
| 0:40 | Log shows `python` step, stdout; if a `self-heal retry` appears, point at it | "Arithmetic doesn't happen in the model's head. It writes Python, which runs in a fresh container with no network. If the code fails, the error goes back and the model fixes it, live." |
| 0:52 | Answer appears with verified / partial badges. Hover claim 1: its receipts light up | "Every claim cites receipts, and every claim is checked mechanically: is the quote really in the captured page? Hover a claim and you see exactly what backs it." |
| 1:02 | Click the claim's thumbnail: highlighted screenshot opens. Press **Verify SHA-256** | "This is the receipt: the exact sentence highlighted on the real page, timestamped inside the sandbox. And the hash is recomputed right here in your browser. If anyone edits the evidence, it stops matching." |
| 1:12 | Close. Click example **Containment (hostile page)**, press **Research it** | "Now the fun part. This product page is hostile." |
| 1:17 | Log: orange `prompt injection detected`; open the receipt, show `blocked: 169.254.169.254, 127.0.0.1` | "Hidden text tells the agent to ignore its instructions, dump the environment and run rm -rf. The page also tries to hit the cloud metadata service. The injection is flagged and fenced as untrusted data, the metadata request is blocked, and the answer just gives the price and colours." |
| 1:32 | Click **Run containment test** in the header; rows turn green one by one | "Even if a model obeys, the sandbox absorbs it. Steal the API keys: there are none inside. rm -rf: read-only filesystem. Infinite loop: killed at ten seconds. Fork bomb: PID limit. Four gigabytes of RAM: OOM-killed. Exfiltration: no network." |
| 1:48 | Banner `8/8 attacks contained … host untouched`. Optional: terminal `docker ps` shows nothing left | "Eight out of eight, and the container is already gone. One container per run, destroyed at the end." |
| 1:55 | Back to the answer page | "Research with Receipts: an agent that executes safely and shows its work. Built on Vultr, end to end." |

## Fallback lines

- **Model slow or down:** tick *Replay the recorded run*. Say: "We'll use the recording from this morning. Same pipeline, receipts and hashes are recomputed, and it's labelled as a replay."
- **A claim comes back partial or unverified:** lean into it. "That's the point: the checker caught a quote that doesn't match the page exactly. It won't let the model bluff."
- **Search rate-limited:** the worker falls back to Wikipedia automatically; keep talking.

## Shots to make sure the video contains

- [ ] Public URL in the address bar
- [ ] Header pills: docker sandbox and Vultr model
- [ ] One highlighted screenshot receipt, opened
- [ ] One Python receipt (code + stdout), ideally with a self-heal retry
- [ ] Verify SHA-256 turning green
- [ ] Hostile page run with the guard event and blocked requests
- [ ] Containment test at 8/8
