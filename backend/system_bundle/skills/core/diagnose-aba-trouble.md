---
name: diagnose-aba-trouble
description: How to diagnose an ABA problem and file a useful bug report — classify the symptom, pull the RIGHT evidence, form a grounded root-cause hypothesis, then call build_bug_report. Never fabricate.
when_to_use: The user reports that something broke, behaved wrong, or wants to report a bug/problem with ABA itself (an error, a failed run/install, a UI glitch, the env, a recipe, or odd agent behavior). Run this before calling build_bug_report.
requires_tools: [read_aba_logs, read_client_context, build_bug_report]
capabilities_needed: []
keywords: [bug, report, broken, error, failed, crash, doesn't work, not working, diagnose, troubleshoot, debug, install, environment, recipe, glitch, problem, meta]
produces: []
domain: meta
---
# Diagnosing an ABA problem before filing a report

The bug report you file is read by an ABA **developer/bugfixer** (a human or an
agent with the source) — NOT by the user. Your job is to hand them a grounded
diagnosis: the observed symptom, how to reproduce, the technical root-cause
hypothesis, and **what you could and couldn't inspect**. You are the only entity
that looks at the evidence, so gather it deliberately. **Never fabricate a
traceback or guess a cause** — if you can't substantiate it, say so.

## Step 1 — classify the symptom, then pull the RIGHT evidence
Match the problem to a class and gather its evidence source. Do NOT guess which
log; target it:

| symptom | where the evidence is |
|---|---|
| a code cell failed (`run_python`/`run_r`) | **your own tool history this turn** — quote the actual traceback you already received |
| a tool returned an error | the **tool_result** in your history — quote it |
| install / setup failed (conda env, micromamba, R toolchain, "Install ABA") | `read_aba_logs(which="installer")` — find the ERROR/CondaError/Traceback |
| backend/server error, or env misprovisioning (ImportError, ABI/`numpy` mismatch, missing package, "kernel died") | `read_aba_logs(which="backend")` (and `which="helper"` if the helper is implicated); note the failing import/package |
| UI / frontend ("the page/button/view/figure didn't…", blank screen, nothing happened on click) | `read_client_context()` — recent console errors + route. You **cannot see the browser** any other way |
| a recipe/skill led you wrong | name the Skill/recipe you invoked and what it told you to do vs. what actually failed |
| suspected bad context/prompt (you were given wrong, missing, contradictory, or truncated context) | review **your own context this turn** — state precisely what was missing/garbled and what you'd have needed |

If the class is ambiguous, gather from more than one source. For install/backend
issues, prefer a targeted `grep` (e.g. `read_aba_logs(which="installer", grep="error")`).

## Step 2 — form a grounded hypothesis
From the evidence (not from the user's phrasing alone) state, tersely and for the
engineer: the root cause, **where** it likely lives (component/step/file), your
**confidence**, and a one-line **repro**. Explicitly flag what you could not
inspect (e.g. "couldn't see the browser", "no matching error in the backend log")
and whether it looks like a real ABA defect vs. a user-environment issue.

## Step 3 — if the evidence isn't there, don't invent it
If you searched the right source and found nothing, say so plainly. Offer to file
from the user's description **with a "couldn't reproduce/locate" note**, and ask
for the missing piece (the traceback, which thread/project, the exact step). Only
file unsubstantiated if the user says to.

## Step 4 — file it
Call `build_bug_report` with the bugfixer-register fields: `headline` (symptom),
`what_doing` (repro/context), `diagnosis` (your Step-2 hypothesis), `error_tail`
(1–3 verbatim error lines). Keep each tight — the email is size-capped, so the
*evidence stays in your reasoning* and only the distilled cause goes in. Then give
the user the returned **Review & send bug report** link. Any advice for the *user*
(workarounds) goes in your chat reply, never in the report.
