# Deploy checklist (copy-paste, about 25 minutes)

Never paste keys into chat or commit them. They only go into `/opt/rwr/.env` on the VM.

## 1. Vultr console (Dre, ~10 min)

1. **Serverless → Inference → Add subscription.** Copy the API key: it becomes `VULTR_INFERENCE_API_KEY`.
2. **Compute → Deploy → Cloud Compute (Shared CPU)**: Ubuntu 24.04 x64, region Amsterdam or Frankfurt, plan with **2 vCPU / 4 GB** or more, add your **SSH key**. Note the public IP.
3. Optional: **Account → API → Enable**, add the VM's IP to the access list. That key is `VULTR_API_KEY`, only needed for `scripts/vultr_sandbox_vm.py`.

## 2. On the VM (~15 min, mostly the image build)

```bash
ssh root@<VM_IP>

# Private repo: add a deploy key. Tick "Allow write access" so step 4 can push the recordings.
ssh-keygen -t ed25519 -N "" -f ~/.ssh/rwr_deploy && cat ~/.ssh/rwr_deploy.pub
#   -> GitHub repo > Settings > Deploy keys > Add, then:
GIT_SSH_COMMAND="ssh -i ~/.ssh/rwr_deploy -o StrictHostKeyChecking=accept-new" \
  git clone -b claude/research-with-receipts git@github.com:ADreLOI/research-with-receipts.git /opt/rwr

cd /opt/rwr
# Optional free HTTPS hostname: replace dots in the IP with dashes.
sudo bash deploy/setup_vultr_vm.sh <VM-IP-WITH-DASHES>.sslip.io      # or with no argument for plain http://<VM_IP>

sudo nano /opt/rwr/.env    # set VULTR_INFERENCE_API_KEY=...  and  APP_ACCESS_CODE=<something>
sudo systemctl restart rwr
```

## 3. Smoke test (~5 min)

One command checks everything and prints a report that is safe to paste into the chat (no secrets):

```bash
cd /opt/rwr
sudo -u rwr .venv/bin/python scripts/doctor.py                   # ends with "summary: N pass, N warn, 0 fail"
```

It covers the config, Docker and the image, Vultr Inference (model list, a JSON round trip and one real agent
step), the sandbox tools from inside a container, the running app and the recordings. A `WARN` on
`sandbox.search` means DuckDuckGo blocks the VM's IP and the Wikipedia fallback is used; the demo still works,
or set `SEARCH_PROVIDER=brave` with `BRAVE_SEARCH_API_KEY`. Then the individual steps:

```bash
sudo -u rwr .venv/bin/python scripts/check_inference.py          # must end with "chat: OK"
curl -s localhost:8000/api/health | python3 -m json.tool | head -20
sudo -u rwr .venv/bin/python scripts/containment_test.py         # expect 8x CONTAINED
sudo -u rwr .venv/bin/python scripts/demo.py 1                   # live run, streams the action log
journalctl -u rwr -n 100 --no-pager                              # if anything fails, send this (no keys in it)
```

The scripts read `APP_ACCESS_CODE` and `PUBLIC_BASE_URL` from `.env`. Then open the public URL in a browser: the header should show `sandbox: docker (runc)` and `llm: vultr <model>` in green.

## 4. Safety net recording (~5 min)

```bash
cd /opt/rwr
sudo -u rwr .venv/bin/python scripts/record_demo.py        # refuses to save runs that used the mock policy
```

Then get `demo/cassettes/` into the repo, either way works:

- **The VM can push** (deploy key with write access):
  ```bash
  git config --global --add safe.directory /opt/rwr
  git config --global user.name "rwr VM" && git config --global user.email "vm@rwr.invalid"
  git add demo/cassettes && git commit -m "Record demo cassettes on the VM"
  export GIT_SSH_COMMAND="ssh -i ~/.ssh/rwr_deploy"
  git pull --rebase origin claude/research-with-receipts && git push origin HEAD:claude/research-with-receipts
  ```
- **It can't:** `tar czf /tmp/cassettes.tgz -C /opt/rwr demo/cassettes`, copy it off with
  `scp root@<VM_IP>:/tmp/cassettes.tgz .` and upload the file to the project chat. It gets committed from there.

## Updating a running VM

```bash
cd /opt/rwr && sudo bash deploy/update.sh    # pulls, re-installs the egress guard, rebuilds the image, restarts, runs doctor
```

## 5. Demo video without screen recording (~6 min, optional)

`scripts/record_video.py` drives the public URL with a headless browser and records a captioned walkthrough
that follows `docs/DEMO_SCRIPT.md` (live run, hover claims, receipt + Verify SHA-256, hostile page,
containment test). It runs inside the sandbox image, which already has Playwright and Chromium:

```bash
cd /opt/rwr && mkdir -p video && chmod 777 video
set -a && . ./.env && set +a          # exports APP_ACCESS_CODE if you set one
docker run --rm -v "$PWD:/w:ro" -v "$PWD/video:/out" -e APP_ACCESS_CODE \
    rwr-sandbox:latest python /w/scripts/record_video.py "$PUBLIC_BASE_URL" --out /out
ls video/    # demo.webm (1440x900), demo.srt (captions), shot-*.png (real screenshots for the README)
scp root@<VM_IP>:/opt/rwr/video/* .   # from your laptop
```

Live model runs take a minute or two each, so trim the waiting in any editor, or add `--replay` to use the
recorded runs (the UI labels them as replays). Upload `demo.webm` to YouTube (unlisted) or Drive.

## 6. Optional: sandboxes on a separate Vultr VM

```bash
echo "VULTR_API_KEY=..." | sudo tee -a /opt/rwr/.env
sudo -u rwr /opt/rwr/.venv/bin/python scripts/vultr_sandbox_vm.py create --write-env
sudo systemctl restart rwr
```

After the event: `sudo -u rwr /opt/rwr/.venv/bin/python scripts/vultr_sandbox_vm.py destroy --all`, then delete the app VM and the inference subscription in the console if you no longer need them.
