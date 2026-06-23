# tests/ood — Open OnDemand integration test harness

Validates ABA's OnDemand deployment chain (`misc/ondemand_plan.md` T0–T10)
on a developer box via Docker Compose, before any cluster access. **Status:
T0 + T1 done, and the REAL ABA app launches end-to-end** — the OOD control
panel starts the actual ABA backend on a Slurm compute node and reverse-proxies
the full ABA UI to the browser (`_shots/03_app.png` = the rendered "Welcome to
ABA" page, assets + `/api` working through the proxy). Nothing here is committed
yet.

One app under `ood-apps/`: `aba/` — the real ABA app, deployed as sys app
**`aba`**. Its `form.yml.erb` is a **preflight control panel** — server-side ERB
discovers lab groups under `/groups`, marks which have an ABA bundle
(`/groups/<lab>/aba`), and detects a cached credential to adjust the token field.
(The earlier `aba_mock` hello-server plumbing fixture was retired 2026-06-22 —
recoverable from git if a lightweight proxy-only proof is ever needed again.)
`ood-apps/sse_probe/` is a separate SSE-reattach diagnostic, not an ABA launcher.

The external OOD stack ([`hmdc/ondemand_development`](https://github.com/hmdc/ondemand_development))
is cloned **outside** this repo at `~/aba/ood-dev` (it's large + has its own
git). This dir holds only *our* assets: the ABA app and the Playwright driver.

```
tests/ood/
  README.md            this file
  ood-apps/aba/        the real ABA OOD interactive app (the batch_connect app)
    manifest.yml  form.yml.erb  submit.yml.erb  view.html.erb  info.md.erb  template/{before,script}.sh.erb
  _pwenv.py            shared Playwright setup (LD_LIBRARY_PATH re-exec + auth/base)
  round_trip.py        Launch -> Running -> Connect -> verify -> Delete driver
  discover.py _form.py  one-off dashboard/form inspectors
  _shots/              screenshots from the last round_trip run
```

## Bring the stack up (this box)

Docker runs via the `docker` group; the login shell predates the membership,
so wrap docker/compose/make in `sg docker -c '…'` (or open a fresh shell).

```bash
# 1. clone + init the dashboard submodule (a --depth1 clone leaves it empty)
git clone https://github.com/hmdc/ondemand_development ~/aba/ood-dev
cd ~/aba/ood-dev && git submodule update --init --recursive

# 2. build the Rails dashboard (pulls a builder image, runs bundle install)
sg docker -c 'make ood_build'

# 3. launch detached (the Makefile's dev_up runs in the FOREGROUND; -d instead)
sg docker -c 'env SID_SLURM_IMAGE=hmdc/sid-slurm:v3-slurm-21-08-6-1 \
  SID_OOD_IMAGE=hmdc/sid-ood:ood-3.1.7.el8 OOD_UID=$(id -u) OOD_GID=$(id -g) \
  docker compose up --build -d'

# 4. CRITICAL perm fix — the dashboard PUN runs as container uid 3210 and must
#    write /home/ood/ondemand/data (= ./data, owned by your host uid). Without
#    this the dashboard 500s ("Permission denied @ dir_s_mkdir .../data/sys").
chmod -R 777 ~/aba/ood-dev/data

# 5. warm up (first authenticated request triggers a ~few-sec passenger spawn)
curl -k -u ood:ood https://localhost:33000/pun/sys/dashboard -o /dev/null -w '%{http_code}\n'
```

Published ports (all free on this box): **33000** HTTPS dashboard, **34000**
Request Tracker, **35000** maildev. Login **ood / ood** (HTTP Basic). OOD v3.1.7.

### Access from your laptop
```bash
ssh -L 33000:localhost:33000 <thisbox>
# then https://localhost:33000  (accept the self-signed cert), login ood/ood
```

### Teardown
```bash
sg docker -c 'cd ~/aba/ood-dev && env SID_SLURM_IMAGE=hmdc/sid-slurm:v3-slurm-21-08-6-1 \
  SID_OOD_IMAGE=hmdc/sid-ood:ood-3.1.7.el8 OOD_UID=$(id -u) OOD_GID=$(id -g) docker compose down -v'
```

## The mock ABA app (T1 — retired)

T1 originally proved connect-in-browser with a lightweight `aba_mock`
(`python3 -m http.server`) — needed because the stock RStudio/rdesktop apps
don't boot on this bare cluster (they exec absent OSC binaries → straight to
*Completed*). That mock was **retired 2026-06-22** once the real `aba` app
became the only deployable one; recover it from git if a backend-free proxy
proof is ever needed again. Deploy the real app per the section below.

## Run the automated round-trip

Playwright (Python) drives the dashboard headless. chromium needs 3 libs not on
this host (`libgbm`, `libwayland-server`, + conda `libxkbcommon`/`xorg-libxdamage`),
staged in `~/aba/aba_runtime/.pwlibs/lib`; `_pwenv.py` puts that on
`LD_LIBRARY_PATH` via a re-exec, so just:
```bash
cd ~/aba/aba/tests/ood
~/aba/aba/.venv/bin/python round_trip.py aba "Connect to ABA" "ABA"
# PASS = launched -> Slurm job -> Running -> connected page showed the marker -> deleted
```

## Gotchas hit during T0 (so you don't re-debug them)
- `--depth 1` clone leaves the `ondemand` submodule empty → `ood_build` fails.
- `make dev_up` runs `docker compose up` in the **foreground**; use `-d`.
- `./data` perm/uid mismatch → dashboard 500 until `chmod -R 777 ./data`.
- Stock RStudio/rdesktop apps reference absent OSC binaries → never Running.
- Slurm `srun -N2` hits `PartitionNodeLimit` (partition caps nodes); use `-N1`.
- Session cards: the left sidebar is also a `div.card`; select the card that
  has the **Delete** button, or you read the wrong element's status.
- cgroup v2 host + privileged systemd `ood` container works on Docker 26.
- **App `script.sh.erb` must be executable** — OOD copies the template file's
  mode onto the rendered `script.sh`, and the wrapper execs it directly. A 644
  template => `script.sh: Permission denied` => job dies instantly. `chmod +x`.
- **The `basic` template `export port` but never assigns it** — the app's
  `before.sh.erb` must `port=$(find_port ${host})` (like the stock RStudio app),
  or `script.sh` gets an empty `$port` and the server errors out.
- `docker cp SRC dev_ood:/…/aba` into an **existing** dir merges/leaves
  stale files (and old modes) — `rm -rf` the target in the container first, then
  cp fresh, so perms (the +x) come across cleanly.
- **Each Slurm node has 1 CPU**, and OOD "Delete" doesn't always `scancel` the
  job — leftover RUNNING jobs fill both nodes and the next launch hangs in
  `PENDING (Resources)`. Between runs: `docker exec dev_slurmctld scancel --user=ood`.
- **Real ABA in the node container:** `docker-compose.yml` bind-mounts
  `/home/pkharchenko/aba` into c1/c2 so the conda `.venv` + repo are visible;
  the app's `script.sh.erb` runs `…/.venv/bin/python -m uvicorn main:app
  --port $port` (no `--root-path` — `/rnode` strips the prefix). ABA imports
  cleanly in the bare Slurm image (probed) — no extra system libs needed.

## Real ABA app (`install/ood/aba`, deployed as sys app `aba`)

> The deployable app + the node preflight live in **`install/ood/`** (the app is
> `install/ood/aba`, the preflight `install/ood/aba_preflight.py`); this dir keeps
> only the test drivers + the harness setup. The SIF build def is in `install/sif/`.

Frontend prefix-awareness (so the SPA works under the proxy):
`frontend/vite.config.ts` `base: process.env.ABA_OOD_BASE || '/'` (dev unaffected);
`frontend/src/oodBase.ts` wraps `fetch`/`EventSource` to prepend `BASE_URL` to
`/api`+`/artifacts`; `main.tsx` sets the router `basename`. Build for OOD with:
```bash
cd frontend && ABA_OOD_BASE='/__OOD_PREFIX__/' npx vite build   # (npm run build trips a pre-existing tsc error in a test file)
```
`backend/main.py` gained `ABA_FRONTEND_DIST` (env override) so each session
serves a private dist copy with `__OOD_PREFIX__` rewritten to `rnode/$host/$port`.
Deploy / launch:
```bash
sg docker -c 'docker exec dev_ood rm -rf /var/www/ood/apps/sys/aba && \
  docker cp ~/aba/aba/install/ood/aba dev_ood:/var/www/ood/apps/sys/aba && \
  docker exec dev_ood chmod -R a+rX /var/www/ood/apps/sys/aba'
# then: tunnel in -> Interactive Apps -> ABA -> paste an Anthropic key (for chat) -> Launch -> Connect
python round_trip.py aba "Connect to ABA" "ABA"   # headless PASS check

## Next (T2+)
Preflight + `status.yaml` + status-card `view.html.erb`; then `site.yaml`
variations (T3), credential chain (T5), and eventually running the real ABA
from the Apptainer image rather than the bind-mounted repo (T7). See
`misc/ondemand_plan.md`.
