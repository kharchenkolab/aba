# OnDemand launcher app — deployment contracts

The `aba/` directory is the OnDemand batch-connect app. It is consumed two
ways: copied verbatim (a bare deployment), or staged by a SITE deployment
repo's deploy script (which may rewrite files in flight). These contracts
keep both paths healthy.

## Shipped files render clean (no template tokens)

Every file in `aba/` must be presentable AS SHIPPED: no `__TOKEN__`
placeholders that only an out-of-repo deploy script knows to replace — a
bare deployment must never show template artifacts on the card
(`tests/test_ood_template_contracts.py` guards this). ERB is fine (OnDemand
renders it, and our ERB is written fail-safe: a missing input degrades to
omitting the element, never to an error or a placeholder).

## Site deployer contract (insert-if-deploying)

A site deploy script MAY enrich the app in flight — e.g. INSERT a version
line as the first paragraph of `manifest.yml`'s description. Additions are
inserts, not token replacements, so a deployment that skips the step gets a
clean default rather than a broken one. The connect-page version footer
needs no deploy-time work at all: `info.md.erb` renders it deployment-side
from `aba-env.sh` + the publish-tree catalog, fail-safe.

## Card-only changes must not need a SIF rebuild

Anything the user sees on the card / form / connect page (`manifest.yml`,
`form.yml.erb`, `info.md.erb`, `view.html.erb`, `icon.svg`) must be
resolvable at render or deploy time — never baked into the image. If a
change to these files requires rebuilding the SIF, the change is designed
wrong.

## The instance ladder lives in site.yaml, and two files read it

Cores and memory only mean something against a node's core count and its
memory-per-core, so the SITE owns the ladder (`site.yaml` → `form.instances`,
`form.walltimes`). Two templates consume it and must agree: `form.yml.erb`
renders the menu, `submit.yml.erb` turns the chosen id into
`-c <cores> --mem=<mem>`. Each also carries a built-in fallback for a
deployment that ships no site config; those fallbacks are byte-identical by
contract, because drift there means the card *displays* one size and *submits*
another with nothing failing.

`--mem` is not optional. Under Slurm `CR_CORE_MEMORY` with no `DefMemPerCPU` /
`DefMemPerNode`, a job that requests no memory is allocated the entire node's
memory, so one session locks every other core on its node out of the pool.

Both are dashboard-side templates: they render before any of this app's shell
has run, so nothing has exported `ABA_SHARE` yet and the `/cluster/aba` literal
in each file is the only thing that resolves `site.yaml`. Both must therefore be
on the site deployer's share-root rewrite list. The `template/` scripts are
different — they run on the node after `before.sh.erb` exports the real value,
and take it from the environment. `tests/test_ood_template_contracts.py` pins
both halves.

## The card icon is found by filename

OnDemand resolves the launcher icon positionally, not from the manifest:
`OodApp#icon_uri` serves `icon.svg` if the app root has one, else
`icon.png`, else a Font Awesome name from the manifest's `icon:`, else
`fas://cog` — generic gears. Renaming or dropping the file therefore
degrades the card silently, with nothing logged; `tests/
test_ood_template_contracts.py` asserts the filename for that reason.

The dashboard embeds it as `<img src=…>`, a separate document that
inherits no page color and loads no external resource, sized into a square
`.app-icon` box at three sizes (100px card, 24px apps table, 14px navbar).
So the file needs literal colors and a square viewBox — `currentColor`,
which the app's own inline `BrandIcon` can use freely, resolves to black
here. Same guard covers those.

## Deploy ordering: backend before substrate-behavior flips

Flipping the deployment to MOUNTED published env packs (squashfuse baked +
`/dev/fuse` bound — `install/sif/build.sh`) makes every base env cold
(adopted read-only; empty package cache) and mount-scoped (no usable prefix
outside its activation namespace). The backend must already understand that
topology — the session runtime contract, activation-composed exec, pylib /
rlib overlay layers, eco-passthrough isolated envs. Deploy order is
therefore: **backend to current `main` first, then the mount flip.**
Version skew the other way is not hypothetical: an old backend against a
new substrate fails every default-lane exec with
`env.realize_failed — no local prefix` (observed at fleet scale, 2026-07-20).
