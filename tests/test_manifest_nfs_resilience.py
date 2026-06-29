"""Gap 1 (NFS resilience): a Slurm job writes its outputs on a compute node;
the login-node `refresh_output_manifest` enumerates the Run dir to build the
output list, but an NFS dir listing (readdir) can lag behind files that are
already readable BY NAME (close-to-open consistency). So the manifest must also
union in the harvester's known output names — otherwise a just-finished job
shows an empty Run until the dir attribute cache expires. Uses pytest's
monkeypatch + the conftest project DB.
"""


def test_ensure_names_recovers_outputs_when_dir_listing_lags(monkeypatch, tmp_path):
    import pathlib
    from core.graph.entities import create_entity, update_entity, get_entity
    from content.bio.lifecycle.runs import refresh_output_manifest

    rid = create_entity(entity_type="analysis", title="nfs-lag test",
                        parent_entity_id="workspace", metadata={"run_state": "open"})
    (tmp_path / "umap.png").write_bytes(b"\x89PNG\r\n\x1a\n")   # bytes irrelevant here
    (tmp_path / "markers.csv").write_text("gene,lfc\nA,1\n")
    update_entity(rid, artifact_path=str(tmp_path))

    # Simulate the lag: readdir sees NOTHING (as if the dir cache is stale).
    monkeypatch.setattr(pathlib.Path, "rglob", lambda self, pat: iter([]))
    refresh_output_manifest(rid, ensure_names=["umap.png", "markers.csv"])

    outs = (get_entity(rid).get("metadata") or {}).get("run", {}).get("outputs", [])
    by_label = {o["label"]: o["kind"] for o in outs}
    assert by_label == {"umap.png": "figure", "markers.csv": "table"}, by_label


def test_ensure_names_dedup_and_add_not_yet_visible(monkeypatch, tmp_path):
    from core.graph.entities import create_entity, update_entity, get_entity
    from content.bio.lifecycle.runs import refresh_output_manifest

    rid = create_entity(entity_type="analysis", title="dedup test",
                        parent_entity_id="workspace", metadata={"run_state": "open"})
    (tmp_path / "out.csv").write_text("x\n1\n")           # present + visible
    update_entity(rid, artifact_path=str(tmp_path))
    # rglob finds out.csv; ensure_names repeats it (dedup) + a trusted name the
    # harvester reported that isn't locally visible yet (NFS) → added anyway.
    refresh_output_manifest(rid, ensure_names=["out.csv", "pending.png"])
    outs = (get_entity(rid).get("metadata") or {}).get("run", {}).get("outputs", [])
    by = {o["label"]: o for o in outs}
    assert [o["label"] for o in outs].count("out.csv") == 1     # no duplicate
    assert "pending.png" in by and by["pending.png"]["kind"] == "figure"
    assert "size" in by["out.csv"]                 # visible → size known
    assert "size" not in by["pending.png"]         # not yet visible → size omitted, still listed
