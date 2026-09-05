import json

import pytest

from epitaph.cli import main
from epitaph.schema import Tombstone
from epitaph.store import TombstoneStore


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    monkeypatch.chdir(r)
    return r


def _approve_all(repo):
    store = TombstoneStore(repo)
    for tomb in store.all():
        tomb.confidence = "approved"
        store.save(tomb)


def _add(repo, attempt, confidence=None):
    argv = ["add", "--attempt", attempt, "--reason", "r", "--retry-when", "w"]
    if confidence:
        argv += ["--confidence", confidence]
    assert main(argv) == 0


def test_export_approved_only_by_default(repo, tmp_path, capsys):
    _add(repo, "approved attempt")
    _approve_all(repo)
    _add(repo, "candidate attempt")
    out = tmp_path / "bundle.json"
    assert main(["export", "-o", str(out)]) == 0
    bundle = json.loads(out.read_text())
    assert bundle["format"] == "epitaph-preset/v1"
    attempts = [r["attempt"] for r in bundle["records"]]
    assert attempts == ["approved attempt"]

    assert main(["export", "--all", "-o", str(out)]) == 0
    bundle = json.loads(out.read_text())
    assert len(bundle["records"]) == 2


def test_export_stdout_is_valid_bundle(repo, capsys):
    _add(repo, "x")
    _approve_all(repo)
    capsys.readouterr()  # drop the "created ..." line before capturing the bundle
    assert main(["export"]) == 0
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["format"] == "epitaph-preset/v1"


def test_import_roundtrip_idempotent(repo, tmp_path, capsys):
    _add(repo, "redis lock for sessions")
    _approve_all(repo)
    bundle = tmp_path / "b.json"
    assert main(["export", "-o", str(bundle)]) == 0

    other = tmp_path / "other-repo"
    other.mkdir()
    assert main(["--repo", str(other), "import", str(bundle)]) == 0
    out = capsys.readouterr().out
    assert "1 imported, 0 already present" in out
    records = TombstoneStore(other).all()
    assert len(records) == 1
    assert records[0].confidence == "approved"  # approval travels with curation
    assert records[0].extra.get("origin")  # provenance recorded

    assert main(["--repo", str(other), "import", str(bundle)]) == 0
    assert "0 imported, 1 already present" in capsys.readouterr().out


def test_import_rejects_invalid_records(repo, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "epitaph-preset/v1", "records": [{"attempt": "no id"}]}))
    code = main(["import", str(bad)])
    assert code == 1
    assert "invalid record" in capsys.readouterr().err


def test_import_bare_list_with_origin(repo, tmp_path, capsys):
    record = Tombstone(
        attempt="a", reason="r", rejected_at="2026-09-01",
        rejected_by="human-review", retry_when="w",
    )
    from epitaph.schema import make_id
    record.id = make_id(record.rejected_at, "seed")
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps([record.to_dict()]))
    assert main(["import", str(bare), "--origin", "preset:react"]) == 0
    tomb = TombstoneStore(repo).all()[0]
    assert tomb.extra["origin"] == "preset:react"
