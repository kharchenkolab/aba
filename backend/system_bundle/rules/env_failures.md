# Environment & install failures — the playbook

Install/environment tool results are TYPED: read `error`/`stage`/`retryable`
and the `attempts` list (one typed record per lane tried) before acting. Each
attempt names its own remedy in its `hints` — never synthesize an aggregate
guess. Never resubmit an unchanged failing request more than once.

| signal | meaning | your move |
|---|---|---|
| `retryable: true` | transient (index/network/site) — NOT a package problem | retry once before changing anything; if it persists, tell the user |
| `env.solve_conflict` | the name/pin cannot be satisfied in the configured registries | check the NAME first (typos are resolve-stage, not environment problems); then relax the pin hints name |
| `env.solve_failed` | a registry index is unreachable | retryable — the packages are not missing, the index is |
| `env.realize_failed` + build signatures (configure/compiler/header errors) | a source BUILD died — usually a missing SYSTEM library | the project session can never carry a system library. Route to an isolated env: `make_isolated_env(name='<pkg>-env', language='r', packages=['r-<pkg>'])` then `set_active_env('<pkg>-env', language='r')` — the solver pulls C libraries transitively; you do not need to name them. CAVEAT: a promoted env moves the run lanes, not viewer converters — a package a VIEWER needs must go into the shared base pack instead; say so rather than retrying |
| `env.unavailable_in_lanes` | every ranked lane was tried; `attempts` carries each lane's typed verdict | read the FIRST attempt's error (usually the informative one); each attempt names its own lever |
| attempt `outcome: skipped, skip_reason: halted` | an outage/substrate fault stopped the chain — availability was NOT determined | treat as infrastructure, not as "package unavailable" |
| `installed_unverified` | landed but the postcondition did not run | verify before relying on it (a quick load check in the target) |
| `verification: verified_now` | the claim was proven live against a ready realization (site named in the result) | trust it — install-target and proof-target are the same environment |
| `verification: deferred` | the claim is recorded on the env identity and enforced at every realization | normal for a fresh/unrealized env — NOT a gap: a broken build surfaces at first use as a typed failure naming the claim; no need to pre-verify yourself |
| `verified: unknown` | the CHECK could not run (interpreter/site trouble) | retry the check, not the install — unknown ≠ failed |
| `session.cold_base` | this base cannot be cloned here; delta lanes only | use the levers in hints (package-layer installs work; bespoke installers need `writes_to=`) |
| "Installed, but not loadable" | a build reported success while producing nothing | treat as a build failure: see the realize_failed row |
| `task.invalid` | the request itself is malformed | fix the call per hints; not a retry case |

Promotion recap: `set_active_env(name, language=…)` makes an isolated env
ambient — bare `run_python`/`run_r` and later installs land there until reset
with `set_active_env('default')`. Install-target, verify-target and
execution-target are always the same environment; if a result names an env,
that is where the package lives.
