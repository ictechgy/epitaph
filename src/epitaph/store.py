"""Filesystem store: one JSON file per tombstone under `<repo>/.tombstones/`.

One file per record keeps git merges small and partial adoption easy
(design decision from the spec).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .schema import ID_RE, SchemaError, Tombstone, make_id, validate

DIRNAME = ".tombstones"


class StoreError(RuntimeError):
    """Storage-level failure (id collision, corrupt file, missing store)."""


class TombstoneStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.dir = self.root / DIRNAME
        self.last_skipped = []  # unreadable files seen by the most recent all()

    @classmethod
    def find(cls, start="."):
        """Nearest ancestor of `start` (inclusive) that has a .tombstones/ dir."""
        path = Path(start).resolve()
        for candidate in (path, *path.parents):
            if (candidate / DIRNAME).is_dir():
                return cls(candidate)
        return None

    def exists(self) -> bool:
        return self.dir.is_dir()

    def create(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, tomb_id: str) -> Path:
        return self.dir / (tomb_id + ".json")

    def has(self, tomb_id: str) -> bool:
        return self.path_for(tomb_id).exists()

    def add(self, tomb: Tombstone, seed=()) -> Tombstone:
        """Insert a new record, assigning a deterministic id when missing."""
        self.create()
        if not tomb.id:
            salt = 0
            tomb.id = make_id(tomb.rejected_at, *seed, salt=salt)
            while self.has(tomb.id):
                salt += 1
                tomb.id = make_id(tomb.rejected_at, *seed, salt=salt)
        elif self.has(tomb.id):
            raise StoreError("tombstone already exists: " + tomb.id)
        self.save(tomb)
        return tomb

    def save(self, tomb: Tombstone) -> None:
        """Validate and write a record (insert or update).

        Written via temp-file + rename so a crash mid-write can never leave a
        half-written record — the ledger is committed to git, and a truncated
        JSON would silently skip in all() ever after.
        """
        data = tomb.to_dict()
        validate(data)
        if not ID_RE.match(tomb.id or ""):
            raise StoreError("refusing to save a record without a valid id")
        self.create()
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), prefix=".tmp-", suffix="")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path_for(tomb.id))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, tomb_id: str):
        # Validate the id before it ever touches the filesystem, so a
        # traversing argument can't read arbitrary .json files.
        if not ID_RE.match(tomb_id or ""):
            return None
        path = self.path_for(tomb_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError("corrupt tombstone file %s: %s" % (path, exc)) from None
        return Tombstone.from_dict(data)

    def all(self):
        """Every readable record, filename order; unreadable files are skipped
        (counted in `last_skipped`) so one bad file can't break listing."""
        self.last_skipped = []
        records = []
        if not self.exists():
            return records
        for path in sorted(self.dir.glob("*.json")):
            if path.name.startswith("."):
                # hidden = our own interrupted temp files, never records
                continue
            try:
                records.append(
                    Tombstone.from_dict(json.loads(path.read_text(encoding="utf-8")))
                )
            except (OSError, json.JSONDecodeError, SchemaError, UnicodeDecodeError):
                # OSError included: a file we can't stat/read (permissions,
                # dangling symlink, a directory named *.json) must not break
                # listing either.
                self.last_skipped.append(path.name)
        return records
