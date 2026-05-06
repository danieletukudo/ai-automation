# Simple template API

You send JSON. The server loads your template (or the built-in `sample_pages.json`),
finds each **layer id** you name, writes the fields you ask for, returns the **full
new JSON** plus a short list of which layer ids changed.

**`/api/2`** and **`/api/3b`** send non-link **text** layers plus `partner_brief` to
`marketing_text_finder.MarketingLLM` (`GEMINI_API_KEY`). Responses include `text_updates`,
`layout_validation`, and `model`.

```
python server.py
→ http://localhost:5001
```

---

## Response

Most routes:

```json
{
  "pages": [ /* full template after edits */ ],
  "updated_layer_ids": ["logo-main", "website-url"]
}
```

`/api/2` and `/api/3b` add **`text_updates`**, **`layout_validation`** (word-count repair
metadata from `MarketingLLM`), and **`model`**.

Error:

```json
{ "error": "…" }
```

---

## Built-in layer ids (same as `sample_pages.json`)

Use these ids in requests **or** pass your own `pages` array.

| Images | Texts | Shapes |
|--------|-------|--------|
| `logo-main`, `logo-secondary`, `call-icon`, `main-person`, `background-image`, `website-icon` | `website-url`, `cta-description`, … | `bottom-teal-bar`, `orange-button`, … |

`GET http://localhost:5001/` lists routes.

---

## `POST /api/apply`  (generic — use this anytime)

**Input**

| Field | Required | Meaning |
|-------|----------|---------|
| `pages` | no | Whole template array. **Omit** = use `sample_pages.json`. |
| `changes` | **yes** | List of `{ "layer_id": "…", …any field to set… }`. |

Each object must include `layer_id` and at least one other key. Those keys are
written **onto that layer** in the JSON (e.g. `text`, `image_url`, `fill`).

`fill` can be `#RRGGBB` — it is stored as `rgb(r,g,b)`.

**Example**

```json
{
  "changes": [
    { "layer_id": "logo-main",    "image_url": "https://example.com/logo.png" },
    { "layer_id": "call-icon",    "image_url": "https://example.com/qr.png" },
    { "layer_id": "website-url", "text": "https://partner.com/my-link" },
    { "layer_id": "bottom-teal-bar", "fill": "#0b2545" }
  ]
}
```

---

## `POST /api/1/logo-qr-website-colors`

Same idea, fixed field names — good for partner branding.

**Input**

| Field | Meaning |
|-------|---------|
| `pages` | optional — full template |
| `logo_layer_id` + `logo_url` | set that layer’s `image_url` |
| `qr_layer_id` + `qr_url` | set that layer’s `image_url` |
| `website_layer_id` + `website_text` | set that layer’s `text` |
| `fills` | optional: `[ { "layer_id": "bottom-teal-bar", "fill": "#0b2545" }, … ]` — use hex or `rgb(...)` |
| `primary_color` | optional hex like `#0b2545` — paints **teal bar** shapes in the sample template (`bottom-teal-bar`, `bullet-bar-1` …). Override the list with `primary_layer_ids`. |
| `accent_color` | optional hex — paints the **orange button** (`orange-button`) in the sample. Override with `accent_layer_ids`. |
| `primary_layer_ids` | optional list of layer ids — if omitted with `primary_color`, defaults to the teal bars in `sample_pages.json`. |
| `accent_layer_ids` | optional list — if omitted with `accent_color`, defaults to `["orange-button"]`. |

You must send **at least one** of: logo block, QR block, website block, `fills`, `primary_color`, or `accent_color`.

**Example (logo + QR + website + brand colors without typing every shape)**

```json
{
  "logo_layer_id": "logo-main",
  "logo_url": "https://example.com/logo.png",
  "qr_layer_id": "call-icon",
  "qr_url": "https://example.com/qr.png",
  "website_layer_id": "website-url",
  "website_text": "https://go.partner.com/you",
  "primary_color": "#0b2545",
  "accent_color": "#0fb5a7"
}
```

**Example (explicit fills instead of primary/accent shortcuts)**

```json
{
  "logo_layer_id": "logo-main",
  "logo_url": "https://example.com/logo.png",
  "qr_layer_id": "call-icon",
  "qr_url": "https://example.com/qr.png",
  "website_layer_id": "website-url",
  "website_text": "https://go.partner.com/you",
  "fills": [
    { "layer_id": "bottom-teal-bar", "fill": "#0b2545" },
    { "layer_id": "orange-button", "fill": "#0fb5a7" }
  ]
}
```

---

## `POST /api/2/ai-rewrite`

Rewrites **text** layers only (via `MarketingLLM.analyze_layers_and_suggest`). Skips the
website/link layer (`website-url`) and any layer whose entire text is a bare `http(s)`
URL. Image/shape context is not sent (text-only). The model may **skip** layers it
considers already fine — unchanged layers are omitted from `text_updates`.

**Env:** `GEMINI_API_KEY` (optional `GEMINI_MODEL`, default `gemini-3-flash-preview`).

**Body**

| Field | Required |
|-------|----------|
| `pages` | optional — omit = `sample_pages.json` |
| `partner_brief` | **yes** — at least `name`; same shape as below |
| `skip_layer_ids` | optional — extra layer ids to skip (default skips `website-url`) |

**Example**

```json
{
  "partner_brief": {
    "name": "Illinois State Black Chamber of Commerce",
    "partner_type": "chamber",
    "industry": "Business Advocacy",
    "audience": "Black-owned businesses in Illinois",
    "tone_hints": "professional, empowering",
    "brand_colors": { "primary": "#0B2545", "accent": "#0FB5A7" },
    "goal": "Drive members to scan QR and hire via remoting.work",
    "notes": "more institutional than input"
  }
}
```

---

## `POST /api/3a/find-replace`

Case‑insensitive find/replace on **non‑URL** parts of each text layer.

- Anything that looks like `http://…` or `https://…` is left alone (so links stay valid).
- The **website link layer** `website-url` is skipped by default (override with `skip_layer_ids`).

**Input**

| Field | Required |
|-------|----------|
| `pages` | optional |
| `find` | **yes** |
| `replace` | **yes** (can be `""`) |
| `skip_layer_ids` | optional list of layer ids to never change. **Default:** `["website-url"]`. Send `[]` if you only want URL‑segment protection (no whole‑layer skip). |

**Example**

```json
{
  "find": "remoting.work",
  "replace": "acme.com"
}
```

---

## `POST /api/3b/ai-with-rules`

Same as `/api/2`, but you must send **`rules`**: the model is instructed to apply
those find/replace pairs to the strings it returns (after rewriting).

**Body**

| Field | Required |
|-------|----------|
| `pages` | optional — omit = `sample_pages.json` |
| `partner_brief` | **yes** — same as `/api/2` |
| `skip_layer_ids` | optional — extra layer ids to skip (default skips `website-url`) |
| `rules` | **yes** — `[{"find":"remoting.work","replace":"acme.com"}, ...]` |

**Example**

```json
{
  "partner_brief": {
    "name": "Illinois State Black Chamber of Commerce",
    "partner_type": "chamber",
    "industry": "Business Advocacy",
    "audience": "Black-owned businesses in Illinois",
    "tone_hints": "professional, empowering",
    "goal": "Drive members to scan QR and hire via remoting.work",
    "notes": "more institutional than input"
  },
  "rules": [
    { "find": "remoting.work", "replace": "acme.com" },
    { "find": "Scan the QR", "replace": "Scan the code" }
  ]
}
```

---

## `POST /api/4/swap-images`

**Input**

| Field | Required |
|-------|----------|
| `pages` | optional |
| `images` | **yes** — object `{ "layer_id": "https://…", … }` |

**Example**

```json
{
  "images": {
    "main-person": "https://example.com/photo.jpg",
    "logo-main": "https://example.com/logo.png"
  }
}
```

---

## Using your own template

Add the full `pages` array to any request (same structure as Templated `pages`):

```json
{
  "pages": [ { "page": "page-1", "layers": { "my-layer": { "type": "text", "text": "Hi" } } } ],
  "changes": [ { "layer_id": "my-layer", "text": "Hello" } ]
}
```

---

## curl

```bash
curl -s -X POST http://localhost:5001/api/apply \
  -H "Content-Type: application/json" \
  -d '{"changes":[{"layer_id":"website-url","text":"https://example.com"}]}' | head -c 400
```
