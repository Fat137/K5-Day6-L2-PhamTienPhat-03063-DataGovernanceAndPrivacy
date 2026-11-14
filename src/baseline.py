"""T08 baseline — "xóa một bản raw theo filename, không tìm derived/backup" (docs/tracks/T08.md §4).

Mô phỏng cách xử lý ngây thơ thường gặp: nhận yêu cầu xóa của subject X, tìm file video gốc có tên
`drive_<X>_*.mp4` rồi xóa. Cố ý KHÔNG:
  - lần theo lineage (backup, frame, face_crop, embedding, annotation, training_export),
  - kiểm legal hold / ACL / nguồn gốc / artefact nhiều subject,
  - đưa ngoại lệ cho người duyệt, ghi audit log.
Đây là mốc mà PoC (src/pipeline.py) phải vượt, chạy trên cùng pool, cùng seed 42.

    python -m src.main --track t08 --pipeline src.baseline --output out/baseline --run-name baseline --offline --seed 42
"""
from __future__ import annotations

import json
import re

import pandas as pd

from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.manifest import verify_manifest
from .metrics.tracks import get_track


def load(ctx: RunContext) -> tuple[list[dict], list[dict]]:
    manifest = ctx.input_dir / "manifest.csv"
    if not manifest.exists():
        raise FileNotFoundError(f"missing {manifest}")
    problems = verify_manifest(manifest)
    if problems and ctx.config.get("strict_provenance", True):
        raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    graph = json.loads((ctx.input_dir / "data_graph.json").read_text(encoding="utf-8"))
    requests = json.loads((ctx.input_dir / "requests.json").read_text(encoding="utf-8"))
    return graph["artefacts"], requests


def process(artefacts: list[dict], requests: list[dict]) -> pd.DataFrame:
    rows = []
    for req in requests:
        pattern = re.compile(rf"^drive_{re.escape(req['subject_id'])}_\d+\.mp4$")
        for a in artefacts:
            if a["type"] == "raw_video" and pattern.match(a.get("filename") or ""):
                rows.append({"request_id": req["request_id"], "artefact_id": a["artefact_id"], "action": "delete"})
    return pd.DataFrame(rows, columns=["request_id", "artefact_id", "action"])


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    if spec.track != "t08":
        raise NotImplementedError(f"src.baseline only implements t08 (got {spec.track})")
    artefacts, requests = load(ctx)
    actions = process(artefacts, requests).sort_values(["request_id", "artefact_id"])
    ctx.stats.update(n_items=len(artefacts), n_requests=len(requests), n_actions=len(actions))
    actions.to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(
        Decision(status="HOLD", reason_code="BASELINE_ONLY",
                 reason="Baseline chỉ xóa video raw theo filename, không truy lineage và không kiểm ngoại lệ: không dùng để thực thi",
                 owner=ctx.config.get("owner", "baseline"), scope=ctx.track, evidence=[str(ctx.out(spec.pred_file))],
                 metrics={"n_requests": len(requests), "n_delete": len(actions)}),
        ctx.out("decision.json"), ctx.out("decision_log.csv"))
