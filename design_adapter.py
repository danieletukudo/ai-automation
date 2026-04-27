"""
Design Adaptation Engine
------------------------
Two-stage LLM pipeline that adapts a fixed-layout flyer template to a new
partner brand without changing the layout:

  Stage 1 — Strategist:  reasons about the partner (industry, audience, tone,
                         emotional feel) and produces a transformation PLAN.

  Stage 2 — Executor:    applies that plan to the actual template layers,
                         returning concrete per-layer changes (text + colors)
                         with a short reason attached to each.

This is the "reason -> decide -> rewrite" split discussed in the design brief:
the LLM is never asked to guess — it follows an explicit plan it produced
in the previous step.
"""

from __future__ import annotations

import os
import json
import re
from typing import Any

from openai import OpenAI
from dotenv import load_dotenv

from text_layout_guard import count_words_template, filter_text_changes

load_dotenv()


# ─────────────────────────────────────────────────────────────
#   LLM client (Gemini via OpenAI-compatible endpoint)
# ─────────────────────────────────────────────────────────────
def _make_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("GEMINI_API_KEY"),
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )


def _strip_json_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw.split("```json", 1)[1].split("```", 1)[0]
    elif raw.startswith("```"):
        raw = raw.split("```", 1)[1].split("```", 1)[0]
    return raw.strip()


def _safe_json_loads(raw: str) -> dict:
    """Parse JSON, tolerating a leading preamble or trailing commentary."""
    raw = _strip_json_fence(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


# ─────────────────────────────────────────────────────────────
#   Data shapes
# ─────────────────────────────────────────────────────────────
# PartnerBrief (dict coming from the UI):
#   {
#     "name": "Illinois State Black Chamber of Commerce",
#     "partner_type": "chamber",          # chamber | mastermind | consulting | community | nonprofit | ...
#     "industry": "Business Advocacy, Black-owned Business Support",
#     "audience": "Black-owned businesses in Illinois",
#     "tone_hints": "professional, empowering, authoritative",
#     "brand_colors": {"primary": "#0B2545", "accent": "#0FB5A7"},
#     "goal": "Drive members to scan QR and hire via remoting.work",
#     "link_url": "https://remoting.work/illinois-chamber",
#     "notes": "Darker/more institutional than the input design."
#   }
#
# Everything except `name` is optional — the Strategist will infer sensibly.


class DesignAdapter:
    def __init__(self, model: str = "gemini-3-flash-preview"):
        self.client = _make_client()
        self.model = model

    # ─────────────────────────────────────────────────────
    #   STAGE 1 — STRATEGIST
    # ─────────────────────────────────────────────────────
    def plan(self, partner_brief: dict, template_context: dict) -> dict:
        """
        Produce a transformation plan. Returns a dict matching this shape:

        {
          "partner_type": "chamber",
          "tone_vector": {"authority": 8, "friendliness": 4, "urgency": 3, "modernity": 6},
          "tone_shift": "friendly/approachable  ->  professional + authoritative",
          "audience_shift": "general SMB owners -> formal chamber members",
          "messaging_shift": "affordability-first -> credibility + trust first",
          "color_strategy": "Swap bright blues for dark navy; replace green accents with teal.",
          "palette": {"primary": "#0B2545", "accent": "#0FB5A7", "background": "#F7F7F5"},
          "emotional_feel": "serious, institutional, trustworthy",
          "do_not_change": ["layout", "structure", "image positions"]
        }
        """
        prompt = f"""You are a senior brand strategist. You DO NOT rewrite copy
or pick final colors yet — you produce a PLAN that a separate executor will
follow. Your output must be a single JSON object, nothing else.

== NEW PARTNER BRIEF ==
{json.dumps(partner_brief, indent=2)}

== CURRENT TEMPLATE CONTEXT ==
{json.dumps(template_context, indent=2)}

== YOUR JOB ==
Reason about:
  1. What KIND of partner this is (chamber, mastermind, consulting, community
     org, nonprofit, advocacy group, etc.) and what that implies emotionally.
  2. How the TONE should shift relative to the current template
     (friendlier / more authoritative / more urgent / more aspirational / etc.).
  3. How the AUDIENCE differs and what that means for word choice.
  4. The MESSAGING angle that will resonate most
     (cost-saving vs. growth vs. pain-point vs. community empowerment).
  5. A COLOR strategy grounded in the partner's brand_colors if provided,
     otherwise inferred from partner_type. Pick a small palette:
     primary, accent, background.
  6. The emotional FEEL in one short phrase.

Core rule — even when the layout stays identical, the FEEL must change with
the partner:
  - community organization  -> lighter, approachable
  - chamber organization    -> stronger, formal, institutional
  - mastermind / network    -> modern, ambitious, growth-driven
  - consulting firm         -> direct, problem-aware, urgent

Return EXACTLY this JSON shape:
{{
  "partner_type": "<one word: chamber | mastermind | consulting | community | nonprofit | advocacy | other>",
  "tone_vector": {{
    "authority": <0-10>,
    "friendliness": <0-10>,
    "urgency": <0-10>,
    "modernity": <0-10>
  }},
  "tone_shift": "<short phrase: X -> Y>",
  "audience_shift": "<short phrase>",
  "messaging_shift": "<short phrase>",
  "color_strategy": "<1-2 sentences explaining the color direction>",
  "palette": {{
    "primary":    "#RRGGBB",
    "accent":     "#RRGGBB",
    "background": "#RRGGBB"
  }},
  "emotional_feel": "<3-6 word phrase>",
  "do_not_change": ["layout", "structure", "image positions"]
}}

Return ONLY the JSON. No markdown fences, no commentary.
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return _safe_json_loads(response.choices[0].message.content)

    # ─────────────────────────────────────────────────────
    #   STAGE 2 — EXECUTOR
    # ─────────────────────────────────────────────────────
    def execute(
        self,
        strategy: dict,
        text_layers: dict[str, str],
        shape_layers: dict[str, str],
        image_layers: dict[str, str] | None = None,
        partner_brief: dict | None = None,
    ) -> dict:
        """
        Apply the strategy to the actual layers. Returns:

        {
          "text_changes":  [ {layer_id, current_text, suggested_text, word_count, reason}, ... ],
          "shape_changes": [ {layer_id, current_fill, suggested_fill, reason}, ... ],
          "summary": "<1-sentence human-readable summary of what changed and why>"
        }

        Hard constraints enforced in the prompt:
          - text word count MUST match the original
          - layer IDs are preserved exactly
          - no new layers invented
          - colors returned as rgb(r,g,b) to match Templated's format
        """
        image_layers = image_layers or {}
        partner_brief = partner_brief or {}

        text_block = "\n".join(
            f'  - "{lid}" ({count_words_template(t)} visible words, {len(t)} chars): "{t}"'
            for lid, t in text_layers.items()
        ) or "  (none)"

        shape_block = "\n".join(
            f'  - "{lid}": current fill = {fill}'
            for lid, fill in shape_layers.items()
        ) or "  (none)"

        image_block = "\n".join(
            f'  - "{lid}": {url}' for lid, url in image_layers.items()
        ) or "  (none)"

        prompt = f"""You are the EXECUTOR. A strategist has already decided how
this template should change. You DO NOT re-plan — you apply the plan.

== STRATEGY (authoritative — follow it) ==
{json.dumps(strategy, indent=2)}

== PARTNER BRIEF (for copy details: name, link, goal) ==
{json.dumps(partner_brief, indent=2)}

== TEMPLATE LAYERS ==

Text layers (editable):
{text_block}

Shape / color layers (editable):
{shape_block}

Image layers (context only — do not emit changes for these):
{image_block}

== YOUR TASK ==

1. For TEXT layers, rewrite only the ones that need to change to reflect the
   strategy (tone_shift, messaging_shift, audience_shift) and to mention the
   new partner where appropriate. SKIP layers that are already fine or are
   purely decorative (single numbers, dates, symbols, etc.).

   HARD RULE: suggested_text must have EXACTLY the same number of VISIBLE words
   as current_text. **Visible words** = strip HTML tags and <br>, then count
   whitespace-separated words (the counts shown per layer use this rule).
   A mismatch overflows the text box and ruins the design — count twice.

2. For SHAPE layers, decide which fills should change to express the
   strategy's palette (primary, accent, background). Map thoughtfully:
   the largest / most prominent shape usually takes the primary or
   background; smaller accents take the accent color. Return colors as
   `rgb(r,g,b)` strings (the template renderer expects that format).
   SKIP shapes that should stay (e.g. pure white/black structural fills)
   unless the strategy says otherwise.

3. Write ONE short summary sentence explaining the overall shift.

Return EXACTLY this JSON:
{{
  "text_changes": [
    {{
      "layer_id": "<exact id from above>",
      "current_text": "<exact original>",
      "suggested_text": "<new text, SAME visible word count>",
      "word_count": <int, must equal visible word count of current_text>,
      "reason": "<one line tying it to the strategy>"
    }}
  ],
  "shape_changes": [
    {{
      "layer_id": "<exact id from above>",
      "current_fill": "<exact original>",
      "suggested_fill": "rgb(r,g,b)",
      "reason": "<one line tying it to the strategy>"
    }}
  ],
  "summary": "<1 sentence>"
}}

Return ONLY the JSON. No markdown fences, no commentary.
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return _safe_json_loads(response.choices[0].message.content)

    # ─────────────────────────────────────────────────────
    #   EXECUTOR variant: obey explicit find/replace rules
    # ─────────────────────────────────────────────────────
    def execute_with_rules(
        self,
        strategy: dict,
        text_layers: dict[str, str],
        shape_layers: dict[str, str],
        replace_rules: list[dict],
        image_layers: dict[str, str] | None = None,
        partner_brief: dict | None = None,
    ) -> dict:
        """
        Same contract as execute(), but the LLM must honor a list of
        mandatory substitutions the user supplied, e.g.:

            [{"find": "NutriHealth", "replace": "CareAI"}, ...]

        The model is told these are HARD constraints: wherever it would have
        written `find`, it must write `replace` instead.
        """
        image_layers = image_layers or {}
        partner_brief = partner_brief or {}
        replace_rules = [r for r in (replace_rules or []) if r.get("find")]

        text_block = "\n".join(
            f'  - "{lid}" ({count_words_template(t)} visible words, {len(t)} chars): "{t}"'
            for lid, t in text_layers.items()
        ) or "  (none)"

        shape_block = "\n".join(
            f'  - "{lid}": current fill = {fill}'
            for lid, fill in shape_layers.items()
        ) or "  (none)"

        image_block = "\n".join(
            f'  - "{lid}": {url}' for lid, url in image_layers.items()
        ) or "  (none)"

        rules_block = "\n".join(
            f'  - Replace "{r.get("find", "")}" -> "{r.get("replace", "")}"'
            for r in replace_rules
        ) or "  (none)"

        prompt = f"""You are the EXECUTOR. A strategist has already decided how
this template should change. You DO NOT re-plan — you apply the plan.

== STRATEGY (authoritative — follow it) ==
{json.dumps(strategy, indent=2)}

== PARTNER BRIEF (for copy details: name, link, goal) ==
{json.dumps(partner_brief, indent=2)}

== MANDATORY SUBSTITUTION RULES ==
These rules are HARD constraints from the user. Wherever you would have
written the "find" term (or any close variant of it), you MUST write the
"replace" term instead. This overrides your own word choice.
{rules_block}

== TEMPLATE LAYERS ==

Text layers (editable):
{text_block}

Shape / color layers (editable):
{shape_block}

Image layers (context only — do not emit changes for these):
{image_block}

== YOUR TASK ==

1. Rewrite the TEXT layers to reflect the strategy AND apply every
   substitution rule above. Skip layers that are already fine or purely
   decorative (numbers, dates, symbols).

   HARD RULES:
     - suggested_text must have the SAME VISIBLE word count as current_text
       (strip HTML / <br> for counting only — same rule as the per-layer counts).
     - Every occurrence of a rule's "find" term (case-insensitive) must be
       replaced with its "replace" term.

2. For SHAPE layers, pick fills expressing the strategy's palette. Return
   colors as `rgb(r,g,b)`. Skip shapes that should stay as-is.

3. Write ONE short summary sentence.

Return EXACTLY this JSON:
{{
  "text_changes": [
    {{
      "layer_id": "<id>",
      "current_text": "<exact original>",
      "suggested_text": "<new text, SAME visible word count, rules applied>",
      "word_count": <int, visible word count>,
      "reason": "<one line>"
    }}
  ],
  "shape_changes": [
    {{
      "layer_id": "<id>",
      "current_fill": "<exact original>",
      "suggested_fill": "rgb(r,g,b)",
      "reason": "<one line>"
    }}
  ],
  "summary": "<1 sentence>"
}}

Return ONLY the JSON. No markdown fences, no commentary.
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return _safe_json_loads(response.choices[0].message.content)

    def finalize_executor_text_changes(
        self,
        text_changes: list[dict],
        originals: dict[str, str],
        *,
        strategy: dict | None,
        partner_brief: dict,
        shape_layers: dict[str, str],
        image_layers: dict[str, str],
    ) -> tuple[list[dict], dict[str, Any]]:
        """
        Enforce visible word-count parity with the template. Drops bad rows;
        optionally one LLM repair pass (LAYOUT_WORD_COUNT_REPAIR=1).
        """
        kept, dropped = filter_text_changes(
            text_changes,
            originals_by_layer=originals,
            skip_sources=frozenset(),
        )
        meta: dict[str, Any] = {
            "dropped_initial": dropped,
            "repair_attempted": False,
            "dropped_after_repair": [],
        }
        if not dropped:
            return kept, meta
        if os.getenv("LAYOUT_WORD_COUNT_REPAIR", "1") in ("0", "false", "False"):
            return kept, meta
        meta["repair_attempted"] = True
        fixed_rows = self._repair_executor_word_counts(
            dropped, originals, strategy, partner_brief, shape_layers, image_layers
        )
        k2, d2 = filter_text_changes(
            fixed_rows,
            originals_by_layer=originals,
            skip_sources=frozenset(),
        )
        kept = kept + k2
        meta["dropped_after_repair"] = d2
        return kept, meta

    def _repair_executor_word_counts(
        self,
        dropped: list[dict],
        originals: dict[str, str],
        strategy: dict | None,
        partner_brief: dict,
        shape_layers: dict[str, str],
        image_layers: dict[str, str],
    ) -> list[dict]:
        """Single follow-up LLM call to fix word-count mismatches."""
        payload = []
        for c in dropped:
            lid = c.get("layer_id", "")
            cur = originals.get(lid, c.get("current_text", ""))
            payload.append(
                {
                    "layer_id": lid,
                    "current_text": cur,
                    "failed_suggestion": c.get("suggested_text", ""),
                    "required_visible_word_count": count_words_template(cur),
                }
            )
        prompt = f"""You fix graphic template text for FIXED-SIZE text boxes.

Strategy (follow when rewriting):
{json.dumps(strategy or {{}}, indent=2)[:3500]}

Partner brief:
{json.dumps(partner_brief or {{}}, indent=2)[:2500]}

Each item failed because suggested_text had the wrong number of VISIBLE words.
VISIBLE = strip HTML tags and <br>, then count whitespace-separated words.
Preserve HTML tag structure from current_text when present.

Items:
{json.dumps(payload, indent=2)}

Return ONLY JSON:
{{"fixed":[{{"layer_id":"<id>","suggested_text":"<fixed>"}}]}}
No markdown fences."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _strip_json_fence(response.choices[0].message.content)
        try:
            data = _safe_json_loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
        out: list[dict] = []
        for item in data.get("fixed", []) or []:
            lid = item.get("layer_id", "")
            if not lid:
                continue
            sug = item.get("suggested_text", "")
            cur = originals.get(lid, "")
            out.append(
                {
                    "layer_id": lid,
                    "current_text": cur,
                    "suggested_text": sug,
                    "word_count": count_words_template(sug),
                    "reason": "Layout-safe repair (visible word count matched).",
                }
            )
        return out

    # ─────────────────────────────────────────────────────
    #   Convenience wrapper — full AI adaptation (legacy)
    # ─────────────────────────────────────────────────────
    def adapt(
        self,
        partner_brief: dict,
        text_layers: dict[str, str],
        shape_layers: dict[str, str],
        image_layers: dict[str, str] | None = None,
    ) -> dict:
        """
        Run the full AI pipeline: plan -> execute.

        Returns:
          {
            "strategy":      { ... from plan() ... },
            "text_changes":  [ ... ],
            "shape_changes": [ ... ],
            "summary":       "...",
            "layout_validation": { ... }   # word-count drops / repair meta
          }
        """
        template_context = {
            "text_layers":  text_layers,
            "shape_layers": shape_layers,
            "image_layers": image_layers or {},
        }

        strategy = self.plan(partner_brief, template_context)
        execution = self.execute(
            strategy=strategy,
            text_layers=text_layers,
            shape_layers=shape_layers,
            image_layers=image_layers,
            partner_brief=partner_brief,
        )
        raw_tc = execution.get("text_changes", []) or []
        final_tc, lv = self.finalize_executor_text_changes(
            raw_tc,
            text_layers,
            strategy=strategy,
            partner_brief=partner_brief,
            shape_layers=shape_layers,
            image_layers=image_layers or {},
        )

        return {
            "strategy":      strategy,
            "text_changes":  final_tc,
            "shape_changes": execution.get("shape_changes", []),
            "summary":       execution.get("summary", ""),
            "layout_validation": lv,
        }


# ─────────────────────────────────────────────────────────────
#   MODE DISPATCHER
# ─────────────────────────────────────────────────────────────
# Modes (all share the same output shape so the UI never branches):
#   logo_qr_website_colors - NO AI: logo + QR + website text + mapped shape colors only
#   ai_rewrite             - full strategist + executor (copy + colors)
#   find_replace           - literal find/replace, no LLM
#   ai_with_rules          - AI rewrite with mandatory find/replace rules
#   image_only             - image swaps only

SUPPORTED_MODES = {
    "logo_qr_website_colors",
    "ai_rewrite",
    "find_replace",
    "ai_with_rules",
    "image_only",
}

# Default mapping for logo_qr_website_colors: which shape layer IDs receive the
# user's Primary / Accent hex colors. Edit per template. Empty "primary" list
# is OK if the design has no obvious primary-filled shapes.
DEFAULT_SHAPE_COLOR_ROLES: dict[str, list[str]] = {
    "primary": ["avatar-bg-1", "avatar-bg-2"],
    "accent": ["gold-accent-circle"],
}


def _hex_to_rgb_fill(hex_color: str | None) -> str | None:
    """Convert #RRGGBB to rgb(r,g,b) for Templated. Returns None if invalid."""
    if not hex_color or not isinstance(hex_color, str):
        return None
    h = hex_color.strip().lstrip("#")
    if len(h) != 6 or not all(c in "0123456789abcdefABCDEF" for c in h):
        return None
    return f"rgb({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)})"


def _shape_changes_from_brand_hexes(
    shape_layers: dict[str, str],
    primary_hex: str | None,
    accent_hex: str | None,
    roles: dict[str, list[str]] | None,
) -> list[dict]:
    """Build shape_changes without AI from user hex colors + role → layer map."""
    roles = roles or DEFAULT_SHAPE_COLOR_ROLES
    out: list[dict] = []
    primary_rgb = _hex_to_rgb_fill(primary_hex)
    accent_rgb = _hex_to_rgb_fill(accent_hex)

    for lid in roles.get("primary") or []:
        if lid not in shape_layers or not primary_rgb:
            continue
        cur = shape_layers[lid]
        if cur == primary_rgb:
            continue
        out.append({
            "layer_id": lid,
            "current_fill": cur,
            "suggested_fill": primary_rgb,
            "reason": "Brand color: primary (no-AI mode).",
            "source": "brand_color",
        })

    for lid in roles.get("accent") or []:
        if lid not in shape_layers or not accent_rgb:
            continue
        cur = shape_layers[lid]
        if cur == accent_rgb:
            continue
        out.append({
            "layer_id": lid,
            "current_fill": cur,
            "suggested_fill": accent_rgb,
            "reason": "Brand color: accent (no-AI mode).",
            "source": "brand_color",
        })
    return out


def _apply_find_replace(text: str, rules: list[dict]) -> str:
    """Case-insensitive literal substitution preserving the original casing
    style of the replacement term. Returns the rewritten text."""
    out = text
    for rule in rules or []:
        find = (rule.get("find") or "").strip()
        repl = rule.get("replace") or ""
        if not find:
            continue
        out = re.sub(re.escape(find), repl, out, flags=re.IGNORECASE)
    return out


def run_mode(
    mode: str,
    partner_brief: dict,
    text_layers: dict[str, str],
    shape_layers: dict[str, str],
    image_layers: dict[str, str] | None = None,
    replace_rules: list[dict] | None = None,
    image_overrides: dict[str, str] | None = None,
    brand_assets: dict | None = None,
    brand_colors: dict[str, str | None] | None = None,
    shape_color_roles: dict[str, list[str]] | None = None,
) -> dict:
    """
    Single entry point the server calls. Returns a dict with this shape
    regardless of mode:

      {
        "mode": "<mode>",
        "strategy":      { ... }  or None,
        "text_changes":  [ ... ],
        "shape_changes": [ ... ],
        "image_changes": [ ... ],   # from image_overrides + brand assets
        "summary":       "...",
        "layout_validation": { ... }   # optional: word-count guard / repair
      }

    Parameters
    ----------
    mode            one of SUPPORTED_MODES
    partner_brief   same shape as DesignAdapter.adapt(); only `name` required
                    for AI modes
    text_layers     {layer_id: current_text}
    shape_layers    {layer_id: current_fill_rgb}
    image_layers    {layer_id: current_image_url}
    replace_rules   [{"find": "...", "replace": "..."}]  (modes 3a/3b)
    image_overrides {layer_id: new_image_url}           (any mode)
    brand_assets    {"logo": {"layer_id", "url"},
                     "qr":   {"layer_id", "url"},
                     "website": {"layer_id", "text"}}
    brand_colors    {"primary": "#RRGGBB", "accent": "#RRGGBB"} — used by
                    logo_qr_website_colors only (no AI).
    shape_color_roles  optional override of DEFAULT_SHAPE_COLOR_ROLES
                       (usually passed from server config).
    """
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unknown mode: {mode!r}. Expected one of {sorted(SUPPORTED_MODES)}.")

    image_layers     = image_layers or {}
    replace_rules    = replace_rules or []
    image_overrides  = dict(image_overrides or {})
    brand_assets     = brand_assets or {}
    brand_colors     = brand_colors or {}

    # Brand assets fold into text/image change lists so the UI can show them
    # in the same review cards as the AI suggestions.
    text_changes: list[dict] = []
    shape_changes: list[dict] = []
    image_changes: list[dict] = []
    strategy: dict | None = None
    summary = ""
    layout_validation: dict[str, Any] = {}

    logo    = brand_assets.get("logo")    or {}
    qr      = brand_assets.get("qr")      or {}
    website = brand_assets.get("website") or {}

    if logo.get("layer_id") and logo.get("url"):
        image_overrides.setdefault(logo["layer_id"], logo["url"])
    if qr.get("layer_id") and qr.get("url"):
        image_overrides.setdefault(qr["layer_id"], qr["url"])

    # Website link is a text layer — becomes a forced text change.
    if website.get("layer_id") and website.get("text"):
        lid = website["layer_id"]
        current = text_layers.get(lid, "")
        if current != website["text"]:
            text_changes.append({
                "layer_id": lid,
                "current_text": current,
                "suggested_text": website["text"],
                "word_count": count_words_template(website["text"]),
                "reason": "Brand asset: website link override.",
                "source": "brand_asset",
            })

    # Build image_changes from overrides (brand assets + user-specified).
    for lid, url in image_overrides.items():
        current = image_layers.get(lid, "")
        if current == url:
            continue
        image_changes.append({
            "layer_id": lid,
            "current_url": current,
            "suggested_url": url,
            "reason": "User-specified image override.",
            "source": "user" if lid not in (logo.get("layer_id"), qr.get("layer_id")) else "brand_asset",
        })

    # Remove website layer from the text_layers passed to any LLM so it
    # doesn't try to rewrite the URL.
    text_layers_for_llm = {
        lid: t for lid, t in text_layers.items()
        if lid != website.get("layer_id")
    }

    if mode == "image_only":
        # No text, no color. Only what we built above.
        summary = f"Applied {len(image_changes)} image / brand-asset override(s)."

    elif mode == "logo_qr_website_colors":
        # No LLM: swap logo/QR/website + apply user hexes to mapped shape layers.
        shape_changes.extend(
            _shape_changes_from_brand_hexes(
                shape_layers,
                brand_colors.get("primary"),
                brand_colors.get("accent"),
                shape_color_roles,
            )
        )
        n_shp = len([c for c in shape_changes if c.get("source") == "brand_color"])
        summary = (
            f"No AI — updated logo, QR, website link, and {n_shp} color layer(s). "
            f"All other flier text is unchanged."
        )

    elif mode == "find_replace":
        # Literal substitution, no LLM.
        for lid, current in text_layers_for_llm.items():
            new = _apply_find_replace(current, replace_rules)
            if new != current:
                text_changes.append({
                    "layer_id": lid,
                    "current_text": current,
                    "suggested_text": new,
                    "word_count": count_words_template(new),
                    "reason": "Literal find & replace rule applied.",
                    "source": "find_replace",
                })

    elif mode == "ai_rewrite":
        adapter = DesignAdapter()
        ai = adapter.adapt(
            partner_brief=partner_brief,
            text_layers=text_layers_for_llm,
            shape_layers=shape_layers,
            image_layers=image_layers,
        )
        strategy = ai.get("strategy")
        text_changes.extend(ai.get("text_changes", []))
        shape_changes.extend(ai.get("shape_changes", []))
        summary = ai.get("summary", "")
        layout_validation.update(ai.get("layout_validation") or {})

    elif mode == "ai_with_rules":
        adapter = DesignAdapter()
        strategy = adapter.plan(
            partner_brief,
            {
                "text_layers":  text_layers_for_llm,
                "shape_layers": shape_layers,
                "image_layers": image_layers,
            },
        )
        exe = adapter.execute_with_rules(
            strategy=strategy,
            text_layers=text_layers_for_llm,
            shape_layers=shape_layers,
            image_layers=image_layers,
            partner_brief=partner_brief,
            replace_rules=replace_rules,
        )
        raw_exe_tc = exe.get("text_changes", []) or []
        final_exe_tc, lv_exe = adapter.finalize_executor_text_changes(
            raw_exe_tc,
            text_layers_for_llm,
            strategy=strategy,
            partner_brief=partner_brief,
            shape_layers=shape_layers,
            image_layers=image_layers or {},
        )
        text_changes.extend(final_exe_tc)
        shape_changes.extend(exe.get("shape_changes", []))
        summary = exe.get("summary", "")
        layout_validation["executor_with_rules"] = lv_exe

    # ─── Final layout guard (all modes with text_changes) ───
    # Drops any row whose visible word count ≠ template snapshot, except
    # brand_asset rows (e.g. website URL may change length on purpose).
    skip_brand = frozenset({"brand_asset"})
    kept_final, dropped_final = filter_text_changes(
        text_changes,
        originals_by_layer=text_layers,
        skip_sources=skip_brand,
    )
    text_changes = kept_final
    if dropped_final:
        layout_validation.setdefault("final_pass_dropped", []).extend(dropped_final)

    if mode == "find_replace":
        summary = (
            f"Find & replace: {len(text_changes)} text layer(s) passed layout "
            f"checks (visible word count matches template)."
        )

    return {
        "mode":          mode,
        "strategy":      strategy,
        "text_changes":  text_changes,
        "shape_changes": shape_changes,
        "image_changes": image_changes,
        "summary":       summary,
        "layout_validation": layout_validation,
    }
