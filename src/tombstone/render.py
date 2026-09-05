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


def format_matches(matches) -> str:
    """Render a list of Match objects as the standard multi-line report."""
    if not matches:
        return NO_MATCH
    lines = ["%d tombstone(s) match:" % len(matches)]
    for match in matches:
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
    lines.append("")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)
