"""
Small template JSON API. Direct routes edit layers; AI routes only rewrite **text**
layers (skipping link/URL layers), via `marketing_text_finder.MarketingLLM`
(`analyze_layers_and_suggest`: word-count-safe suggestions).

Routes
------
  POST /api/apply                  — generic: list of layer updates
  POST /api/1/logo-qr-website-colors — logo URL + QR URL + website text + optional shape fills
  POST /api/2/ai-rewrite           — partner_brief + template → MarketingLLM suggests copy per layer
  POST /api/3a/find-replace        — literal find/replace (skips URL segments + optional layers)
  POST /api/3b/ai-with-rules       — same as /api/2 plus mandatory find/replace rules in the brief
  POST /api/4/swap-images          — set image_url for many layers at once

AI needs GEMINI_API_KEY (optional GEMINI_MODEL, default gemini-3-flash-preview).

Run: python server.py  →  http://localhost:5001
"""

import json
import os
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

from marketing_text_finder import MarketingLLM

load_dotenv()

app = Flask(__name__)
CORS(app)

# Text layers the AI never touches (link / URL line). Override per request with skip_layer_ids.
_DEFAULT_AI_SKIP_TEXT_IDS = frozenset({"website-url"})


# ─── load default template ─────────────────────────────────
def _default_pages() -> list:
    path = os.path.join(os.path.dirname(__file__), "sample_pages.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


SAMPLE_PAGES = _default_pages()

# When you send primary_color / accent_color without primary_layer_ids /
# accent_layer_ids, we paint these shape layers in sample_pages.json (teal bars
# + orange CTA). Override with your own lists if your template uses other ids.
DEFAULT_PRIMARY_SHAPE_IDS = [
    "bottom-teal-bar",
    "bullet-bar-1",
    "bullet-bar-2",
    "bullet-bar-3",
]
DEFAULT_ACCENT_SHAPE_IDS = ["orange-button"]


def _deep_copy_pages(pages: list) -> list:
    return json.loads(json.dumps(pages or []))


def _pages_from_body(data: dict) -> list:
    p = data.get("pages")
    if p is None:
        return _deep_copy_pages(SAMPLE_PAGES)
    if not isinstance(p, list):
        raise ValueError("'pages' must be a JSON array (or omit it to use the sample template)")
    return _deep_copy_pages(p)


def _hex_to_rgb(fill: str) -> str:
    """#RRGGBB → rgb(r,g,b). Anything else returned as-is."""
    if not isinstance(fill, str) or not fill.startswith("#"):
        return fill
    h = fill.strip().lstrip("#")
    if len(h) != 6 or not all(c in "0123456789abcdefABCDEF" for c in h):
        return fill
    return f"rgb({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)})"


# Find/replace: never rewrite inside http(s) URLs; optionally skip whole layers (website link).
_SKIP_FIND_REPLACE_LAYER_IDS = frozenset({"website-url"})
# Split on URLs so we only run replacement outside URL segments.
_URL_SEGMENT_RE = re.compile(r"(https?://[^\s<>'\"\]\)]+)", re.IGNORECASE)


def _replace_text_skip_urls(old: str, find: str, replace: str) -> str:
    """Apply case-insensitive literal replace to `old`, but never inside http(s) URL segments."""
    if not find:
        return old
    parts = _URL_SEGMENT_RE.split(old)
    out: list[str] = []
    for part in parts:
        if part.lower().startswith(("http://", "https://")):
            out.append(part)
        else:
            out.append(re.sub(re.escape(find), replace, part, flags=re.IGNORECASE))
    return "".join(out)


def _dedupe_ids(ids: list[str]) -> list[str]:
    """Preserve order, drop duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _text_is_only_a_url(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    return bool(re.fullmatch(r"https?://[^\s]+", t, re.IGNORECASE))


def _collect_text_layers_for_ai(
    pages: list,
    skip_layer_ids: frozenset[str],
) -> list[dict]:
    """Every text layer except skipped ids and except layers whose entire text is only a URL."""
    rows: list[dict] = []
    for page in pages:
        for lid, layer in (page.get("layers") or {}).items():
            if not isinstance(layer, dict):
                continue
            if (layer.get("type") or "").lower() != "text":
                continue
            if lid in skip_layer_ids:
                continue
            text = layer.get("text")
            if text is None:
                continue
            if not isinstance(text, str):
                text = str(text)
            if _text_is_only_a_url(text):
                continue
            rows.append({"layer_id": lid, "text": text})
    return rows


def _marketing_llm_rewrite(
    partner_brief: dict,
    layers_payload: list[dict],
    rules: list[dict] | None,
) -> tuple[dict[str, str], dict]:
    """
    Uses MarketingLLM.analyze_layers_and_suggest with empty image/shape context (text-only).
    Returns (layer_id -> suggested_text, metadata including layout_validation).
    """
    if not os.getenv("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY is not set")
    text_layers = {row["layer_id"]: row["text"] for row in layers_payload}
    company_desc = json.dumps(partner_brief, indent=2)
    if rules:
        company_desc += (
            "\n\nMandatory post-processing: after choosing suggested_text for each layer, "
            "apply these find/replace rules in order (case-insensitive match for find):\n"
            + json.dumps(rules, indent=2)
        )

    model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    llm = MarketingLLM(model=model)
    result = llm.analyze_layers_and_suggest(text_layers, {}, {}, company_desc)

    allowed_ids = {row["layer_id"] for row in layers_payload}
    updates: dict[str, str] = {}
    for s in result.get("suggestions") or []:
        lid = s.get("layer_id")
        st = s.get("suggested_text")
        if isinstance(lid, str) and isinstance(st, str) and lid in allowed_ids:
            updates[lid] = st

    meta = {
        "layout_validation": result.get("layout_validation"),
        "model": model,
    }
    return updates, meta


def _apply_text_updates(pages: list, updates: dict[str, str]) -> list[str]:
    changed: list[str] = []
    for lid, new_text in updates.items():
        if _set_layer(pages, lid, {"text": new_text}):
            changed.append(lid)
    return changed


def _set_layer(pages: list, layer_id: str, fields: dict) -> bool:
    """
    Find layer_id inside pages[*].layers and update with fields.
    Returns True if the layer was found.
    """
    for page in pages:
        layers = page.get("layers") or {}
        if layer_id not in layers or not isinstance(layers[layer_id], dict):
            continue
        layer = layers[layer_id]
        for key, val in fields.items():
            if key == "fill" and isinstance(val, str):
                layer[key] = _hex_to_rgb(val)
            else:
                layer[key] = val
        return True
    return False


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Body must be a JSON object")
    return data


def _ok(pages: list, updated_layer_ids: list):
    return jsonify({"pages": pages, "updated_layer_ids": updated_layer_ids})


def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


# ─── POST /api/apply ───────────────────────────────────────
# Body:
#   pages   optional — full template array; omit = use sample_pages.json
#   changes required — list of objects, each must have "layer_id" plus any
#                      fields to set on that layer (text, image_url, fill, …)
#
# Example:
#   { "changes": [
#       { "layer_id": "logo-main", "image_url": "https://example.com/logo.png" },
#       { "layer_id": "website-url", "text": "https://partner.com/invite" },
#       { "layer_id": "bottom-teal-bar", "fill": "#0b2545" }
#     ] }
@app.route("/api/apply", methods=["POST"])
def api_apply():
    try:
        data = _json_body()
        changes = data.get("changes")
        if not isinstance(changes, list) or not changes:
            return _err('Send "changes": [ { "layer_id": "...", "field": value }, ... ]')
        pages = _pages_from_body(data)
        updated: list[str] = []
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            lid = ch.get("layer_id")
            if not lid:
                return _err('Each change needs a "layer_id"')
            fields = {k: v for k, v in ch.items() if k != "layer_id"}
            if not fields:
                return _err(f'Change for "{lid}" has no fields to set (add e.g. text, image_url, fill)')
            if _set_layer(pages, lid, fields):
                updated.append(lid)
            else:
                return _err(f'Layer id not found in template: "{lid}"', 404)
        return _ok(pages, updated)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", 500)


# ─── POST /api/1/logo-qr-website-colors ───────────────────
# Body (send at least one block):
#   pages              optional
#   logo_layer_id      + logo_url
#   qr_layer_id        + qr_url
#   website_layer_id   + website_text
#   fills              optional list of { "layer_id", "fill" } (hex #RRGGBB ok)
#   primary_color      optional hex — fills DEFAULT_PRIMARY_SHAPE_IDS unless you pass primary_layer_ids
#   accent_color       optional hex — fills DEFAULT_ACCENT_SHAPE_IDS unless you pass accent_layer_ids

@app.route("/api/1/logo-qr-website-colors", methods=["POST"])
def api_1_logo_qr_website_colors():
    try:
        data = _json_body()
        pages = _pages_from_body(data)
        updated: list[str] = []

        lid = data.get("logo_layer_id")
        url = data.get("logo_url")
        if lid and url:
            if not _set_layer(pages, lid, {"image_url": url}):
                return _err(f'Layer id not found: "{lid}"', 404)
            updated.append(lid)
        lid = data.get("qr_layer_id")
        url = data.get("qr_url")
        if lid and url:
            if not _set_layer(pages, lid, {"image_url": url}):
                return _err(f'Layer id not found: "{lid}"', 404)
            updated.append(lid)
        lid = data.get("website_layer_id")
        txt = data.get("website_text")
        if lid and txt is not None:
            if not _set_layer(pages, lid, {"text": str(txt)}):
                return _err(f'Layer id not found: "{lid}"', 404)
            updated.append(lid)

        # Explicit per-layer fills: [{ "layer_id": "...", "fill": "#hex or rgb" }]
        fills = data.get("fills")
        if fills is not None:
            if not isinstance(fills, list):
                return _err('"fills" must be a list of { "layer_id", "fill" }')

            for item in fills:
                if not isinstance(item, dict):
                    continue
                flid = item.get("layer_id") or item.get("layerId")
                fill = item.get("fill")
                if not flid or fill is None:
                    return _err('Each fills[] item needs "layer_id" and "fill"')
                if not _set_layer(pages, str(flid), {"fill": fill}):
                    return _err(f'Layer id not found: "{flid}"', 404)
                updated.append(str(flid))

        # Shortcut: brand colors — same as passing fills[] for many shapes at once.
        # Works with sample_pages.json defaults; override with primary_layer_ids / accent_layer_ids.
        primary = data.get("primary_color")
        if primary not in (None, ""):
            plids = data.get("primary_layer_ids")
            if plids is None:
                plids = DEFAULT_PRIMARY_SHAPE_IDS
            elif not isinstance(plids, list) or not plids:
                return _err(
                    '"primary_layer_ids" must be a non-empty list of layer ids '
                    '(or omit it to use the default teal bars in sample_pages.json)'
                )
            for pid in plids:
                pid = str(pid)
                if not _set_layer(pages, pid, {"fill": primary}):
                    return _err(f'Layer id not found for primary_color: "{pid}"', 404)
                updated.append(pid)

        accent = data.get("accent_color")
        if accent not in (None, ""):
            alids = data.get("accent_layer_ids")
            if alids is None:
                alids = DEFAULT_ACCENT_SHAPE_IDS
            elif not isinstance(alids, list) or not alids:
                return _err(
                    '"accent_layer_ids" must be a non-empty list '
                    '(or omit it to use the default accent shape in sample_pages.json)'
                )
            for aid in alids:
                aid = str(aid)
                if not _set_layer(pages, aid, {"fill": accent}):
                    return _err(f'Layer id not found for accent_color: "{aid}"', 404)
                updated.append(aid)

        if not updated:
            return _err(
                "Nothing to update. Send logo_layer_id+logo_url, qr_layer_id+qr_url, "
                "website_layer_id+website_text, fills: [{layer_id, fill}], "
                "and/or primary_color / accent_color (optional primary_layer_ids / accent_layer_ids)."
            )

        return _ok(pages, _dedupe_ids(updated))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", 500)



# ─── POST /api/2/ai-rewrite ─────────────────────────────────
# Body:
#   pages            optional
#   partner_brief    required — { name, partner_type?, industry?, audience?, tone_hints?,
#                     brand_colors?, goal?, notes?, … }
#   skip_layer_ids   optional — extra layer ids to never send to the LLM (default skips website-url)


@app.route("/api/2/ai-rewrite", methods=["POST"])
def api_2_ai_rewrite():
    try:
        data = _json_body()
        brief = data.get("partner_brief")
        if not isinstance(brief, dict) or not str(brief.get("name") or "").strip():
            return _err('Send partner_brief: { "name": "...", ... }')

        raw_skip = data.get("skip_layer_ids")
        if raw_skip is None:
            skip_ids = _DEFAULT_AI_SKIP_TEXT_IDS
        elif isinstance(raw_skip, list):
            skip_ids = frozenset(str(x) for x in raw_skip)
        else:
            return _err('"skip_layer_ids" must be a list of layer id strings (or omit it)')

        pages = _pages_from_body(data)
        layers_payload = _collect_text_layers_for_ai(pages, skip_ids)
        if not layers_payload:
            return _err("No editable text layers found (only URL/link layers or empty template?)")

        updates, meta = _marketing_llm_rewrite(brief, layers_payload, rules=None)
        if not updates:
            return _err("No text suggestions returned (model skipped every layer or empty response)")

        changed = _apply_text_updates(pages, updates)
        return jsonify(
            {
                "pages": pages,
                # "updated_layer_ids": _dedupe_ids(changed),
                # "text_updates": updates,
                # **meta,
            }
        )
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", 500)


# ─── POST /api/3a/find-replace ─────────────────────────────
# Body:
#   pages   optional
#   find    required string
#   replace required string (can be "")
#   skip_layer_ids  optional list of layer ids to never touch (default: website-url).
#                   Pass [] to only rely on URL-skipping (no whole-layer skip).
@app.route("/api/3a/find-replace", methods=["POST"])
def api_3a_find_replace():
    try:
        data = _json_body()
        pages = _pages_from_body(data)
        find = data.get("find")
        replace = data.get("replace")
        if find is None or replace is None:
            return _err('Send "find" and "replace" (strings)')
        find = str(find)
        replace = str(replace)

        raw_skip = data.get("skip_layer_ids")
        if raw_skip is None:
            skip_ids = _SKIP_FIND_REPLACE_LAYER_IDS
        elif isinstance(raw_skip, list):
            skip_ids = frozenset(str(x) for x in raw_skip)
        else:
            return _err('"skip_layer_ids" must be a list of layer id strings (or omit it)')

        updated: list[str] = []
        for page in pages:
            for lid, layer in (page.get("layers") or {}).items():
                if not isinstance(layer, dict):
                    continue
                if (layer.get("type") or "").lower() != "text":
                    continue
                if layer.get("text") is None:
                    continue
                if lid in skip_ids:
                    continue
                old = layer["text"]
                new = _replace_text_skip_urls(old, find, replace)
                if new != old:
                    layer["text"] = new
                    updated.append(lid)
        return _ok(pages, updated)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", 500)


# ─── POST /api/3b/ai-with-rules ───────────────────────────
# Same as /api/2 plus:
#   rules   required — [ {"find":"…","replace":"…"}, … ] (applied to model output)


@app.route("/api/3b/ai-with-rules", methods=["POST"])
def api_3b_ai_with_rules():
    try:
        data = _json_body()
        brief = data.get("partner_brief")
        if not isinstance(brief, dict) or not str(brief.get("name") or "").strip():
            return _err('Send partner_brief: { "name": "...", ... }')

        rules = data.get("rules")
        if not isinstance(rules, list) or not rules:
            return _err('Send "rules": [ {"find": "...", "replace": "..."}, ... ]')
        norm_rules: list[dict] = []
        for r in rules:
            if not isinstance(r, dict):
                return _err("Each rules[] item must be an object")
            if "find" not in r:
                return _err('Each rule needs "find"')
            if "replace" not in r:
                return _err('Each rule needs "replace"')
            norm_rules.append({"find": str(r["find"]), "replace": str(r["replace"])})

        raw_skip = data.get("skip_layer_ids")
        if raw_skip is None:
            skip_ids = _DEFAULT_AI_SKIP_TEXT_IDS
        elif isinstance(raw_skip, list):
            skip_ids = frozenset(str(x) for x in raw_skip)
        else:
            return _err('"skip_layer_ids" must be a list of layer id strings (or omit it)')

        pages = _pages_from_body(data)
        layers_payload = _collect_text_layers_for_ai(pages, skip_ids)
        if not layers_payload:
            return _err("No editable text layers found (only URL/link layers or empty template?)")

        updates, meta = _marketing_llm_rewrite(brief, layers_payload, rules=norm_rules)
        if not updates:
            return _err("No text suggestions returned (model skipped every layer or empty response)")

        changed = _apply_text_updates(pages, updates)
        return jsonify(
            {
                "pages": pages,
                # "updated_layer_ids": _dedupe_ids(changed),
                # "text_updates": updates,
                # **meta,
            }
        )
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", 500)


# ─── POST /api/4/swap-images ───────────────────────────────
# Body:
#   pages           optional
#   images          required — object { "layer_id": "https://...", ... }
@app.route("/api/4/swap-images", methods=["POST"])
def api_4_swap_images():
    try:
        data = _json_body()
        pages = _pages_from_body(data)
        images = data.get("images")
        if not isinstance(images, dict) or not images:
            return _err('Send "images": { "layer_id": "https://...", ... }')
        updated: list[str] = []
        for lid, url in images.items():
            if not isinstance(lid, str) or not lid:
                return _err("images keys must be layer id strings")
            if url is None or not str(url).strip():
                return _err(f'Empty url for layer "{lid}"')
            if not _set_layer(pages, lid, {"image_url": str(url).strip()}):
                return _err(f'Layer id not found: "{lid}"', 404)
            updated.append(lid)
        return _ok(pages, updated)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", 500)


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "message": "Simple layer editor — returns { pages, updated_layer_ids }",
        "template_file": "sample_pages.json",
        "routes": [
            {"post": "/api/apply", "desc": "Generic: changes: [{layer_id, ...fields}]"},
            {"post": "/api/1/logo-qr-website-colors", "desc": "Logo + QR + website + optional fills[]"},
            {"post": "/api/2/ai-rewrite", "desc": "partner_brief + MarketingLLM rewrites text layers"},
            {"post": "/api/3a/find-replace", "desc": "find + replace across text layers (URL-safe)"},
            {"post": "/api/3b/ai-with-rules", "desc": "like /api/2 plus mandatory rules[]"},
            {"post": "/api/4/swap-images", "desc": 'images: {layer_id: url}'},
        ],
    })

if __name__ == "__main__":
    print("Simple API — http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=True)
