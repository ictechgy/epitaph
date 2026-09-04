"""Deterministic matching for `check` / `check_nogo`.

Normalization + substring / token-overlap only for v0.1 (embedding
similarity is a v0.2 option per the roadmap). All pure functions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Letters/digits plus Hangul syllables and jamo so Korean attempts and
# reasons tokenize sanibly.
_TOKEN_RE = re.compile(r"[a-z0-9\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]+")

SUBSTRING_SCORE = 1.0
FILE_SCORE = 0.8
DEFAULT_MIN_OVERLAP = 0.5

# Queries are natural-language sentences ("maybe a redis lock would fix
# this"); tombstone text is dense. Stopwords are dropped from the query
# side only so overlap measures content words, not sentence glue.
_STOPWORDS = frozenset(
    """a an the this that these those it its is are was were be been being
    to of in on for with and or not no would could should can may might will
    shall must do does did i we you they he she maybe perhaps please some any
    my our your me us him her them what which who how when where why""".split()
)


def tokenize(text: str):
    return _TOKEN_RE.findall((text or "").lower())


def content_tokens(text: str):
    """Query tokens minus stopwords, in order, deduplicated."""
    seen = set()
    out = []
    for token in tokenize(text):
        if token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def normalized(text: str) -> str:
    return " ".join(tokenize(text))


@dataclass
class Match:
    tombstone: object
    score: float
    reasons: list = field(default_factory=list)


def haystack(tomb) -> str:
    """Normalized attempt+reason text — the semantic identity of a tombstone.

    Scope entries deliberately don't contribute tokens here: paths are full
    of common words (session, lock, manager), and counting them as text
    makes any query sharing one path word surface unrelated tombstones.
    Scope anchors match through `files` instead.
    """
    return normalized(" \n ".join([tomb.attempt, tomb.reason]))


def _file_reasons(tomb, files):
    reasons = []
    for file in files or []:
        norm_file = normalized(file)
        if not norm_file:
            continue
        for scope in tomb.scope:
            norm_scope = normalized(scope)
            if norm_scope and (norm_scope in norm_file or norm_file in norm_scope):
                reasons.append("file %r matches scope %r" % (file, scope))
    return reasons


def match_one(tomb, query=None, files=None, min_overlap=DEFAULT_MIN_OVERLAP):
    """Return a Match or None.

    A single query token must appear exactly; longer queries match on
    normalized substring or containment >= min_overlap of query tokens.
    Scope anchors match files in both directions (query file inside a
    scope entry or a scope entry inside the query file).
    """
    reasons = []
    score = 0.0
    hay = haystack(tomb)

    if query:
        query_tokens = content_tokens(query)
        norm_query = " ".join(query_tokens)
        if norm_query:
            # Phrase-substring needs >=2 tokens: a single token like "red"
            # would otherwise fuzzy-hit inside "redis" (no prefix fuzz).
            if len(query_tokens) >= 2 and norm_query in hay:
                score = SUBSTRING_SCORE
                reasons.append("text substring match: %r" % norm_query)
            else:
                hits = len(set(query_tokens) & set(hay.split()))
                overlap = hits / len(query_tokens)
                needed = 1.0 if len(query_tokens) == 1 else min_overlap
                if overlap >= needed:
                    score = overlap
                    if len(query_tokens) >= 2 and hits == len(query_tokens):
                        reasons.append(
                            "normalized substring match (case/punctuation-insensitive): "
                            "all %d query tokens present" % hits
                        )
                    else:
                        reasons.append(
                            "token overlap %d%% (%d/%d query tokens)"
                            % (round(overlap * 100), hits, len(query_tokens))
                        )

    file_hits = _file_reasons(tomb, files)
    if file_hits:
        score = max(score, FILE_SCORE)
        reasons.extend(file_hits)

    if not reasons:
        return None
    return Match(tombstone=tomb, score=score, reasons=reasons)


def match_tombstones(query=None, files=None, tombstones=(), min_overlap=DEFAULT_MIN_OVERLAP):
    """All matches for a query/files pair, best score first."""
    matches = []
    for tomb in tombstones:
        match = match_one(tomb, query=query, files=files, min_overlap=min_overlap)
        if match is not None:
            matches.append(match)
    matches.sort(key=lambda m: (-m.score, m.tombstone.id))
    return matches
