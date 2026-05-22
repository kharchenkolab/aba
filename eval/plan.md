# ABA evaluation loop — technical plan

A systematic, AI-driven way to evaluate and progressively optimize the platform
for its mission: **help a scientist run the full research cycle — data →
analysis → patterns → findings → claims → manuscript — as a partner**, and stay
usable as work accumulates over a long project.

Replaces "hand-test a few scenarios" with an automated loop: realistic scenarios
run against the real platform, instrumented and judged, so design changes can be
A/B-compared and regressions caught.

---

## 1. What we optimize (the questions, made measurable)

- **Navigation at scale** — as results/findings accumulate, can the scientist
  find what they need? → retrieval recall, actions/time-to-find.
- **Clutter** — do they get lost? → curated-vs-total ratio, scan cost.
- **Effort** — how much manual work to track findings, form claims, progress to
  a manuscript? → actions-per-milestone.
- **Consistency** — versions, duplicates, contradictions, dangling provenance.
- **Long-horizon** — resume after weeks: context-to-resume, correct recall.
- **Perception/interaction** — is the right control visible, reachable, legible?
  (the things a pure-API agent can't feel).

---

## 2. Architecture — two layers over one scenario corpus

```
                       ┌─────────── scenario corpus (eval/scenarios) ───────────┐
                       │  L1..L4 specs + planted ground truth + seeded graphs    │
                       └─────────────────────────────┬───────────────────────────┘
                                                      │
        ┌─────────────── Layer A: LOGIC / IA ─────────┴───── Layer B: PERCEPTION ───────────┐
        │  simulated scientist drives the real API     │  the SAME states rendered in a       │
        │  (the info the UI surfaces)                   │  real browser are audited/judged     │
        │  → effort, retrieval, provenance, consistency │  → visibility, clipping, contrast,   │
        │                                               │    scroll/click cost, discoverability │
        └───────────────────────────────────────────────┴──────────────────────────────────────┘
                                                      │
                                  metrics + probes + judges → scorecard
                                                      │
                          baseline → friction report → design change (variant)
                                  → re-run corpus → A/B compare → keep/revert
```

Layer A tests *information architecture and logic*; Layer B tests the *rendered
experience*. They share the corpus and run on the same produced states.

---

## 3. Layer A — the simulated scientist (logic / IA)

An LLM that **plays the scientist**, pursuing a research goal, driving the
platform through the same affordances a human has. It lives in `eval/driver/`.

**Loop:**
```
persona + goal
  → render platform state as the UI shows it (view.py)
  → scientist LLM picks ONE action (tool call)         ← Haiku by default
  → execute via the real API (actions.py)
  → platform/Guide responds, state changes
  → repeat until goal met or step budget; log every action
```

**`view.py` (UI-honest renderer).** Calls only the platform's *read* endpoints
and formats them into the compact text a human sees — **and nothing more** (no
raw graph). Sources: `GET /api/entities` (the tree as the rail shows it, with
section caps), `GET /api/messages` (chat tail), focused entity summary /
`GET /api/entities/:id/preview`, advisor notes, and `GET /api/search?q=` only
when the agent searches. *If the renderer hides what the UI hides, the agent
gets lost where a human would — that's what validates the eval.*

**`actions.py` (action vocabulary → endpoints).** The scientist's "tools" mirror
UI affordances:
| action | endpoint |
|---|---|
| `send_message(text, focus?)` | `POST /api/chat` (runs the Guide + tools) |
| `search(query)` | `GET /api/search` |
| `focus(entity_id)` | (client-side; changes what `view.py` shows + scopes) |
| `pin(entity_id)` / `keep_message(text)` | `PATCH /api/entities/:id` / `POST /api/messages/pin` |
| `promote_figure(id, interpretation)` | `POST /api/entities/:id/promote-to-result` |
| `draft_finding(refs)` → review → save | `POST /api/findings/draft` → `…/from-draft` |
| `edit_finding(id, summary/caveat/status)` | `POST /api/findings/:id/fields` |
| `promote_to_claim(finding_ids, text)` | `POST /api/findings` (claim) |

**`personas/`** — system prompts: a scientist with a goal and working style
("explore, react to results, keep what matters, build findings, abandon dead
ends"). One persona per scenario or a shared base + goal override.

### Cost modes
| mode | scientist | Guide | cost | use |
|---|---|---|---|---|
| **Live** | Haiku | Haiku | tokens (2 agents) | open-ended: does a change help an *exploring* scientist? Small corpus, periodic. |
| **Replay** | recorded action log | fake JSONL (existing seam) | ≈ free | regression + UI A/B on a *fixed* trajectory, every commit. |

The existing fake-model seam (`backend/llm.py`, `ABA_FAKE_SESSION`) already
replays the **Guide**. `driver/replay.py` extends it to replay the **scientist's
action log** too → fully deterministic, zero-token runs for regression/A-B of
UI/organization changes (the produced state is identical; only the design under
test varies). Default everything to **Haiku**; reserve Opus + judges for
periodic full evals (matches the standing token policy).

---

## 4. Layer B — perception / interaction

Runs the same scenario states in a real browser (reuse the Playwright + Node
toolchain from `tests/e2e/`). Three tiers by cost/signal, in `eval/audits/`.

**Tier 1 — deterministic audits (cheap, no LLM, every state):**
- **contrast** (WCAG) via **axe-core** (`@axe-core/playwright`) → catches
  light-on-light, undefined-color icons.
- **clipping/occlusion** — element rect outside an `overflow:hidden` ancestor,
  or covered (`elementsFromPoint`) → catches clipped menus, tree overflow.
- **reachability** — every primary action present, non-zero, reachable without
  horizontal scroll / within N vertical scrolls.
- **tap-target size**, off-viewport-when-shouldn't-be.
- **Lighthouse** pass for the standard a11y/best-practice signals.

**Tier 2 — Playwright *semantic* driving (interaction cost + hard failures):**
drive via the rendered DOM (`getByRole().click()`); "button not visible" →
logged failure; scroll distance and click count become physical metrics.

**Tier 3 — vision (discoverability + perceptual judge), periodic:**
- find-the-affordance probe: VLM gets a screenshot + "where would you click to
  pin this figure?" → coordinates → click; failure = discoverability defect.
- screenshot **pairwise A/B** judge (clarity, clutter, anything clipped/cramped).
- Anthropic **computer-use** (vision) is the natural fit here.

Tier 1 is the immediate, highest-ROI piece (would have auto-flagged most bugs we
hit by hand). Tiers 2–3 layer on.

---

## 5. Metrics — a balanced scorecard

Collected in `eval/metrics/`. No single number (Goodhart guard).

- **Effort**: actions-per-milestone (first kept figure, a result, a finding,
  a claim, a manuscript section).
- **Retrieval**: recall@k and actions-to-find on probe queries.
- **Clutter**: curated-vs-total entity ratio; items to scan to reach a target;
  staleness.
- **Provenance integrity**: every claim traces data→figure→result→finding→claim;
  count orphans / dangling / contradictions / duplicate findings.
- **Consistency**: versions superseded correctly; no active contradictory claims.
- **Context efficiency**: tokens/context to resume an old artifact (memory
  capsule payoff) — sourced from `context_assemblies` (§3.6).
- **Progression**: did the project climb the pyramid or stall?
- **Perception defects**: Tier-1 audit failures; Tier-2 scroll/click cost;
  Tier-3 discoverability misses.

## 6. Probes (`eval/probes/`)
After a scenario builds state, pose checkable tasks to a fresh agent:
"find the figure with the donor outlier", "list all evidence for claim C3",
"what in the manuscript is unsupported?" Score against the scenario's planted
ground truth.

## 7. Judges (`eval/judges/`)
- **Logic judge** — LLM over final state + trajectory: organized? navigable?
  trustworthy?
- **Perception judge** — VLM over screenshots.
- Always **pairwise A/B** (variant A vs B) — more reliable than absolute scores.
- **Calibrate** judges against occasional human ratings so they track our taste.

## 8. The optimization loop
1. Run the corpus → collect scorecard (Layer A + B).
2. Surface the biggest friction (e.g., "12 actions to assemble a finding";
   "recall@5 for old figures = 40%"; "menu clipped in packed tree").
3. Hypothesize one design change; implement **behind a variant/flag**.
4. Re-run corpus; **A/B compare**; keep iff it improves the target without
   regressing the scorecard. Freeze "golden" scenarios + thresholds as a gate.

---

## 9. Tooling (adopt, don't rebuild)
- Perception/navigation agents: **Anthropic computer-use** (vision, discoverability),
  **Stagehand** / **browser-use** (DOM-grounded, cheaper) — optional; our own
  thin Playwright driver also works.
- Deterministic audits: **axe-core**, **Lighthouse**, **Pa11y**.
- Visual anomaly: **Applitools / Percy** (optional, later).
- Methodology refs: **WebArena / OSWorld** (env + task-success pattern).
- We own: scenarios, mission rubrics, metric glue.

## 10. Repo layout
```
eval/
  plan.md                    # this file
  scenarios/
    scenarios.md             # the corpus spec (v1: 8 scenarios)
    data/gen.py              # synthetic CSV generator (deterministic)
    data/*.csv               # generated datasets
    seed/*.py                # fabricate L4 project graphs via the entity API
  driver/   {personas/, view.py, actions.py, driver.py, replay.py}
  probes/                    # retrieval/navigation challenges + answers
  audits/                    # Tier-1 axe/Lighthouse + clip/contrast checks
  judges/                    # LLM/VLM rubrics + pairwise A/B harness
  metrics/                   # collectors + scorecard
  recordings/                # frozen action+Guide transcripts for Replay (in git)
  runs/                      # outputs: logs, screenshots, scores (gitignored)
```

## 11. Token / cost policy
Plumbing & most iterations: **fake/Haiku**. **Replay** mode = zero tokens for
regression/A-B. **Opus + judges**: periodic full evals only. L1–L2 run often;
L3 periodically; L4 is mostly read/probe over pre-seeded graphs (cheap).

## 12. Risks & mitigations
- **Goodhart** (optimizing a proxy hurts the goal) → balanced scorecard + human
  calibration + qualitative judge.
- **Fidelity ceiling** (LLM scientists ≠ real scientists) → treat metrics as
  *directional*; periodic human spot-checks.
- **Judge self-gaming** → pairwise, diverse scenarios, human-calibrated.
- **Honest rendering** (`view.py` must not leak the backend graph) → the single
  most important validity rule.

## 13. Build stages (each independently useful)
1. **Data + seeds** (`data/gen.py`, `seed/*.py`) — runnable scenario substrate.
2. **Tier-1 audits** on the current app — immediate defect surfacing.
3. **Driver** (`view.py` + `actions.py` + `driver.py`, Haiku) — Layer A live on
   L1–L2; capture effort/provenance metrics.
4. **Probes + metrics** — retrieval/clutter/provenance scoring.
5. **Replay** — freeze transcripts; deterministic regression + A/B.
6. **Judges** (logic + perception, pairwise).
7. **Variant runner + scorecard dashboard** — close the loop.

See `scenarios/scenarios.md` for the v1 corpus and planted ground truth.
