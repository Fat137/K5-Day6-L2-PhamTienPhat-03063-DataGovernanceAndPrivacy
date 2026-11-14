"""T08 tests (Phạm Tiến Phát): the deletion policy must be safe first, complete second, and repeatable.

Success path : one-command run, HOLD until every exception is reviewed, GO + mock delete after review.
Failure/edge : legal hold never deleted (also not by a reviewer), other people's data never deleted,
               malformed review files fail loudly, broken lineage -> REWORK, re-runs are idempotent.
No test reads ground truth except the `requires_gt` one (skipped unless the instructor overlay is installed).
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from src import main as runner
from src.metrics.tracks import get_track
from src.pipeline import Lineage, classify, plan_request, validate_graph

FIX = Path("fixtures/t08_deletion")
GRAPH = json.loads((FIX / "data_graph.json").read_text(encoding="utf-8"))["artefacts"]
REQUESTS = {r["request_id"]: r for r in json.loads((FIX / "requests.json").read_text(encoding="utf-8"))}
ART = {a["artefact_id"]: a for a in GRAPH}


def run_poc(out: Path) -> int:
    return runner.main(["--config", "configs/lab.yaml", "--output", str(out), "--run-name", "poc", "--offline", "--seed", "42"])


def run_baseline(out: Path) -> int:
    return runner.main(["--track", "t08", "--pipeline", "src.baseline", "--output", str(out),
                        "--run-name", "baseline", "--offline", "--seed", "42"])


def read(out: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(out / name, dtype=str, keep_default_na=False)


def decision(out: Path) -> dict:
    return json.loads((out / "decision.json").read_text(encoding="utf-8"))


def review_all(out: Path, edits: dict | None = None, **overrides) -> None:
    """Approve every queued exception as reviewer Phạm Tiến Phát; `edits` = {item_id: override_value}."""
    q = read(out, "review_queue.csv")
    q["reviewer"], q["reviewer_decision"], q["reviewed_at"] = "Phạm Tiến Phát", "approve", "2026-11-14"
    for item, value in (edits or {}).items():
        q.loc[q.item_id == item, ["reviewer_decision", "override_value"]] = ["edit", value]
    for col, val in overrides.items():
        q[col] = val
    q.to_csv(out / "review_queue_reviewed.csv", index=False, encoding="utf-8-sig")


def held_ids() -> set[str]:
    """Independent of the pipeline: every artefact with a legal hold on itself or on any ancestor."""
    lin = Lineage(GRAPH)
    roots = [a["artefact_id"] for a in GRAPH if a.get("legal_hold")]
    return set(roots) | {d for r in roots for d in lin.descendants(r)}


@pytest.fixture(scope="module")
def unreviewed(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("poc")
    assert run_poc(out) == 0
    return out


# ------------------------------------------------------------------ success path
def test_one_command_run_writes_contract_outputs_and_holds_for_review(unreviewed):
    a = read(unreviewed, "actions.csv")
    assert list(a.columns[:3]) == get_track("t08").pred_columns
    assert set(a.action) <= {"delete", "retain_legal_hold", "escalate"}
    assert not a.duplicated(["request_id", "artefact_id"]).any()
    assert set(a.request_id) == set(REQUESTS)
    for f in ["review_queue.csv", "dry_run_plan.csv", "request_summary.csv", "audit_log.jsonl", "decision_log.csv"]:
        assert (unreviewed / f).exists(), f
    # every exception is queued for a human, nothing is executed before review
    q = read(unreviewed, "review_queue.csv")
    assert len(q) == int((a.action != "delete").sum()) > 0
    d = decision(unreviewed)
    assert d["status"] == "HOLD" and d["reason_code"] == "PENDING_HUMAN_REVIEW" and d["owner"] == "Phạm Tiến Phát"
    assert not (unreviewed / "mock_store.json").exists()


def test_every_action_has_an_audit_entry(unreviewed):
    a = read(unreviewed, "actions.csv")
    lines = (unreviewed / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
    logged = {json.loads(l)["item_id"] for l in lines if l.strip()}
    assert set(a.artefact_id) <= logged


def test_review_then_go_then_idempotent_mock_delete(tmp_path):
    out = tmp_path / "poc"
    assert run_poc(out) == 0
    review_all(out)
    assert run_poc(out) == 0
    assert decision(out)["status"] == "GO"
    ev1 = read(out, "deletion_evidence.csv")
    n_delete = read(out, "actions.csv").query("action == 'delete'").artefact_id.nunique()
    assert len(ev1) == n_delete and set(ev1.status) == {"deleted"}
    actions1 = (out / "actions.csv").read_bytes()
    assert run_poc(out) == 0                                     # second run: same plan, nothing deleted twice
    assert (out / "actions.csv").read_bytes() == actions1
    assert set(read(out, "deletion_evidence.csv").status) == {"already_deleted"}


def test_same_seed_same_plan_in_fresh_folders(tmp_path):
    assert run_poc(tmp_path / "a") == 0 and run_poc(tmp_path / "b") == 0
    assert (tmp_path / "a" / "actions.csv").read_bytes() == (tmp_path / "b" / "actions.csv").read_bytes()


def test_poc_traces_lineage_beyond_raw_video(unreviewed, tmp_path):
    """Failure mode 'chỉ xóa raw nhưng giữ bản sao và embedding': baseline misses them, PoC does not."""
    assert run_baseline(tmp_path) == 0
    base = read(tmp_path, "actions.csv")
    assert set(base.artefact_id.map(lambda x: ART[x]["type"])) == {"raw_video"}
    poc = read(unreviewed, "actions.csv")
    deleted_types = set(poc[poc.action == "delete"].artefact_id.map(lambda x: ART[x]["type"]))
    assert {"backup", "frame", "face_crop", "embedding", "annotation"} <= deleted_types


# ------------------------------------------------------------------ failure / edge cases
def test_legal_hold_and_its_derivatives_are_never_deleted(unreviewed):
    a = read(unreviewed, "actions.csv")
    on_hold = a[a.artefact_id.isin(held_ids())]
    assert len(on_hold) > 0
    assert set(on_hold.action) == {"retain_legal_hold"}


def test_baseline_deletes_held_evidence_failure_mode(tmp_path):
    """Documents the baseline's 'xóa nhầm dữ liệu thuộc exception' failure (a0043, a0099 are under hold)."""
    assert run_baseline(tmp_path) == 0
    base = read(tmp_path, "actions.csv")
    assert {"a0043", "a0099"} <= set(base[base.action == "delete"].artefact_id)


def test_never_deletes_other_peoples_data(unreviewed):
    a = read(unreviewed, "actions.csv")
    deletes = a[a.action == "delete"]
    for r in deletes.itertuples():
        assert ART[r.artefact_id]["subject_ids"] == [REQUESTS[r.request_id]["subject_id"]], r
    # face crops of another person cut from a shared frame are excluded, not deleted
    excluded = read(unreviewed, "excluded_other_subject.csv")
    assert {"a0012", "a0013"} <= set(excluded[excluded.request_id == "DR-001"].artefact_id)
    assert not set(zip(excluded.request_id, excluded.artefact_id)) & set(zip(a.request_id, a.artefact_id))


def test_reviewer_cannot_force_delete_under_legal_hold(tmp_path):
    out = tmp_path / "poc"
    assert run_poc(out) == 0
    review_all(out, edits={"DR-002|a0043": "delete", "DR-002|a0044": "delete"})
    assert run_poc(out) == 0
    a = read(out, "actions.csv").set_index(["request_id", "artefact_id"])
    assert a.loc[("DR-002", "a0043"), "action"] == "retain_legal_hold"
    assert a.loc[("DR-002", "a0044"), "action"] == "retain_legal_hold"
    audit = (out / "audit_log.jsonl").read_text(encoding="utf-8")
    assert audit.count("review:override_blocked_legal_hold") == 2
    assert "a0043" not in json.loads((out / "mock_store.json").read_text())["deleted"]


def test_reviewer_override_to_delete_is_applied_when_allowed(tmp_path):
    out = tmp_path / "poc"
    assert run_poc(out) == 0
    review_all(out, edits={"DR-004|a0075": "delete"})           # unknown-origin embedding, no hold
    assert run_poc(out) == 0
    a = read(out, "actions.csv").set_index(["request_id", "artefact_id"])
    assert a.loc[("DR-004", "a0075"), "action"] == "delete"
    assert a.loc[("DR-004", "a0075"), "review_status"] == "reviewed:edit"


@pytest.mark.parametrize("bad, expected", [
    ({"reviewer": ""}, "no `reviewer`"),                            # decision without an owner
    ({"reviewer_decision": "maybe"}, "unknown reviewer_decision"),  # invalid decision value
])
def test_malformed_review_file_fails_loudly(tmp_path, capsys, bad, expected):
    out = tmp_path / "poc"
    assert run_poc(out) == 0
    review_all(out, **bad)
    assert run_poc(out) == 2
    assert expected in capsys.readouterr().err
    assert decision(out)["status"] == "HOLD"                       # previous safe decision is not replaced by GO


def test_edit_without_valid_override_value_fails(tmp_path, capsys):
    out = tmp_path / "poc"
    assert run_poc(out) == 0
    review_all(out, edits={"DR-001|a0011": "purge"})
    assert run_poc(out) == 2
    assert "override_value" in capsys.readouterr().err


def test_non_utf8_review_file_fails_with_excel_hint(tmp_path, capsys):
    out = tmp_path / "poc"
    assert run_poc(out) == 0
    review_all(out)
    raw = (out / "review_queue_reviewed.csv").read_text(encoding="utf-8-sig")
    (out / "review_queue_reviewed.csv").write_bytes(raw.encode("cp1258", errors="replace"))
    assert run_poc(out) == 2
    assert "CSV UTF-8" in capsys.readouterr().err


def test_edited_queue_is_backed_up_not_overwritten(tmp_path):
    out = tmp_path / "poc"
    assert run_poc(out) == 0
    q = read(out, "review_queue.csv")
    q.loc[0, "review_note"] = "ghi chú viết nhầm vào review_queue.csv"
    q.to_csv(out / "review_queue.csv", index=False, encoding="utf-8-sig")
    assert run_poc(out) == 0
    assert "ghi chú viết nhầm" in (out / "review_queue.edited_backup.csv").read_text(encoding="utf-8-sig")


# ------------------------------------------------------------------ policy unit tests (synthetic graphs)
def _art(aid, subjects, parents=(), **kw):
    return {"artefact_id": aid, "type": kw.pop("type", "frame"), "subject_ids": list(subjects), "parent_ids": list(parents),
            "uri": f"mock://{aid}", "acl": kw.pop("acl", ["data-platform"]), "legal_hold": kw.pop("legal_hold", None),
            "origin_known": kw.pop("origin_known", True)}


def test_policy_precedence_hold_beats_everything():
    g = [_art("r", ["s1"], type="raw_video", legal_hold="LH-1"),
         _art("f", ["s1", "s2"], ["r"], acl=["vendor"], origin_known=False)]
    lin, req = Lineage(g), {"request_id": "R", "subject_id": "s1", "identity_verified": False}
    assert classify(req, "f", lin, "data-platform")[0] == "LEGAL_HOLD_INHERITED"
    g[0]["legal_hold"] = None
    assert classify(req, "f", Lineage(g), "data-platform")[0] == "IDENTITY_NOT_VERIFIED"
    req["identity_verified"] = True
    assert classify(req, "f", Lineage(g), "data-platform")[0] == "NO_DELETE_PERMISSION"


def test_untagged_derivative_of_own_data_is_escalated_not_ignored():
    g = [_art("r", ["s1"], type="raw_video"), _art("c", [], ["r"], type="face_crop")]   # tag missing on the crop
    rows, excluded = plan_request({"request_id": "R", "subject_id": "s1", "identity_verified": True}, Lineage(g), "data-platform")
    assert {(r["artefact_id"], r["reason_code"]) for r in rows} == {("r", "SOLE_SUBJECT"), ("c", "LINEAGE_TAG_MISSING")}
    assert excluded == []


def test_broken_lineage_is_reported():
    g = [_art("a", ["s1"], ["missing-parent"]), _art("a", ["s1"])]
    kinds = {p["problem"] for p in validate_graph(g, [{"request_id": "R", "subject_id": "s1"}])}
    assert {"dangling_parent", "duplicate_artefact_id"} <= kinds


# ------------------------------------------------------------------ hidden-GT check (instructor overlay only)
@pytest.mark.requires_gt
def test_poc_beats_baseline_without_false_deletes(unreviewed, tmp_path):
    spec = get_track("t08")
    gt = FIX / spec.default_gt
    assert run_baseline(tmp_path) == 0
    score = lambda p: {m.name: m.value for m in spec.evaluator(gt, p, {}).metrics}  # noqa: E731
    poc, base = score(unreviewed / "actions.csv"), score(tmp_path / "actions.csv")
    assert poc["False-delete count"] == 0
    assert poc["Deletion Completeness"] > base["Deletion Completeness"]
