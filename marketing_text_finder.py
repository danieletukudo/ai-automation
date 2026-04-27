"""
Marketing-focused LLM text analyzer.
Callable from server.py or standalone.
"""

import os
import json
from openai import OpenAI
from dotenv import load_dotenv

from text_layout_guard import count_words_template, filter_suggestions

load_dotenv()

# Module-level company description (can be overridden at call time)


class MarketingLLM:
    def __init__(self, model: str = "gemini-3-flash-preview"):
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        self.client = OpenAI(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url=base_url,
        )
        self.model = model

    def _strip_json_fence(self, raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw.split("```json", 1)[1].split("```", 1)[0]
        elif raw.startswith("```"):
            raw = raw.split("```", 1)[1].split("```", 1)[0]
        return raw.strip()

    def _repair_suggestions_word_count(
        self,
        bad_rows: list[dict],
        text_layers: dict,
        company_desc: str,
    ) -> list[dict]:
        """
        One follow-up LLM call: rewrite suggested_text so visible word count
        matches the original for each layer. Returns merged suggestion dicts.
        """
        if not bad_rows:
            return []

        payload = []
        for s in bad_rows:
            lid = s.get("layer_id", "")
            cur = text_layers.get(lid, s.get("current_text", ""))
            n = count_words_template(cur)
            payload.append(
                {
                    "layer_id": lid,
                    "current_text": cur,
                    "failed_suggestion": s.get("suggested_text", ""),
                    "required_visible_word_count": n,
                }
            )

        prompt = f"""You fix marketing copy for FIXED-SIZE graphic text boxes.

Company context:
{company_desc}

These layers FAILED validation because suggested_text had the wrong number of
VISIBLE words. "Visible" means: strip HTML tags and <br> for counting only; do not
count tags as words.

For EACH item below, output a NEW suggested_text that:
  1. Has EXACTLY `required_visible_word_count` visible words (count after stripping tags).
  2. Preserves the same HTML tag pattern as current_text if current_text contains
     tags (e.g. if current_text starts with <b>, your suggested_text should wrap the
     visible words the same way).
  3. Fits the company and reads naturally.

Items (JSON):
{json.dumps(payload, indent=2)}

Return ONLY valid JSON:
{{
  "fixed": [
    {{ "layer_id": "<id>", "suggested_text": "<fixed text, exact visible word count>" }}
  ]
}}
No markdown fences."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = self._strip_json_fence(response.choices[0].message.content)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        out = []
        fixed_by_id = {x["layer_id"]: x.get("suggested_text", "") for x in data.get("fixed", [])}
        for s in bad_rows:
            lid = s.get("layer_id", "")
            if lid not in fixed_by_id:
                continue
            out.append({
                "layer_id": lid,
                "current_text": text_layers.get(lid, s.get("current_text", "")),
                "suggested_text": fixed_by_id[lid],
                "reason": (s.get("reason", "") + " [word-count repaired]").strip(),
            })
        return out

    def analyze_html_and_suggest(self, html_content: str, company_desc: str) -> dict:
        """
        Ask Gemini (acting as marketing) to find every visible text element
        in the HTML and decide which ones should change to match the company.
        Returns: { "suggestions": [ { "selector", "current_text", "suggested_text", "reason" }, ... ] }
        """
        prompt = f"""
You are a senior marketing and UX copywriter. You specialize in rebranding web apps
for health-tech companies.

Company description:
{company_desc}

Your task:
1. Read through ALL visible text content in the HTML below (buttons, labels, placeholders,
   headings, status messages, aria labels, etc.).
2. For EACH piece of visible text, decide whether it should change to better reflect
   the company's mission.
3. Return a JSON object with a "suggestions" key — an array of recommendation objects.

Each recommendation must have:
  - "selector"       : CSS selector or description of where the text lives
  - "current_text"   : the exact text as it appears in the HTML
  - "suggested_text" : the text you propose to replace it with
  - "reason"         : one-line explanation of why this change fits the brand

Rules:
- Keep suggested_text short and brand-appropriate (≤ 60 chars unless it's a paragraph).
- Do NOT change code — only recommend text changes.
- If a piece of text is already brand-neutral or fine as-is, skip it.
- Return ONLY valid JSON. No markdown, no commentary outside the JSON.

HTML to analyze:
---
{html_content}
---
"""
        response = self.client.chat.completions.create(
            model="gemini-3-flash-preview",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content
        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif raw.startswith("```"):
            raw = raw.split("```")[1].split("```")[0].strip()
        return json.loads(raw)

    def analyze_layers_and_suggest(
        self,
        text_layers: dict,
        image_layers: dict,
        shape_layers: dict,
        company_desc: str,
    ) -> dict:
        """
        Given the full template context (text, image, shape layers) plus the
        company description, ask Gemini which text layers should change and
        what the replacement should be — with strict length matching.
        Returns: { "suggestions": [ { "layer_id", "current_text", "suggested_text", "word_count", "reason" }, ... ] }
        """
        text_block = "\n".join(
            f'  - Layer "{lid}" ({count_words_template(text)} visible words, {len(text)} chars): "{text}"'
            for lid, text in text_layers.items()
        )

        context_parts = []
        if image_layers:
            img_block = "\n".join(
                f'  - Layer "{lid}": {url}' for lid, url in image_layers.items()
            )
            context_parts.append(f"Image layers (for context — these show what visuals the template uses):\n{img_block}")
        if shape_layers:
            shape_block = "\n".join(
                f'  - Layer "{lid}": fill {fill}' for lid, fill in shape_layers.items()
            )
            context_parts.append(f"Shape/color layers (for context — these show the template's color scheme):\n{shape_block}")

        extra_context = "\n\n".join(context_parts)

        prompt = f"""You are a world-class marketing copywriter who specialises in rebranding
design templates for health-tech and nutrition companies. You understand that
these text layers live inside a fixed-layout graphic template where every text
box has a specific size — so the replacement text MUST fit the same space.

Company description:
{company_desc}

== FULL TEMPLATE CONTEXT ==

Text layers (these are the ones you may suggest changes for):
{text_block}

{extra_context}

Use the image URLs and color fills above to understand the overall look, feel,
and topic of this design template. This context should inform your copywriting
decisions — the new text must feel natural alongside these visuals and colors.

== YOUR TASK ==

1. Review EVERY text layer. Decide whether it should change to better market
   this company and its mission.
2. Skip layers that are already on-brand, or purely decorative (single numbers,
   dates, symbols, etc.).
3. For each layer you change, the suggested_text MUST have the SAME number of
   VISIBLE words as the original. **Visible word count** = strip all HTML tags and
   <br> tags, then count whitespace-separated words (see the counts we gave per layer).
   - If the original has 3 visible words, your suggestion must have exactly 3 visible words.
   - Preserve HTML wrappers from the original when present (e.g. <b>...</b>).
   This is critical — a mismatch will overflow the text box and ruin the design.

Return a JSON object:
{{
  "suggestions": [
    {{
      "layer_id": "<exact layer ID>",
      "current_text": "<exact original text>",
      "suggested_text": "<your replacement — SAME word count>",
      "word_count": <number of words (must match original)>,
      "reason": "<one-line explanation>"
    }}
  ]
}}

Rules:
- SAME visible word count is mandatory. Double-check before returning.
- Write punchy, compelling marketing copy — not generic filler.
- Match the tone to the visuals (professional, clinical, friendly, bold, etc.).
- Return ONLY valid JSON. No markdown fences, no extra commentary.
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```json"):
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif raw.startswith("```"):
            raw = raw.split("```")[1].split("```")[0].strip()
        data = json.loads(raw)
        suggestions = data.get("suggestions") or []
        kept, dropped = filter_suggestions(suggestions, text_layers)
        repair_meta: dict = {"dropped_after_repair": [], "repair_attempted": False}

        if dropped and os.getenv("LAYOUT_WORD_COUNT_REPAIR", "1") not in ("0", "false", "False"):
            repair_meta["repair_attempted"] = True
            fixed_list = self._repair_suggestions_word_count(dropped, text_layers, company_desc)
            fixed_kept, fixed_dropped = filter_suggestions(fixed_list, text_layers)
            kept.extend(fixed_kept)
            repair_meta["dropped_after_repair"] = fixed_dropped

        out = {"suggestions": kept, "layout_validation": repair_meta}
        if dropped:
            out["layout_validation"]["dropped_initial"] = dropped
        return out


def main():
    """Standalone run — fetches layers from the running server and prints suggestions."""
    import requests

    company_desc = (
        "Use your everyday food to manage and improve your chronic conditions—with AI.\n"
        "An AI solution for people with chronic condition. Tell our AI your sickness, and it "
        "recommends food that maintains your health and can restore or improve your condition "
        "based on your health data."
    )

    print("Fetching template layers from server...")
    res = requests.get("http://localhost:5001/api/layers", timeout=15)
    res.raise_for_status()
    pages = res.json()

    text_layers = {}
    image_layers = {}
    shape_layers = {}
    for page in pages:
        for layer_id, layer in page.get("layers", {}).items():
            if layer.get("text") is not None:
                text_layers[layer_id] = layer["text"]
            elif layer.get("image_url") is not None:
                image_layers[layer_id] = layer["image_url"]
            elif layer.get("fill") is not None:
                shape_layers[layer_id] = layer["fill"]

    if not text_layers:
        print("No text layers found.")
        return

    print(f"Found {len(text_layers)} text, {len(image_layers)} image, {len(shape_layers)} shape layers.")
    print("Analyzing with Gemini...")
    llm = MarketingLLM()
    result = llm.analyze_layers_and_suggest(text_layers, image_layers, shape_layers, company_desc)

    suggestions = result.get("suggestions", [])
    print(f"\n{'='*60}")
    print(f"  {len(suggestions)} TEXT CHANGES RECOMMENDED")
    print(f"{'='*60}\n")

    for i, s in enumerate(suggestions, 1):
        print(f"[{i}] LAYER        : {s['layer_id']}")
        print(f"    CURRENT      : {s['current_text']!r}")
        print(f"    SUGGESTED    : {s['suggested_text']!r}")
        print(f"    REASON       : {s['reason']}")
        print()

    with open("text_recommendations.json", "w") as f:
        json.dump(result, f, indent=2)
    print("Saved to text_recommendations.json")


if __name__ == "__main__":
    main()
