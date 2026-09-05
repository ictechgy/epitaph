"""Revert detector: scan `git log` for revert commits and file candidate
tombstones. Deterministic rules only — no LLM, no network (v0.1 principle).

A commit counts as a revert when its subject starts with `Revert "` and/or
its body contains `This reverts commit <sha>`. Idempotency: the tombstone id
is derived from the revert commit sha, so re-running detect never duplicates.
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


def _git(repo, *args, check=True):
    proc = subprocess.run(("git",) + args, cwd=str(repo), capture_output=True, text=True)
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


def _commit_exists(repo, sha):
    return _git(repo, "cat-file", "-e", sha + "^{commit}", check=False).returncode == 0


def _strip_revert_wrapper(subject):
    stripped = subject
    if stripped.startswith(REVERT_PREFIX):
        stripped = stripped[len(REVERT_PREFIX):]
    if stripped.endswith('"'):
        stripped = stripped[:-1]
    return stripped


def _commit_subject(repo, sha):
    return _git(repo, "log", "-1", "--pretty=%s", sha).stdout.strip()


def _commit_files(repo, sha):
    out = _git(repo, "show", "--name-only", "--pretty=format:", sha).stdout
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


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

    for sha, subject, body, date in scan_reverts(repo, since=since):
        if not is_revert(subject, body):
            continue
        report.reverts += 1
        tomb_id = make_id(date, "revert", sha)
        if store.has(tomb_id):
            report.skipped.append(tomb_id)
            continue
        match = REVERTS_COMMIT_RE.search((subject or "") + "\n" + (body or ""))
        reverted = ""
        if match and _commit_exists(repo, match.group(1)):
            reverted = match.group(1)
        if reverted:
            attempt = _commit_subject(repo, reverted) or _strip_revert_wrapper(subject)
            scope = _commit_files(repo, reverted)
            evidence = ["revert " + sha, "commit " + reverted]
        else:
            attempt = _strip_revert_wrapper(subject)
            scope = _commit_files(repo, sha)
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
                    "human review: edit the reason and run `tombstone approve %s`."
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
