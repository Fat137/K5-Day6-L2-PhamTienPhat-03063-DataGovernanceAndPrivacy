"""T08 label-free self-check: baseline vs PoC WITHOUT ground truth (gt/ is instructor-only).

Official metrics (Deletion Completeness, False-delete count, ...) need fixtures/t08_deletion/gt/ and are
computed by the instructor with `python -m src.evaluate`. This script reports what can be checked from the
input graph alone, so the numbers are generated, never typed by hand:

    python -m tools.self_check_t08 out/baseline out/poc --out reports/self_check_t08

Definitions (all derived from data_graph.json + requests.json, NOT from gt/):
  scope pair        (request, artefact) where the artefact's subject_ids contain the requester
  scope coverage    scope pairs that received any action / scope pairs        (did we even find it?)
  held              artefact with a legal hold on itself or on any ancestor   (our frozen policy: hold propagates)
  held deletes      delete rows on held artefacts                             (must be 0; irreversible)
  foreign deletes   delete rows on an artefact that holds someone other than the requester (must be 0)
  audit coverage    action rows whose artefact_id has an entry in audit_log.jsonl
Caveat: `held` and `foreign` encode OUR policy; the hidden GT may draw the line elsewhere (see report §05).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.pipeline import Lineage  # noqa: E402

FIX = ROOT / "fixtures" / "t08_deletion"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 4), round(c + h, 4))


def load_inputs():
    art = json.loads((FIX / "data_graph.json").read_text(encoding="utf-8"))["artefacts"]
    req = json.loads((FIX / "requests.json").read_text(encoding="utf-8"))
    return art, req


def check_run(run: Path, art: list[dict], req: list[dict]) -> dict:
    lin = Lineage(art)
    a = pd.read_csv(run / "actions.csv", dtype=str)
    subj = {r["request_id"]: r["subject_id"] for r in req}
    scope = {(r["request_id"], x["artefact_id"]) for r in req for x in art if r["subject_id"] in x["subject_ids"]}
    acted = set(zip(a.request_id, a.artefact_id))
    holds = [x["artefact_id"] for x in art if x.get("legal_hold")]
    held = set(holds) | {d for h in holds for d in lin.descendants(h)}
    dels = a[a.action == "delete"]
    held_del = dels[dels.artefact_id.isin(held)]
    foreign = dels[[lin.art[x]["subject_ids"] != [subj[r]] for r, x in zip(dels.request_id, dels.artefact_id)]]
    out_of_scope = [p for p in acted if p not in scope]
    audit = run / "audit_log.jsonl"
    logged = {json.loads(l).get("item_id") for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()} if audit.exists() else set()
    info = json.loads((run / "run_info.json").read_text(encoding="utf-8"))
    dec = json.loads((run / "decision.json").read_text(encoding="utf-8"))
    n_items = len(art)  # same unit for both runs: input artefacts scanned
    cov_k, cov_n = len(acted & scope), len(scope)
    by_type = {}
    for t in sorted({x["type"] for x in art}):
        s_t = {p for p in scope if lin.art[p[1]]["type"] == t}
        by_type[t] = {"scope": len(s_t), "acted": len(s_t & acted),
                      "delete": int(dels.artefact_id.map(lambda x: lin.art[x]["type"]).eq(t).sum())}
    return {
        "run": str(run.as_posix()), "run_name": info.get("run_name"), "git_sha": info.get("git_sha"), "seed": info.get("seed"),
        "rows": len(a), "actions": a.action.value_counts().to_dict(),
        "scope_pairs": cov_n, "scope_covered": cov_k, "scope_coverage": round(cov_k / cov_n, 4), "scope_coverage_wilson95": wilson(cov_k, cov_n),
        "held_pairs_in_scope": sum(1 for p in scope if p[1] in held),
        "held_deletes": len(held_del), "held_deletes_ids": sorted(set(held_del.artefact_id)),
        "foreign_deletes": len(foreign), "out_of_scope_rows": len(out_of_scope),
        "audit_coverage": round(a.artefact_id.isin(logged).mean(), 4) if len(a) else None,
        "decision": f"{dec['status']} ({dec['reason_code']})", "owner": dec.get("owner"),
        "runtime_s": info.get("runtime_seconds"), "runtime_per_100_items_s": round(100 * info.get("runtime_seconds", 0) / max(1, int(n_items)), 4),
        "by_type": by_type, "machine": info.get("machine"),
    }


def to_markdown(b: dict, p: dict) -> str:
    def row(label, key, better):
        return f"| {label} | {b[key]} | {p[key]} | {better} |"
    L = [
        "# Self-check T08 · Baseline vs PoC (không dùng ground truth)",
        "",
        f"Sinh bởi `python -m tools.self_check_t08` — không sửa tay. Baseline `{b['run']}` (git {b['git_sha']}), "
        f"PoC `{p['run']}` (git {p['git_sha']}), seed {p['seed']}.",
        "Metric chính thức (`Deletion Completeness`, `False-delete count`) cần `gt/` của giảng viên — xem `src.evaluate`.",
        "",
        "| Chỉ số (không cần GT) | Baseline | PoC | Tốt hơn |",
        "|---|---|---|---|",
        row("Số dòng hành động", "rows", "—"),
        f"| Phân bố hành động | {b['actions']} | {p['actions']} | — |",
        f"| Scope coverage (cặp được xử lý / {p['scope_pairs']} cặp trong phạm vi) | {b['scope_covered']}/{b['scope_pairs']} = {b['scope_coverage']} "
        f"(Wilson95 {b['scope_coverage_wilson95']}) | {p['scope_covered']}/{p['scope_pairs']} = {p['scope_coverage']} (Wilson95 {p['scope_coverage_wilson95']}) | higher |",
        f"| Xóa dữ liệu đang legal hold (kể cả kế thừa) | {b['held_deletes']} {b['held_deletes_ids']} | {p['held_deletes']} {p['held_deletes_ids']} | lower (mục tiêu 0) |",
        row("Xóa artefact chứa người khác", "foreign_deletes", "lower (mục tiêu 0)"),
        row("Dòng ngoài phạm vi yêu cầu", "out_of_scope_rows", "lower (mục tiêu 0)"),
        row("Audit coverage (hành động có dòng audit)", "audit_coverage", "higher"),
        row("Quyết định", "decision", "—"),
        row("Runtime (s)", "runtime_s", "lower"),
        row("Runtime / 100 artefact đầu vào (s)", "runtime_per_100_items_s", "lower"),
        "",
        "## Slice theo loại artefact (cặp trong phạm vi → được xử lý / bị xóa)",
        "",
        "| Loại | Trong phạm vi | Baseline xử lý | Baseline xóa | PoC xử lý | PoC xóa |",
        "|---|---|---|---|---|---|",
    ]
    for t, s in p["by_type"].items():
        bs = b["by_type"][t]
        L.append(f"| {t} | {s['scope']} | {bs['acted']} | {bs['delete']} | {s['acted']} | {s['delete']} |")
    m = p.get("machine") or {}
    L += ["", f"Máy: {m.get('platform', '?')} · CPU {m.get('processor') or m.get('cpu', '?')} · Python {m.get('python', '?')}",
          "", "Lưu ý: \"legal hold\" và \"người khác\" ở đây theo **chính sách đã chốt của PoC** (hold lan xuống toàn bộ bản dẫn xuất); "
          "GT ẩn của giảng viên có thể vạch ranh giới khác — xem report §05."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("poc")
    ap.add_argument("--out", default="reports/self_check_t08", help="writes <out>.json and <out>.md")
    a = ap.parse_args(argv)
    art, req = load_inputs()
    b, p = check_run(Path(a.baseline), art, req), check_run(Path(a.poc), art, req)
    md = to_markdown(b, p)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"baseline": b, "poc": p}, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
