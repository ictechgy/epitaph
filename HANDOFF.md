# HANDOFF — what the next session should do

State at writing: v0.1.0 **shipped** — github.com/ictechgy/epitaph (CI green) and
pypi.org/project/epitaph (trusted publishing via `.github/workflows/pypi.yml`, tag-triggered;
`git tag vX.Y.Z && git push origin vX.Y.Z` releases). 96/96 tests green, clean tree.
Read `AGENTS.md` first (invariants + gotchas), `기획서.md` for the original vision, README for UX.

## 1. Do first (small, decided) — ALL DONE

- [x] **Ship it** — DONE 2026-09-05: github.com/ictechgy/epitaph pushed, Actions CI green
      (Python 3.10/3.13 matrix).
- [x] **Name decision** — DONE 2026-09-05: dist/repo/console-script name is **`epitaph`**
      (PyPI + GitHub verified clear), and the import module moved to `src/epitaph/`
      before the first release (env var is now `EPITAPH_REPO`). Records stay
      "tombstones" in `.tombstones/`; user-facing strings say `epitaph <subcommand>`.
- [x] Publish to PyPI — DONE 2026-09-05 via Trusted Publishers (pypi.yml); README install
      line flipped to `pipx install epitaph` / `uv tool install epitaph`.

## 2. v0.2 — the feature line (from 기획서, in order)

- [x] **AGENTS.md snippet generator** — DONE: `epitaph snippets` (and `init --snippets`).
      Creates `AGENTS.md` if absent, appends the check_nogo rule idempotently, touches
      `CLAUDE.md` only when it already exists (never forks the source of truth).
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
      Priority note: convenience only — the stale audit (§3) is the real moat;
      if time is tight, build that first.
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
  Status check 2026-09-05: M9 is still an *unimplemented roadmap item* in yield-audit
  (v0.2 there) — the joint demo is blocked until that lands.
