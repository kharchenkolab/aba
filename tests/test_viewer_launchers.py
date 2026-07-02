"""External-viewer launcher registry (core/viewers/launchers.py) — the generic
seam behind `mode: external` viewers. Pure; no backend deps."""
import pytest

from core.viewers.launchers import (
    LaunchResult, register_launcher, get_launcher, launch,
)


def test_register_and_launch_returns_url():
    register_launcher("demo", lambda node, ctx: LaunchResult(url="/demo/?f=" + (node.get("name") or "")))
    assert get_launcher("demo") is not None
    res = launch("demo", {"name": "x.lstar.zarr"}, {})
    assert res.url == "/demo/?f=x.lstar.zarr"
    assert res.prepare_job_id is None


def test_missing_launcher_raises_keyerror():
    with pytest.raises(KeyError):
        launch("does-not-exist", {}, {})


def test_prepare_job_and_label_pass_through():
    register_launcher("job", lambda n, c: LaunchResult(url="/v/", prepare_job_id="job_1", label="Open"))
    res = launch("job", {}, {})
    assert res.prepare_job_id == "job_1"
    assert res.label == "Open"


def test_register_replaces_existing():
    register_launcher("dup", lambda n, c: LaunchResult(url="/one"))
    register_launcher("dup", lambda n, c: LaunchResult(url="/two"))
    assert launch("dup", {}, {}).url == "/two"


def test_ctx_is_passed_through():
    register_launcher("ctx", lambda node, ctx: LaunchResult(url="/v/" + (ctx.get("path") or "")))
    assert launch("ctx", {}, {"path": "work/a.h5ad"}).url == "/v/work/a.h5ad"
