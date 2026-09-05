# HANDOFF — what the next session should do

State at writing: v0.1.0, 3 commits, 88/88 tests green, clean tree, **no remote, not on PyPI**.
Read `AGENTS.md` first (invariants + gotchas), `기획서.md` for the original vision, README for UX.

## 1. Do first (small, decided)

- [ ] **Ship it**: create the GitHub repo, push, confirm the Actions CI workflow goes green
      (`.github/workflows/ci.yml` exists but has never run).
- [ ] **Name decision before PyPI**: working name `tombstone` collides with Android crash
      dumps / Cassandra tombstones. Leading candidate: **`epitaph`** (search-clean, matches
      the reason-centric design). If renaming: package/repo/console-script/README/AGENTS.md
      in one sweep + `git mv`-style commit. Check GitHub + PyPI availability first.
- [ ] Publish to PyPI (after name), then flip README install line from "from a checkout" to real install commands (`uvx`/`pipx`).

## 2. v0.2 — the feature line (from 기획서, in order)

- [ ] **Session give-up detection**: parse agent transcripts for "I'll try a different
      approach" transitions and draft `rejected_by: agent-gaveup` candidates.
      Start from the transcript adapter in the sibling project `../yield-audit`
      (`src/yield_audit/transcripts.py`) — but note it is **Claude Code-only**
      (reads `~/.claude/projects/<munged-cwd>/*.jsonl`). Vendor decision: tombstone
      itself must stay vendor-agnostic — define a small adapter interface
      (e.g. `iter_transcripts(repo) -> TranscriptEvents`, detected by format, not
      by vendor name), ship the Claude Code adapter first, and leave adapters for
      other vendors (Codex session logs, Cursor history) as drop-ins. README's
      MCP section already documents Cursor/Codex config; keep give-up detection
      documented the same way ("Claude Code today, adapter interface for the rest").
- [ ] **LLM drafter (optional flag)**: propose attempt/reason drafts for manual `add` and
      revert candidates. Must stay optional — v0.1 is fully functional without it.
- [ ] **AGENTS.md snippet generator**: `tombstone init --snippets` (or similar) that
      appends the recommended `check_nogo` rule (README "Recommended one-line rule")
      to the user's AGENTS.md/CLAUDE.md.
- [ ] **check_nogo refinement**: optional embedding similarity behind the lexical matcher
      (opt-in, local model). Keep lexical as default; deterministic-first principle.

## 3. v0.3 — the differentiator

- [ ] **Stale audit via symbol graphs**: a tombstone whose `scope` symbols no longer exist
      should auto-flip to `status: stale`. Use IndexStoreDB (iOS) / Kotlin symbol extraction —
      this is the maintainer's static-analysis edge and the main moat vs. generic memory tools.

## 4. Known small debts (P3, from review rounds)

- [ ] Scope-filter logic duplicated: `cli.cmd_list --scope` and `mcp._recent` implement the
      same normalized bidirectional containment — extract to matcher.py.
- [ ] `.cursor` merge-conflict markers self-heal today (garbage sha → full scan); consider a
      friendlier warning when the fallback triggers (`detect` could print "cursor unusable,
      rescanned full history").
- [ ] `detect` drafts `rejected_by: human-review` blindly — consider surfacing an explicit
      "correct me" marker in `list` output for candidates.

## Context pointers

- Differentiation must stay visible in README (deadends.dev = global error-signature layer,
  we = repo-scoped attempt ledger; complementary, not competing).
- yield-audit (`../yield-audit`) M9 is the measurement that proves this tool's value —
  consider a joint demo ("M9 measured X tokens of repeat tax; tombstone removed the top item").
