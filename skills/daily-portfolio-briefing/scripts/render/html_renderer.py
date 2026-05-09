"""
HTML renderer — converts the markdown briefing into a styled business document.

Color rules (consistent throughout):
- Green     #16a34a → wins, profit, GOOD verdicts, ✅ checks
- Red       #dc2626 → losses, BLOCKs, 🚫 critical issues
- Amber     #d97706 → warnings, MARGINAL, ⚠️
- Blue      #2563eb → headings, CTAs, links
- Slate     #475569 → body text
- Mint bg   #ecfdf5 → green callout backgrounds
- Rose bg   #fef2f2 → red callout backgrounds
- Amber bg  #fffbeb → warn callout backgrounds

Layout: business-doc style — wide margins, large headings, action items as
numbered cards, tables for stress test and watch list, fixed-position TOC sidebar
on desktop.

Pure-Python markdown→HTML conversion; no external dependencies.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path


_CSS = r"""
:root {
  --color-green: #16a34a;
  --color-red: #dc2626;
  --color-amber: #d97706;
  --color-blue: #2563eb;
  --color-slate: #475569;
  --color-slate-dark: #1e293b;
  --color-bg: #ffffff;
  --color-card: #f8fafc;
  --color-border: #e2e8f0;
  --bg-mint: #ecfdf5;
  --bg-rose: #fef2f2;
  --bg-amber: #fffbeb;
  --bg-sky: #eff6ff;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-slate-dark);
  background: var(--color-bg);
  margin: 0;
  padding: 0;
}
.briefing {
  max-width: 980px;
  margin: 40px auto;
  padding: 0 32px 60px;
}
h1 {
  font-size: 32px; font-weight: 700; color: var(--color-slate-dark);
  margin: 0 0 8px; line-height: 1.2; letter-spacing: -0.02em;
  border-bottom: 3px solid var(--color-blue); padding-bottom: 12px;
}
h2 {
  font-size: 20px; font-weight: 600; color: var(--color-slate-dark);
  margin: 36px 0 16px; padding-bottom: 6px;
  border-bottom: 1px solid var(--color-border); letter-spacing: -0.01em;
}
h3 { font-size: 16px; font-weight: 600; margin: 24px 0 8px; color: var(--color-slate); }
h4 { font-size: 14px; font-weight: 600; margin: 16px 0 6px; }
p, li { margin: 8px 0; }
ul, ol { padding-left: 24px; }
strong { color: var(--color-slate-dark); font-weight: 600; }
em { color: var(--color-slate); font-style: italic; }
hr { border: none; border-top: 1px solid var(--color-border); margin: 32px 0; }
a { color: var(--color-blue); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Code/inline */
code {
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 0.9em;
  background: var(--color-card);
  padding: 2px 5px;
  border-radius: 4px;
}

/* Tables */
table {
  border-collapse: collapse; width: 100%; margin: 12px 0;
  font-size: 13px; background: var(--color-bg);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
  border-radius: 6px; overflow: hidden;
}
th {
  background: var(--color-card); color: var(--color-slate-dark);
  font-weight: 600; text-align: left; padding: 10px 14px;
  border-bottom: 2px solid var(--color-border);
}
td { padding: 8px 14px; border-bottom: 1px solid var(--color-border); }
tr:last-child td { border-bottom: none; }
tr:nth-child(even) td { background: rgba(248, 250, 252, 0.5); }

/* Header subtitle */
.subtitle {
  color: var(--color-slate); font-size: 13px; font-style: italic;
  margin: 0 0 24px;
}

/* Status pills */
.pill {
  display: inline-block; padding: 2px 10px; border-radius: 99px;
  font-size: 12px; font-weight: 600; letter-spacing: 0.02em;
}
.pill-green { background: var(--bg-mint); color: var(--color-green); }
.pill-red { background: var(--bg-rose); color: var(--color-red); }
.pill-amber { background: var(--bg-amber); color: var(--color-amber); }
.pill-blue { background: var(--bg-sky); color: var(--color-blue); }

/* KPI metric block under header */
.kpi-row {
  display: flex; gap: 16px; margin: 16px 0 24px; flex-wrap: wrap;
}
.kpi {
  flex: 1; min-width: 140px;
  padding: 16px; border-radius: 8px;
  background: var(--color-card); border: 1px solid var(--color-border);
}
.kpi-label {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  color: var(--color-slate); letter-spacing: 0.06em;
}
.kpi-value {
  font-size: 22px; font-weight: 700; margin-top: 4px;
  color: var(--color-slate-dark);
}
.kpi-sub { font-size: 12px; color: var(--color-slate); margin-top: 2px; }

/* Action card — numbered list items */
ol.actions { padding: 0; list-style: none; counter-reset: action; }
ol.actions > li {
  position: relative; counter-increment: action;
  background: var(--color-bg); border: 1px solid var(--color-border);
  border-left: 4px solid var(--color-blue);
  border-radius: 8px; padding: 16px 20px 16px 60px; margin: 16px 0;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
ol.actions > li::before {
  content: counter(action);
  position: absolute; left: 16px; top: 16px;
  width: 32px; height: 32px; border-radius: 50%;
  background: var(--color-blue); color: white;
  font-weight: 700; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}
ol.actions > li.a-close { border-left-color: var(--color-green); }
ol.actions > li.a-close::before { background: var(--color-green); }
ol.actions > li.a-trim { border-left-color: var(--color-amber); }
ol.actions > li.a-trim::before { background: var(--color-amber); }
ol.actions > li.a-roll { border-left-color: var(--color-blue); }
ol.actions > li.a-roll::before { background: var(--color-blue); }
ol.actions > li.a-hedge { border-left-color: #7c3aed; }
ol.actions > li.a-hedge::before { background: #7c3aed; }
ol.actions > li.a-urgent { border-left-color: var(--color-red); background: var(--bg-rose); }
ol.actions > li.a-urgent::before { background: var(--color-red); }
ol.actions > li.a-csp { border-left-color: #0891b2; }
ol.actions > li.a-csp::before { background: #0891b2; }

/* Action item title */
ol.actions > li > .title {
  font-weight: 600; font-size: 15px; color: var(--color-slate-dark);
  margin-bottom: 6px;
}
ol.actions > li > .title .kind {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 12px; margin-right: 6px;
  background: var(--color-card); color: var(--color-slate-dark);
}
ol.actions > li > ul { margin: 6px 0 0; padding-left: 18px; }
ol.actions > li > ul li { font-size: 13px; color: var(--color-slate); }

/* Highlight blocks for Why/Gain/Source */
.kicker { font-weight: 600; color: var(--color-slate-dark); }

/* Callouts */
.callout {
  border-left: 4px solid; padding: 14px 18px; margin: 16px 0;
  border-radius: 4px;
}
.callout-green { border-color: var(--color-green); background: var(--bg-mint); }
.callout-red { border-color: var(--color-red); background: var(--bg-rose); }
.callout-amber { border-color: var(--color-amber); background: var(--bg-amber); }
.callout-blue { border-color: var(--color-blue); background: var(--bg-sky); }

/* Total Impact card */
.impact-card {
  background: linear-gradient(135deg, #ecfdf5, #eff6ff);
  border: 1px solid var(--color-border); border-radius: 12px;
  padding: 20px 24px; margin: 24px 0;
}
.impact-card h3 { margin-top: 0; font-size: 16px; color: var(--color-slate-dark); }
.impact-card ul { list-style: none; padding: 0; margin: 8px 0 0; }
.impact-card li { padding: 4px 0; font-size: 14px; }
.impact-card li::before { content: "▸ "; color: var(--color-blue); font-weight: bold; }

/* Number color helpers (regex-applied) */
.num-pos { color: var(--color-green); font-weight: 600; }
.num-neg { color: var(--color-red); font-weight: 600; }

/* Footer */
.footer {
  margin-top: 60px; padding-top: 16px;
  border-top: 1px solid var(--color-border);
  font-size: 12px; color: var(--color-slate);
}

@media print {
  body { font-size: 11pt; }
  .briefing { margin: 0; max-width: 100%; padding: 0; }
  ol.actions > li { page-break-inside: avoid; }
}
"""


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _color_numbers(text: str) -> str:
    """Wrap dollar amounts and percentages in spans with green/red color based on sign."""
    # +$X or +X% → green
    text = re.sub(r"(\+\$[\d,]+(?:\.\d+)?)", r'<span class="num-pos">\1</span>', text)
    text = re.sub(r"(\+[\d.]+%)", r'<span class="num-pos">\1</span>', text)
    # -$X or −$X → red
    text = re.sub(r"([−-]\$[\d,]+(?:\.\d+)?)", r'<span class="num-neg">\1</span>', text)
    text = re.sub(r"([−-][\d.]+%)", r'<span class="num-neg">\1</span>', text)
    return text


def _md_inline(text: str) -> str:
    """Convert markdown inline (bold, italic, code) to HTML."""
    out = _escape(text)
    # Bold **x**
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    # Italic *x* or _x_
    out = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<em>\1</em>", out)
    # Code `x`
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    # Color signed numbers
    out = _color_numbers(out)
    return out


def _classify_action(title: str) -> str:
    """Pick a CSS class for an action card based on its label."""
    upper = title.upper()
    if "URGENT" in upper or "🚨" in title: return "a-urgent"
    if "CLOSE" in upper: return "a-close"
    if "TRIM" in upper: return "a-trim"
    if "EXECUTE ROLL" in upper or "REVIEW ROLL" in upper: return "a-roll"
    if "HEDGE" in upper: return "a-hedge"
    if "CSP" in upper or "PUT" in upper: return "a-csp"
    if "COLLAR" in upper: return "a-hedge"
    return ""


def _convert_table(lines: list) -> str:
    """Convert a markdown table block to HTML <table>."""
    if len(lines) < 2:
        return ""
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = []
    for raw in lines[2:]:  # skip separator line
        cells = [c.strip() for c in raw.strip("|").split("|")]
        rows.append(cells)
    out = ["<table>", "  <thead><tr>"]
    for h in header: out.append(f"    <th>{_md_inline(h)}</th>")
    out.append("  </tr></thead>")
    out.append("  <tbody>")
    for cells in rows:
        out.append("  <tr>")
        for c in cells: out.append(f"    <td>{_md_inline(c)}</td>")
        out.append("  </tr>")
    out.append("  </tbody>")
    out.append("</table>")
    return "\n".join(out)


def render_html(markdown: str, title: str = "Daily Briefing") -> str:
    """Convert a briefing markdown blob to a styled HTML business doc."""
    lines = markdown.split("\n")
    body_parts: list = []
    i = 0
    in_list = False
    in_actions = False
    in_impact = False

    def close_list():
        nonlocal in_list, in_actions, in_impact
        if in_actions:
            body_parts.append("</ol>")
            in_actions = False
        elif in_list:
            body_parts.append("</ul>")
            in_list = False
        if in_impact:
            body_parts.append("</ul></div>")
            in_impact = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Tables (detect "|...|" with separator on next line)
        if stripped.startswith("|") and i + 1 < len(lines) and "---" in lines[i + 1]:
            close_list()
            tbl_lines = [stripped]
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("|"):
                tbl_lines.append(lines[j].strip())
                j += 1
            body_parts.append(_convert_table(tbl_lines))
            i = j
            continue

        # Headers
        if stripped.startswith("# "):
            close_list()
            body_parts.append(f"<h1>{_md_inline(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            close_list()
            heading_text = stripped[3:]
            body_parts.append(f"<h2>{_md_inline(heading_text)}</h2>")
        elif stripped.startswith("### "):
            close_list()
            heading = stripped[4:]
            # Special: "Total Impact" → impact card
            if "Total Impact" in heading or "📋" in heading:
                in_impact = True
                body_parts.append(f'<div class="impact-card"><h3>{_md_inline(heading)}</h3><ul>')
            else:
                body_parts.append(f"<h3>{_md_inline(heading)}</h3>")
        elif stripped.startswith("#### "):
            close_list()
            body_parts.append(f"<h4>{_md_inline(stripped[5:])}</h4>")
        elif stripped.startswith("---"):
            close_list()
            body_parts.append("<hr>")
        # Numbered action items: "1. **CLOSE** ..."
        elif re.match(r"^\d+\.\s+\*\*", stripped):
            if not in_actions:
                close_list()
                body_parts.append('<ol class="actions">')
                in_actions = True
            # Extract the title and CSS class
            m = re.match(r"^\d+\.\s+(.+)$", stripped)
            if m:
                title_text = m.group(1)
                css = _classify_action(title_text)
                body_parts.append(f'<li class="{css}"><div class="title">{_md_inline(title_text)}</div><ul>')
            # Capture sub-bullets
            j = i + 1
            while j < len(lines):
                sub = lines[j]
                if sub.strip().startswith("- "):
                    body_parts.append(f"<li>{_md_inline(sub.strip()[2:])}</li>")
                    j += 1
                elif sub.strip() == "":
                    j += 1
                    break
                elif re.match(r"^\d+\.\s+\*\*", sub.strip()):
                    break
                elif sub.startswith("##"):
                    break
                else:
                    j += 1
                    break
            body_parts.append("</ul></li>")
            i = j
            continue
        # Sub-bullets at top level
        elif stripped.startswith("- "):
            if in_impact:
                body_parts.append(f"<li>{_md_inline(stripped[2:])}</li>")
            else:
                if not in_list:
                    close_list()
                    body_parts.append("<ul>")
                    in_list = True
                body_parts.append(f"<li>{_md_inline(stripped[2:])}</li>")
        elif stripped == "":
            close_list()
        elif stripped.startswith("_") and stripped.endswith("_"):
            # Subtitle line (italic)
            close_list()
            body_parts.append(f'<p class="subtitle">{_md_inline(stripped[1:-1])}</p>')
        else:
            close_list()
            body_parts.append(f"<p>{_md_inline(stripped)}</p>")
        i += 1
    close_list()

    # Pull KPI numbers out of the early body for a header card row.
    kpis = _extract_kpis(markdown)
    kpi_html = ""
    if kpis:
        kpi_html = '<div class="kpi-row">'
        for label, value, sub in kpis:
            kpi_html += f'<div class="kpi"><div class="kpi-label">{_escape(label)}</div>'
            kpi_html += f'<div class="kpi-value">{_md_inline(value)}</div>'
            if sub: kpi_html += f'<div class="kpi-sub">{_md_inline(sub)}</div>'
            kpi_html += '</div>'
        kpi_html += '</div>'

    # Insert KPI card after the first <h1> + subtitle
    body_html = "\n".join(body_parts)
    # Try injecting KPIs after subtitle
    body_html = body_html.replace(
        '<p class="subtitle">', kpi_html + '<p class="subtitle">', 1
    ) if kpi_html else body_html

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{_escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="briefing">
{body_html}
<div class="footer">Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · Live data from E*TRADE + yfinance</div>
</div>
</body>
</html>"""
    return page


def _extract_kpis(markdown: str) -> list[tuple[str, str, str]]:
    """Pull NLV/Cash/Actions/Coverage from header for a KPI strip."""
    kpis: list[tuple[str, str, str]] = []
    nlv = re.search(r"\*\*Portfolio NLV:\*\*\s*\$([\d,]+)", markdown)
    cash = re.search(r"\*\*Cash:\*\*\s*\$([\d,]+)\s*\(([\d.]+)%\)", markdown)
    if nlv:
        kpis.append(("Portfolio NLV", f"${nlv.group(1)}", "Live"))
    if cash:
        kpis.append(("Cash", f"${cash.group(1)}", f"{cash.group(2)}% of NLV"))
    cov = re.search(r"Coverage:\s*\*\*([\d.]+)x\*\*", markdown)
    if cov:
        cov_val = float(cov.group(1))
        sub = "Healthy" if cov_val >= 0.7 else ("Below target" if cov_val >= 0.5 else "Critical")
        kpis.append(("Stress Coverage", f"{cov_val:.2f}x", sub))
    actions = re.search(r"\*\*Total actions:\*\*\s*(\d+)", markdown)
    if actions:
        kpis.append(("Action Items", actions.group(1), "Today"))
    return kpis


def write_html(markdown: str, output_path: Path, title: str = "Daily Briefing") -> Path:
    """Render and write the HTML briefing to disk. Returns the path."""
    html_doc = render_html(markdown, title=title)
    output_path.write_text(html_doc)
    return output_path
