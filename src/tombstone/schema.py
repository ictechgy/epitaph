"""Tombstone record schema: validation, id generation, (de)serialization.

One tombstone = one JSON file in a repo's `.tombstones/` directory.
Enums and formats are validated strictly; unknown extra keys are preserved
so the schema can grow without corrupting old records.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field

ID_RE = re.compile(r"^ts-\d{8}-[0-9a-f]{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REJECTED_BY_VALUES = ("human-review", "ci", "agent-gaveup")
STATUS_VALUES = ("active", "stale", "overturned")
CONFIDENCE_VALUES = ("approved", "candidate")

FIELDS = (
    "id",
    "attempt",
    "scope",
    "rejected_at",
    "rejected_by",
    "reason",
    "evidence",
    "retry_when",
    "status",
    "confidence",
)


class SchemaError(ValueError):
    """A record violates the tombstone schema."""


def make_id(rejected_at: str, *seed: str, salt: int = 0) -> str:
    """Deterministic id `ts-YYYYMMDD-<4 hex>` derived from date + seed.

    `detect` seeds with the revert sha so re-running it is idempotent;
    `add` seeds with the attempt text and the store bumps `salt` on collision.
    """
    payload = "\x1f".join((rejected_at, *seed, str(salt)))
    hex4 = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:4]
    return "ts-%s-%s" % (rejected_at.replace("-", ""), hex4)


def validate(data: object) -> None:
    """Raise SchemaError unless `data` is a valid tombstone record."""
    if not isinstance(data, dict):
        raise SchemaError("record must be a JSON object")
    missing = [name for name in FIELDS if name not in data]
    if missing:
        raise SchemaError("missing required field(s): " + ", ".join(missing))

    if not isinstance(data["id"], str) or not (data["id"] == "" or ID_RE.match(data["id"])):
        raise SchemaError("id must match ts-YYYYMMDD-<4 hex>, got %r" % (data["id"],))

    if not isinstance(data["rejected_at"], str) or not DATE_RE.match(data["rejected_at"]):
        raise SchemaError(
            "rejected_at must be an ISO date (YYYY-MM-DD), got %r" % (data["rejected_at"],)
        )
    try:
        dt.date.fromisoformat(data["rejected_at"])
    except ValueError:
        raise SchemaError(
            "rejected_at is not a real calendar date: %r" % (data["rejected_at"],)
        ) from None

    for name in ("attempt", "reason", "retry_when"):
        if not isinstance(data[name], str):
            raise SchemaError("%s must be a string" % name)
    if not data["attempt"].strip():
        raise SchemaError("attempt must be a non-empty string")

    for name in ("scope", "evidence"):
        value = data[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SchemaError("%s must be a list of strings" % name)

    for name, allowed in (
        ("rejected_by", REJECTED_BY_VALUES),
        ("status", STATUS_VALUES),
        ("confidence", CONFIDENCE_VALUES),
    ):
        if data[name] not in allowed:
            raise SchemaError(
                "%s must be one of %s, got %r" % (name, "|".join(allowed), data[name])
            )

    if "overturn_reason" in data and not isinstance(data["overturn_reason"], str):
        raise SchemaError("overturn_reason must be a string")


@dataclass
class Tombstone:
    id: str = ""
    attempt: str = ""
    scope: list = field(default_factory=list)
    rejected_at: str = ""
    rejected_by: str = ""
    reason: str = ""
    evidence: list = field(default_factory=list)
    retry_when: str = ""
    status: str = "active"
    confidence: str = "candidate"
    overturn_reason: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {name: getattr(self, name) for name in FIELDS}
        if self.overturn_reason:
            data["overturn_reason"] = self.overturn_reason
        for key, value in self.extra.items():
            data.setdefault(key, value)
        return data

    @classmethod
    def from_dict(cls, data: object) -> "Tombstone":
        validate(data)
        source = dict(data)
        fields = {name: source[name] for name in FIELDS}
        extra = {
            key: value
            for key, value in source.items()
            if key not in FIELDS and key != "overturn_reason"
        }
        return cls(**fields, overturn_reason=source.get("overturn_reason", ""), extra=extra)
