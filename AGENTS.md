# AGENTS.md — tombstone

A repo-scoped ledger of rejected agent attempts. One JSON file per tombstone in
`.tombstones/` inside the *user's* repo (not this one). Agents query it via MCP
(`check_nogo`) or CLI (`epitaph check`) **before** retrying an approach; humans approve
candidates with one line. See README.md for the full story and differentiation vs
deadends.dev / Mem0 / ADRs.

## Layout

```
src/epitaph/
  schema.py   record schema, strict validate(), ID_RE (ts-YYYYMMDD-<4 hex>), make_id (sha-seeded)
  store.py    TombstoneStore: one JSON file per record under .tombstones/, tolerant all()
  matcher.py  deterministic matching: normalize → substring / token-overlap (+ stopwords, query side)
  detect.py   git revert scanner (incremental via .tombstones/.cursor) + DetectReport
  render.py   THE match-report renderer — shared by CLI `check` and MCP `check_nogo`
  cli.py      argparse CLI (init/add/approve/overturn/list/show/check/detect/install-hook/snippets/review)
  mcp.py      zero-dep stdio JSON-RPC MCP server (read-only tools only)
examples/     valid tombstone records shown in the README
tests/        pytest; detect tests build real temp git repos via subprocess
```

## Commands

```bash
pip install -e . && pip install pytest   # zero runtime deps; pytest is test-only
pytest                                    # from repo root
python -m epitaph.mcp --repo /some/repo # MCP smoke (speak JSON-RPC on stdin)
```

## Invariants — do not break

- **Zero runtime dependencies** (stdlib only), Python ≥ 3.10. The MCP server must stay
  dependency-free and **read-only** (no mutation tools).
- **Deterministic ids**: `ts-YYYYMMDD-<4 hex>` via `make_id(date, *seed)` — `detect` seeds
  with the revert sha so rescans never duplicate; `add` bumps `salt` on collision. Never
  weaken `ID_RE` or `validate()`; unknown extra keys on records must be preserved
  (schema evolution).
- **Tombstones are testimony, not verdicts**: never emit "impossible" language;
  `retry_when` is expected on records; only the human `approve` command reaches
  `approved` confidence — an agent must never self-approve.
- **Matching haystack = `attempt + reason` only.** Scope paths are full of common words
  and match exclusively via the `files` argument (both directions). Query-side tokens drop
  stopwords; single-token queries need an exact token (no prefix fuzz, `"red" ↛ "redis"`);
  multi-token needs substring or ≥ 0.5 overlap. `matcher.py` tests pin all of this.
- **detect is incremental**: scan `git log <cursor>..HEAD` using the sha in
  `.tombstones/.cursor`; write the cursor only when the store exists (never create
  `.tombstones/` silently on a storeless repo); fall back to a full scan when the cursor
  is missing or stale (history rewrite). `--full` bypasses the cursor. Dates come from
  `%cd` (committer), not `%ad`.
- **Hook argument order**: the installed hook runs `epitaph --repo "$(...)" detect`.
  `--repo` is a top-level argparse option and MUST precede the subcommand — the previous
  template put it after `detect` and argparse silently rejected it, disabling every hook
  (regression test: `test_installed_hook_actually_detects` executes the real hook).
- **One renderer**: `render.format_matches()` formats match reports for both CLI and MCP.
  Never re-implement report formatting at a call site. It caps output at `MATCH_LIMIT`
  (top matches + "and N more") — epitaph sells token economy; don't uncap without cause.
  `epitaph review` is the human-only approval loop: on closed stdin it approves NOTHING
  (an agent piping input must never self-approve).

## Testing gotchas

- detect tests create real git repos via subprocess (see `tests/test_detect.py` fixture).
- After running `detect` in a test, do **not** `git add -A`: that sweeps `.tombstones/`
  into the commit, and reverting that commit deletes the ledger mid-test. Stage specific
  files.
- The hook regression test points PATH at the venv `bin/` because the hook invokes bare
  `tombstone`.

## When you touch X, also update Y

- `matcher.py` semantics → README "check" paragraph + `tests/test_matcher.py`.
- Record schema → README record-schema block + `examples/` + `tests/test_schema.py`.
- `render.py` wording → `tests/test_mcp.py` and `tests/test_cli.py` assertions.
- Naming (decided 2026-09-05): dist/repo/console-script/import module are all
  **`epitaph`** (`src/epitaph/`); records stay "tombstones" in `.tombstones/`.
  User-facing command strings say `epitaph <subcommand>`. The hook invokes bare
  `epitaph` — keep the regression test green after touching it.
