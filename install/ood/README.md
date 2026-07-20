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
`form.yml.erb`, `info.md.erb`, `view.html.erb`) must be resolvable at
render or deploy time — never baked into the image. If a change to these
files requires rebuilding the SIF, the change is designed wrong.

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
