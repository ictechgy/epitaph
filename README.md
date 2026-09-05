# epitaph

> Every agent memory accumulates successes and preferences. **epitaph** accumulates refusals: it records rejected patches, rolled-back approaches, and abandoned paths as structured tombstones inside your repo — so the next agent checks before walking the same dead end. *"This approach was buried here, in this module, on August 12."*

**Status:** v0.1 (deterministic core) — on PyPI as [`epitaph`](https://pypi.org/project/epitaph/). Records are "tombstones" living in `.tombstones/`, which is where the tool's working name came from.

## The problem: failure knowledge evaporates three times

1. **git history** — `Revert "Add Redis lock"` records *what* was reverted but destroys *why*, under what conditions, and what the alternative was.
2. **Review** — PR closes and objection threads bury rejection reasons in unsearchable form.
3. **Sessions** — the moment an agent declares "I'll try a different approach" lives only in the transcript, and dies with the session.

The result is the worst item on any inter-session yield audit: **re-discovering the same failure**. Human teams solved this with brains and folklore ("we tried that in August, it went badly"). In the agent era, that brain is not stored anywhere.

epitaph is the **repo-scoped attempt ledger**: one JSON file per rejected attempt, committed to the repo, queryable by machine before the retry happens.

## Quickstart

```bash
# install (published on PyPI)
pipx install epitaph    # or: pip install epitaph / uv tool install epitaph

cd path/to/your/repo

epitaph init
epitaph add \
  --attempt "Redis-based distributed lock to serialize session writes" \
  --reason "Race window was not closed; retry storm under load" \
  --scope src/session/lock.py src/session/manager.py \
  --evidence "PR #412" "revert 9f2e1a" \
  --retry-when "once a fencing token sits in front of the lock"

epitaph list
epitaph check "redis lock for sessions"   # what an agent runs BEFORE retrying
epitaph approve ts-20260812-a3f2          # one human line: candidate -> approved
```

### Automatic detection

`detect` scans `git log` for revert commits (subject starting with `Revert "` and/or a body containing `This reverts commit <sha>`) and drafts **candidate**-confidence tombstones linking the evidence:

```bash
epitaph detect
# created ts-20260902-1b7e (candidate)
# 1 revert commit(s) scanned, 1 created, 0 already recorded
```

Or install a post-commit hook that runs detect automatically (it can never fail your commit):

```bash
epitaph install-hook
```

`detect` is incremental and idempotent: it remembers the last scanned commit in `.tombstones/.cursor` (so the post-commit hook only pays for new history), and the tombstone id is derived from the revert sha, so even a forced full rescan (`epitaph detect --full`) never duplicates. If a history rewrite strands the cursor, detect falls back to a full scan automatically.

### Record schema (one JSON file per tombstone in `.tombstones/`)

```json
{
  "id": "ts-20260812-a3f2",
  "attempt": "Redis-based distributed lock to serialize session writes",
  "scope": ["src/session/lock.py", "src/session/manager.py"],
  "rejected_at": "2026-08-12",
  "rejected_by": "human-review",
  "reason": "Race window was not actually closed; 3 tests went flaky in CI.",
  "evidence": ["PR #412", "revert 9f2e1a"],
  "retry_when": "Revisit once a fencing token sits in front of the lock.",
  "status": "active",
  "confidence": "approved"
}
```

- `id` — `ts-YYYYMMDD-<4 hex>`
- `rejected_by` — `human-review` | `ci` | `agent-gaveup`
- `status` — `active` | `stale` (target code gone) | `overturned` (a retry succeeded — kept on purpose; honest failure data includes its own refutations)
- `confidence` — `approved` (human-confirmed) | `candidate` (not yet approved, shown with lower confidence on query)

One file per tombstone minimizes git merge conflicts and makes partial adoption easy. `.tombstones/` is meant to be **committed** — team sharing is the core value. Working solo? Add it to your personal gitignore.

See [`examples/`](examples/) for full records.

## MCP server (Claude Code and any MCP client)

Zero-dependency stdio MCP server (JSON-RPC 2.0, no `mcp` package needed):

```bash
python -m epitaph.mcp          # repo resolved from cwd, walking up
python -m epitaph.mcp --repo /path/to/repo
```

Two tools:

- `check_nogo(attempt?, files?)` — match an intended approach / target files against the ledger.
- `recent_tombstones(scope?, limit?)` — browse the ledger, newest first.

Claude Code config (`.mcp.json` in the project root, or `~/.claude.json`):

```json
{
  "mcpServers": {
    "epitaph": {
      "command": "python3",
      "args": ["-m", "epitaph.mcp"],
      "env": { "EPITAPH_REPO": "/absolute/path/to/your/repo" }
    }
  }
}
```

Or with the CLI: `claude mcp add epitaph -- python3 -m epitaph.mcp` (run from the repo root; the server resolves the repo from its working directory if `EPITAPH_REPO` is unset).

The server is a plain stdio JSON-RPC process — any MCP-capable client works. Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "epitaph": {
      "command": "python3",
      "args": ["-m", "epitaph.mcp"],
      "env": { "EPITAPH_REPO": "/absolute/path/to/your/repo" }
    }
  }
}
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.epitaph]
command = "python3"
args = ["-m", "epitaph.mcp"]
env = { EPITAPH_REPO = "/absolute/path/to/your/repo" }
```

Clients without MCP support can use the CLI instead — `epitaph check "..." --file path` produces the same match report through the same renderer.

Recommended one-line rule for `AGENTS.md` / `CLAUDE.md` — inject it with `epitaph snippets` (creates `AGENTS.md` if absent, appends to `CLAUDE.md` only when it already exists, idempotent):

```markdown
Before implementing an approach, call check_nogo with your planned approach and target
files. On a match, read `reason` and `retry_when`: either address `retry_when` or pick
a different path. Tombstones are records of past rejections, not bans.
```

## CLI reference

| Command | Purpose |
|---|---|
| `epitaph init` | Create `.tombstones/` in the target repo. `--detect` immediately mines existing history for reverts; `--snippets` also injects the rule below |
| `epitaph snippets` | Inject the recommended `check_nogo` rule into `AGENTS.md` (and `CLAUDE.md` if present) — idempotent |
| `epitaph add --attempt T --reason R [--from-commit SHA] [--scope P...] [--evidence R...] [--rejected-by WHO] [--retry-when W] [--date YYYY-MM-DD] [--confidence C] [--status S]` | Record a rejection (defaults to `candidate`). `--from-commit` prefills attempt/scope/evidence/date from a commit — a revert resolves its target — explicit flags win |
| `epitaph approve <id>` | Promote a tombstone to `approved` (one human line) |
| `epitaph review` | Walk candidates one by one: approve / skip / quit (interactive, human-only; closed stdin approves nothing) |
| `epitaph overturn <id> --reason R` | A retry succeeded — keep the refutation on record |
| `epitaph list [--status S] [--scope P]` | List tombstones, newest first |
| `epitaph show <id>` | Print one tombstone in full |
| `epitaph check [TEXT] [--file P]...` | Query by attempt text and/or files before retrying |
| `epitaph detect [--full]` | Scan git history for reverts, draft candidate tombstones (incremental via `.cursor`) |
| `epitaph giveup [--limit N]` | Scan agent session transcripts (Claude Code, Codex — discovered by format) for give-up transitions ("I'll try a different approach"), draft `rejected_by: agent-gaveup` candidates |
| `epitaph stale [--apply]` | Audit active tombstones whose scope anchors are all gone from the repo (path missing / symbol not found) — report by default, `--apply` flips to `status: stale` |
| `epitaph install-hook` | Install a post-commit hook that runs detect |

Global: `--repo PATH` (default: cwd, walking up), `--version`.

`check` uses deterministic normalized substring and token-overlap matching (0.5 containment for multi-token queries, exact token for single tokens) against `attempt + reason` — scope paths are full of common words and only match via `--file` — and also matches queried files against scope anchors in both directions. Every hit prints its confidence and *why* it matched; on a repo without a ledger it answers softly (`nothing recorded here yet`) instead of erroring, matching the MCP behavior agents see. Reports are capped at the top 20 matches ("… and N more") on both CLI and MCP, so a broad query can't dump the whole ledger into an agent's context.

## Workflow: detect -> draft -> approve -> query

| Stage | Owner | Notes |
|---|---|---|
| Detect | deterministic rules (no LLM) | git revert commits (`detect`) and session give-up transitions (`giveup`, transcripts read locally for the repo's cwd only — Claude Code and Codex formats today, adapters are drop-ins); never-merged PR closes and CI-failure branch abandonment are on the roadmap |
| Draft | optional LLM (v0.2) | v0.1 works fully without drafting |
| Approve | one human line | `epitaph approve ts-...`. **An agent may never judge on its own** — a tombstone is testimony, not a verdict |
| Query | MCP tools + CLI | `check_nogo` / `recent_tombstones` / `epitaph check` |

## Principles

- **Fully local by default** — tombstones live only in your repo and on your machine. Zero network calls at runtime.
- **Tombstones do not assert** — they record "this was rejected on this date for this reason"; they never claim "this is impossible". `retry_when` (the refutation condition) is expected on every record.
- **Candidates allowed** — unapproved `candidate` records are kept and shown with lower confidence; approval friction is one line.
- **Deterministic core (v0.1)** — detection and matching are plain rules; LLM drafting is a v0.2 option, never a dependency.
- **Human approval required** — only `epitaph approve` (a human action) reaches `approved` confidence.

## Differentiation

| Adjacent tool | How epitaph differs |
|---|---|
| **[deadends.dev](https://github.com/dbwls99706/deadends.dev)** | Global, **error-signature**-centric ("don't `sudo pip` when you hit CUDA OOM") — not repo-scoped, community-curated, manually reported. epitaph: **repo-scoped**, **attempt-level** (the approach, not the error message), auto-detected, human-approved, expiring. **Not competitors — two layers**: global error signatures live there, project-context rejections live here. v1.x plans canon-ID cross-links so the layers interoperate. |
| Mem0 / Letta / CLAUDE.md memory | Positive memory of successes and preferences. Negative memory needs its own data structure and its own query pattern (pre-flight lookup in the planning stage, not mid-chat recall). |
| ADR (Architecture Decision Record) | Manual documents of *human design decisions*. epitaph records rejected *attempts*, with automatic detection and machine querying built in. |
| `.cursorrules` negative rules | Context-free commands ("don't do X"). A tombstone is a record with evidence, conditions, and an expiry path. |
| yield-audit M9 (repeated inter-session knowledge cost) | Measurement (the size of the repeated tax) ↔ epitaph (removal of its single most expensive line item). They sell each other. |

## Privacy

Tombstone records can contain sensitive reasons. Everything stays local: no telemetry, no network calls, no cloud sync. The records are plain JSON files inside your repo — audit them with `git grep` before sharing, the same way you audit any other committed file.

## Limitations (v0.1)

- Revert detection is heuristic (subject/body patterns); it drafts candidates, humans confirm.
- Matching is lexical — synonyms and paraphrases may need the v0.2 embedding option.
- No stale audit yet (v0.3: symbol-graph check that the anchored code still exists).
- `detect` defaults `rejected_by` to `human-review` because git alone cannot tell who drove the revert; correct it during review.

## License

Apache-2.0. See [LICENSE](LICENSE).
