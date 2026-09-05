from epitaph.matcher import (
    match_one,
    match_tombstones,
    normalized,
    tokenize,
)
from epitaph.schema import Tombstone


def make_tomb(**over):
    fields = dict(
        id="ts-20260812-a3f2",
        attempt="Redis-based distributed lock to serialize session writes",
        scope=["src/session/lock.py", "src/session/manager.py"],
        rejected_at="2026-08-12",
        rejected_by="human-review",
        reason="race window remained; 3 flaky tests",
        evidence=["PR #412"],
        retry_when="with a fencing token",
        status="active",
        confidence="approved",
    )
    fields.update(over)
    return Tombstone(**fields)


def test_tokenize_handles_korean_and_case():
    assert tokenize("Redis 기반 분산 락!") == ["redis", "기반", "분산", "락"]
    assert tokenize("Hello, WORLD") == ["hello", "world"]


def test_normalized_strips_punctuation():
    assert normalized("src/session/lock.py") == "src session lock py"


def test_substring_match_ignores_punctuation_and_case():
    match = match_one(make_tomb(), query="redis distributed lock")
    assert match is not None
    assert match.score == 1.0
    assert any("substring" in r for r in match.reasons)


def test_token_overlap_match():
    # no substring, but 2 of 3 query tokens present
    match = match_one(make_tomb(), query="lock serialize sessions")
    assert match is not None
    assert any("token overlap" in r for r in match.reasons)


def test_token_overlap_below_threshold_misses():
    assert match_one(make_tomb(), query="kafka event sourcing") is None


def test_single_token_requires_exact_token():
    assert match_one(make_tomb(), query="redis") is not None
    assert match_one(make_tomb(), query="red") is None  # no prefix fuzz
    assert match_one(make_tomb(), query="dis") is None


def test_korean_query_matches_korean_attempt():
    tomb = make_tomb(attempt="Redis 기반 분산 락으로 세션 직렬화")
    assert match_one(tomb, query="분산 락") is not None
    assert match_one(tomb, query="카프카 스트림") is None


def test_file_matches_scope_both_directions():
    hit = match_one(make_tomb(), files=["src/session/lock.py"])
    assert hit is not None
    assert any("scope" in r for r in hit.reasons)
    # shorter query path contained in a scope entry
    hit2 = match_one(make_tomb(), files=["session/manager.py"])
    assert hit2 is not None
    assert match_one(make_tomb(), files=["src/billing/invoice.py"]) is None


def test_reason_and_scope_are_searched_too():
    match = match_one(make_tomb(), query="flaky tests")
    assert match is not None


def test_no_query_and_no_files_means_no_match():
    assert match_one(make_tomb(), query=None, files=None) is None


def test_match_tombstones_sorted_best_first():
    strong = make_tomb(id="ts-20260812-0001")
    weak = make_tomb(
        id="ts-20260812-0002",
        attempt="kafka consumer for sessions",
        reason="lock free design",
        scope=["src/queue/consumer.py"],
    )
    matches = match_tombstones(query="redis lock serialize sessions", tombstones=[weak, strong])
    assert [m.tombstone.id for m in matches] == [strong.id, weak.id]


def test_match_tombstones_filters_non_matches():
    tombs = [make_tomb(), make_tomb(id="ts-20260812-0003", attempt="unrelated thing")]
    matches = match_tombstones(query="redis lock", tombstones=tombs)
    assert len(matches) == 1
