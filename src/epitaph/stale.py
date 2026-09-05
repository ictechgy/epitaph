"""Stale audit: tombstones whose scope anchors no longer exist.

A record is testimony about a place in the code. When every anchor for that
place is gone — the file was deleted, the symbol renamed away — the
rejection may no longer apply to the code that replaced it. The audit only
*reports* by default; flipping to ``status: stale`` is ``--apply``, a human
decision, exactly like approve.

Anchor semantics (stdlib-only, language-neutral):

- Path-like entries (contain a separator or a known source suffix) are
  checked with a filesystem ``exists`` against the repo root.
- Other entries are treated as symbols and searched for in the repo's text
  files (bounded walk: skip VCS/build dirs, large files, binaries). A
  tree-sitter/IndexStoreDB-grade symbol graph is a future upgrade; a text
  hit is a deliberately cheap, recall-leaning proxy — an anchor "exists" if
  the symbol appears anywhere in the current tree.

A tombstone flips only when *all* of its anchors are gone. Partial
survival keeps it active: testimony, not verdicts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".tombstones", "__pycache__",
    "node_modules", ".venv", "venv", "dist", "build", ".eggs",
}
MAX_FILE_BYTES = 1_000_000  # symbols do not live in >1MB blobs worth scanning

_PATH_SUFFIXES = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".kt", ".kts", ".java",
    ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".rb", ".m", ".mm", ".md",
    ".json", ".yaml", ".yml", ".toml", ".sh", ".sql",
)


class StaleError(RuntimeError):
    """Audit failed (unusable repo path)."""


@dataclass
class StaleFinding:
    tombstone: object
    missing: list = field(default_factory=list)  # anchors gone from the repo
    total: int = 0


def is_pathlike(entry: str) -> bool:
    return "/" in entry or "\\" in entry or entry.endswith(_PATH_SUFFIXES)


def _iter_text_files(repo: Path):
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                with path.open("rb") as handle:
                    if b"\x00" in handle.read(8192):  # binary sniff
                        continue
            except OSError:
                continue
            yield path


class _SymbolSearcher:
    """First-hit text search per symbol, memoized across the audit run."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.cache = {}

    def exists(self, symbol: str) -> bool:
        if symbol in self.cache:
            return self.cache[symbol]
        found = False
        for path in _iter_text_files(self.repo):
            try:
                if symbol in path.read_text(encoding="utf-8", errors="replace"):
                    found = True
                    break
            except OSError:
                continue
        self.cache[symbol] = found
        return found


def audit_stale(repo, store):
    """StaleFindings for active, scoped tombstones whose anchors are ALL gone.

    Unscoped tombstones and already-stale/overturned ones are not audited:
    there is nothing to check them against / they already carry their verdict.
    """
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise StaleError("not a directory: %s" % repo)
    searcher = _SymbolSearcher(repo)
    findings = []
    for tomb in store.all():
        if tomb.status != "active" or not tomb.scope:
            continue
        missing = []
        for anchor in tomb.scope:
            anchor = anchor.strip()
            if not anchor:
                continue
            if is_pathlike(anchor):
                candidate = repo / anchor.lstrip("./")
                if not candidate.exists():
                    missing.append(anchor)
            elif not searcher.exists(anchor):
                missing.append(anchor)
        if tomb.scope and len(missing) == len([a for a in tomb.scope if a.strip()]):
            findings.append(StaleFinding(tombstone=tomb, missing=missing, total=len(tomb.scope)))
    return findings
