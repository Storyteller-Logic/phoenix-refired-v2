"""Command line Owner review gate for memory candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from brain.review_gate import (
    CandidateDetail,
    ReviewAction,
    ReviewDecision,
    ReviewResult,
    candidate_detail,
    pending_candidates,
    review_candidate,
    review_candidates,
)
from brain.substrate import connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brain-review")
    parser.add_argument("db", type=Path, help="Path to the Brain SQLite DB")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List pending candidates")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--sources", action="store_true")

    show_parser = sub.add_parser("show", help="Show one candidate with provenance")
    show_parser.add_argument("candidate_id", type=int)

    decide_parser = sub.add_parser("decide", help="Apply an Owner review decision")
    decide_parser.add_argument("candidate_id", type=int)
    decide_parser.add_argument(
        "action",
        choices=[
            "approve_global",
            "keep_agent",
            "reject",
            "rewrite_global",
            "rewrite_agent",
            "defer",
        ],
    )
    decide_parser.add_argument("--reason", default="")
    decide_parser.add_argument("--rewrite-claim")
    decide_parser.add_argument("--session-id", type=int)

    apply_parser = sub.add_parser("apply", help="Apply a JSON review decision file")
    apply_parser.add_argument("decisions", type=Path)
    apply_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    if args.command in {"list", "show"}:
        with connect(args.db) as conn:
            if args.command == "list":
                for candidate in pending_candidates(conn, limit=args.limit):
                    if args.sources:
                        _print_detail(candidate_detail(conn, candidate.candidate_id))
                    else:
                        print(
                            f"{candidate.candidate_id}\tagent={candidate.agent_name}"
                            f"\troute={candidate.route}\tscope={candidate.proposed_scope}"
                            f"\t{candidate.claim}"
                        )
            else:
                _print_detail(candidate_detail(conn, args.candidate_id))
        return 0

    if args.command == "apply":
        decisions = _load_decisions(args.decisions)
        with connect(args.db, blessed=True) as conn:
            results = review_candidates(conn, decisions)
            if args.dry_run:
                conn.rollback()
            else:
                conn.commit()
        _print_summary(results, dry_run=bool(args.dry_run))
        return 0

    with connect(args.db, blessed=True) as conn:
        result = review_candidate(
            conn,
            args.candidate_id,
            _action(args.action),
            args.reason,
            rewrite_claim=args.rewrite_claim,
            session_id=args.session_id,
        )
        conn.commit()
    fields = [
        f"action={result.action}",
        f"candidate_id={result.candidate_id}",
    ]
    if result.rewritten_candidate_id is not None:
        fields.append(f"rewritten_candidate_id={result.rewritten_candidate_id}")
    if result.memory_id is not None:
        fields.append(f"memory_id={result.memory_id}")
    if result.approval_wal_id is not None:
        fields.append(f"approval_wal_id={result.approval_wal_id}")
    print("\t".join(fields))
    return 0


def _action(value: str) -> ReviewAction:
    return cast(ReviewAction, value)


def _load_decisions(path: Path) -> list[ReviewDecision]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("decisions")
    if not isinstance(raw, list):
        raise ValueError("decision file must be a JSON array or an object with decisions")
    decisions: list[ReviewDecision] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"decision {index}: must be an object")
        required = {"candidate_id", "action", "reason"}
        allowed = {*required, "rewrite_claim", "session_id"}
        if not required.issubset(item) or not set(item).issubset(allowed):
            raise ValueError(
                f"decision {index}: keys must include {sorted(required)} "
                f"and only optional rewrite_claim/session_id"
            )
        candidate_id = item["candidate_id"]
        action = item["action"]
        reason = item["reason"]
        rewrite_claim = item.get("rewrite_claim")
        session_id = item.get("session_id")
        if not isinstance(candidate_id, int):
            raise ValueError(f"decision {index}: candidate_id must be an integer")
        if not isinstance(action, str):
            raise ValueError(f"decision {index}: action must be a string")
        if not isinstance(reason, str):
            raise ValueError(f"decision {index}: reason must be a string")
        if rewrite_claim is not None and not isinstance(rewrite_claim, str):
            raise ValueError(f"decision {index}: rewrite_claim must be a string")
        if session_id is not None and not isinstance(session_id, int):
            raise ValueError(f"decision {index}: session_id must be an integer")
        decisions.append(
            ReviewDecision(
                candidate_id,
                _action(action),
                reason,
                rewrite_claim=rewrite_claim,
                session_id=session_id,
            )
        )
    return decisions


def _print_summary(results: list[ReviewResult], *, dry_run: bool) -> None:
    counts: dict[str, int] = {
        "approve_global": 0,
        "keep_agent": 0,
        "reject": 0,
        "rewrite_global": 0,
        "rewrite_agent": 0,
        "defer": 0,
    }
    for result in results:
        counts[result.action] = counts.get(result.action, 0) + 1
    print(f"dry_run={str(dry_run).lower()}")
    print(
        "summary\t"
        f"accepted_global={counts['approve_global'] + counts['rewrite_global']}\t"
        f"accepted_agent={counts['keep_agent'] + counts['rewrite_agent']}\t"
        f"rejected={counts['reject']}\t"
        f"rewritten={counts['rewrite_global'] + counts['rewrite_agent']}\t"
        f"deferred={counts['defer']}"
    )
    for result in results:
        fields = [
            f"action={result.action}",
            f"candidate_id={result.candidate_id}",
        ]
        if result.rewritten_candidate_id is not None:
            fields.append(f"rewritten_candidate_id={result.rewritten_candidate_id}")
        if result.memory_id is not None:
            fields.append(f"memory_id={result.memory_id}")
        if result.approval_wal_id is not None:
            fields.append(f"approval_wal_id={result.approval_wal_id}")
        print("\t".join(fields))


def _print_detail(detail: CandidateDetail) -> None:
    summary = detail.summary
    print(f"candidate_id: {summary.candidate_id}")
    print(f"agent: {summary.agent_name} ({summary.agent_id})")
    print(f"route: {summary.route}")
    print(f"proposed_scope: {summary.proposed_scope}")
    print(f"category: {summary.category or ''}")
    print(f"status: {summary.status}")
    print(f"claim: {summary.claim}")
    print("triage:")
    for key, value in sorted(detail.triage.items()):
        print(f"  {key}: {value}")
    print("sources:")
    for source in detail.sources:
        print(
            f"  wal_id={source.wal_id} session={source.session_id} "
            f"turn={source.turn} role={source.role}: {source.content}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
