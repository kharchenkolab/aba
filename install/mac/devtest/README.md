# Mac installer smoke test

`smoke.py` runs the **real** install playbook
(`../helper/src/aba_installer/install.yml`) end to end, but confined to a
throwaway root so it's repeatable on any Mac and removable in one command.
It's how you validate `environment.yml` + the playbook on a real Mac without
touching the machine's actual `~/.aba`, `~/bin`, or `~/Library`.

## How it isolates

Everything the install writes is keyed off `$HOME`, so the harness redirects
`$HOME` to `<root>/home` (default root `~/aba/.smoke`). The conda env, the
cloned repo, the launcher (`~/.aba/bin/aba`) — all inside the throwaway tree.

It also points the playbook at the **working tree** instead of GitHub via the
`ABA_ENV_YML_SRC` / `ABA_REPO_SRC` escape hatches, so un-pushed changes are
what get installed and tested.

```
<root>/home/                  redirected $HOME
<root>/home/.aba/             ABA_HOME (env, repo, launcher, logs, config)
<root>/mamba/                 conda package cache — kept across runs (fast re-installs)
<root>/bin/micromamba         micromamba binary — kept
```

## Usage

```sh
# one-time: a venv with the helper installed (kept under ~/aba, not the repo)
python3 -m venv ~/aba/.venv-installer
~/aba/.venv-installer/bin/pip install -e install/core/helper

PY=~/aba/.venv-installer/bin/python

$PY install/mac/devtest/smoke.py up        # full from-scratch install
$PY install/mac/devtest/smoke.py serve      # start backend + verify it serves the UI
$PY install/mac/devtest/smoke.py status     # what's installed / running
$PY install/mac/devtest/smoke.py stop       # stop the backend
$PY install/mac/devtest/smoke.py down        # remove the install (keep caches)
$PY install/mac/devtest/smoke.py down --purge  # remove everything incl. caches

# iterate on one step (the slow one is create-env, ~700 MB first time):
$PY install/mac/devtest/smoke.py run create-env
$PY install/mac/devtest/smoke.py --list      # step ids
```

Set `ABA_SMOKE_ROOT` to relocate the throwaway root.

## What a green run proves

- `environment.yml` resolves + installs on this arch (incl. R + Bioconductor).
- `R` runs (the install dir is space-free — the conda R wrapper breaks on a
  space, which is why ABA installs under `~/.aba`, not `~/Library/Application
  Support/ABA`).
- The frontend builds (`npm ci` + `vite build`) and the backend serves the
  built SPA at `http://127.0.0.1:8000` with `/api` on the same origin.
- The `aba` launcher installs and `aba up` boots the backend.
