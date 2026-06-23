# ABA Apptainer image (T9)

Self-contained ABA image so a compute node runs ABA from one artifact instead of
the bind-mounted host venv. **v1 = self-contained** (bakes the working conda venv
+ backend + prefix-built frontend dist). The *slim image + mounted shared conda
env* variant (per `misc/ondemand.md` P7) is the follow-up.

## Toolchain (this box has no system apptainer)
Bootstrapped rootless apptainer via micromamba (unprivileged user namespaces are
enabled: `unprivileged_userns_clone=1`):

```sh
cd /home/pkharchenko/aba/tools
curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj bin/micromamba
MAMBA_ROOT_PREFIX=$PWD/mamba ./bin/micromamba create -y -p ./apptainer-env -c conda-forge \
    apptainer squashfuse fuse-overlayfs e2fsprogs
```

**Gotcha:** apptainer finds `squashfuse` on `PATH`. Invoke it with `apptainer-env/bin`
on `PATH`, else it can't mount the SIF and falls back to *extracting the whole image
to a temp sandbox on every run* (slow). A real cluster's setuid apptainer mounts
directly regardless.

```sh
export PATH=/home/pkharchenko/aba/tools/apptainer-env/bin:$PATH
export APPTAINER=/home/pkharchenko/aba/tools/apptainer-env/bin/apptainer
export APPTAINER_TMPDIR=/home/pkharchenko/aba/tools/apptainer-tmp   # on /home (big)
```

## Build
Definition: `tools/aba.def`. The repo has dangling/cyclic symlinks (`vendor_skills/pagoda2`,
`system_bundle/.claude -> .`) that apptainer's `%files` (`cp -fLr`, follows links)
chokes on, so the backend + dist are pre-staged clean:

```sh
STAGE=/home/pkharchenko/aba/tools/stage; rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -a /home/pkharchenko/aba/aba/backend "$STAGE/backend"
cp -a /home/pkharchenko/aba/aba/frontend/dist "$STAGE/frontend-dist"
# drop broken links + ancestor-cycle links (.claude -> .)
find "$STAGE" -type l | while read l; do readlink -e "$l" >/dev/null 2>&1 || rm -f "$l"; done
find "$STAGE" -type l | while read l; do d=$(readlink -f "$(dirname "$l")"); t=$(readlink -f "$l"); \
  [ -d "$t" ] && case "$d/" in "$t/"*) rm -f "$l";; esac; done

$APPTAINER build --sandbox tools/aba_sandbox/ tools/aba.def   # iterate (no per-run extraction)
$APPTAINER build --force  tools/aba.sif      tools/aba_sandbox/   # portable artifact (~2-2.5G)
```
The venv is baked at `/opt/aba-venv` (NOT under `/home` — `--containall` shadows
`/home`). Conda's python resolves its prefix from the binary location, so `/opt` is
fine (verified: all heavy deps import).

The **R/CLI tools env** must be baked the same way, at a sibling image path
(`/opt/aba-envs/tools`), and `ABA_TOOLS_DIR` pointed at it (run block below).
Otherwise the tools env defaults to `ABA_ENVS_DIR/tools` (the per-group dir) and
R **rebuilds for every lab** on first use. Bake it with the same micromamba
create the runtime uses — `core/exec/r.py`'s `RUNTIME_SPECS + R_CORE_DEPS` plus
`-c conda-forge r-irkernel` (the IRkernel run_r needs; ~1–2G total). Only the R
*base* ships in the image; per-project `r_libs` + the `pylib` overlay (growth)
still land under the writable `ABA_ENVS_DIR` bind. Same base/growth split as
Python (venv base in image; pip overlay per-group).

## Run (host-side validation — driver: `tests/ood/_sifval.py`)
On the host, `/groups` + `/cluster/aba` don't exist (they're container mounts), so
bind the mock dirs:

```sh
$APPTAINER run --containall \
  --bind /home/pkharchenko/aba/ood-groups:/groups \
  --bind /home/pkharchenko/aba/ood-cluster:/cluster/aba \
  --env ABA_SITE_CONFIG=/cluster/aba/site.yaml --env ABA_GROUP=kharchenko \
  --env ABA_RUNTIME_DIR=/groups/kharchenko/aba/<user> \
  --env ABA_ENVS_DIR=/groups/kharchenko/aba/.envs \
  --env ABA_TOOLS_DIR=/opt/aba-envs/tools \
  --env ANTHROPIC_API_KEY=... --env ABA_LLM_CREDENTIAL=apikey \
  --env ABA_PORT=8765 tools/aba.sif
```
HOME is set inside the runscript to `$ABA_RUNTIME_DIR/.home` (apptainer forbids
`--env HOME`). **Validated:** health, bundle scope `[system, lab]` from site.yaml,
streamed chat (real Anthropic), `run_python` (in-image venv).

## OOD wiring (production)
On a real cluster node (setuid apptainer, no docker nesting) the OOD `script.sh.erb`
replaces the direct `uvicorn` call with `apptainer exec <binds/env> aba.sif python -m
uvicorn …` — same binds/env as above, plus the per-session frontend-prefix copy
(`cp /opt/aba/frontend-dist -> writable`, sed `__OOD_PREFIX__`, bind it, point
`ABA_FRONTEND_DIST` at it). **Not run through the dev OOD nodes** (c1/c2 are docker
containers without apptainer — nested apptainer-in-docker is a separate dev concern);
the artifact is validated host-side.
