"""Registry of UI states to audit.

Each State drives the app into a specific rendered view (via the read/write
endpoints + the clicks a human uses) and declares the primary actions that must
stay reachable and the elements that must be fully visible there. The harness
boots the app once and walks the registry; each setup() runs against a fresh
page and is responsible for resetting shared server state (projects) first.

Audits run in isolated multi-project mode (ABA_PROJECTS_DIR → temp), so a fresh
boot lands on the empty 3-card Home.
"""
from __future__ import annotations
import json
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SEED = Path(__file__).resolve().parents[1] / "scenarios" / "seed" / "large_project.py"


@dataclass
class State:
    name: str
    setup: Callable                       # (page, ctx) -> None
    primary_actions: list[str] = field(default_factory=list)  # must be reachable
    must_show: list[str] = field(default_factory=list)        # must be fully visible / unoccluded
    audit_root: str | None = None         # scope contrast/tap-target to this subtree
    notes: str = ""


# ---- server-state helpers ----------------------------------------------------

def _get(api, path):
    with urllib.request.urlopen(f"{api}{path}") as r:
        return json.loads(r.read())


def _post(api, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{api}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "null")


def _delete(api, path):
    urllib.request.urlopen(urllib.request.Request(f"{api}{path}", method="DELETE")).read()


def _set_projects(api, names: list[str]):
    """Delete every project, then create the given ones (last becomes current)."""
    for p in _get(api, "/projects"):
        _delete(api, f"/projects/{p['id']}")
    for n in names:
        _post(api, "/projects", {"name": n})


def _seed_large(ctx):
    """One project whose DB is populated by the large_project seed (scale-at-rest:
    4 datasets, ~30 figures, results/findings/claims/manuscript)."""
    _set_projects(ctx.api, ["Large project"])
    cur = next(p for p in _get(ctx.api, "/projects") if p["current"])
    dbfile = ctx.projects_dir / f"{cur['id']}.db"
    subprocess.run([sys.executable, str(SEED), "--db", str(dbfile)],
                   check=True, capture_output=True)


def _enter_workspace(page, ctx):
    page.goto(ctx.base_url, wait_until="networkidle")
    page.locator('button[title="Project"]').click()
    page.wait_for_selector(".tree", timeout=6000)


# ---- state setups ------------------------------------------------------------

def home_empty(page, ctx):
    _set_projects(ctx.api, [])
    page.goto(ctx.base_url, wait_until="networkidle")
    page.wait_for_selector(".home__cards", timeout=8000)


def home_populated(page, ctx):
    _set_projects(ctx.api, ["IFN study", "T-cell pilot", "Archive 2024"])
    page.goto(ctx.base_url, wait_until="networkidle")
    page.wait_for_selector(".home__hub", timeout=8000)
    page.wait_for_selector(".home__side", timeout=4000)


def home_create_modal(page, ctx):
    _set_projects(ctx.api, ["IFN study"])
    page.goto(ctx.base_url, wait_until="networkidle")
    page.wait_for_selector(".home__hub", timeout=8000)
    page.locator(".home__btn--primary", has_text="New project").click()
    page.wait_for_selector(".modal", timeout=3000)


def home_delete_modal(page, ctx):
    _set_projects(ctx.api, ["IFN study"])
    page.goto(ctx.base_url, wait_until="networkidle")
    page.wait_for_selector(".home__hub", timeout=8000)
    page.locator(".home__cur-actions .home__proj-menu").click()
    page.locator(".home__menu-danger").click()
    page.wait_for_selector(".modal", timeout=3000)


def workspace_chat(page, ctx):
    _set_projects(ctx.api, ["IFN study"])
    page.goto(ctx.base_url, wait_until="networkidle")
    page.locator('button[title="Project"]').click()
    page.wait_for_selector(".composer__input", timeout=6000)


def skills_panel(page, ctx):
    _set_projects(ctx.api, ["IFN study"])
    page.goto(ctx.base_url, wait_until="networkidle")
    page.locator('button[title^="Skills"]').click()
    page.wait_for_selector(".skills", timeout=4000)


def queues_panel(page, ctx):
    _set_projects(ctx.api, ["IFN study"])
    page.goto(ctx.base_url, wait_until="networkidle")
    page.locator('button[title^="Queues"]').click()
    page.wait_for_selector(".queues", timeout=4000)


def search_modal(page, ctx):
    _set_projects(ctx.api, ["IFN study"])
    page.goto(ctx.base_url, wait_until="networkidle")
    page.locator('button[title="Project"]').click()
    page.wait_for_selector(".composer__input", timeout=6000)
    page.keyboard.press("Control+k")
    page.wait_for_selector(".search-modal", timeout=3000)


def project_tree_packed(page, ctx):
    _seed_large(ctx)
    _enter_workspace(page, ctx)
    page.wait_for_selector(".tree__more", timeout=4000)   # section caps engaged


def workspace_finding(page, ctx):
    _seed_large(ctx)
    _enter_workspace(page, ctx)
    page.locator('[data-entity-type="finding"]').first.click()
    page.wait_for_selector(".fv-ladder", timeout=4000)


def workspace_figure(page, ctx):
    _seed_large(ctx)
    _enter_workspace(page, ctx)
    page.locator('[data-entity-type="figure"]').first.click()
    page.wait_for_selector(".entity-surface", timeout=4000)


STATES = [
    State("home-empty", home_empty,
          primary_actions=[".home__card"],
          must_show=[".home__cards"],
          notes="zero projects — welcome / 3-card start screen"),
    State("home-populated", home_populated,
          primary_actions=[".home__btn--primary", ".home__cur-actions .home__btn--primary"],
          must_show=[".home__main", ".home__side"],
          notes="two-column hub + other-projects side list"),
    State("home-create-modal", home_create_modal,
          primary_actions=[".modal .home__btn--primary"],
          must_show=[".modal"],
          notes="name-a-project modal"),
    State("home-delete-modal", home_delete_modal,
          primary_actions=[".modal .home__btn--danger"],
          must_show=[".modal"],
          notes="delete confirmation modal (via ⋯ menu)"),
    State("workspace-chat", workspace_chat,
          primary_actions=[".composer__input", ".hl-toggle"],
          must_show=[".hl-toggle", ".composer__input"],
          notes="chat-first workspace — header highlighter + composer"),
    State("skills-panel", skills_panel,
          primary_actions=[".skills"],
          must_show=[".skills"],
          audit_root=".skills",
          notes="Skills overlay — the original light-on-light contrast surface"),
    State("queues-panel", queues_panel,
          must_show=[".queues"],
          audit_root=".queues",
          notes="Queues overlay"),
    State("search-modal", search_modal,
          primary_actions=[".search-q, .search-input-row input"],
          must_show=[".search-modal"],
          audit_root=".search-modal",
          notes="Cmd-K search modal"),
    State("project-tree-packed", project_tree_packed,
          primary_actions=[".tree__section-head"],
          audit_root=".tree",
          notes="seeded large project — packed tree (contrast/tap on a dense tree). "
                "No must_show: tree items live in a scroll region (below-fold is OK)."),
    State("workspace-finding", workspace_finding,
          must_show=[".fv-ladder"],
          audit_root=".entity-surface",
          notes="seeded finding — maturity ladder + evidence"),
    State("workspace-figure", workspace_figure,
          audit_root=".entity-surface",
          notes="seeded figure — inspector chrome (stub, no artifact)"),
]
