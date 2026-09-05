# Presets — curated pre-seed ledgers

A new repo starts with an empty ledger and nothing to check against. Presets
fix the cold start: mine a well-known project's reject history with
`epitaph detect`, **approve the records a human actually endorses**, and ship
the approved set here for anyone to import.

## Curation policy (the two rules)

1. **A preset ships approved records only.** `candidates/` holds raw detect
   output — auto-drafted, unreviewed, clearly named as candidates. Never
   import a candidates file into a repo you care about without reviewing.
2. **Approval is a human act.** Maintainers approve with `epitaph review` (or
   `epitaph approve <id>`) in a scratch clone; the exported bundle is the
   preset. Imported approved records keep their confidence — that is the
   point of curation — and gain an `origin` provenance key.

## Producing a preset

```bash
git clone --filter=blob:none --no-checkout https://github.com/org/proj /tmp/proj
epitaph --repo /tmp/proj detect          # drafts candidates from revert history
epitaph --repo /tmp/proj review          # human: approve the subset you endorse
epitaph --repo /tmp/proj export -o presets/<name>.json   # approved records only
```

Blobless clones are enough: detect needs the commit graph and tree names,
not file contents.

## Using a preset

```bash
epitaph import presets/react.json        # idempotent by id, adds `origin` key
epitaph list                             # review what landed
```

Records whose ids already exist are skipped, so re-importing after a preset
update only adds what is new.

## Layout

```
presets/
  README.md            this file
  <name>.json          approved, human-endorsed presets (shipped)
  candidates/
    <name>.json        raw detect output, candidates only — awaiting review
```
