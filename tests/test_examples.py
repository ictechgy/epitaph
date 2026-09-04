import json
from pathlib import Path

import pytest

from tombstone.schema import Tombstone

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.skipif(not EXAMPLES.is_dir(), reason="examples/ not shipped in this checkout")
def test_example_files_are_valid_tombstones():
    files = sorted(EXAMPLES.glob("*.json"))
    assert files, "expected example tombstones"
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        tomb = Tombstone.from_dict(record)
        assert tomb.id == record["id"]
        # file name must carry the record id
        assert path.stem == tomb.id
