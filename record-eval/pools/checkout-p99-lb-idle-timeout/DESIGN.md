# DESIGN — checkout-p99-lb-idle-timeout

Ground truth for a fictional performance-regression investigation (INC-2419) at a
mid-size e-commerce marketplace, June 2026. `pool.json` is derived from this
document; every number that appears in more than one finding has its single
authoritative value in §4, and §5 states how dependent numbers must relate.
This file is authoring material — it is not replayed.

## 1 · System sketch

Services (Kubernetes on VMs, three availability zones a/b/c):

- **edge-gateway** — TLS termination, routing, request deadline **2,500 ms**
  (exceeding it returns 504 to the client).
- **orders-api** — Java service, 12 pods; owns `POST /v1/checkout` and
  `GET /v1/orders/{id}/status`. Talks to pricing-svc and inventory-svc (parallel
  calls) and to the ledger database.
- **pricing-svc** — quote computation; v341 (Jun 16) introduced a new rules engine.
- **inventory-svc** — stock reservation.
- **ledger** — PostgreSQL 15 primary + 2 replicas. orders-api reaches the primary
  through a stable VIP on the **ilb** (internal stateful L4 load-balancer fleet,
  one logical instance per AZ) used for failover routing.
- **feature-store** — pricing-svc's data tier (not on the checkout critical path
  at p99; pricing+inventory are called in parallel).

Database access from orders-api: HikariCP per pod — maxPoolSize 100, minIdle 10,
idleTimeout 600 s, maxLifetime 1,800 s; pgJDBC socketTimeout 1,500 ms; TCP
keepalive **off** (pre-incident). One application-level retry on connection
failure. A checkout performs a median **3** ledger transactions (borrows);
order-status performs **1**.

Traffic profile: weekday peak 14:00–19:00 UTC. orders-api total ~2,400 rps peak;
checkout 310 rps peak / 40 rps trough; order-status 480 rps peak. Peak arrivals
are bursty (promo pushes), so pools oscillate 55–85 open connections per pod.
AZ traffic split has been **20/40/40** (a/b/c) since a May 21 zonal drain was
never rebalanced — this is why stage 1 of the change (AZ-a only) produced a
sub-threshold fleet signal.

## 2 · Ground truth — what actually happened

**Root cause.** Change **NETOPS-4112** ("connection-table hardening" after a
May 30 ilb table-exhaustion incident; projected occupancy 2.1 M → 640 k entries)
lowered the ilb idle-flow timeout **350 s → 60 s**, staged: **AZ-a Jun 12
22:04 UTC**, **AZ-b/c Jun 16 11:30 UTC**. The ilb's eviction policy drops
non-SYN packets of evicted flows **without RST**.

**Mechanism.** Pooled ledger connections that idle ≥ 60 s (Hikari only evicts at
600 s, keepalive off) become silent landmines: the first packet after idle is
retransmitted (3 retransmits, exponential backoff), never answered, and the
request burns the full 1,500 ms socketTimeout, then reconnects and retries
(+21 ms: 12 ms setup + 9 ms query). Fixed penalty per burn: **1,521 ms**.
Peak burstiness swells the pool, lulls idle connections into the 60–600 s
"landmine cohort" (11 % of the pool at peak, <1 % off-peak — off-peak pools are
small and hot), so the symptom is **peak-only**, and any pod restart produces a
clean ~40 min before the cohort re-forms (the false-fix signature).

**Impact arithmetic.** Per-borrow dead probability at peak **1.4 %**. Checkout
(3 borrows): 1−(1−0.014)³ ≈ **4.2 %** of requests exposed; order-status
(1 borrow): **1.4 %**. Retried borrows re-draw a dead connection **20 %** of the
time (burns cluster in post-lull bursts), so 0.8 % of checkouts double-burn
(310 base + 3,042 > 2,500 ms deadline → 504) and 3.4 % single-burn (succeed slow).
Peak error rate 0.05 % + 0.84 % ≈ **0.9 %**; peak p99 (successes) **1,620 ms**;
p95 285 ms; p50 64 ms (thread-pool spillover only).

**The red herring.** pricing-svc **v341** deployed Jun 16 **14:20** — hours after
stage 2 (11:30) pushed the fleet into full regression. v341's rules engine
genuinely costs pricing +40 ms at its own p99 (45→85 ms) — a real but benign
regression (<10 ms effect on checkout p99; pricing is a parallel call). The
deploy tracker (which does not ingest netops changes) showed v341 as the *only*
order-path change near the apparent onset, so the team blamed it. The Jun 24
rollback to v340 was bundled with a precautionary orders-api rolling restart —
p99 collapsed to 348 ms and victory was declared; it relapsed the same
afternoon. `overturns` edges encode this misattribution: the restart-signature
analysis (F15) overturns the false-fix reading (F10), the v341 re-canary (F16)
overturns the pricing attribution (F05), and the 5-min per-AZ onset
reconstruction (F20) overturns the daily-rollup onset estimate (F03).

**Why detection missed it** (Q3, emerges mid-investigation): the p99 page
threshold had been raised 800→2,500 ms in March (OBS-1873, never reverted) and
regressed peaks sat at 1,590–1,655 ms; the error alert fires at 5 % (peak was
0.9 %); the fast-burn page needs 14.4x (peak-hour burn was 9x) while the 3x
slow-burn rule filed 23 tickets into an untriaged queue; the synthetic canary
opens a fresh connection per probe (1/min, concurrency 1) so it structurally
cannot draw a stale pooled connection. June's error budget was 100 % consumed
by Jun 22 20:00; detection came from a partner escalation on Jun 23 10:15 —
10.5 days after stage 1, 6.9 days after stage 2.

**Fix and prevention** (Q4): AZ-b ilb canary revert Jun 27 18:40 (dead borrows
7.9→0.1/s in 12 min, AZ-b p99 1,610→335 ms) — simultaneously the final causal
proof and the first fix validation; fleet revert + client hardening Jun 28
(TCP keepalive 30 s via pod sysctl, Hikari keepaliveTime 30 s, idleTimeout
600→45 s); clean peak Jun 29 (p99 318 ms); 2x load test Jul 2 (620 rps, p99
322 ms, zero dead borrows); alert-policy replay would have paged Jun 16 12:07
(6.9 days earlier) but still misses stage 1 (fleet p99 480 < 800 ms) — only the
redesigned pooled canary (persistent pool, forced 90 s idle cycles; game-day
MTTD 9 min) covers that class; a conformance sweep of 41 service→dependency
pairs found 3 more latent pairs with the same failure geometry.

## 3 · Timeline (all times UTC, 2026)

| when | event |
|---|---|
| May 21 | zonal drain leaves AZ traffic at 20/40/40 (a/b/c), never rebalanced |
| May 30 | ilb connection-table exhaustion incident → hardening program |
| Jun 12 22:04 | **NETOPS-4112 stage 1**: ilb idle timeout 350→60 s, AZ-a only |
| Jun 12 22:10 | AZ-a tail step begins (fleet p99 stays sub-threshold: 0.84 % exposed < 1 %) |
| Jun 15 | first full weekday peak: fleet p99 480 ms — under even the old 800 ms threshold |
| Jun 16 11:30 | **stage 2**: AZ-b/c; fleet fully exposed from 11:33 |
| Jun 16 14:20 | pricing-svc v341 deploy (rules engine) — the red herring |
| Jun 16–22 | five weekday peaks at p99 1,590–1,655 ms; 23 slow-burn tickets unread; budget gone Jun 22 20:00 |
| Jun 19 03:10 | routine orders-api deploy (restart #1 — later a key control) |
| Jun 22 17:00 | weekly business review flags conversion −1.8 % w/w |
| Jun 23 10:15 | partner escalation → **INC-2419 opened**; scoping (F01–F08) |
| Jun 24 11:30 | pricing rollback **+ bundled orders-api restart** (#2) → p99 348 ms; false victory |
| Jun 24 15:05 | relapse at peak (p99 1,610 ms, pricing still v340) |
| Jun 25 09:20 | checkout trace sampling → 100 % |
| Jun 25 09:45 | manual restart (#3); restart-signature analysis lands midday |
| Jun 25 16:00 | pricing v341 re-canary, no restart: no effect — pricing exonerated |
| Jun 26 13:00 | `dead_on_borrow` counter deployed; tcpdump 14:50 |
| Jun 27 (am) | NETOPS-4112 found via idle-threshold search of the netops system |
| Jun 27 18:40 | ilb timeout canary revert in AZ-b — causal proof + first fix validation |
| Jun 28 09:00 | fleet revert + client keepalive/eviction hardening |
| Jun 29 | first clean peak: p99 318 ms |
| Jun 30–Jul 3 | detection retrospective (Q3) and durability work (Q4): replay, load test, conformance sweep, canary game-day |

## 4 · Canonical numbers

Single authoritative values. Findings must quote these exactly (stated rounding
noted where it applies).

**Latency (checkout, successful requests, weekday peak)**

| quantity | value |
|---|---|
| baseline p50 / p95 / p99 | 58 / 190 / 310 ms |
| regressed p50 / p95 / p99 | 64 / 285 / **1,620 ms** |
| order-status baseline p50 / p99 | 55 / 120 ms |
| order-status regressed p99 | 1,560 ms |
| browse p99 / cart p99 (unchanged) | 145 / 210 ms |
| pricing-svc p99 before / after v341 | 45 / 85 ms |
| single-burn fixed penalty | 1,521 ms (= 1,500 socketTimeout + 21 retry) |
| retry breakdown | 12 ms connection setup + 9 ms query |
| restart recovery level / duration | ~350 ms (348 measured Jun 24) / 38–44 min |
| Jun 15 fleet peak p99 (stage 1 only) | 480 ms (daily rollup read 470) |
| Jun 15 AZ-a-only peak p99 | ≈1,600 ms |
| Jun 24 midday pre-intervention p99 | 1,590 ms |
| Jun 24/25 relapse peak p99 | 1,610 ms |
| v341 re-canary cell / control p99 | 1,615 / 1,625 ms |
| AZ-b post-revert p99 | 335 ms (control AZs ~1,620) |
| post-fix peak p99 (Jun 29) / load test (Jul 2) | 318 / 322 ms |

**Rates, volumes, errors**

| quantity | value |
|---|---|
| checkout peak / trough | 310 / 40 rps |
| order-status peak | 480 rps |
| orders-api total peak | 2,400 rps |
| borrows per checkout / per status | 3 / 1 (median) |
| ledger borrow rate at peak | 1,410 /s (930 checkout + 480 status) |
| per-borrow dead probability (peak) | 1.4 % |
| dead borrows: total / checkout-path / status-path | 19.7 / 13.0 / 6.7 per s |
| checkout request exposure | 4.2 % (single-burn 3.4 % + double-burn 0.8 %) |
| order-status exposure | 1.4 % |
| edge-histogram slow-or-error share (peak) | 4.3 % |
| retry conditional dead-draw | 20 % |
| error rate baseline / regressed peak | 0.05 % / 0.9 % |
| edge deadline | 2,500 ms |
| conversion delta / support tickets | −1.8 % w/w / 212 vs 31 |

**Connection lifecycle & infrastructure**

| quantity | value |
|---|---|
| ilb idle timeout before / after NETOPS-4112 | 350 s / 60 s |
| stage 1 / stage 2 | AZ-a Jun 12 22:04 / AZ-b,c Jun 16 11:30 |
| observed tail steps | AZ-a Jun 12 22:10 / AZ-b,c Jun 16 11:33 |
| Hikari maxPoolSize / minIdle / idleTimeout / maxLifetime | 100 / 10 / 600 s / 1,800 s |
| pgJDBC socketTimeout | 1,500 ms |
| pods / AZ traffic split | 12 / 20-40-40 (a/b/c) |
| peak pool oscillation per pod | 55–85 connections |
| landmine cohort (idle 60–600 s), peak / off-peak | 11 % / <1 % of pool |
| dead-borrow idle threshold | ≥60 s (none ≤58 s; median idle at death 96 s) |
| retransmits before deadline / RST observed | 3 / none |
| conn-table occupancy target | 2.1 M → 640 k entries |
| hardening: TCP keepalive / Hikari keepaliveTime / idleTimeout | 30 s / 30 s / 45 s |

**Detection & remediation**

| quantity | value |
|---|---|
| p99 page threshold (Mar 11, OBS-1873) | 800 → 2,500 ms |
| regressed weekday peaks range | 1,590–1,655 ms |
| error alert threshold | 5 % / 10 min |
| burn thresholds: fast page / slow ticket | 14.4x per 1 h / 3x per 6 h |
| observed burn: peak-hour / daily | 9x / 4.4x |
| unread slow-burn tickets | 23 |
| June budget exhausted | Jun 22 20:00 |
| synthetic canary | 1 probe/min × 2 regions, concurrency 1, fresh conn; p99 313–317 ms throughout |
| detection | INC-2419 Jun 23 10:15; gap 10.5 d (stage 1) / 6.9 d (stage 2) |
| alert replay first page | Jun 16 12:07 (6.9 d earlier); stage 1 still missed |
| AZ-b revert | Jun 27 18:40; dead borrows 7.9→0.1 /s in 12 min |
| load test | 620 rps (2x), p99 322 ms, 0 dead borrows |
| conformance sweep | 41 pairs, 3 at-risk |
| pooled-canary game-day MTTD | 9 min |

**Tangentials** (bear on no question): analytics batch 02:00–03:10 evicts 91 %
of ledger buffer cache (+35 ms first-morning queries, ~20 min); orders-api image
1.34 GB with a 480 MB stale debug layer (+90 s pull; 12-pod rolling restart
19 min vs ~9); inventory-svc WARN spam 4.3 k lines/min = 38 % of its log volume.

## 5 · Number-consistency notes

How dependent findings' values are derived, so edits stay coherent:

1. **Fixed penalty:** 1,521 ms = 1,500 (socketTimeout) + 21 (12 setup + 9 query).
   Every burned span quotes 1,500±8 ms; every retry quotes 21 ms (F14, F26).
2. **Exposure arithmetic:** per-borrow 1.4 % → checkout (3 borrows)
   1−(1−0.014)³ = 4.15 ≈ **4.2 %**; order-status (1 borrow) **1.4 %** (F21, F27).
   Dead-borrow rate = borrow rate × 1.4 %: 1,410 × 0.014 = 19.7/s, split
   930→13.0 and 480→6.7 (F21). Checkout-path check: 13.0 / 310 = 4.2 %.
3. **Error decomposition:** double-burn = 4.2 % × 20 % (retry re-draw) = 0.84 %
   ≈ 0.8 %; single-burn = 4.2 − 0.8 = 3.4 %. Double-burn total ≥ 310 + 3,042 >
   2,500 ms deadline → 504. Peak error = 0.05 (baseline) + 0.84 ≈ **0.9 %**.
   Edge-histogram share 3.4 + 0.9 = **4.3 %** — matches exposure 4.2 % within
   rounding, and F21 vs F06 must agree at exactly this rounding seam.
4. **Quantile placement:** burned successes are 3.4 % of successes. 3.4 % > 1 %
   → p99 lands *inside* the burn cluster: p99 ≈ 1,521 + own-time at the
   distribution point 0.01/0.034 ≈ p71 of baseline (~100 ms) → **1,620 ms**.
   3.4 % < 5 % → p95 stays in the unburned zone: 0.034 + 0.966·P(own>x) = 0.05
   → own ≈ p98.3 → **285 ms**. Same logic for order-status: 1.4 % > 1 % →
   p99 ≈ 1,521 + own p29 (~40 ms) → **1,560 ms**. Both regressed p99s sitting
   just above 1,521 is itself a mechanism fingerprint (F27).
5. **Stage 1 sub-threshold:** fleet exposure = 4.2 % × 20 % (AZ-a share) =
   0.84 % < 1 % → fleet p99 stays out of the burn cluster (tail-boundary value
   480 ms) while AZ-a-only p99 reads ≈1,600 ms (F20). This is why the daily
   rollup put onset at Jun 16 (F03) and why even the old 800 ms threshold
   misses stage 1 (F37).
6. **Burn-rate math:** SLI = success within 2,500 ms; budget 0.1 % (99.9 %
   monthly). Peak-hour burn = 0.9/0.1 = **9x** < 14.4x (no page). Traffic-
   weighted daily failure ≈ 0.44 % → **4.4x** > 3x (tickets fire → 23 unread).
   4.4x × 7 d ÷ 30 d ≈ 103 % → budget exhausted Jun 22 20:00 (F30).
7. **Timing lags:** observed tail steps trail the config pushes by the first
   post-change 60 s idle windows: 22:04→22:10, 11:30→11:33 (F20, F24, F25).
8. **Restart relapse:** ~40 min = pool re-growth under peak burstiness + first
   burst-grown connections crossing 60 s idle; measured 38–44 min across the
   three restart events (F15), matching the landmine-cohort formation time (F23).
9. **Detection gaps:** Jun 12 22:04 → Jun 23 10:15 = 10.5 d; Jun 16 11:30 →
   Jun 23 10:15 = 6.9 d; replay page Jun 16 12:07 → 6.9 d improvement (F37, F40
   quote these).

## 6 · Question arc

- **Q1 — What exactly regressed?** Scoping: surfaces, magnitude, onset, who is
  affected. Active from F01.
- **Q2 — What caused it?** The causal chain, including the pricing misattribution
  and its overturning. Active from F04.
- **Q3 — Why did detection miss it for 10 days?** **Emerges mid-pool**: first
  finding is F29 (position 29/40); no earlier finding is tagged Q3. Born the
  moment causal work wound down and someone asked "how did we not see this."
- **Q4 — Is the fix durable; what prevents recurrence?** Evidence begins F28
  (position 28/40) — but the *direction* is naturally declared during the doubt
  phase (~F15–F16, "whatever this is, we need a durable fix and a prevention
  story"), which is exactly the intent-precedes-evidence gap.

## 7 · How the pool supports the 8 scenario shapes

- **slow-burn** — the connection-lifecycle direction accumulates one finding at
  a time (F14 → F17 → F19 → F21 → F22 → F23 → F24 → F26): quantized spans, then
  idle-age pattern, then packet capture, then counters, then the change record,
  then lab repro. Promotion from hunch to section can be tested anywhere along
  that ladder.
- **pivot** — the pricing-blame arc (F05, F10, F12) is a real direction with
  real findings that must be demoted/appendixed after F15/F16 land; Class-3
  consent for restructuring a direction the narrative had committed to.
- **interleaved** — Q1 and Q2 interleave natively across F01–F27; Q3 findings
  have deliberately shallow dependencies (F29→F01, F30→F02, F34→none), so a
  three-question interleaving is a valid topological order even though the
  canonical sequence groups Q3 late.
- **flood** — F19–F27 (9 findings) are one plausible sitting: the Jun 26–27
  deep-dive day (tcpdump, counter, idle-age split, pool telemetry, change
  record, per-AZ match, staging repro, order-status corroboration, AZ-b revert).
  Internal dependencies are ordered within the run.
- **contradiction** — three overturns edges land mid-arc, not at the end:
  F15→F10 (position 15/40), F16→F05 (16/40), F20→F03 (20/40). Each is a
  revise-with-provenance case: the false fix, the wrong suspect, the wrong onset.
- **proactive-intent** — Q4's evidence begins only at F28 (70 % through), yet
  declaring "we will need a durable-fix + prevention section" is natural from
  ~F15 — a ~13-finding gap where declared intent precedes any Q4 evidence, so
  the stub+plan conversion is exercised. (Q3 similarly: the question can be
  declared at incident review before F29 exists.)
- **absence** — a natural multi-week gap fits between the fix landing
  (F35/F36, Jun 28–29) and the retrospective/durability batch (F37–F40): replay,
  load test, sweep, and game-day have no time coupling to the incident and can
  resume after a 3-week clock advance with structure held.
- **busy-scientist** — the tangential drive-bys (F09, F13, F36) plus weak
  one-offs with shallow deps (F03, F12, F18, F34) support micro-sittings and
  gestures without follow-up; nothing about them requires a closing ceremony.

## 8 · Finding index (id · one-liner · questions · strength · deps · overturns)

| id | one-liner | Q | S | deps | overturns |
|---|---|---|---|---|---|
| F01 | checkout p99 310→1,620 ms at peak, p50 flat | Q1 | strong | — | |
| F02 | errors 0.05→0.9 % (504 at 2,500 ms), under 5 % alert | Q1 | moderate | F01 | |
| F03 | onset misread as Jun 16 pm from 24 h rollups | Q1 | weak | F01 | |
| F04 | only ledger-backed endpoints regressed | Q1,Q2 | moderate | F01 | |
| F05 | pricing v341 blamed (only tracked change; own p99 45→85) | Q2 | moderate | F03 | |
| F06 | edge histogram bimodal: 4.3 % slow-or-error, peak-only | Q1 | moderate | F01 | |
| F07 | trace decomposition: excess entirely in ledger phase | Q1,Q2 | moderate | F04 | |
| F08 | ledger Postgres server healthy | Q2 | moderate | F07 | |
| F09 | (tangential) analytics batch evicts 91 % buffer cache | — | weak | F08 | |
| F10 | rollback+restart bundle → 348 ms; false victory | Q2 | weak | F05 | |
| F11 | relapse same peak despite rollback (1,610 ms on v340) | Q1,Q2 | moderate | F10 | |
| F12 | deploy tracker shows only v341; no infra feed | Q2 | weak | F03 | |
| F13 | (tangential) 480 MB stale debug layer in image | — | weak | F10 | |
| F14 | exemplars: one 1,500±8 ms ledger span + 21 ms retry | Q2 | strong | F07 | |
| F15 | recovery follows ANY restart, 38–44 min, version-independent | Q2 | strong | F10,F11 | F10 |
| F16 | v341 re-canary without restart: 1,615 vs 1,625 ms — exonerated | Q2 | strong | F05,F15 | F05 |
| F17 | 9/9 exemplars: first use after ≥60 s idle | Q2 | weak | F14 | |
| F18 | conversion −1.8 % w/w; tickets 212 vs 31 | Q1 | weak | F01 | |
| F19 | tcpdump: idle ≥60 s flows blackholed, no RST | Q2 | strong | F17 | |
| F20 | 5-min per-AZ: true onset Jun 12 22:10 + Jun 16 11:33 | Q1,Q2 | moderate | F03,F06 | F03 |
| F21 | dead_on_borrow 19.7/s; 1.4 %/borrow; 4.2 % checkout exposure | Q1,Q2 | moderate | F14,F06 | |
| F22 | dead borrows iff idle ≥60 s (median 96 s) | Q2 | moderate | F21,F19 | |
| F23 | landmine cohort 11 % at peak, <1 % off-peak; ~40 min to form | Q1,Q2 | moderate | F22,F15 | |
| F24 | NETOPS-4112: ilb idle 350→60 s, staged, no-RST eviction | Q2 | strong | F20,F22 | |
| F25 | per-AZ step times match staged rollout (3–6 min lag) | Q1,Q2 | moderate | F24,F20 | |
| F26 | staging repro: identical signature at 60 s, clean at 350 s | Q2 | strong | F24,F19 | |
| F27 | order-status single-borrow arithmetic corroborates | Q1,Q2 | moderate | F21,F04 | |
| F28 | AZ-b ilb revert: 7.9→0.1 dead/s, p99 1,610→335 ms | Q2,Q4 | strong | F24,F21 | |
| F29 | page threshold 800→2,500 ms (Mar 11), unactioned TODO | Q3 | strong | F01 | |
| F30 | burn 4.4x daily / 9x peak; 23 unread tickets; budget gone | Q3 | moderate | F02 | |
| F31 | canary blind by construction (fresh conn per probe) | Q3 | moderate | F22 | |
| F32 | change-visibility gap cost ~3 days of attribution | Q3 | moderate | F12,F24 | |
| F33 | onset misread traced to 24 h rollup default view | Q1,Q3 | weak | F20 | |
| F34 | 3 on-call handoffs; "promo" story; no trend review | Q3 | weak | — | |
| F35 | fleet revert + keepalive hardening; clean peak 318 ms | Q4 | strong | F28 | |
| F36 | (tangential) inventory WARN spam 38 % of log volume | — | weak | — | |
| F37 | alert replay: page Jun 16 12:07, 6.9 d earlier; stage 1 still missed | Q3,Q4 | moderate | F29,F30 | |
| F38 | 2x load test: p99 322 ms, 0 dead borrows | Q4 | moderate | F35 | |
| F39 | conformance sweep: 3 more at-risk pairs of 41 | Q2,Q4 | moderate | F24 | |
| F40 | pooled canary game-day: MTTD 9 min vs 10.5 d | Q3,Q4 | moderate | F31,F26 | |

Counts: 40 findings; strengths 10 weak / 20 moderate / 10 strong (25/50/25);
13 multi-question (32.5 %); 3 no-question; 3 overturns edges (F15→F10, F16→F05,
F20→F03); longest depends_on chain F01→F04→F07→F14→F17→F19→F22→F24→F28→F35→F38
(11 nodes, validator-confirmed).
