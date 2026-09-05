"""Revert detector: scan `git log` for revert commits and file candidate
tombstones. Deterministic rules only — no LLM, no network (v0.1 principle).

A commit counts as a revert when its subject starts with `Revert "` and/or
its body contains `This reverts commit <sha>`. Idempotency: the tombstone id
is derived from the revert commit sha, so re-running detect never duplicates.

Subprocess policy: a scan is O(1) git calls, not O(reverts) — existence is
one `cat-file --batch-check`, subject/date/files for every target commit one
`git log --no-walk --stdin --name-only`. Per-revert forks would make full
scans of large histories pay thousands of process spawns.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .schema import Tombstone, make_id
from .store import TombstoneStore

REVERT_PREFIX = 'Revert "'
REVERTS_COMMIT_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)

_FIELD = "\x1f"
_RECORD = "\x1e"


class DetectError(RuntimeError):
    """git failed in a way detection cannot recover from."""


@dataclass
class DetectReport:
    reverts: int = 0
    created: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


def _git(repo, *args, check=True, input=None):
    proc = subprocess.run(
        ("git",) + args, cwd=str(repo), capture_output=True, text=True, input=input
    )
    if check and proc.returncode != 0:
        raise DetectError("`git %s` failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc


def scan_reverts(repo, since=None):
    """[(sha, subject, body, committer-date)] newest first.

    With ``since`` (a commit sha), only commits after it are scanned — the
    incremental path the post-commit hook relies on. If the cursor sha is no
    longer reachable (history rewrite), fall back to a full scan.
    """
    # %x1e terminates each record, so multi-line bodies (%b) and
    # multi-commit logs both parse: fields never contain \x1f, records
    # never contain \x1e. %cd (committer date) keeps rejected_at on the
    # wall-clock order of the history even after rebases.
    fmt = "--pretty=format:%H%x1f%s%x1f%b%x1f%cd%x1e"
    if since:
        proc = _git(repo, "log", since + "..HEAD", "--date=short", fmt, check=False)
        if proc.returncode != 0:
            since = None
        else:
            return _parse_log(proc.stdout)
    proc = _git(repo, "log", "--date=short", fmt, check=False)
    if proc.returncode != 0:
        # An unborn branch (no commits yet) is a normal empty result.
        if "does not have any commits yet" in proc.stderr:
            return []
        raise DetectError("`git log` failed: %s" % proc.stderr.strip())
    return _parse_log(proc.stdout)


def _parse_log(stdout):
    commits = []
    for chunk in stdout.split(_RECORD):
        parts = chunk.strip("\n").split(_FIELD)
        if len(parts) != 4:
            continue
        sha, subject, body, date = (part.strip() for part in parts)
        if sha:
            commits.append((sha, subject, body, date))
    return commits


def is_revert(subject, body):
    return subject.startswith(REVERT_PREFIX) or bool(REVERTS_COMMIT_RE.search(body or ""))


def _strip_revert_wrapper(subject):
    stripped = subject
    if stripped.startswith(REVERT_PREFIX):
        stripped = stripped[len(REVERT_PREFIX):]
    if stripped.endswith('"'):
        stripped = stripped[:-1]
    return stripped


def existing_commits(repo, shas):
    """Subset of `shas` that exist as commits — one cat-file call.

    `--batch-check` answers one line per input object, in input order, so
    abbreviated shas keep their original spelling for downstream git calls.
    """
    shas = list(dict.fromkeys(shas))
    if not shas:
        return set()
    proc = _git(
        repo,
        "cat-file", "--batch-check=%(objecttype)",
        check=False,
        input="\n".join(shas) + "\n",
    )
    if proc.returncode != 0:
        # One malformed line poisons the whole batch; fall back to per-sha.
        return {s for s in shas if _git(repo, "cat-file", "-e", s + "^{commit}", check=False).returncode == 0}
    out = set()
    for sha, line in zip(shas, proc.stdout.splitlines()):
        if line.strip() == "commit":
            out.add(sha)
    return out


def batch_commit_info(repo, shas):
    """{full sha: (subject, committer-date, [files])} — one git log call.

    The record separator leads each record (%x1e%H...): a trailing one would
    strand the `--name-only` file list of record k inside chunk k+1.
    """
    shas = list(dict.fromkeys(shas))
    if not shas:
        return {}
    fmt = "--pretty=format:%x1e%H%x1f%s%x1f%cd%x1f"
    proc = _git(
        repo,
        "log", "--no-walk", "--stdin", "--date=short", "--name-only", fmt,
        check=False,
        input="\n".join(shas) + "\n",
    )
    if proc.returncode != 0:
        return {}
    info = {}
    for chunk in proc.stdout.split(_RECORD):
        parts = chunk.strip("\n").split(_FIELD)
        if len(parts) != 4 or not re.fullmatch(r"[0-9a-f]{7,40}", parts[0]):
            continue
        files = sorted({ln.strip() for ln in parts[3].splitlines() if ln.strip()})
        info[parts[0]] = (parts[1], parts[2], files)
    return info


def commit_details(repo, sha):
    """(subject, body, committer-date) of one commit — raises if unknown."""
    proc = _git(
        repo,
        "log", "-1", "--no-walk", "--date=short",
        "--pretty=format:%H%x1f%s%x1f%b%x1f%cd%x1e",
        sha,
        check=False,
    )
    parsed = _parse_log(proc.stdout)
    if proc.returncode != 0 or not parsed:
        raise DetectError("no such commit: %s" % sha)
    return parsed[0][1], parsed[0][2], parsed[0][3]


def _lookup(info, abbrev):
    """Resolve an abbreviated sha against batch_commit_info's full-sha keys."""
    for full in info:
        if full.startswith(abbrev):
            return full
    return None


def detect(repo, store=None, full=False) -> DetectReport:
    repo = Path(repo).resolve()
    store = store or TombstoneStore(repo)
    report = DetectReport()

    # Incremental by default: a `.cursor` file records the last scanned
    # commit, so the post-commit hook only pays for new history. The cursor
    # lives inside .tombstones/, so deleting the ledger also deletes the
    # cursor and the next detect rescans everything.
    cursor = store.dir / ".cursor"
    since = None
    if not full and cursor.is_file():
        recorded = cursor.read_text(encoding="utf-8").strip()
        if recorded:
            since = recorded

    reverts = []
    for commit in scan_reverts(repo, since=since):
        if is_revert(commit[1], commit[2]):
            reverts.append(commit)
    report.reverts = len(reverts)

    # Prefetch everything the loop needs in two git calls: which revert
    # targets still exist, and subject/date/files for both the targets and
    # the revert commits themselves (the latter scope the fallback path).
    targets = []
    for sha, subject, body, _date in reverts:
        match = REVERTS_COMMIT_RE.search((subject or "") + "\n" + (body or ""))
        if match:
            targets.append(match.group(1))
    existing = existing_commits(repo, targets)
    info = batch_commit_info(repo, list(existing) + [c[0] for c in reverts])

    for sha, subject, body, date in reverts:
        tomb_id = make_id(date, "revert", sha)
        if store.has(tomb_id):
            report.skipped.append(tomb_id)
            continue
        match = REVERTS_COMMIT_RE.search((subject or "") + "\n" + (body or ""))
        reverted = ""
        if match and match.group(1) in existing:
            reverted = match.group(1)
        if reverted:
            entry = info.get(_lookup(info, reverted) or "")
            attempt = (entry[0] if entry else "") or _strip_revert_wrapper(subject)
            scope = entry[2] if entry else []
            evidence = ["revert " + sha, "commit " + reverted]
        else:
            entry = info.get(sha)
            attempt = _strip_revert_wrapper(subject)
            scope = entry[2] if entry else []
            evidence = ["revert " + sha]
        tomb = store.add(
            Tombstone(
                id=tomb_id,
                attempt=attempt,
                scope=scope,
                rejected_at=date,
                # Unknowable from git alone; correct it during human review.
                rejected_by="human-review",
                reason=(
                    "Auto-detected from revert commit %s (%s). Candidate pending "
                    "human review: edit the reason and run `epitaph approve %s`."
                    % (sha[:10], subject, tomb_id)
                ),
                evidence=evidence,
                retry_when="",
                status="active",
                confidence="candidate",
            )
        )
        report.created.append(tomb.id)

    # Persist the scan position only when a ledger already exists: an empty
    # scan on a storeless repo must not silently create .tombstones/ and
    # hide the fact that no ledger was ever started.
    if store.exists():
        head = _git(repo, "rev-parse", "HEAD", check=False)
        if head.returncode == 0 and head.stdout.strip():
            cursor.write_text(head.stdout.strip() + "\n", encoding="utf-8")
    return report
