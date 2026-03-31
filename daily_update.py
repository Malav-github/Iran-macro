"""
daily_update.py
===============
Fetches latest Iran conflict macro data via Anthropic Claude API,
updates the Iran dashboard HTML, and saves it for deployment.

Run manually:  python daily_update.py
GitHub Actions runs this on a daily cron schedule.

SETUP (local):
  pip install anthropic python-dotenv markdown
  Add ANTHROPIC_API_KEY to .env file
"""

import anthropic
import os
import re
import json
import datetime
from pathlib import Path
from dotenv import load_dotenv
import markdown as md

load_dotenv()  # no-op in GitHub Actions; reads .env locally


# ─── CONFIG ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL             = "claude-sonnet-4-20250514"
OUTPUT_FILE       = Path("index.html")
DATA_FILE         = Path("data.json")
IST_OFFSET        = datetime.timedelta(hours=5, minutes=30)


# ─── PROMPTS ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a verified financial research platform producing a daily
macro intelligence briefing on the Iran conflict and its global
economic impact.

RULES — follow without exception:
- Every claim must have a source name, URL, and date
- Format all source citations as markdown links: [Source: [name](url) · date]
- Distinguish verified (2+ sources) from corroborated (1 source)
- Flag unverified claims with ⚠
- No buy/sell recommendations
- Never fabricate a figure or URL — if a URL is uncertain, omit it
- If data is unavailable, say so clearly
- Do NOT include any disclaimer text in your response
- Do NOT add any introduction before section 1
- Always end your response with the ---DATA--- JSON block
- Do NOT wrap the ---DATA--- block in markdown code fences
"""

DAILY_PROMPT = """
Produce today's Iran conflict macro intelligence briefing.
Today's date: {date}

Include these sections:

1. SITUATION UPDATE (2-3 sentences — what changed in last 24 hours)

2. KEY METRICS (label every figure with source and date)
   - Brent crude price and % change
   - Hormuz traffic (vessels or transits per day if available)
   - USD/INR rate
   - S&P 500 level
   - Gold price

3. ENERGY IMPACT
   - Hormuz status
   - IEA response if any
   - Supply disruption estimate

4. INDIA IMPACT
   - Rupee status
   - Fuel price situation
   - Aviation impact if relevant

5. SCENARIO OUTLOOK
   - Base case (short conflict)
   - Extended scenario
   - Key trigger to watch this week

6. TOP 3 SOURCES TODAY
   For each: source name, headline, date,
   verification verdict (VERIFIED/CORROBORATED/UNVERIFIED)

End your response with this exact block — no code fences, no extra text after it:
---DATA---
{{
  "brent_price": "e.g. $112.78/bbl or null",
  "brent_change": "e.g. +55% MTD or null",
  "hormuz_vessels": "short value e.g. ~5-6/day or null",
  "usd_inr": "e.g. ₹95.07 or null",
  "sp500": "e.g. 6,391 or null",
  "gold": "e.g. $4,568/oz or null",
  "situation_summary": "2-3 sentences, plain text only, no markdown, no asterisks, max 400 chars",
  "key_trigger": "one plain-text sentence, max 200 chars",
  "last_updated": "{datetime}"
}}
---END DATA---
"""


# ─── CLAUDE API CALL ─────────────────────────────────────────────────────────

def fetch_briefing() -> tuple[str, dict]:
    """Call Claude API, return (briefing_markdown, data_dict)."""
    if not ANTHROPIC_API_KEY:
        raise ValueError(
            "ANTHROPIC_API_KEY not found. "
            "Add it to your .env file or GitHub Secrets."
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    now      = datetime.datetime.utcnow()
    ist      = now + IST_OFFSET
    date_str = ist.strftime("%A, %d %B %Y")
    dt_str   = ist.strftime("%Y-%m-%d %H:%M IST")

    prompt = DAILY_PROMPT.format(date=date_str, datetime=dt_str)

    print(f"  → Calling Claude API ({MODEL})...")

    response = client.messages.create(
        model=MODEL,
        max_tokens=5000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )

    # Collect only text blocks (skip tool_use, tool_result blocks)
    content = ""
    for block in response.content:
        if block.type == "text":
            content += block.text

    print(f"  ✓ Response received ({len(content)} chars)")
    print(f"  ✓ Tokens: {response.usage.input_tokens} in "
          f"+ {response.usage.output_tokens} out")

    # ── Parse structured DATA block ──────────────────────────────────────────
    data = {}
    dt_str = (datetime.datetime.utcnow() + IST_OFFSET).strftime("%Y-%m-%d %H:%M IST")

    if "---DATA---" in content:
        try:
            raw = content.split("---DATA---")[1].split("---END DATA---")[0].strip()
            # Strip any accidental code fences Claude may have added
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
            data = json.loads(raw)
            print("  ✓ Data block parsed")
        except Exception as e:
            print(f"  ⚠ JSON parse failed: {e} — using regex fallback")

    # ── Regex fallbacks ───────────────────────────────────────────────────────

    if not data.get("brent_price"):
        m = re.search(
            r'\$\s*(1[0-9]{2}(?:\.\d{1,2})?)\s*(?:/bbl|per barrel)',
            content, re.IGNORECASE
        )
        if m:
            data["brent_price"] = f"${m.group(1)}/bbl"

    if not data.get("usd_inr"):
        m = re.search(
            r'(?:USD/INR|₹|INR)[^\d]*(\d{2}\.\d{1,2})',
            content, re.IGNORECASE
        )
        if m:
            data["usd_inr"] = f"₹{m.group(1)}"

    if not data.get("sp500"):
        # Handles comma-formatted numbers (6,391) and HTML-escaped S&amp;P
        m = re.search(
            r'S(?:&amp;|&)P\s*500[^\d]*(\d{1,2},\d{3}(?:\.\d{1,2})?|\d{4,5}(?:\.\d{1,2})?)',
            content, re.IGNORECASE
        )
        if m:
            data["sp500"] = m.group(1)

    if not data.get("gold"):
        m = re.search(
            r'[Gg]old[^\$]*\$\s*(\d[\d,]+(?:\.\d{1,2})?)',
            content, re.IGNORECASE
        )
        if m:
            data["gold"] = f"${m.group(1)}/oz"

    if not data.get("hormuz_vessels"):
        # Matches "~5-6/day", "~5-6 vessels/day", "~5-6 transits/day" etc.
        m = re.search(
            r'(~?[\d\u2013\-\s]+)\s*(?:vessels?|transits?)?\s*/\s*day',
            content, re.IGNORECASE
        )
        if m:
            val = m.group(1).strip()
            data["hormuz_vessels"] = val if val.endswith("/day") else val + "/day"

    if not data.get("situation_summary"):
        # Grab the full paragraph after "SITUATION UPDATE"
        m = re.search(
            r'SITUATION UPDATE.*?\n+(.*?)(?=\n\[Source:|\n\n)',
            content, re.IGNORECASE | re.DOTALL
        )
        if m:
            data["situation_summary"] = m.group(1).strip()

    data["last_updated"] = dt_str

    # Strip data block and any trailing code fences from display content
    display = content.split("---DATA---")[0].strip()
    display = re.sub(r'\n?```(?:json)?\s*$', '', display).strip()

    return display, data


# ─── HTML BUILDER ────────────────────────────────────────────────────────────

def build_html(briefing: str, data: dict) -> str:
    """Build the complete dashboard HTML."""
    ist     = datetime.datetime.utcnow() + IST_OFFSET
    updated = ist.strftime("%d %b %Y · %H:%M IST")

    briefing_html = md.markdown(
        briefing,
        extensions=["tables", "fenced_code"]
    )

    def card(label, value, sub=""):
        val = value or "—"
        # Split parenthetical note to sub-line e.g. "~5-6/day (vs. avg 138)"
        if val != "—" and " (" in str(val):
            main, note = val.split(" (", 1)
            val = main.strip()
            sub = "(" + note.strip()
        cls = ""
        if val != "—":
            if "+" in str(val):
                cls = "up"
            elif str(val).startswith("-"):
                cls = "down"
        return (
            f'<div class="metric-card">'
            f'<div class="mc-label">{label}</div>'
            f'<div class="mc-val {cls}">{val}</div>'
            f'<div class="mc-sub">{sub}</div>'
            f'</div>'
        )

    metrics_html = (
        card("Brent Crude",    data.get("brent_price"), data.get("brent_change", "")) +
        card("Hormuz Traffic", data.get("hormuz_vessels")) +
        card("USD / INR",      data.get("usd_inr")) +
        card("S&P 500",        data.get("sp500")) +
        card("Gold",           data.get("gold"))
    )

    situation = data.get("situation_summary", "")
    trigger   = data.get("key_trigger", "")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Iran Conflict — Daily Macro Intelligence · {ist.strftime("%d %b %Y")}</title>
<meta name="description" content="Daily verified macro intelligence on the Iran conflict — energy, markets, and India impact. Updated every morning at 6:00 AM IST.">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{
  --bg:#0d0f14;--bg2:#13161e;--bg3:#1a1e28;
  --border:#ffffff12;--text:#e8eaf0;--muted:#8890a4;
  --accent:#e8593c;--amber:#f0a050;--green:#4caf82;
  --red:#e85c5c;--blue:#5b9bd5;
  --font:system-ui,-apple-system,sans-serif;
}}
body{{background:var(--bg);color:var(--text);
      font-family:var(--font);font-size:14px;
      line-height:1.6;padding-bottom:40px}}
header{{background:var(--bg2);border-bottom:1px solid var(--border);
        padding:14px 20px;position:sticky;top:0;z-index:10}}
.hi{{max-width:900px;margin:0 auto;display:flex;
     justify-content:space-between;align-items:center;
     flex-wrap:wrap;gap:8px}}
.logo{{font-size:14px;font-weight:700}}
.logo span{{color:var(--accent)}}
.upd{{font-size:11px;color:var(--muted);background:var(--bg3);
       padding:3px 10px;border-radius:20px;border:1px solid var(--border)}}
.disc{{background:var(--bg3);border-left:3px solid var(--amber);
       padding:10px 20px;font-size:11px;color:var(--muted)}}
.disc strong{{color:var(--text)}}
.wrap{{max-width:900px;margin:0 auto;padding:0 20px}}
.alert{{background:#e8593c12;border:1px solid #e8593c30;
        border-left:3px solid var(--accent);
        border-radius:0 6px 6px 0;padding:12px 16px;
        margin:20px 0;font-size:13px;line-height:1.6}}
.al{{font-size:11px;font-weight:700;color:var(--accent);
     margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em}}
.metrics{{display:grid;grid-template-columns:repeat(5,1fr);
           gap:1px;background:var(--border);
           border:1px solid var(--border);
           border-radius:8px;overflow:hidden;margin:20px 0}}
.metric-card{{background:var(--bg2);padding:14px 16px}}
.mc-label{{font-size:10px;color:var(--muted);
            text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px}}
.mc-val{{font-size:20px;font-weight:700;margin-bottom:2px}}
.mc-sub{{font-size:10px;color:var(--muted);line-height:1.4}}
.up{{color:var(--red)}}.down{{color:var(--green)}}
.trigger{{background:var(--bg2);border:1px solid var(--border);
           border-left:3px solid var(--blue);
           border-radius:0 6px 6px 0;padding:10px 16px;
           margin:0 0 20px;font-size:13px}}
.tl{{font-size:10px;font-weight:700;color:var(--blue);
     margin-bottom:3px;text-transform:uppercase;letter-spacing:.08em}}
.briefing{{background:var(--bg2);border:1px solid var(--border);
            border-radius:10px;padding:24px;margin:20px 0}}
.briefing h1{{font-size:18px;font-weight:700;color:var(--text);
               margin:0 0 16px;padding-bottom:10px;
               border-bottom:1px solid var(--border)}}
.briefing h2{{font-size:14px;font-weight:700;color:var(--text);
               margin:20px 0 10px;padding-bottom:6px;
               border-bottom:1px solid var(--border);
               display:flex;align-items:center;gap:8px}}
.briefing h2::before{{content:'';display:block;width:3px;height:14px;
                       border-radius:2px;background:var(--accent);flex-shrink:0}}
.briefing h3{{font-size:13px;font-weight:600;color:var(--muted);
               margin:14px 0 6px;text-transform:uppercase;letter-spacing:.06em}}
.briefing p{{font-size:13px;color:var(--muted);margin-bottom:10px;line-height:1.7}}
.briefing ul,.briefing ol{{margin:0 0 12px 20px;color:var(--muted)}}
.briefing li{{font-size:13px;padding:3px 0;line-height:1.6}}
.briefing strong{{color:var(--text);font-weight:600}}
.briefing em{{color:var(--amber);font-style:normal}}
.briefing hr{{border:none;border-top:1px solid var(--border);margin:16px 0}}
.briefing a{{color:var(--blue);text-decoration:none}}
.briefing a:hover{{text-decoration:underline}}
.briefing table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:12px}}
.briefing th{{text-align:left;padding:8px 10px;font-size:11px;font-weight:600;
               color:var(--muted);border-bottom:1px solid var(--border);background:var(--bg3)}}
.briefing td{{padding:8px 10px;border-bottom:1px solid var(--border);
               color:var(--muted);vertical-align:top}}
.briefing td a{{color:var(--blue);text-decoration:none}}
.briefing td a:hover{{text-decoration:underline}}
.briefing tr:last-child td{{border-bottom:none}}
.briefing tr:hover td{{background:#ffffff04}}
.briefing blockquote{{border-left:3px solid var(--blue);padding:8px 14px;
                       margin:10px 0;background:var(--bg3);border-radius:0 6px 6px 0}}
.briefing blockquote p{{color:var(--muted);margin:0}}
footer{{max-width:900px;margin:30px auto 0;padding:20px;
         border-top:1px solid var(--border);
         font-size:11px;color:var(--muted);line-height:1.8}}
@media(max-width:480px){{
  .metrics{{grid-template-columns:1fr 1fr}}
  .mc-val{{font-size:16px}}
  .briefing{{padding:16px}}
}}
</style>
</head>
<body>

<header>
  <div class="hi">
    <div class="logo">Iran Conflict · <span>Daily Macro</span></div>
    <div class="upd">Updated {updated}</div>
  </div>
</header>

<div class="disc">
  <strong>⚠ Disclaimer:</strong> For informational purposes only.
  Not investment advice. All claims sourced as indicated.
  Updated once daily to ensure full verification before publication.
</div>

<div class="wrap">

  {f'<div class="alert"><div class="al">⚡ Situation today</div>{situation}</div>' if situation else ''}

  <div class="metrics">{metrics_html}</div>

  {f'<div class="trigger"><div class="tl">📌 Key trigger to watch</div>{trigger}</div>' if trigger else ''}

  <div class="briefing">{briefing_html}</div>

</div>

<footer>
  <strong>About:</strong> Daily verified macro intelligence on the Iran conflict.
  Every claim is sourced and cited. Updated once daily at 6:00 AM IST.
  <br><br>
  Not affiliated with any financial institution. Not investment advice.
</footer>

</body>
</html>"""


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 50)
    print("  Iran Conflict — Daily Macro Update")
    print(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 50)

    print("\n[1/3] Fetching briefing from Claude...")
    try:
        briefing, data = fetch_briefing()
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return

    print("\n[2/3] Building dashboard HTML...")
    html = build_html(briefing, data)
    print(f"  ✓ HTML built ({len(html):,} chars)")

    print("\n[3/3] Saving files...")
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  ✓ {OUTPUT_FILE}")
    print(f"  ✓ {DATA_FILE}")

    print("\n" + "═" * 50)
    print("  ✓ Update complete")
    print(f"  Open {OUTPUT_FILE} in your browser to preview")
    print("═" * 50 + "\n")


if __name__ == "__main__":
    main()
