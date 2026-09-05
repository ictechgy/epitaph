"""Command line interface: `epitaph <command>`."""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

from . import __version__
from .detect import detect
from .matcher import match_tombstones, normalized
from .render import format_matches
from .schema import (
    CONFIDENCE_VALUES,
    FIELDS,
    REJECTED_BY_VALUES,
    STATUS_VALUES,
    Tombstone,
)
from .store import TombstoneStore
from .store import StoreError
from .schema import SchemaError


class CliError(RuntimeError):
    """User-facing CLI error (missing store, unknown id, bad arguments)."""


MARKER_BEGIN = "# >>> tombstone >>>"
MARKER_END = "# <<< tombstone <<<"

SNIPPET_BEGIN = "<!-- >>> tombstone rule >>> -->"
SNIPPET_END = "<!-- <<< tombstone rule <<< -->"

SNIPPET = """<!-- >>> tombstone rule >>> -->
Before implementing an approach, call check_nogo with your planned approach and target
files. On a match, read `reason` and `retry_when`: either address `retry_when` or pick
a different path. Tombstones are records of past rejections, not bans.
<!-- <<< tombstone rule <<< -->"""


def _append_snippet(path: Path) -> bool:
    """Append the tombstone rule to an agent-instructions file. False if already there."""
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if SNIPPET_BEGIN in content:
            return False
        path.write_text(content.rstrip("\n") + "\n\n" + SNIPPET + "\n", encoding="utf-8")
    else:
        path.write_text(SNIPPET + "\n", encoding="utf-8")
    return True


def cmd_snippets(args):
    repo = Path(args.repo or ".").resolve()
    # AGENTS.md is the cross-vendor convention — create it if absent.
    agents = repo / "AGENTS.md"
    existed = agents.exists()
    if _append_snippet(agents):
        print("%s AGENTS.md" % ("updated" if existed else "created"))
    else:
        print("AGENTS.md already has the tombstone rule")
    # CLAUDE.md is appended only when it already exists, so we never fork the
    # source of truth for repos that rely solely on AGENTS.md.
    claude = repo / "CLAUDE.md"
    if claude.exists():
        if _append_snippet(claude):
            print("updated CLAUDE.md")
        else:
            print("CLAUDE.md already has the tombstone rule")
    print("next: commit the change so every agent working in this repo sees the rule")
    return 0


def _split_list(values):
    """Flatten `nargs="+"` lists, also splitting comma-separated items."""
    out = []
    for value in values or []:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def _resolve_store(args, create=False):
    if getattr(args, "repo", None):
        store = TombstoneStore(args.repo)
    else:
        store = TombstoneStore.find(".") or TombstoneStore(Path(".").resolve())
    if create:
        store.create()
    elif not store.exists():
        raise CliError(
            "no .tombstones/ directory found (looked from %s upward) — run "
            "`epitaph init` first" % Path(getattr(args, "repo", None) or ".").resolve()
        )
    return store


def _get_or_fail(store, tomb_id):
    tomb = store.get(tomb_id)
    if tomb is None:
        raise CliError("no such tombstone: %s" % tomb_id)
    return tomb


def cmd_init(args):
    store = _resolve_store(args, create=True)
    print("initialized %s" % store.dir)
    report = None
    if getattr(args, "detect", False):
        report = detect(Path(args.repo or ".").resolve())
        for tomb_id in report.created:
            print("created %s (candidate)" % tomb_id)
        print(
            "%d revert commit(s) scanned, %d created, %d already recorded"
            % (report.reverts, len(report.created), len(report.skipped))
        )
    if getattr(args, "snippets", False):
        cmd_snippets(args)
    print("next steps (in this order):")
    if report and report.created:
        print(
            "  1. epitaph review        # %d candidate(s) await a human decision"
            % len(report.created)
        )
    else:
        print("  1. epitaph detect        # mine existing history for reverts — first tombstones, free")
    print("  2. epitaph install-hook  # keep detecting reverts after every commit")
    print("  3. epitaph snippets      # teach agents the check_nogo rule (AGENTS.md)")
    print(
        '  4. epitaph add --attempt "..." --reason "..." --scope src/foo.py '
        '[--retry-when "..."]'
    )
    return 0


def cmd_add(args):
    store = _resolve_store(args, create=True)
    tomb = Tombstone(
        attempt=args.attempt,
        scope=_split_list(args.scope),
        rejected_at=args.date or dt.date.today().isoformat(),
        rejected_by=args.rejected_by,
        reason=args.reason,
        evidence=_split_list(args.evidence),
        retry_when=args.retry_when or "",
        status=args.status,
        confidence=args.confidence,
    )
    tomb = store.add(tomb, seed=("add", tomb.attempt))
    print("created %s -> %s" % (tomb.id, store.path_for(tomb.id)))
    if not tomb.retry_when.strip():
        print(
            "note: retry_when is empty — a tombstone should state the condition "
            "under which a retry makes sense."
        )
    if tomb.confidence == "candidate":
        print("note: candidate until approved: epitaph approve %s" % tomb.id)
    return 0


def cmd_approve(args):
    store = _resolve_store(args, create=False)
    tomb = _get_or_fail(store, args.id)
    if tomb.confidence == "approved":
        print("already approved: %s" % tomb.id)
        return 0
    tomb.confidence = "approved"
    store.save(tomb)
    print("approved %s -> %s" % (tomb.id, store.path_for(tomb.id)))
    if not tomb.retry_when.strip():
        print(
            "warning: retry_when is empty — state the condition under which a "
            "retry makes sense."
        )
    return 0


def cmd_overturn(args):
    store = _resolve_store(args, create=False)
    tomb = _get_or_fail(store, args.id)
    tomb.status = "overturned"
    tomb.overturn_reason = args.reason
    store.save(tomb)
    print("overturned %s (status=overturned)" % tomb.id)
    return 0


def cmd_list(args):
    store = _resolve_store(args, create=False)
    records = store.all()
    if args.status:
        records = [t for t in records if t.status == args.status]
    if args.scope:
        needle = normalized(args.scope)
        records = [
            t
            for t in records
            if needle
            and any(
                needle in normalized(s) or normalized(s) in needle for s in t.scope
            )
        ]
    records.sort(key=lambda t: (t.rejected_at, t.id), reverse=True)
    if not records:
        print("no tombstones" + (" matching filters" if (args.status or args.scope) else ""))
        return 0
    rows = []
    for t in records:
        attempt = t.attempt if len(t.attempt) <= 64 else t.attempt[:63] + "..."
        rows.append((t.id, t.confidence, t.status, t.rejected_at, attempt))
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    for row in rows:
        print(
            "%-*s  %-*s  %-*s  %-*s  %s"
            % (widths[0], row[0], widths[1], row[1], widths[2], row[2], widths[3], row[3], row[4])
        )
    print("")
    print("%d tombstone(s)" % len(records))
    if store.last_skipped:
        print("warning: skipped unreadable file(s): %s" % ", ".join(store.last_skipped))
    return 0


def _print_tombstone(store, tomb):
    data = tomb.to_dict()
    for key in FIELDS:
        value = data[key]
        if key in ("scope", "evidence"):
            print("%s:" % key)
            for item in value:
                print("  - %s" % item)
        else:
            print("%s: %s" % (key, value))
    if tomb.overturn_reason:
        print("overturn_reason: %s" % tomb.overturn_reason)
    for key, value in tomb.extra.items():
        print("%s: %s" % (key, value))
    print("file: %s" % store.path_for(tomb.id))


def cmd_show(args):
    store = _resolve_store(args, create=False)
    tomb = _get_or_fail(store, args.id)
    _print_tombstone(store, tomb)
    return 0


def cmd_review(args):
    """Walk candidates one by one; only a human deciding now may approve.

    Non-interactive stdin (EOF) stops the walk without approving anything —
    an agent piping input must never be able to self-approve.
    """
    store = _resolve_store(args, create=False)
    records = [t for t in store.all() if t.confidence == "candidate" and t.status == "active"]
    records.sort(key=lambda t: (t.rejected_at, t.id))
    if not records:
        print("no candidates awaiting review")
        return 0
    approved = skipped = 0
    for index, tomb in enumerate(records, start=1):
        print("")
        print("=" * 60)
        _print_tombstone(store, tomb)
        try:
            answer = input(
                "approve? [y]es / [n]o / [q]uit (%d left): " % (len(records) - index)
            ).strip().lower()
        except EOFError:
            print("\nstdin closed — stopping without approving the rest.")
            break
        if answer in ("y", "yes"):
            tomb.confidence = "approved"
            store.save(tomb)
            approved += 1
            print("approved %s" % tomb.id)
            if not tomb.retry_when.strip():
                print(
                    "warning: retry_when is empty — state the condition under "
                    "which a retry makes sense."
                )
        elif answer in ("q", "quit"):
            break
        else:
            skipped += 1
    print("")
    print(
        "%d approved, %d skipped, %d candidate(s) remain"
        % (approved, skipped, len(records) - approved - skipped)
    )
    return 0


def cmd_check(args):
    files = list(args.file or [])
    query = " ".join(args.query) if args.query else None
    if not query and not files:
        raise CliError("provide search text and/or --file (see `epitaph check --help`)")
    # A storeless repo gets the same soft answer agents see over MCP —
    # "nothing is known" is the truthful answer, not an error.
    store = TombstoneStore.find(args.repo or ".")
    if store is None:
        print("no tombstones recorded here yet — nothing is known against this attempt.")
        print("start a ledger with `epitaph init` (records land in .tombstones/).")
        return 0
    matches = match_tombstones(query=query, files=files, tombstones=store.all())
    print(format_matches(matches))
    if store.last_skipped:
        print("warning: skipped unreadable file(s): %s" % ", ".join(store.last_skipped))
    return 0


def cmd_detect(args):
    report = detect(Path(args.repo or ".").resolve(), full=args.full)
    for tomb_id in report.created:
        print("created %s (candidate)" % tomb_id)
    for tomb_id in report.skipped:
        print("already recorded: %s" % tomb_id)
    print(
        "%d revert commit(s) scanned, %d created, %d already recorded"
        % (report.reverts, len(report.created), len(report.skipped))
    )
    return 0


def cmd_install_hook(args):
    repo = Path(args.repo or ".").resolve()
    proc = subprocess.run(
        ("git", "rev-parse", "--git-path", "hooks"),
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise CliError("not a git repository: %s (%s)" % (repo, proc.stderr.strip()))
    hooks = Path(proc.stdout.strip())
    if not hooks.is_absolute():
        hooks = repo / hooks
    hook = hooks / "post-commit"
    block = (
        MARKER_BEGIN
        + "\n"
        + "# Installed by `epitaph install-hook`. Scans for reverts after each\n"
        + "# commit; must never fail the commit.\n"
        + "# --repo goes BEFORE the subcommand (top-level argparse option).\n"
        + 'epitaph --repo "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" '
        + "detect >/dev/null 2>&1 || true\n"
        + MARKER_END
        + "\n"
    )
    if hook.exists():
        content = hook.read_text(encoding="utf-8")
        if MARKER_BEGIN in content:
            print("post-commit hook already installed: %s" % hook)
            return 0
        hook.write_text(content.rstrip("\n") + "\n\n" + block, encoding="utf-8")
    else:
        hook.write_text("#!/bin/sh\n" + block, encoding="utf-8")
    hook.chmod(0o755)
    print("installed post-commit hook: %s" % hook)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="epitaph",
        description=(
            "A repo-scoped ledger of rejected agent attempts. Tombstones are "
            "testimony, not verdicts: they record what was tried and why it "
            "was rejected, so the next agent doesn't walk the same dead end."
        ),
    )
    parser.add_argument("--version", action="version", version="epitaph " + __version__)
    parser.add_argument(
        "--repo",
        default=None,
        help="target repository (default: cwd, walking up to find .tombstones/)",
    )
    sub = parser.add_subparsers(dest="command", metavar="command", required=True)

    p = sub.add_parser("init", help="create .tombstones/ in the target repo")
    p.add_argument(
        "--detect",
        action="store_true",
        help="immediately scan git history for reverts (first tombstones, free)",
    )
    p.add_argument(
        "--snippets",
        action="store_true",
        help="also inject the check_nogo rule into AGENTS.md (and CLAUDE.md if present)",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser(
        "snippets",
        help="inject the recommended check_nogo rule into AGENTS.md (and CLAUDE.md if present)",
    )
    p.set_defaults(func=cmd_snippets)

    p = sub.add_parser("add", help="record a rejected attempt (defaults to candidate)")
    p.add_argument("--attempt", required=True, help="what was tried")
    p.add_argument("--reason", required=True, help="why it was rejected")
    p.add_argument(
        "--scope", nargs="+", default=[], metavar="PATH",
        help="file/symbol anchors (e.g. src/session/lock.py)",
    )
    p.add_argument(
        "--evidence", nargs="+", default=[], metavar="REF",
        help="pointers: PR numbers, commit shas, session ids",
    )
    p.add_argument(
        "--rejected-by", choices=REJECTED_BY_VALUES, default="human-review",
        help="who rejected it (default: human-review)",
    )
    p.add_argument(
        "--retry-when", default="",
        help="condition under which a retry makes sense (anti-verdict clause)",
    )
    p.add_argument("--status", choices=STATUS_VALUES, default="active")
    p.add_argument(
        "--confidence", choices=CONFIDENCE_VALUES, default="candidate",
        help="use `approved` only when a human is deciding right now",
    )
    p.add_argument("--date", default=None, help="rejected_at as ISO date (default: today)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("approve", help="promote a tombstone to approved (one human line)")
    p.add_argument("id", help="tombstone id, e.g. ts-20260812-a3f")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser(
        "overturn", help="mark a tombstone overturned: a retry succeeded"
    )
    p.add_argument("id", help="tombstone id")
    p.add_argument("--reason", required=True, help="what changed so the retry worked")
    p.set_defaults(func=cmd_overturn)

    p = sub.add_parser("list", help="list tombstones, newest first")
    p.add_argument("--status", choices=STATUS_VALUES, default=None)
    p.add_argument("--scope", default=None, metavar="PATH", help="filter by scope anchor")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="print one tombstone in full")
    p.add_argument("id", help="tombstone id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser(
        "review",
        help="walk candidate tombstones one by one and approve/skip (human-only loop)",
    )
    p.set_defaults(func=cmd_review)

    p = sub.add_parser(
        "check", help="query tombstones by attempt text and/or files before retrying"
    )
    p.add_argument("query", nargs="*", help="attempt text (free form)")
    p.add_argument("--file", action="append", default=[], metavar="PATH",
                   help="file you are about to touch (repeatable)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser(
        "detect", help="scan git history for revert commits and draft candidate tombstones"
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="ignore the .cursor position and rescan the full history (never duplicates)",
    )
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser(
        "install-hook", help="install a post-commit hook that runs detect (never fails a commit)"
    )
    p.set_defaults(func=cmd_install_hook)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (CliError, StoreError, SchemaError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
