"""T08 · Retention & Delete Request Simulator — PoC pipeline (Phạm Tiến Phát, IND-03063).

Lab contract = 5 automated steps, run by `python -m src.main ...`:
    01 load      requests + data graph, provenance check (manifest.csv sha256), schema/graph validation
    02 process   for each delete request: find the subject's artefacts, trace lineage, apply the frozen
                 policy -> one action per (request, artefact): delete / retain_legal_hold / escalate
    03 rank      every exception (retain/escalate) goes to the human review queue, most severe first
    04 review    apply reviewer decisions from out/review_queue_reviewed.csv (+ audit log)
    05 decide    GO / HOLD / REWORK; dry-run plan always written; mock deletion only if GO and not dry_run

Policy (frozen 2026-11-14, before any evaluation; see reports/metric_contract.md):
    scope       = artefacts whose `subject_ids` contain the requester. Descendants that do NOT carry the
                  subject (e.g. a face crop of *another* person cut from a shared frame) are out of scope
                  and never deleted for this request — deleting them would destroy someone else's data.
    precedence  = legal hold > identity not verified > no delete permission (ACL) / unknown origin
                  > multi-subject > lineage tag missing > delete
    legal hold  = propagates to ALL descendants of a held artefact (backup, frame, crop, embedding, export):
                  copies and derivatives of evidence are preserved until the hold is released.
No ground truth is read here (GT guard), nothing touches the network, no real data is deleted.
"""
from __future__ import annotations

import json
from collections import defaultdict

import pandas as pd

from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.io import write_json
from .common.manifest import verify_manifest
from .common.review import REVIEW_COLUMNS, load_review_decisions
from .metrics.tracks import get_track

ACTIONS = ("delete", "retain_legal_hold", "escalate")

# reason_code -> (action, review severity). Higher severity = reviewed first.
POLICY = {
    "LEGAL_HOLD": ("retain_legal_hold", 5.0),
    "LEGAL_HOLD_INHERITED": ("retain_legal_hold", 4.5),
    "IDENTITY_NOT_VERIFIED": ("escalate", 4.0),
    "NO_DELETE_PERMISSION": ("escalate", 3.5),
    "UNKNOWN_ORIGIN": ("escalate", 3.0),
    "MULTI_SUBJECT": ("escalate", 2.0),
    "LINEAGE_TAG_MISSING": ("escalate", 1.5),
    "SOLE_SUBJECT": ("delete", 0.0),
}
ACTION_COLUMNS = ["request_id", "artefact_id", "action", "reason_code", "reason", "type", "uri",
                  "subject_ids", "hold_source", "review_status", "score"]


# ---------------------------------------------------------------- graph helpers
class Lineage:
    """Parent/child index over the artefact graph."""

    def __init__(self, artefacts: list[dict]):
        self.art = {a["artefact_id"]: a for a in artefacts}
        self.children: dict[str, list[str]] = defaultdict(list)
        for a in artefacts:
            for p in a["parent_ids"]:
                self.children[p].append(a["artefact_id"])

    def _walk(self, start: str, nxt) -> set[str]:
        seen, stack = set(), [start]
        while stack:
            for n in nxt(stack.pop()):
                if n not in seen and n in self.art:
                    seen.add(n)
                    stack.append(n)
        return seen

    def ancestors(self, aid: str) -> set[str]:
        return self._walk(aid, lambda x: self.art[x]["parent_ids"] if x in self.art else [])

    def descendants(self, aid: str) -> set[str]:
        return self._walk(aid, lambda x: self.children.get(x, []))

    def hold_source(self, aid: str) -> str | None:
        """Artefact id carrying the legal hold that covers `aid` (itself first, then nearest ancestor)."""
        if self.art[aid].get("legal_hold"):
            return aid
        held = sorted(a for a in self.ancestors(aid) if self.art[a].get("legal_hold"))
        return held[0] if held else None


def validate_graph(artefacts: list[dict], requests: list[dict]) -> list[dict]:
    """Structural problems that make the deletion plan untrustworthy (-> REWORK)."""
    problems = []
    ids = [a["artefact_id"] for a in artefacts]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        problems.append({"problem": "duplicate_artefact_id", "ids": dup})
    known = set(ids)
    for a in artefacts:
        for k in ("type", "subject_ids", "parent_ids", "acl"):
            if k not in a:
                problems.append({"problem": "missing_field", "artefact_id": a.get("artefact_id"), "field": k})
        for p in a.get("parent_ids", []):
            if p not in known:
                problems.append({"problem": "dangling_parent", "artefact_id": a["artefact_id"], "parent": p})
    rids = [r.get("request_id") for r in requests]
    if len(set(rids)) != len(rids):
        problems.append({"problem": "duplicate_request_id"})
    for r in requests:
        if not r.get("request_id") or not r.get("subject_id"):
            problems.append({"problem": "bad_request", "request": r})
    return problems


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    graph_path, req_path = ctx.input_dir / "data_graph.json", ctx.input_dir / "requests.json"
    for p in (graph_path, req_path):
        if not p.exists():
            raise FileNotFoundError(f"missing input {p}")
    artefacts = json.loads(graph_path.read_text(encoding="utf-8"))["artefacts"]
    requests = json.loads(req_path.read_text(encoding="utf-8"))
    graph_problems = validate_graph(artefacts, requests)
    if graph_problems:
        ctx.audit.log(actor="pipeline", action="graph_problems", item_id="data_graph.json", after=graph_problems[:20])
    ctx.stats.update(n_artefacts=len(artefacts), n_requests=len(requests), n_graph_problems=len(graph_problems))
    return {"artefacts": artefacts, "requests": requests, "graph_problems": graph_problems}


# ---------------------------------------------------------------- 02 process
def classify(req: dict, aid: str, lin: Lineage, operator: str) -> tuple[str, str, str | None]:
    """Return (reason_code, human reason, hold_source) for one in-scope artefact. Order = policy precedence."""
    a = lin.art[aid]
    src = lin.hold_source(aid)
    if src == aid:
        return "LEGAL_HOLD", f"artefact under legal hold {a['legal_hold']}", src
    if src:
        return "LEGAL_HOLD_INHERITED", f"derived from {src} under legal hold {lin.art[src]['legal_hold']}", src
    if not req.get("identity_verified", False):
        return "IDENTITY_NOT_VERIFIED", "requester identity not verified", None
    if operator not in (a.get("acl") or []):
        return "NO_DELETE_PERMISSION", f"ACL {a.get('acl')} does not include {operator}", None
    if not a.get("origin_known", True):
        return "UNKNOWN_ORIGIN", "origin unknown: cannot confirm what this artefact contains or who else it covers", None
    others = sorted(set(a["subject_ids"]) - {req["subject_id"]})
    if others:
        return "MULTI_SUBJECT", f"also contains {', '.join(others)}: deleting would remove other people's data", None
    return "SOLE_SUBJECT", "only the requester's data, no exception", None


def plan_request(req: dict, lin: Lineage, operator: str) -> tuple[list[dict], list[dict]]:
    """Actions for one request + excluded descendants (other people's data reached through lineage)."""
    subj = req["subject_id"]
    tagged = sorted(k for k, a in lin.art.items() if subj in a["subject_ids"])
    rows = []
    for aid in tagged:
        code, reason, src = classify(req, aid, lin, operator)
        rows.append({"request_id": req["request_id"], "artefact_id": aid, "reason_code": code, "reason": reason,
                     "hold_source": src or ""})
    # Lineage check: anything derived from an artefact that belongs ONLY to the requester must carry the
    # requester too; if it does not, the tag is probably missing -> human decides (never silently skipped).
    sole = [aid for aid in tagged if lin.art[aid]["subject_ids"] == [subj]]
    untagged = sorted({d for aid in sole for d in lin.descendants(aid)} - set(tagged))
    for aid in untagged:
        src = lin.hold_source(aid)
        code, reason = (("LEGAL_HOLD_INHERITED", f"derived from {src} under legal hold") if src else
                        ("LINEAGE_TAG_MISSING", f"derived from {subj}'s own data but not tagged with {subj}"))
        rows.append({"request_id": req["request_id"], "artefact_id": aid, "reason_code": code, "reason": reason,
                     "hold_source": src or ""})
    # Descendants of shared artefacts that do not carry the requester belong to someone else: excluded.
    excluded = sorted({d for aid in tagged for d in lin.descendants(aid)} - set(tagged) - set(untagged))
    excl = [{"request_id": req["request_id"], "artefact_id": d, "subject_ids": lin.art[d]["subject_ids"]} for d in excluded]
    return rows, excl


def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    lin = Lineage(data["artefacts"])
    operator = str(ctx.param("operator", "data-platform"))
    rows, excluded = [], []
    for req in sorted(data["requests"], key=lambda r: r["request_id"]):
        r, e = plan_request(req, lin, operator)
        rows += r
        excluded += e
    df = pd.DataFrame(rows)
    df["action"] = df.reason_code.map(lambda c: POLICY[c][0])
    df["score"] = df.reason_code.map(lambda c: POLICY[c][1])
    df["type"] = df.artefact_id.map(lambda a: lin.art[a]["type"])
    df["uri"] = df.artefact_id.map(lambda a: lin.art[a]["uri"])
    df["subject_ids"] = df.artefact_id.map(lambda a: ";".join(lin.art[a]["subject_ids"]))
    df["review_status"] = df.action.map(lambda a: "not_required" if a == "delete" else "pending")
    df = df.sort_values(["request_id", "artefact_id"], kind="mergesort").reset_index(drop=True)[ACTION_COLUMNS]
    for e in excluded:
        ctx.audit.log(actor="pipeline", action="exclude_other_subject", item_id=e["artefact_id"],
                      reason="descendant does not carry the requester; belongs to " + ";".join(e["subject_ids"]),
                      request_id=e["request_id"])
    pd.DataFrame(excluded, columns=["request_id", "artefact_id", "subject_ids"]).to_csv(ctx.out("excluded_other_subject.csv"), index=False)
    ctx.stats.update(n_items=len(df), n_excluded_other_subject=len(excluded),
                     planned=df.action.value_counts().to_dict())
    return df


# ---------------------------------------------------------------- 03 rank
def item_key(df: pd.DataFrame) -> pd.Series:
    return df.request_id + "|" + df.artefact_id


def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    """Every exception is reviewed (no budget cut-off): a missed legal hold is not recoverable."""
    exc = scored[scored.action != "delete"].copy()
    exc["item_id"] = item_key(exc)
    exc = exc.sort_values(["score", "request_id", "artefact_id"], ascending=[False, True, True], kind="mergesort")
    q = pd.DataFrame({
        "rank": range(1, len(exc) + 1),
        "item_id": exc.item_id.values,
        "score": exc.score.values,
        "reason": (exc.reason_code + ": " + exc.reason).values,
        "suggested_action": exc.action.values,
        "slice": exc.type.values,
        "evidence_path": exc.uri.values,
    })
    for c in ["reviewer", "reviewer_decision", "override_value", "review_note", "reviewed_at"]:
        q[c] = ""
    path = ctx.out("review_queue.csv")
    # utf-8-sig (BOM) so Excel shows Vietnamese correctly on double-click; pandas reads it back transparently.
    new = q[REVIEW_COLUMNS].to_csv(index=False).encode("utf-8-sig")
    if path.exists():  # never silently destroy a reviewer's in-place edits (e.g. saved from Excel)
        old = path.read_bytes()
        norm = lambda b: b.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")  # noqa: E731
        if norm(old) != norm(new):
            backup = ctx.out("review_queue.edited_backup.csv")
            backup.write_bytes(old)
            print(f"[pipeline] WARNING: {path} was edited; copied to {backup}. Put reviewer decisions in "
                  f"{ctx.output_dir / 'review_queue_reviewed.csv'} (saved as CSV UTF-8), not in review_queue.csv")
    path.write_bytes(new)
    ctx.stats["n_review_queue"] = len(q)
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    """Apply out/review_queue_reviewed.csv. Decisions:
        approve  -> keep the suggested action          escalate -> action = escalate
        edit / reject -> action = override_value (delete | retain_legal_hold | escalate; required)
        skip     -> leave pending
    Safety gate: a reviewer cannot turn a legal-hold row into `delete` — the hold must be released in
    the source system first (the row stays retain_legal_hold and the attempt is logged).
    """
    final = scored.copy()
    final["item_id"] = item_key(final)
    path = ctx.output_dir / "review_queue_reviewed.csv"
    if not path.exists():
        ctx.stats["n_reviewed"] = 0
        return final.drop(columns="item_id")
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{path} is not UTF-8 (Excel: Save As -> 'CSV UTF-8 (Comma delimited)'): {e}") from e
    reviewed = load_review_decisions(path)
    unknown = sorted(set(reviewed.item_id) - set(queue.item_id))
    if unknown:
        raise ValueError(f"review file has items that are not in the current queue: {unknown[:5]}")
    blocked = 0
    for r in reviewed.itertuples(index=False):
        mask = final.item_id == r.item_id
        before = final.loc[mask, "action"].iloc[0]
        aid = final.loc[mask, "artefact_id"].iloc[0]
        d, new = r.reviewer_decision, before
        if d == "approve":
            new = before
        elif d == "escalate":
            new = "escalate"
        elif d in ("edit", "reject"):
            new = str(r.override_value).strip().lower()
            if new not in ACTIONS:
                raise ValueError(f"{r.item_id}: decision {d!r} needs override_value in {ACTIONS}, got {r.override_value!r}")
        elif d == "skip":
            ctx.audit.log(actor=r.reviewer, action="review:skip", item_id=aid, before=before, after=before,
                          reason=r.review_note, request_id=r.item_id.split("|")[0])
            continue
        if new == "delete" and final.loc[mask, "hold_source"].iloc[0]:
            blocked += 1
            ctx.audit.log(actor=r.reviewer, action="review:override_blocked_legal_hold", item_id=aid, before=before,
                          after=before, reason=f"attempted delete under legal hold; note: {r.review_note}",
                          request_id=r.item_id.split("|")[0])
            new = before
        final.loc[mask, "action"] = new
        final.loc[mask, "review_status"] = f"reviewed:{d}"
        ctx.audit.log(actor=r.reviewer, action=f"review:{d}", item_id=aid, before=before, after=new,
                      reason=r.review_note, request_id=r.item_id.split("|")[0])
    ctx.stats.update(n_reviewed=len(reviewed), n_override_blocked=blocked)
    return final.drop(columns="item_id")


# ---------------------------------------------------------------- 05 decide (+ dry-run / mock delete)
def decide(ctx: RunContext, final: pd.DataFrame, graph_problems: list | None = None) -> Decision:
    owner = ctx.config.get("owner") or "UNSET-owner"
    pending = int((final.review_status == "pending").sum())
    counts = final.action.value_counts().to_dict()
    metrics = {"n_rows": len(final), "pending_review": pending, **{f"n_{k}": int(v) for k, v in counts.items()}}
    if graph_problems:
        return Decision("REWORK", "GRAPH_INVALID", f"{len(graph_problems)} lineage/schema problems: fix data graph before deleting",
                        owner, ctx.track, [str(ctx.out("audit_log.jsonl"))], metrics)
    if pending:
        return Decision("HOLD", "PENDING_HUMAN_REVIEW", f"{pending} exceptions await reviewer approval (out/review_queue.csv)",
                        owner, ctx.track, [str(ctx.out("review_queue.csv"))], metrics)
    return Decision("GO", "ALL_EXCEPTIONS_REVIEWED", "every exception has a reviewer decision; deletions may be executed",
                    owner, ctx.track, [str(ctx.out("actions.csv")), str(ctx.out("audit_log.jsonl"))], metrics)


def execute(ctx: RunContext, final: pd.DataFrame, decision: Decision) -> None:
    """Dry-run plan always; mock deletion only when decision is GO and params.dry_run is false.
    The mock store (out/mock_store.json) makes re-runs idempotent: an artefact deleted before is a no-op."""
    deletes = final[final.action == "delete"].drop_duplicates("artefact_id")
    final.to_csv(ctx.out("dry_run_plan.csv"), index=False)
    dry_run = bool(ctx.param("dry_run", True))
    if dry_run or decision.canonical != "GO":
        ctx.stats["executed"] = f"no ({'dry_run' if dry_run else decision.reason_code})"
        return
    store_path = ctx.out("mock_store.json")
    store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.exists() else {"deleted": []}
    done = set(store["deleted"])
    evidence = []
    for r in deletes.itertuples(index=False):
        status = "already_deleted" if r.artefact_id in done else "deleted"
        done.add(r.artefact_id)
        ctx.audit.log(actor="pipeline", action=f"mock_delete:{status}", item_id=r.artefact_id, after=r.uri,
                      reason=r.reason_code, request_id=r.request_id)
        evidence.append({"artefact_id": r.artefact_id, "uri": r.uri, "type": r.type, "status": status})
    write_json(store_path, {"deleted": sorted(done)})
    pd.DataFrame(evidence, columns=["artefact_id", "uri", "type", "status"]).to_csv(ctx.out("deletion_evidence.csv"), index=False)
    ctx.stats["executed"] = {"deleted": sum(e["status"] == "deleted" for e in evidence),
                             "already_deleted": sum(e["status"] == "already_deleted" for e in evidence)}


def request_summary(final: pd.DataFrame, requests: list[dict]) -> pd.DataFrame:
    req = pd.DataFrame(requests)[["request_id", "subject_id", "received_at", "deadline", "identity_verified"]]
    c = final.pivot_table(index="request_id", columns="action", values="artefact_id", aggfunc="count", fill_value=0)
    c = c.reindex(columns=list(ACTIONS), fill_value=0).add_prefix("n_").reset_index()
    p = final.assign(p=final.review_status == "pending").groupby("request_id").p.sum().rename("n_pending").reset_index()
    out = req.merge(c, on="request_id", how="left").merge(p, on="request_id", how="left").fillna(0)
    out["status"] = out.apply(lambda r: "awaiting_review" if r.n_pending else
                              ("partially_fulfilled" if r.n_retain_legal_hold or r.n_escalate else "fulfilled"), axis=1)
    return out


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    if spec.track != "t08":
        raise NotImplementedError(f"src.pipeline implements t08 only (got {spec.track})")
    data = load(ctx)
    scored = process(ctx, data)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    for r in final.itertuples(index=False):  # one audit entry per (request, artefact) action
        ctx.audit.log(actor="pipeline", action=f"plan:{r.action}", item_id=r.artefact_id, after=r.action,
                      reason=f"{r.reason_code}: {r.reason}", request_id=r.request_id, review_status=r.review_status)
    final[spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns]].to_csv(ctx.out(spec.pred_file), index=False)
    request_summary(final, data["requests"]).to_csv(ctx.out("request_summary.csv"), index=False)
    decision = decide(ctx, final, data["graph_problems"])
    write_decision(decision, ctx.out("decision.json"), ctx.out("decision_log.csv"))
    execute(ctx, final, decision)
