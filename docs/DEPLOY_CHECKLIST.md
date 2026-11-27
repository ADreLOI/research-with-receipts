# Deploy checklist (copy-paste, about 25 minutes)

Never paste keys into chat or commit them. They only go into `/opt/rwr/.env` on the VM.

## 1. Vultr console (Dre, ~10 min)

1. **Serverless → Inference → Add subscription.** Copy the API key: it becomes `VULTR_INFERENCE_API_KEY`.
2. **Compute → Deploy → Cloud Compute (Shared CPU)**: Ubuntu 24.04 x64, region Amsterdam or Frankfurt, plan with **2 vCPU / 4 GB** or more, add your **SSH key**. Note the public IP.
3. Optional: **Account → API → Enable**, add the VM's IP to the access list. That key is `VULTR_API_KEY`, only needed for `scripts/vultr_sandbox_vm.py`.

## 2. On the VM (~15 min, mostly the image build)

```bash
ssh root@<VM_IP>

# Private repo: add a read-only deploy key (or make the repo public first).
ssh-keygen -t ed25519 -N "" -f ~/.ssh/rwr_deploy && cat ~/.ssh/rwr_deploy.pub
#   -> GitHub repo > Settings > Deploy keys > Add (read-only), then:
GIT_SSH_COMMAND="ssh -i ~/.ssh/rwr_deploy -o StrictHostKeyChecking=accept-new" \
  git clone -b claude/research-with-receipts git@github.com:ADreLOI/research-with-receipts.git /opt/rwr

cd /opt/rwr
# Optional free HTTPS hostname: replace dots in the IP with dashes.
sudo bash deploy/setup_vultr_vm.sh <VM-IP-WITH-DASHES>.sslip.io      # or with no argument for plain http://<VM_IP>

sudo nano /opt/rwr/.env    # set VULTR_INFERENCE_API_KEY=...  and  APP_ACCESS_CODE=<something>
sudo systemctl restart rwr
```

## 3. Smoke test (~5 min)

```bash
cd /opt/rwr
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
sudo -u rwr .venv/bin/python scripts/record_demo.py
git config --global --add safe.directory /opt/rwr     # the checkout is owned by the rwr user
git add demo/cassettes && git commit -m "Record demo cassettes on the VM" && git push   # needs a key with write access
```

## 5. Optional: sandboxes on a separate Vultr VM

```bash
echo "VULTR_API_KEY=..." | sudo tee -a /opt/rwr/.env
sudo -u rwr /opt/rwr/.venv/bin/python scripts/vultr_sandbox_vm.py create --write-env
sudo systemctl restart rwr
```

After the event: `sudo -u rwr /opt/rwr/.venv/bin/python scripts/vultr_sandbox_vm.py destroy --all`, then delete the app VM and the inference subscription in the console if you no longer need them.
