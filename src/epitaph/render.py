"""Shared match-report rendering.

The CLI (`epitaph check`) and the MCP server (`check_nogo`) must show
agents and humans the same format; both go through this module.
"""
from __future__ import annotations

_DISCLAIMER = (
    "tombstones are testimony, not verdicts — verify retry_when before "
    "treating a match as forbidden."
)

NO_MATCH = (
    "no matching tombstones — nothing recorded against this attempt. "
    "Proceed, and consider `epitaph add` if it gets rejected."
)

# Matches are score-sorted already; a query that hits the whole ledger must
# not dump it into the caller's context — epitaph sells token economy, so
# its own report is capped and points at `epitaph list` for the full view.
MATCH_LIMIT = 20


def format_matches(matches, limit=MATCH_LIMIT) -> str:
    """Render Match objects as the standard multi-line report (top `limit`)."""
    if not matches:
        return NO_MATCH
    shown = matches if limit is None else matches[:limit]
    hidden = len(matches) - len(shown)
    lines = ["%d tombstone(s) match:" % len(matches)]
    for match in shown:
        t = match.tombstone
        lines.append("")
        lines.append(
            "[%s/%s] %s  (rejected %s by %s)"
            % (t.confidence, t.status, t.id, t.rejected_at, t.rejected_by)
        )
        lines.append("  attempt: %s" % t.attempt)
        lines.append("  why matched: %s" % "; ".join(match.reasons))
        lines.append("  reason: %s" % (t.reason or "(none)"))
        lines.append("  retry_when: %s" % (t.retry_when or "(unspecified)"))
    if hidden > 0:
        lines.append("")
        lines.append(
            "... and %d more match(es) not shown — `epitaph list` shows the "
            "full ledger." % hidden
        )
    lines.append("")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)
