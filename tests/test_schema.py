import re

import pytest

from tombstone.schema import (
    FIELDS,
    SchemaError,
    Tombstone,
    make_id,
    validate,
)


def base_record(**over):
    record = {
        "id": "ts-20260812-a3f2",
        "attempt": "Redis-based distributed lock",
        "scope": ["src/session/lock.py"],
        "rejected_at": "2026-08-12",
        "rejected_by": "human-review",
        "reason": "racy",
        "evidence": ["PR #412"],
        "retry_when": "with fencing tokens",
        "status": "active",
        "confidence": "approved",
    }
    record.update(over)
    return record


def test_make_id_format():
    assert re.fullmatch(r"ts-\d{8}-[0-9a-f]{4}", make_id("2026-08-12", "seed"))


def test_make_id_derived_from_date():
    assert make_id("2026-08-12", "x").startswith("ts-20260812-")


def test_make_id_deterministic_and_seed_sensitive():
    assert make_id("2026-08-12", "x") == make_id("2026-08-12", "x")
    assert make_id("2026-08-12", "x") != make_id("2026-08-12", "y")
    assert make_id("2026-08-13", "x") != make_id("2026-08-12", "x")
    assert make_id("2026-08-12", "x") != make_id("2026-08-12", "x", salt=1)


def test_validate_accepts_full_record():
    validate(base_record())


def test_validate_accepts_empty_id_for_store_assignment():
    validate(base_record(id=""))


def test_validate_missing_field():
    for field in FIELDS:
        record = base_record()
        del record[field]
        with pytest.raises(SchemaError, match="missing required field"):
            validate(record)


def test_validate_bad_id_format():
    with pytest.raises(SchemaError, match="ts-YYYYMMDD"):
        validate(base_record(id="abc-123"))


@pytest.mark.parametrize("bad", ["2026-13-01", "08/12/2026", "2026-2-3", "20260812", "2026-02-30"])
def test_validate_bad_dates(bad):
    with pytest.raises(SchemaError):
        validate(base_record(rejected_at=bad))


@pytest.mark.parametrize("field", ["attempt", "reason", "retry_when"])
def test_validate_strings(field):
    with pytest.raises(SchemaError):
        validate(base_record(**{field: 42}))


def test_validate_attempt_nonempty():
    with pytest.raises(SchemaError, match="non-empty"):
        validate(base_record(attempt="   "))


@pytest.mark.parametrize("field", ["scope", "evidence"])
def test_validate_list_fields(field):
    with pytest.raises(SchemaError, match=field):
        validate(base_record(**{field: "not-a-list"}))
    with pytest.raises(SchemaError, match=field):
        validate(base_record(**{field: ["ok", 7]}))


def test_validate_enums():
    with pytest.raises(SchemaError, match="rejected_by"):
        validate(base_record(rejected_by="vibes"))
    with pytest.raises(SchemaError, match="status"):
        validate(base_record(status="done"))
    with pytest.raises(SchemaError, match="confidence"):
        validate(base_record(confidence="sure"))


def test_validate_non_dict():
    with pytest.raises(SchemaError, match="JSON object"):
        validate([1, 2, 3])


def test_from_dict_roundtrip_preserves_extra_keys():
    record = base_record(overturn_reason="retry landed", custom_field={"a": 1})
    tomb = Tombstone.from_dict(record)
    assert tomb.to_dict() == record
    assert tomb.extra == {"custom_field": {"a": 1}}


def test_from_dict_rejects_invalid():
    with pytest.raises(SchemaError):
        Tombstone.from_dict(base_record(confidence="auto-approved"))


def test_tombstone_defaults():
    tomb = Tombstone()
    assert tomb.status == "active"
    assert tomb.confidence == "candidate"
    assert tomb.to_dict()["scope"] == []
