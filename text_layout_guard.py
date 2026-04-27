"""
Template text layout guard — word count parity for fixed-layout designs.

Templated fliers break when copy is longer/shorter than the box. We treat
“words” as **visible** words: HTML tags and <br> are ignored for counting only;
the model is still expected to preserve tag structure when the original has it.
"""

from __future__ import annotations

import re
from html import unescape

_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)


def plain_for_word_count(s: str) -> str:
    """Strip tags / normalize breaks so word count matches what readers see."""
    if not s:
        return ""
    t = (
        s.replace("<br>", " ")
        .replace("<br/>", " ")
        .replace("<br />", " ")
        .replace("<BR>", " ")
    )
    t = _TAG_RE.sub(" ", t)
    t = unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def count_words_template(s: str) -> int:
    """Number of visible words (same rule we enforce everywhere)."""
    p = plain_for_word_count(s)
    if not p:
        return 0
    return len(p.split())


def word_counts_match(original: str, suggested: str) -> bool:
    return count_words_template(original) == count_words_template(suggested)


def filter_text_changes(
    changes: list[dict],
    *,
    originals_by_layer: dict[str, str],
    skip_sources: frozenset[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Drop text_changes whose suggested_text does not match the original's
    visible word count. originals_by_layer must hold the true template text
    per layer_id (from the client snapshot).

    skip_sources: e.g. frozenset({"brand_asset"}) — never drop those.
    """
    skip = skip_sources or frozenset()
    kept: list[dict] = []
    dropped: list[dict] = []
    for c in changes:
        lid = c.get("layer_id") or ""
        src = c.get("source") or ""
        if src in skip:
            cur = originals_by_layer.get(lid, c.get("current_text") or "")
            c["word_count"] = count_words_template(c.get("suggested_text") or "")
            c["required_word_count"] = count_words_template(cur)
            kept.append(c)
            continue
        cur = originals_by_layer.get(lid, c.get("current_text") or "")
        sug = c.get("suggested_text") or ""
        need = count_words_template(cur)
        got = count_words_template(sug)
        if need == got:
            c["word_count"] = got
            c["required_word_count"] = need
            kept.append(c)
        else:
            dropped.append({
                **c,
                "validation_error": (
                    f"visible word count {got} != required {need} "
                    "(dropped to protect layout)"
                ),
                "required_word_count": need,
            })
    return kept, dropped


def filter_suggestions(
    suggestions: list[dict],
    text_layers: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """Same as filter_text_changes but for marketing API shape."""
    kept, dropped = [], []
    for s in suggestions:
        lid = s.get("layer_id") or ""
        cur = text_layers.get(lid, s.get("current_text") or "")
        sug = s.get("suggested_text") or ""
        need = count_words_template(cur)
        got = count_words_template(sug)
        if need == got:
            s["word_count"] = got
            s["required_word_count"] = need
            kept.append(s)
        else:
            dropped.append({
                **s,
                "validation_error": (
                    f"visible word count {got} != required {need} "
                    "(dropped to protect layout)"
                ),
                "required_word_count": need,
            })
    return kept, dropped
