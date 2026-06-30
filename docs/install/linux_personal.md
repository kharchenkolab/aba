# Installing ABA on Linux

ABA is a local workspace where an AI agent does bioinformatics for you — loads
data, runs analyses, makes figures — through a chat in your browser. This guide
installs it for **one user** on a Linux desktop, workstation, or server.

- To offload heavy jobs to a **Slurm cluster**, use
  [cluster_personal.md](cluster_personal.md) instead.
- For a **multi-user** deployment via Open OnDemand, see
  [cluster_open_ondemand.md](cluster_open_ondemand.md).

## What you'll need

- `git` and `curl`. A usable **Python 3** (≥3.9 with `venv`) is ideal but **not
  required** — if none is on your `PATH`, the installer bootstraps its own with
  micromamba. To provide one anyway:
  - Debian/Ubuntu: `sudo apt install git curl python3-venv`
  - Fedora/RHEL: `sudo dnf install git curl python3`
- ~10–12 GB free in your home directory (the bioinformatics environment).
- A credential — an **Anthropic API key** or a **Claude.ai subscription**.

## Install

The `aba` repository is private for now, so clone it over SSH and run the installer
from the checkout (it installs *from* the clone — no second download):

```bash
git clone git@github.com:kharchenkolab/aba.git
cd aba
./install/linux/setup.sh                 # desktop: opens a browser to finish setup
#   or, on a server / login node with no browser:
./install/linux/setup.sh --headless
```

The installer checks your prerequisites (with a clear message if something's
missing), builds a **self-contained** environment (Python + R + the bio stack —
your system Python is never touched), imports the curated **recipe library**,
builds the web interface, and starts ABA. Expect ~15–20 min and ~10 GB. It installs
under `~/.aba` and adds an `aba` launcher.

> **No usable Python?** The installer bootstraps one with micromamba automatically,
> so a missing/old system Python rarely blocks you. To use a specific interpreter
> instead, point it there: `ABA_PYTHON=/path/to/python ./install/linux/setup.sh`.

### Where your data lives

By default everything is under `~/.aba`. The *runtime* — your projects, data,
results, and per-user environments (the part that grows) — can go on a bigger or
faster disk while the base environment stays in `~/.aba`:

```bash
./install/linux/setup.sh --runtime-dir /data/$USER/aba
```

Choose this at install time. To relocate the *entire* install (the environment
**and** the data) instead, use `--install-dir /opt/aba` (default `~/.aba`):

```bash
./install/linux/setup.sh --install-dir /opt/aba
```

Package caches (conda packages and pip downloads) also live under the install dir,
so `--install-dir` moves them with the environment — and conda can hardlink from the
cache into the env, which keeps the build fast as long as both are on one filesystem.

## Sign in

```bash
aba auth                      # Sign in with Claude: prints a URL — approve it in any
                              # browser (e.g. on your laptop) and paste the code back
aba auth --api-key sk-ant-…   # or use an Anthropic API key
```

## Using ABA

- On a desktop, open **http://localhost:8000**.
- On a server, tunnel from your laptop, then open the same URL:
  ```bash
  ssh -L 8000:localhost:8000 you@server
  ```
- Commands:

  | command | what it does |
  |---|---|
  | `aba up` / `aba stop` | start / stop ABA |
  | `aba status` / `aba logs` | is it running? / tail the log |
  | `aba update` | pull the latest ABA + recipe library, refresh the environment |
  | `aba doctor` | diagnose problems and suggest fixes |
  | `aba auth` | set or change your credential |

## Keeping it up to date

`aba update` pulls the newest ABA and recipe library and refreshes the environment
(a quick no-op when nothing changed).

## If something goes wrong

Run **`aba doctor`** — it checks the environment, web build, recipes, credential,
and backend, and prints how to fix each problem. Re-running `setup.sh` is safe; it
skips whatever is already done.
