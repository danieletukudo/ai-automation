"""
Templated Backend Server
------------------------
Flask server that exposes Templated API operations to the frontend.
Keeps the API key server-side so it's never exposed in the browser.

Run:  python server.py
Then open: http://localhost:5000
"""

import os
import json
import threading
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__, static_folder=".")
CORS(app)
app.config["API_KEY"] = (os.getenv("TEMPLATED_API_KEY") or "").strip()
app.config["TEMPLATE_ID"] = (os.getenv("TEMPLATED_TEMPLATE_ID") or "").strip()
BASE_URL = "https://api.templated.io/v1"


# ─── Fonts catalog (Google Fonts) ─────────────────────────
# The frontend expects:
#   { fonts: [{name, category, popularity?}], ms_fonts: [...], popular: [...] }
_GOOGLE_FONTS_CACHE: dict | None = None
_GOOGLE_FONTS_LOCK = threading.Lock()

# Handful of fonts that tend to be crowd favourites (used by UI).
POPULAR_FONTS = [
    "Roboto", "Inter", "Open Sans", "Lato", "Montserrat", "Poppins",
    "Noto Sans", "Raleway", "Nunito", "Work Sans", "Source Sans 3",
    "Playfair Display", "Merriweather", "Lora", "EB Garamond",
    "IBM Plex Sans", "IBM Plex Serif", "DM Sans", "Fira Sans", "Rubik",
]


def _fetch_google_fonts() -> list[dict]:
    """Pull Google's full public font catalog (no API key needed)."""
    r = requests.get(
        "https://fonts.google.com/metadata/fonts",
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; templated-editor/1.0)"},
    )
    r.raise_for_status()
    body = (r.text or "").lstrip()
    if body.startswith(")]}'"):
        body = body[4:]
    data = json.loads(body)
    out: list[dict] = []
    for f in data.get("familyMetadataList") or []:
        out.append({
            "name": f.get("family"),
            "category": f.get("category") or "Other",
            # Google's "popularity" is a rank (lower is more popular)
            "popularity": f.get("popularity"),
        })
    return [x for x in out if x.get("name")]


@app.route("/api/fonts", methods=["GET"])
def list_fonts():
    """Return Google Fonts catalog for the UI."""
    global _GOOGLE_FONTS_CACHE
    with _GOOGLE_FONTS_LOCK:
        if _GOOGLE_FONTS_CACHE is None:
            try:
                fonts = _fetch_google_fonts()
            except Exception as e:
                return jsonify({"error": f"could not load Google fonts: {e}"}), 502
            _GOOGLE_FONTS_CACHE = {
                "fonts": fonts,
                "ms_fonts": [],
                "popular": POPULAR_FONTS,
                "total": len(fonts),
            }
        return jsonify(_GOOGLE_FONTS_CACHE)


# ─── Per-request credentials (API key + template id) ──────
def _looks_like_uuid(s: str) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) != 36:
        return False
    parts = s.split("-")
    if len(parts) != 5 or [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    hexdigits = set("0123456789abcdefABCDEF")
    return all(ch in hexdigits for p in parts for ch in p)


def _get_templated_creds(data: dict | None = None) -> tuple[str, str] | tuple[None, None]:
    """
    Resolve Templated credentials in this order:
      - POST JSON body fields: api_key, template_id
      - Headers: X-Templated-Api-Key, X-Templated-Template-Id
      - Query params: api_key, template_id
      - Server defaults (env/app.config)
    """
    data = data or {}

    api_key = (data.get("api_key") or "").strip() or (request.headers.get("X-Templated-Api-Key") or "").strip()
    template_id = (data.get("template_id") or "").strip() or (request.headers.get("X-Templated-Template-Id") or "").strip()

    if not api_key:
        api_key = (request.args.get("api_key") or "").strip()
    if not template_id:
        template_id = (request.args.get("template_id") or "").strip()

    api_key = api_key or (app.config.get("API_KEY") or "")
    template_id = template_id or (app.config.get("TEMPLATE_ID") or "")

    api_key = api_key.strip()
    template_id = template_id.strip()

    if not api_key or not template_id:
        return None, None

    if not _looks_like_uuid(api_key):
        raise ValueError("Invalid api_key format (expected UUID string)")
    if not _looks_like_uuid(template_id):
        raise ValueError("Invalid template_id format (expected UUID string)")

    return api_key, template_id

# ─── Brand-asset defaults ────────────────────────────────
# Layer IDs the template uses for the three fixed brand slots:
#   - logo_graphic    : the partner's logo image
#   - qr_code         : the partner's QR code image
#   - website_url     : the partner's website link (text layer)
# The URL / text fields are left blank on purpose — the partner fills them in
# from the UI each time. Per-request overrides are accepted via /api/adapt.
app.config["BRAND_ASSET_DEFAULTS"] = {
    "logo":    {"layer_id": "logo-graphic", "url":  ""},
    "qr":      {"layer_id": "qr-code",      "url":  ""},
    "website": {"layer_id": "website-url",  "text": ""},
}

# ─── Locked layers ───────────────────────────────────────
# Layers that must NEVER be modified by any mode (AI or otherwise). These are
# Remoting.work's own constants that stay identical across every partner flier.
# They're stripped from all LLM prompts AND filtered out of any change lists
# before the response leaves /api/adapt.
app.config["LOCKED_LAYERS"] = {
    "remoting-work-badge",   # Remoting.work's own badge image
}

# Company description used for marketing text suggestions
COMPANY_DESC = (
    "Use your everyday food to manage and improve your chronic conditions—with AI.\n"
    "An AI solution for people with chronic condition. Tell our AI your sickness, and it "
    "recommends food that maintains your health and can restore or improve your condition "
    "based on your health data."
)


# ─── Serve the HTML editor ────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "templated_editor.html")


# ─── Design adaptation (Strategist + Executor) ────────────
@app.route("/api/adapt-design", methods=["POST"])
def adapt_design():
    """
    Full design-adaptation pipeline. Given a partner brief plus the current
    template layers, returns a strategy + per-layer text/color changes.

    Body:
    {
      "partner_brief": {
        "name": "Illinois State Black Chamber of Commerce",
        "partner_type": "chamber",
        "industry": "Business Advocacy",
        "audience": "Black-owned businesses in Illinois",
        "tone_hints": "professional, empowering",
        "brand_colors": {"primary": "#0B2545", "accent": "#0FB5A7"},
        "goal": "Drive members to scan QR and hire via remoting.work",
        "link_url": "https://remoting.work/illinois-chamber",
        "notes": "more institutional than input"
      },
      "text_layers":  { "<layer-id>": "text", ... },
      "shape_layers": { "<layer-id>": "rgb(...)", ... },
      "image_layers": { "<layer-id>": "https://...", ... }   # optional
    }
    """
    try:
        from design_adapter import DesignAdapter
    except ImportError:
        return jsonify({"error": "design_adapter.py not found"}), 500

    data = request.get_json() or {}
    partner_brief = data.get("partner_brief") or {}
    text_layers   = data.get("text_layers")  or {}
    shape_layers  = data.get("shape_layers") or {}
    image_layers  = data.get("image_layers") or {}

    if not partner_brief.get("name"):
        return jsonify({"error": "partner_brief.name is required"}), 400
    if not text_layers and not shape_layers:
        return jsonify({"error": "No editable layers provided"}), 400

    try:
        adapter = DesignAdapter()
        result = adapter.adapt(
            partner_brief=partner_brief,
            text_layers=text_layers,
            shape_layers=shape_layers,
            image_layers=image_layers,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Mode-based adaptation (new, preferred entry point) ──



@app.route("/api/adapt", methods=["POST"])
def adapt_by_mode():
    """
    Mode-based design adaptation. One endpoint for all 5 modes.

    Request body:
    {
      "mode": "branding_only" | "ai_rewrite" | "find_replace"
            | "ai_with_rules" | "image_only",

      "partner_brief":     { ...same shape as /api/adapt-design... },
      "text_layers":       { "<layer-id>": "text", ... },
      "shape_layers":      { "<layer-id>": "rgb(...)", ... },
      "image_layers":      { "<layer-id>": "https://...", ... },

      "replace_rules":     [ {"find": "X", "replace": "Y"}, ... ],   # modes 3a/3b
      "image_overrides":   { "<layer-id>": "https://...", ... },     # any mode
      "brand_assets": {
          "logo":    {"layer_id": "<id>", "url":  "https://..."},
          "qr":      {"layer_id": "<id>", "url":  "https://..."},
          "website": {"layer_id": "<id>", "text": "remoting.work/..."}
      }
    }

    Response shape is always:
      {
        "mode": "<mode>",
        "strategy":      { ... } | null,
        "text_changes":  [ ... ],
        "shape_changes": [ ... ],
        "image_changes": [ ... ],
        "summary":       "..."
      }
    """
    try:
        from design_adapter import run_modes, SUPPORTED_MODES
    except ImportError:
        return jsonify({"error": "design_adapter.py not found"}), 500

    data = request.get_json() or {}

    # Accept either `modes: ["a", "b", ...]` (multi-select) or the legacy
    # singular `mode: "a"`. Empty / unknown entries are rejected.
    raw_modes = data.get("modes")
    if raw_modes is None:
        single = (data.get("mode") or "").strip()
        modes = [single] if single else []
    else:
        if not isinstance(raw_modes, list):
            return jsonify({"error": "'modes' must be a list of mode strings"}), 400
        modes = [str(m).strip() for m in raw_modes if str(m).strip()]

    if not modes:
        return jsonify({
            "error": "Pick at least one mode. Send `modes: [...]` or `mode: \"...\"`."
        }), 400

    bad = [m for m in modes if m not in SUPPORTED_MODES]
    if bad:
        return jsonify({
            "error": f"Unknown mode(s): {bad}. Expected: {sorted(SUPPORTED_MODES)}"
        }), 400

    selected = set(modes)

    partner_brief   = data.get("partner_brief")   or {}
    text_layers     = data.get("text_layers")     or {}
    shape_layers    = data.get("shape_layers")    or {}
    image_layers    = data.get("image_layers")    or {}
    replace_rules   = data.get("replace_rules")   or []
    image_overrides = data.get("image_overrides") or {}
    brand_colors    = data.get("brand_colors")    or {}

    # Strip locked layers from everything the adapter sees — they're never
    # modified, never shown to the LLM, never overridable.
    locked = app.config["LOCKED_LAYERS"]
    text_layers     = {k: v for k, v in text_layers.items()     if k not in locked}
    shape_layers    = {k: v for k, v in shape_layers.items()    if k not in locked}
    image_layers    = {k: v for k, v in image_layers.items()    if k not in locked}
    image_overrides = {k: v for k, v in image_overrides.items() if k not in locked}

    # Merge user-provided brand_assets on top of server defaults so the UI
    # can omit fields it doesn't want to override.
    brand_assets_in = data.get("brand_assets") or {}
    defaults = app.config["BRAND_ASSET_DEFAULTS"]
    brand_assets = {
        "logo":    {**defaults["logo"],    **(brand_assets_in.get("logo")    or {})},
        "qr":      {**defaults["qr"],      **(brand_assets_in.get("qr")      or {})},
        "website": {**defaults["website"], **(brand_assets_in.get("website") or {})},
    }

    # Per-mode required-field checks. Fired only for the modes actually picked.
    needs_brief_modes = {"ai_rewrite", "ai_with_rules"}
    if selected & needs_brief_modes and not partner_brief.get("name"):
        return jsonify({
            "error": "partner_brief.name is required when AI Rewrite or AI with Rules is selected"
        }), 400
    if "find_replace" in selected and not any(r.get("find") for r in replace_rules):
        return jsonify({
            "error": "Find & Replace requires at least one rule with 'find'"
        }), 400
    if "ai_with_rules" in selected and not any(r.get("find") for r in replace_rules):
        return jsonify({
            "error": "AI with Rules requires at least one find/replace rule"
        }), 400
    if (
        selected == {"image_only"}
        and not image_overrides
        and not any(brand_assets[k].get("url") for k in ("logo", "qr"))
    ):
        return jsonify({
            "error": "Swap Image Only requires at least one image override or brand image"
        }), 400

    try:
        result = run_modes(
            modes=sorted(selected),
            partner_brief=partner_brief,
            text_layers=text_layers,
            shape_layers=shape_layers,
            image_layers=image_layers,
            replace_rules=replace_rules,
            image_overrides=image_overrides,
            brand_assets=brand_assets,
            brand_colors=brand_colors,
        )
        for key in ("text_changes", "shape_changes", "image_changes"):
            result[key] = [c for c in result.get(key, []) if c.get("layer_id") not in locked]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Marketing text suggestions via LLM ───────────────────
@app.route("/api/marketing-text", methods=["POST"])
def marketing_text():
    """
    Calls Gemini (acting as marketing) to analyze template text layers and suggest
    replacements that fit the company description.
    Body: { "text_layers": { "layer-id": "current text", ... }, "company_desc": "..." }
    """
    try:
        from marketing_text_finder import MarketingLLM
    except ImportError:
        return jsonify({"error": "marketing_text_finder.py not found"}), 500

    data = request.get_json() or {}
    text_layers = data.get("text_layers", {})
    image_layers = data.get("image_layers", {})
    shape_layers = data.get("shape_layers", {})
    company_desc = data.get("company_desc", COMPANY_DESC)

    if not text_layers:
        return jsonify({"error": "No text layers provided — wait for template to load first"}), 400

    try:
        llm = MarketingLLM()
        result = llm.analyze_layers_and_suggest(
            text_layers, image_layers, shape_layers, company_desc
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500




# ─── Get all template layers ─────────────────────────────
@app.route("/api/layers", methods=["GET"])
def get_layers():
    """Return all editable layers for the configured template."""
    try:
        api_key, template_id = _get_templated_creds()
        if not api_key or not template_id:
            return jsonify({"error": "Missing credentials. Provide api_key + template_id (headers, query params, or JSON)."}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    url = f"{BASE_URL}/template/{template_id}/pages"

    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500


# ─── Render template with layer values  ───────────────────
@app.route("/api/render", methods=["POST"])
def render():
    """
    Accept layer values, pass to Templated render endpoint.
    Body: { "layers": { "layer-id": { "text": "..." } } }
    """
    data = request.get_json() or {}
    try:
        api_key, template_id = _get_templated_creds(data)
        if not api_key or not template_id:
            return jsonify({"error": "Missing credentials. Provide api_key + template_id."}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    layers = data.get("layers", {})

    url = f"{BASE_URL}/render"
    payload = {
        "template": template_id,
        "format": "jpg",
        "layers": layers,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500


# ─── Proxy image downloads (to avoid CORS) ───────────────
@app.route("/api/proxy-image", methods=["GET"])
def proxy_image():
    """Proxy image downloads so the browser can fetch without CORS issues."""
    image_url = request.args.get("url", "")
    if not image_url:
        return jsonify({"error": "url parameter required"}), 400

    try:
        response = requests.get(image_url, timeout=15, stream=True)
        response.raise_for_status()
        headers = {
            "Content-Type": response.headers.get("Content-Type", "image/jpeg"),
            "Content-Disposition": "attachment; filename=templated.jpg",
            "Cache-Control": "no-cache",
        }
        return response.raw.read(), 200, headers
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    if app.config.get("TEMPLATE_ID"):
        print(f"Template ID (default): {app.config['TEMPLATE_ID']}")
    print(f"Open http://localhost:5000 in your browser")
    app.run(host="0.0.0.0", port=5001, debug=True)
