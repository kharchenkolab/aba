# Installing ABA on Linux

ABA is a local workspace where an AI agent does bioinformatics for you — loads
data, runs analyses, makes figures — through a chat in your browser. This guide
installs it for **one user** on a Linux desktop, workstation, or server.

- To offload heavy jobs to a **Slurm cluster**, use
  [cluster_personal.md](cluster_personal.md) instead.
- For a **multi-user** deployment via Open OnDemand, see
  [cluster_open_ondemand.md](cluster_open_ondemand.md).

## What you'll need

- `git`, `curl`, and Python 3 with the `venv` module:
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

> **No `python3-venv`?** The installer will say so. Install the package, or point
> it at any venv-capable python: `ABA_PYTHON=/path/to/python ./install/linux/setup.sh`.

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
