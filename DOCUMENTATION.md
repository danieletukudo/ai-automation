# Project Documentation (Ground Level)

This document explains **how the whole system works** from the ground up.
It is written in **very simple language**, with examples and “what comes in / what comes out”.

---

## What this project is (in one sentence)

You have:

- A **template** (a flier design) on **Templated.io**
- A small **backend server** (`server.py`) that talks to Templated and (optionally) to an AI model
- A simple **browser UI** (`templated_editor.html`) where you:
  - see all template layers (text, images, colors)
  - change them manually
  - or run “modes” that change them automatically

**The output** is a rendered flier image (JPG) from Templated.

---

## Key idea: “Layers”

Think of the template like a sticker book.

Each “thing” on the flier is a **layer** with an **ID**:

- **Text layer**: contains `text`
- **Image layer**: contains `image_url`
- **Shape/color layer**: contains `fill` (like `rgb(12,34,56)`)

Examples of layer IDs in your template:

- `remoting-work-badge` (image — **Remoting Work Badge** slot; default target for the partner logo URL)
- `qr-code` (image)
- `main-professional-photo` (image)
- `logo-graphic` (image — optional; not the default logo slot)
- `website-url` (text)
- `main-heading` (text)

The UI fetches layers from the backend, shows them as inputs, then sends your edits back to the backend to render.

---

## System components

### 1) `templated_editor.html` (Frontend UI)

This is a single HTML page with JavaScript.

It:

- calls backend APIs like `/api/layers`, `/api/render`, `/api/adapt`
- shows you fields for every layer
- has modals:
  - “Review AI Copy” (uses `/api/marketing-text`)
  - “Adapt to Partner” (uses `/api/adapt`)

### 2) `server.py` (Backend API)

This is a Flask server.

It:

- stores the Templated API key (so the browser never sees it)
- calls Templated endpoints:
  - list template layers
  - render a final image
- calls AI logic (Gemini via OpenAI-compatible API) through:
  - `marketing_text_finder.py`
  - `design_adapter.py`

### 3) `design_adapter.py` (AI “Strategist + Executor” pipeline + mode dispatcher)

This file does **AI adaptation** in two steps:

- **Strategist**: makes a plan (tone, palette, direction)
- **Executor**: applies the plan to specific layers (text + colors)

It also contains a **mode dispatcher** (`run_mode`) that supports:

- `logo_qr_website_colors` (no AI — logo, QR, website text, mapped colors only)
- `ai_rewrite`
- `find_replace`
- `ai_with_rules`
- `image_only`

### 4) `marketing_text_finder.py` (AI copy suggestions)

This file is simpler:

- It looks at the current text layers
- AI suggests marketing copy replacements
- It enforces: **same word count** (because the design layout can break)

---

## Important configuration and rules

### Brand asset “constants” (always provided)

In `server.py` there are default “brand asset slots”:

- Logo layer: `remoting-work-badge` (Remoting Work Badge image — partners replace its URL)
- QR layer: `qr-code`
- Website text layer: `website-url`

The UI always asks the user to provide:

- logo URL
- QR URL
- website text

Those become forced changes regardless of mode (when you fill them in).

### Shape color map (no-AI mode only)

In `server.py`, `SHAPE_COLOR_ROLES` lists which **shape layer IDs** get the user’s
**Primary** and **Accent** hex colors when `mode` is `logo_qr_website_colors`.

By default, **primary** is applied to `avatar-bg-1` and `avatar-bg-2` (avatar
circles), and **accent** to `gold-accent-circle`. Edit `SHAPE_COLOR_ROLES` in
`server.py` if your template uses different layer IDs.

### Locked layers (never change)

`server.py` → `LOCKED_LAYERS` lists layer IDs that **cannot** be edited by any mode
(empty by default). The partner **logo** is applied to `remoting-work-badge`, so
that layer must **not** be in this set. Add layer IDs here only for assets that
must stay identical on every flier.

---

## How the app flows (step-by-step)

### Step A — UI loads layers

1. Browser opens `templated_editor.html`
2. It calls:

`GET /api/layers`

3. Backend calls Templated to get every layer
4. UI shows inputs for:
   - all text layers
   - all images
   - all fills (colors)

### Step B — You edit values

You can:

- type into text boxes
- paste image URLs
- adjust colors

### Step C — Render the image

When you click **Generate**:

`POST /api/render`

Backend sends your layer values to Templated render.
Templated returns a URL to the generated JPG.
UI previews the JPG.

### Optional Step D — AI features

There are **two AI features**:

1) “Review AI Copy” (marketing suggestions) → `/api/marketing-text` → uses `marketing_text_finder.py`
2) “Adapt to Partner” (modes + strategist/executor) → `/api/adapt` → uses `design_adapter.py`

---

## API Documentation (`server.py`)

This section describes each endpoint.

### 1) `GET /` (serves the UI)

**What it does**
- Returns the HTML file `templated_editor.html`.

**You usually don’t call this manually** — you open it in the browser.

---

### 2) `GET /api/layers`

**What it does**
- Fetches the template pages/layers from Templated.io for the configured template ID.

**Input**
- No body

**Output**
- JSON array of pages (Templated format)

**Why it exists**
- The UI needs the list of layer IDs and current values to build the editor.

---

### 3) `POST /api/render`

**What it does**
- Asks Templated to render the template with your layer overrides.

**Input body**

```json
{
  "layers": {
    "main-heading": { "text": "Hello world" },
    "remoting-work-badge": {
      "image_url": "https://example.com/partner-logo.png",
      "object_fit": "contain"
    },
    "some-shape": { "fill": "rgb(10,20,30)" }
  }
}
```

**Output**
- JSON from Templated render, usually containing `url` or `render_url`.

**Important**
- You can pass partial layers, but the UI usually sends all layers.
- `object_fit` values understood by the Templated render API:
  - `contain` — fit inside the slot, keep aspect ratio (recommended for logos/badges so a new image doesn't overflow).
  - `cover` — fill the slot, keep aspect ratio, may crop.
  - `fill` — stretch to fill the slot (may distort).
  - `scale-down` — shrink only if the source is larger than the slot.

---

### Editing fonts on text layers

Every text layer in Templated carries its typography as first-class fields — `font_family`, `font_weight`, `font_size`, `color`, `letter_spacing`, `line_height`, `text_stroke_*`, `text_highlight_color`. The render API accepts any of these as per-layer overrides, so changing typography is a pure payload change (no AI, no image math).

**`GET /api/fonts`** — the full catalog we show the user.

The backend merges two sources into one catalog and caches it for the process lifetime:
1. **Google Fonts metadata** (`https://fonts.google.com/metadata/fonts`) — ~1,930 families with `category` (Sans Serif / Serif / Display / Handwriting / Monospace) and popularity ranking.
2. **Templated `/v1/fonts`** — adds any custom/team-uploaded fonts on this account.

Failure to reach Google is non-fatal — we fall back to the Templated list.

Response:
```json
{
  "total": 1931,
  "fonts": [
    {
      "name":         "ABeeZee",
      "category":     "Sans Serif",
      "popularity":   154,
      "is_google":    true,
      "is_uploaded":  false
    },
    ...
  ],
  "popular": [
    "Roboto", "Inter", "Open Sans", "Lato", "Montserrat",
    "Poppins", "Noto Sans", "Raleway", "Nunito", "Work Sans", ...
  ],
  "ms_fonts": [
    { "name": "Arial",           "substitute": "Arimo" },
    { "name": "Calibri",         "substitute": "Carlito" },
    { "name": "Times New Roman", "substitute": "Tinos" },
    { "name": "Verdana",         "substitute": "Open Sans" },
    { "name": "Georgia",         "substitute": "Noto Serif" },
    ... 34 total ...
  ]
}
```

**Arial, Calibri, Times New Roman — how they work.** Those are proprietary Microsoft fonts that are *not* licensed for web rendering. The Templated renderer (and every other server-side renderer) can only use Google Fonts or fonts you upload yourself. We still list 34 common MS/Mac system fonts in the dropdown (Arial, Calibri, Times New Roman, Cambria, Verdana, Georgia, Comic Sans MS, Segoe UI, Helvetica, Impact, Consolas, Palatino, …) — when the user picks one, the backend silently rewrites `font_family` to the closest Google equivalent in `_apply_font_substitutes()` (`server.py`) before calling Templated. The first five are **metric-compatible** substitutes (identical character widths, layout-preserving). The rest are visually close matches. Users never have to know about the substitution — they see "Arial", the render shows Arial-lookalike.

**Per-field controls**

Each text field in the left panel has a collapsible "Aa Font & style" block:

- **Font family** — a **searchable** dropdown. A small search box above the `<select>` live-filters 1,900+ fonts as you type (matches substrings in names, and matches both "Arial" and "Arimo" for MS entries). Below the search box is a `<select>` with `<optgroup>`s in this order:
  1. **Currently in template** — the font the template itself uses (pre-selected).
  2. **Popular** — ~20 curated favourites (Roboto, Inter, Poppins, Lora, Playfair Display, …).
  3. **Microsoft fonts (auto-substituted)** — Arial, Calibri, Times New Roman, Verdana, Georgia, Comic Sans MS, Segoe UI, …, each labelled `Arial → Arimo` to show what will actually render.
  4. **Sans Serif (710)**, **Serif (347)**, **Display (466)**, **Handwriting (356)**, **Monospace (50)**.

  When a search is active, an "N matches" badge appears next to the input, and the first real hit is auto-selected so the preview updates as you refine. If the layer's current font isn't in the catalog (custom upload), it's preserved in a "Custom / unknown" group.

- **Weight** — `normal`, `bold`, or numeric `100…900`.
- **Size (px)** — numeric.
- **Color** — `<input type="color">` (hex picker). Alpha is preserved from the original `rgba(...)`.

**Render-time font substitution** (`server.py::_apply_font_substitutes`). Every call to `POST /api/render` passes its `layers` dict through this helper, which looks up each `font_family` in `MS_FONT_SUBSTITUTES` and rewrites it if it's an MS/system font name. The substitution is idempotent (Arimo stays Arimo) and whitespace-tolerant ("` Arial `" → "Arimo"). Unit-tested for all 34 entries plus pass-through cases.

The summary line shows a live badge like `Poppins · 700 · 82px` so changes are visible without expanding. A "reset to template default" button restores all four to the values returned by `/api/layers`.

**Render payload discipline**

`collectTextFontOverrides(id, layer)` only includes a font property if the user actually changed it versus the layer's original — unchanged fields are dropped from the payload. This keeps untouched text rendering identical to the template and makes diffs easy to reason about:

```json
{
  "layers": {
    "main-heading": {
      "text":        "<b>Beautiful typography</b>",
      "font_family": "Lora",
      "font_weight": "700",
      "font_size":   "72px",
      "color":       "rgba(20,30,60,1)"
    },
    "scan-to-hire-label": { "text": "Scan to Hire" }
  }
}
```

The AI rewrite / find-replace / adapt flows never touch font fields — typography is a manual decision.

---

### Image slot sizing & fit (server-side resize)

Every template image layer has a fixed **slot size** in pixels (`layer.width × layer.height`). An uploaded replacement almost never matches those exact pixels, which is what caused earlier overlap / misalignment bugs.

All image math lives in Python. The frontend never touches pixels — it just displays what the server returns.

**`POST /api/fit-image`** — server-side resize + re-host.

Input:
```json
{
  "url": "https://github.com/github.png",
  "layer_id": "remoting-work-badge",
  "object_fit": "contain"
}
```

The backend (in `server.py`):
1. Looks up `layer.width × layer.height` for `layer_id` from the cached template metadata.
2. Fetches the image bytes at `url` (sends a browser-like `User-Agent` so CDNs don't 403 us).
3. Uses **Pillow** in `_fit_to_slot(...)` to composite the source onto a transparent PNG of **exactly** slot dims using the requested fit:
   - `contain` → letterbox, keep ratio, pad transparent (default; best for logos).
   - `cover` → fill slot, keep ratio, center-crop overflow.
   - `fill` → stretch to slot (may distort).
   - `scale-down` → like `contain` but never upscales.
4. Uploads the resulting PNG to Templated's `POST /v1/upload` and returns the resulting CDN URL.
5. Caches `(src_url, fit, w, h) → hosted_url` in-memory so repeated requests skip the re-fetch and re-upload.

Response:
```json
{
  "image_url":  "https://templated-assets.s3.amazonaws.com/upload/…",
  "data_url":   "data:image/png;base64,…",
  "width":      362,
  "height":     152,
  "object_fit": "contain",
  "bytes":      7936,
  "source_url": "https://github.com/github.png",
  "cached":     false
}
```

The frontend uses this endpoint automatically:
- The URL input `onblur`, the 👁 button, the Fit dropdown change, and the Adapt modal "Apply" all call `/api/fit-image`.
- On success the input's value is swapped for the hosted URL, the thumbnail uses the inline `data_url` for instant preview, and the field records the `(source, fit)` pair so re-fits aren't redundant.
- Before `/api/render` fires, `ensureAllImagesFitted()` awaits any in-flight fits and kicks off fits for layers the user interacted with but hasn't fitted yet (prevents races between paste + Generate).
- Layers the user never touched keep the template's original asset URL verbatim.

`collectLayers()` sends `{ image_url, object_fit: "fill" }` for server-fitted layers (so Templated draws the already-correct pixels 1:1) and plain `{ image_url }` for untouched defaults.

Layer slot dimensions + aspect ratio are also displayed as a badge next to each image field (e.g. `362×152px · 2.38:1`).

---

### 4) `GET /api/proxy-image?url=...`

**What it does**
- Downloads an image server-side and returns bytes to the browser.

**Why**
- Avoids CORS problems when the browser tries to show the rendered image.

---

### Layout safety (word count)

AI copy is validated with **`text_layout_guard.py`**:

- **Visible word count** = strip HTML tags and `<br>`, then count whitespace-separated words (same rule in prompts and in code).
- Suggestions or `text_changes` that **do not match** the template layer’s visible word count are **dropped** so they cannot break the layout.
- Optional **one repair LLM call** tries to fix mismatches first (`LAYOUT_WORD_COUNT_REPAIR`, default `1`). Set to `0` to skip repair and only drop.

API responses may include **`layout_validation`** (drops, repair metadata).

---

### 5) `POST /api/marketing-text`

**What it does**
- Uses AI to suggest better marketing copy for some of the text layers.

**Input body**

```json
{
  "text_layers": {
    "main-heading": "Full-time, Dedicated Professionals Your Business Needs to Scale",
    "testimonial-text-1": "Since using remoting.work, we got time..."
  },
  "company_desc": "Optional. If omitted, server uses COMPANY_DESC."
}
```

**Output**

```json
{
  "suggestions": [
    {
      "layer_id": "main-heading",
      "current_text": "Full-time, Dedicated Professionals Your Business Needs to Scale",
      "suggested_text": "Full-time, Dedicated Professionals Your Team Needs to Scale",
      "word_count": 8,
      "reason": "Makes the value clearer for the target audience"
    }
  ]
}
```

**Key rule**
- AI must keep the **same word count** as the original text.

---

### 6) `POST /api/adapt` (New, main endpoint)

This is the endpoint that powers your “mode picker”.

#### Input body (full shape)

```json
{
  "mode": "logo_qr_website_colors",
  "brand_colors": { "primary": "#0b2545", "accent": "#0fb5a7" },
  "partner_brief": {
    "name": "Illinois Chamber",
    "partner_type": "chamber",
    "industry": "Business Advocacy",
    "audience": "Member business owners",
    "goal": "Drive scans",
    "tone_hints": "professional, empowering",
    "link_url": "https://remoting.work/illinois-chamber",
    "brand_colors": { "primary": "#0b2545", "accent": "#0fb5a7" },
    "notes": "More institutional"
  },
  "text_layers":  { "layer-id": "text..." },
  "shape_layers": { "layer-id": "rgb(...)" },
  "image_layers": { "layer-id": "https://..." },

  "replace_rules": [
    { "find": "Business", "replace": "Chamber" }
  ],
  "image_overrides": {
    "main-professional-photo": "https://example.com/new-photo.jpg"
  },
  "brand_assets": {
    "logo":    { "layer_id": "remoting-work-badge", "url": "https://example.com/partner-logo.png" },
    "qr":      { "layer_id": "qr-code", "url": "https://example.com/qr.png" },
    "website": { "layer_id": "website-url", "text": "go.remoting.work/illinois" }
  }
}
```

#### Output body (same shape for every mode)

```json
{
  "mode": "logo_qr_website_colors",
  "strategy": null,
  "text_changes":  [ ],
  "shape_changes": [ ],
  "image_changes": [ ],
  "summary": "..."
}
```

#### What each mode means

- **`logo_qr_website_colors`**
  - **No AI** (fast): does not call Gemini at all
  - Applies logo URL, QR URL, website text, and your primary/accent hex colors
  - Colors are applied only to shape IDs listed in `server.py` → `SHAPE_COLOR_ROLES`
  - Does **not** need `partner_brief.name` or any partner story fields

- **`ai_rewrite`**
  - Full Strategist + Executor:
    - new text suggestions
    - new color suggestions
  - Also applies brand assets and image overrides

- **`find_replace`**
  - No AI
  - Applies your `replace_rules` literally to every text layer
  - Also applies brand assets and image overrides

- **`ai_with_rules`**
  - AI rewrites copy and chooses colors
  - AI is told your `replace_rules` are **hard constraints**

- **`image_only`**
  - No AI, no colors, no text
  - Only image overrides (and brand assets logo/QR)

#### Locked layer behavior

The server removes locked layer IDs (from `LOCKED_LAYERS`) from:

- request `text_layers`, `shape_layers`, `image_layers`
- request `image_overrides`
- the final output arrays (`text_changes`, `shape_changes`, `image_changes`)

So it is impossible to change those layers via API.

---

### 7) `POST /api/adapt-design` (Old endpoint)

This is the older single-purpose AI endpoint.

It’s still here for backward compatibility.
The new system should use `/api/adapt` instead.

---

## Deep dive: `design_adapter.py`

This file has two “halves”:

1) The **AI class** `DesignAdapter`
2) The **mode dispatcher** `run_mode(...)`

### A) AI class: `DesignAdapter`

#### 1) `_make_client()`

**What it does**
- Creates an OpenAI-style client pointed at Gemini:
  - `base_url="https://generativelanguage.googleapis.com/v1beta/openai/"`
  - uses env var `GEMINI_API_KEY`

**Why**
- So the rest of the code can call `client.chat.completions.create(...)`.

---

#### 2) `_strip_json_fence(raw: str)`

**Problem it solves**
- Sometimes the model returns:

```
```json
{ ... }
```
```

This function removes those backticks so JSON parsing works.

---

#### 3) `_safe_json_loads(raw: str) -> dict`

**What it does**
- Tries to parse JSON cleanly.
- If parsing fails, it searches for the first `{ ... }` block and parses that.

**Why**
- Models sometimes add extra text.

---

#### 4) `DesignAdapter.plan(partner_brief, template_context) -> dict`

This is the **Strategist** stage.

**Input**
- `partner_brief`: partner name + optional info
- `template_context`: the current layers (text/colors/images) so AI understands what it’s adapting

**Output**
- A “strategy plan” JSON:
  - partner type guess
  - tone vector numbers
  - palette colors
  - messaging shift phrases

**Important**
- This stage does NOT change any specific layer.
It only makes the plan.

---

#### 5) `DesignAdapter.execute(strategy, text_layers, shape_layers, image_layers, partner_brief) -> dict`

This is the **Executor** stage.

**Input**
- `strategy`: output of `plan()`
- `text_layers`: `{layer_id: current_text}`
- `shape_layers`: `{layer_id: current_fill}`

**Output**
```json
{
  "text_changes": [
    {
      "layer_id": "main-heading",
      "current_text": "...",
      "suggested_text": "...",
      "word_count": 8,
      "reason": "..."
    }
  ],
  "shape_changes": [
    {
      "layer_id": "some-shape",
      "current_fill": "rgb(...)",
      "suggested_fill": "rgb(...)",
      "reason": "..."
    }
  ],
  "summary": "..."
}
```

**Hard rule**
- Suggested text must have **exactly the same word count** as the original.

---

#### 6) `DesignAdapter.execute_with_rules(...)`

Same as `execute()` but with an extra input:

- `replace_rules`: list of `{find, replace}`

The prompt tells the model:
- those rules are **hard constraints**
- it must apply them everywhere

This is used for the mode `ai_with_rules`.

---

#### 7) `DesignAdapter.adapt(...)`

Convenience wrapper:

- builds template context
- calls `plan()`
- calls `execute()`
- returns a combined object:
  - `strategy`
  - `text_changes`
  - `shape_changes`
  - `summary`

---

### B) Mode dispatcher: `run_mode(...)`

This is used by `/api/adapt` in the backend.

#### 1) `SUPPORTED_MODES`

A set containing:

- `logo_qr_website_colors`
- `ai_rewrite`
- `find_replace`
- `ai_with_rules`
- `image_only`

If you pass something else, the server returns HTTP 400.

---

#### 2) `_apply_find_replace(text: str, rules: list[dict]) -> str`

**What it does**
- For each rule:
  - find “find” text (case-insensitive)
  - replace with “replace”

**Example**

Input:
- text: `"Meet NutriHealth today."`
- rules: `[{ "find": "NutriHealth", "replace": "CareAI" }]`

Output:
- `"Meet CareAI today."`

This is the engine behind mode `find_replace`.

---

#### 3) `run_mode(...) -> dict`

This is the single entry point the server uses.

It always returns:

- `text_changes`: list of “what text should change”
- `shape_changes`: list of “what colors should change”
- `image_changes`: list of “what images should change”

It also merges in:

- `brand_assets` → always forces logo/QR/website overrides
- `image_overrides` → extra image swaps (like `main-professional-photo`)

##### How brand assets become changes

- Logo + QR are image changes:
  - create entries in `image_changes`
- Website link is a text change:
  - creates an entry in `text_changes`

##### Important safety rule

Website layer is removed from AI inputs so the AI does not “rewrite” a URL.

##### No-AI helpers (`logo_qr_website_colors`)

- `_hex_to_rgb_fill(hex)` — turns `#RRGGBB` into `rgb(r,g,b)` for Templated.
- `_shape_changes_from_brand_hexes(...)` — builds `shape_changes` from your hex
  colors and `SHAPE_COLOR_ROLES` / `DEFAULT_SHAPE_COLOR_ROLES` (no LLM).

---

## Deep dive: `marketing_text_finder.py`

This file is “AI copy suggestions only”.

### `MarketingLLM.__init__()`

- Creates Gemini client using `GEMINI_API_KEY`

### `analyze_layers_and_suggest(text_layers, image_layers, shape_layers, company_desc) -> dict`

**Input**
- `text_layers`: all text layer IDs → text
- optional context:
  - `image_layers`
  - `shape_layers`
- `company_desc`: description of the company

**Output**
```json
{
  "suggestions": [
    {
      "layer_id": "main-heading",
      "current_text": "...",
      "suggested_text": "...",
      "word_count": 8,
      "reason": "..."
    }
  ]
}
```

**Hard rule**
- Same word count

### `analyze_html_and_suggest(html_content, company_desc) -> dict`

This is an older / alternative function:

- It analyzes raw HTML and suggests text changes by CSS selector.

In your current UI flow, you mainly use `analyze_layers_and_suggest`.

---

## Quick “copy/paste” examples (curl)

### Render without edits

```bash
curl -s -X POST http://localhost:5001/api/render \
  -H "Content-Type: application/json" \
  -d '{"layers":{}}'
```

### Find & Replace mode (no AI)

```bash
curl -s -X POST http://localhost:5001/api/adapt \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "find_replace",
    "text_layers": { "main-heading": "Hello Business World" },
    "shape_layers": {},
    "image_layers": {},
    "replace_rules": [{ "find": "Business", "replace": "Chamber" }]
  }'
```

### Image-only swap

```bash
curl -s -X POST http://localhost:5001/api/adapt \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "image_only",
    "image_layers": { "main-professional-photo": "https://old.jpg" },
    "image_overrides": { "main-professional-photo": "https://new.jpg" }
  }'
```

---

## “What should I read first?”

If you are lost, read in this order:

1) `server.py` endpoint list (under “API Documentation” above)
2) `templated_editor.html`:
   - `fetchLayers()` (loads layers)
   - `collectLayers()` (builds the `layers` object)
   - `renderTemplate()` (calls `/api/render`)
   - `runAdapt()` (calls `/api/adapt`)
3) `design_adapter.py`:
   - `run_mode()` first (it explains the modes)
   - then `DesignAdapter.plan()` and `execute()`
4) `marketing_text_finder.py`:
   - `analyze_layers_and_suggest()`

---

## Notes / gotchas

### 1) Word-count matching

This is a big theme:

- Graphic templates have fixed box sizes.
- If the AI adds extra words, text can overflow.

So we enforce: **same word count** in AI prompts.

### 2) Secret keys

The backend contains:

- Templated API key (currently hardcoded in `server.py`)
- Gemini key (expected in `.env` as `GEMINI_API_KEY`)

Do not expose these in the frontend.

---

## Glossary (simple)

- **Template**: the design blueprint stored on Templated.io
- **Layer**: one editable element on the template (text/image/color)
- **Render**: generate a final image (JPG) from the template + your edits
- **Mode**: the “how do we change things?” option the user selects
- **Strategist**: AI that makes a plan
- **Executor**: AI that applies the plan to exact layer IDs

