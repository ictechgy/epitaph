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
- [x] **Session give-up detection** — DONE 2026-09-06 (after v0.2.0 shipped):
      `epitaph giveup [--limit N]` + `transcripts.py`. Format-discovered adapters
      (Claude Code munged-dir shortcut + Codex rollout format — both cwd-scoped,
      key-based defensive parsing, EN+KO give-up patterns). Drafts are always
      `candidate` / `rejected_by: agent-gaveup`; ids seeded with (vendor, session,
      message ts) so rescans never duplicate; storeless repos get counts + an
      init hint, never a silently created ledger. yield-audit's transcripts/
      package (base/claude/codex) was mined for the schema groundings — its
      adapter contract is the reference if more vendors are added.
      Note: yield-audit's own module evolved past the single claude.py file this
      entry originally referenced.
- [ ] **LLM drafter (optional flag)**: propose attempt/reason drafts for manual `add` and
      revert candidates. Must stay optional — v0.1 is fully functional without it.
      Priority note: convenience only — the stale audit (§3) is the real moat;
      if time is tight, build that first.
- [ ] **check_nogo refinement**: optional embedding similarity behind the lexical matcher
      (opt-in, local model). Keep lexical as default; deterministic-first principle.

## 3. v0.3 — the differentiator

- [x] **Stale audit** — DONE 2026-09-06 (stdlib-only first cut): `epitaph stale [--apply]`
      + `stale.py`. Path anchors → filesystem exists; symbol anchors → bounded text scan
      (VCS/build dirs skipped, >1MB and binary files skipped, memoized per run). Flips
      only when EVERY anchor is gone; report by default, `--apply` flips (human-gated
      like approve). The IndexStoreDB/Kotlin symbol-graph upgrade remains open —
      swapping the scan for a real symbol graph must keep the conservative flip rule
      (see AGENTS.md invariant). This is the main moat vs. generic memory tools:
      the ledger is *self-maintaining*, records expire when the code moves on.

## 4. Known small debts (P3, from review rounds)

- [ ] Scope-filter logic duplicated: `cli.cmd_list --scope` and `mcp._recent` implement the
      same normalized bidirectional containment — extract to matcher.py.
- [ ] `.cursor` merge-conflict markers self-heal today (garbage sha → full scan); consider a
      friendlier warning when the fallback triggers (`detect` could print "cursor unusable,
      rescanned full history").
- [x] Candidate surfacing — DONE 2026-09-06: `epitaph review` walks candidates
      interactively (approve/skip/quit); EOF approves nothing. `init --detect` +
      review-first hints close the cold-start loop.

## 4.5 UX/perf quick wins — DONE 2026-09-06 (pre-v0.2)

- [x] Cold start: `init` recommends `detect` first (free tombstones from history);
      `init --detect` runs it immediately.
- [x] Match reports capped at top 20 + "… and N more" (CLI + MCP, one renderer).
- [x] detect full scans are O(1) git calls: batch `cat-file --batch-check` for
      target existence + one `git log --stdin --name-only` for subjects/dates/files
      (measured: 5 reverts → 4 git calls; was ~3-per-revert). Watch the record
      separator: it must LEAD each record (`%x1e%H...`), a trailing one strands
      the file list of record k inside chunk k+1.
- [x] MCP server caches records on a per-file mtime signature; CLI edits to the
      ledger are visible on the next tool call without re-reading every file.
- [x] `epitaph add --from-commit SHA` prefills attempt/scope/evidence/rejected_at
      (revert commits resolve their target; explicit flags win).
- [x] `epitaph review` human approval loop (see above).

## 5. Pre-seed presets — pipeline DONE 2026-09-06, first curation pending

- [x] `epitaph export` (approved-only default, `--all` for candidates) and
      `epitaph import` (idempotent by id, `origin` provenance key) shipped.
- [x] `presets/` layout + curation policy (presets ship APPROVED records only;
      candidates/ is raw detect output, clearly labeled).
- [x] First mining run: facebook/react blobless clone → 194 revert candidates
      (2013–2026) exported to `presets/candidates/react.json`.
- [ ] NEXT (human step): review the react candidates in a scratch clone and
      export the endorsed subset as `presets/react.json` — the first real
      preset. Recipe in presets/README.md.

## Context pointers

- Differentiation must stay visible in README (deadends.dev = global error-signature layer,
  we = repo-scoped attempt ledger; complementary, not competing).
- yield-audit (`../yield-audit`) M9 is the measurement that proves this tool's value —
  consider a joint demo ("M9 measured X tokens of repeat tax; tombstone removed the top item").
  Status check 2026-09-05: M9 is still an *unimplemented roadmap item* in yield-audit
  (v0.2 there) — the joint demo is blocked until that lands.
