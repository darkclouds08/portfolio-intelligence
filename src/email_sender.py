"""
src/email_sender.py
====================
Builds and sends the daily portfolio digest email.

Three sections:
  1. PRIORITY ACTIONS  — Gemini analysis. Sorted by priority then invested amount.
                         Indian stocks shown first within same priority tier.
  2. NEWS FEED         — Collapsible per-stock blocks using <details><summary>.
                         No summaries here (Section 1 covers that).
                         Just headline + source + date + link.
                         Ordered: HIGH priority stocks first, then by invested amount.
  3. PORTFOLIO PULSE   — Pure math. Sector sentiment, movers, no-news list.

Mobile-first HTML. News window shown in header.
"""

import sys
import os
import smtplib
import logging
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    SENDER_EMAIL,
    RECIPIENT_EMAIL,
    GMAIL_APP_PASSWORD,
    NEWS_DAYS_BACK,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Style constants
# ─────────────────────────────────────────────

PRIORITY_STYLE = {
    "HIGH":   {"bg": "#FFF0F0", "border": "#E53935", "badge": "#E53935", "icon": "🔴"},
    "MEDIUM": {"bg": "#FFFDE7", "border": "#F9A825", "badge": "#F9A825", "icon": "🟡"},
    "LOW":    {"bg": "#F1F8E9", "border": "#558B2F", "badge": "#558B2F", "icon": "🟢"},
}

SENTIMENT_ICON = {"positive": "📈", "negative": "📉", "neutral": "➡️"}

ACTION_LABEL = {
    "hold":               "✅ Hold",
    "watch":              "👁 Watch",
    "research_exit":      "⚠️ Consider Exit",
    "research_buy_more":  "💡 Consider Adding",
    "no_news":            "🔇 No News",
}

THESIS_COLOR = {
    "intact":   "#2E7D32",
    "weakened": "#E65100",
    "broken":   "#B71C1C",
    "unclear":  "#757575",
}


def fmt_invested(result: dict) -> str:
    """Formats invested amount for display."""
    inv = result.get("invested_inr")
    if inv:
        return f"₹{inv:,.0f} invested"
    return ""


# ─────────────────────────────────────────────
# Section 1 — Priority Actions
# ─────────────────────────────────────────────

def build_analysis_card(result: dict) -> str:
    """Single stock analysis card. No article text — just Gemini's synthesis."""
    priority = result.get("priority", "LOW")
    style = PRIORITY_STYLE.get(priority, PRIORITY_STYLE["LOW"])

    ticker     = result.get("ticker", "")
    name       = result.get("name", ticker)
    market     = result.get("market", "")
    sector     = result.get("sector", "")
    sentiment  = result.get("sentiment", "neutral")
    summary    = result.get("summary", "No summary.")
    action     = result.get("action_hint", "no_news")
    thesis     = result.get("thesis_status", "unclear")
    gain_pct   = result.get("gain_pct")
    invested   = fmt_invested(result)

    gain_str   = f"{gain_pct:+.1f}%" if gain_pct is not None else "N/A"
    gain_color = "#2E7D32" if (gain_pct or 0) >= 0 else "#C62828"
    flag       = "🇮🇳" if market == "IN" else "🇺🇸"
    s_icon     = SENTIMENT_ICON.get(sentiment, "➡️")
    a_label    = ACTION_LABEL.get(action, action)
    t_color    = THESIS_COLOR.get(thesis, "#757575")

    return f"""
<div style="border:1px solid {style['border']};border-left:4px solid {style['border']};
     border-radius:8px;background:{style['bg']};padding:14px 16px;margin:8px 0;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;
       flex-wrap:wrap;gap:6px;">
    <div>
      <span style="font-size:15px;font-weight:700;">{flag} {ticker}</span>
      <span style="color:#555;font-size:12px;margin-left:6px;">{name}</span>
      <span style="color:#999;font-size:11px;margin-left:6px;">• {sector}</span>
    </div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
      <span style="background:{style['badge']};color:#fff;padding:2px 8px;
            border-radius:10px;font-size:10px;font-weight:700;">{priority}</span>
      <span style="font-weight:700;font-size:14px;color:{gain_color};">{gain_str}</span>
      <span>{s_icon}</span>
    </div>
  </div>
  {f'<div style="font-size:11px;color:#888;margin:4px 0;">{invested}</div>' if invested else ''}
  <p style="margin:10px 0 8px;font-size:13px;color:#333;line-height:1.6;">{summary}</p>
  <div style="display:flex;gap:16px;font-size:12px;flex-wrap:wrap;">
    <span>Action: <strong>{a_label}</strong></span>
    <span style="color:{t_color};">Thesis: <strong>{thesis}</strong></span>
  </div>
</div>"""


def build_section1_html(analysis_results: list[dict]) -> str:
    """
    Section 1: Priority Actions.
    Groups: HIGH → MEDIUM → LOW.
    Within each group sorted by invested_inr (Indian first at same level).
    Skips stocks where action is no_news AND sentiment is neutral (nothing to say).
    """
    def has_real_news(r):
        return not (r.get("action_hint") == "no_news" and r.get("sentiment") == "neutral")

    def sort_key(r):
        # Indian first (market=IN), then by invested amount descending
        market_boost = 0 if r.get("market") == "IN" else 1
        return (market_boost, -(r.get("invested_inr") or 0))

    high   = sorted([r for r in analysis_results if r.get("priority") == "HIGH"   and has_real_news(r)], key=sort_key)
    medium = sorted([r for r in analysis_results if r.get("priority") == "MEDIUM" and has_real_news(r)], key=sort_key)
    low    = sorted([r for r in analysis_results if r.get("priority") == "LOW"    and has_real_news(r)], key=sort_key)

    def group_html(label, color, results):
        if not results:
            return ""
        cards = "".join(build_analysis_card(r) for r in results)
        return f"""
<h3 style="color:{color};margin:20px 0 8px;font-size:14px;font-weight:700;
     border-bottom:2px solid {color}33;padding-bottom:6px;">
  {label} <span style="color:#aaa;font-weight:400;font-size:12px;">({len(results)})</span>
</h3>{cards}"""

    content = (
        group_html("🔴 HIGH PRIORITY — Action Required", "#C62828", high) +
        group_html("🟡 MEDIUM PRIORITY — Keep Watching",  "#E65100", medium) +
        group_html("🟢 LOW PRIORITY — All Clear",         "#2E7D32", low)
    )

    if not content.strip():
        content = '<p style="color:#999;font-style:italic;padding:8px 0;">No significant news today.</p>'

    return f"""
<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;
     box-shadow:0 1px 4px rgba(0,0,0,0.07);">
  <h2 style="margin:0 0 4px;font-size:17px;color:#1a1a2e;">📊 Section 1 — Priority Actions</h2>
  <p style="margin:0 0 16px;font-size:12px;color:#999;">
    AI analysis sorted by urgency → investment size. Indian stocks prioritised.
  </p>
  {content}
</div>"""


# ─────────────────────────────────────────────
# Section 2 — News Feed (pure Python, no AI)
# Collapsible per-stock using <details><summary>
# Headlines only — no summaries (Section 1 covers that)
# ─────────────────────────────────────────────

def build_article_link_row(article: dict) -> str:
    """Single article — just title as link + source + time. No summary."""
    title  = article.get("title", "Untitled")
    link   = article.get("link", "#")
    source = article.get("source", "")
    pub    = article.get("published")

    if pub:
        now = datetime.now(timezone.utc)
        if pub.date() == now.date():
            time_str = pub.strftime("%I:%M %p UTC")
        else:
            time_str = pub.strftime("%b %d %I:%M %p UTC")
    else:
        time_str = "recent"

    return f"""
<div style="padding:8px 0;border-bottom:1px solid #f5f5f5;">
  <a href="{link}" style="color:#1565C0;font-size:13px;font-weight:500;
     text-decoration:none;line-height:1.5;" target="_blank">{title} ↗</a>
  <div style="margin-top:2px;font-size:11px;color:#aaa;">{source} &nbsp;•&nbsp; {time_str}</div>
</div>"""


def build_collapsible_stock_news(
    stock: dict,
    articles: list[dict],
    priority: str = "LOW",
) -> str:
    """
    One collapsible block per stock using <details><summary>.
    Works in Gmail and most mobile email clients without JavaScript.
    Header shows ticker, name, gain%, article count.
    Click to expand and see headlines.
    """
    ticker   = stock.get("ticker", "")
    name     = stock.get("name", ticker)
    market   = stock.get("market", "")
    gain_pct = stock.get("gain_pct")
    invested = stock.get("invested_inr")

    flag       = "🇮🇳" if market == "IN" else "🇺🇸"
    gain_str   = f"{gain_pct:+.1f}%" if gain_pct is not None else ""
    gain_color = "#2E7D32" if (gain_pct or 0) >= 0 else "#C62828"
    inv_str    = f" · ₹{invested:,.0f}" if invested else ""
    style      = PRIORITY_STYLE.get(priority, PRIORITY_STYLE["LOW"])
    n          = len(articles)

    article_rows = "".join(build_article_link_row(a) for a in articles)

    return f"""
<details style="margin-bottom:10px;border:1px solid {style['border']};
        border-radius:8px;overflow:hidden;">
  <summary style="cursor:pointer;padding:11px 14px;background:{style['bg']};
           border-left:4px solid {style['border']};
           list-style:none;display:flex;justify-content:space-between;
           align-items:center;flex-wrap:wrap;gap:4px;user-select:none;">
    <div>
      <span style="font-weight:700;font-size:13px;">{flag} {ticker}</span>
      <span style="color:#666;font-size:12px;margin-left:6px;">{name}</span>
    </div>
    <div style="display:flex;gap:8px;align-items:center;">
      <span style="color:{gain_color};font-weight:700;font-size:13px;">{gain_str}</span>
      <span style="font-size:11px;color:#888;">{inv_str}</span>
      <span style="background:{style['badge']};color:#fff;font-size:10px;
            padding:2px 7px;border-radius:8px;">{n} article{'s' if n>1 else ''} ▾</span>
    </div>
  </summary>
  <div style="padding:4px 14px 10px;background:#fff;">
    {article_rows}
  </div>
</details>"""


def build_section2_html(filtered_news: dict, analysis_results: list[dict]) -> str:
    """
    Section 2: Raw News Feed.
    Ordered: HIGH priority first, then MEDIUM, then LOW.
    Within each tier: Indian stocks first, then by invested amount.
    Each stock is a collapsible dropdown.
    """
    priority_map  = {r.get("ticker"): r.get("priority", "LOW") for r in analysis_results}
    invested_map  = {r.get("ticker"): (r.get("invested_inr") or 0) for r in analysis_results}
    market_map    = {r.get("ticker"): r.get("market", "US") for r in analysis_results}

    stocks_with_news = {t: d for t, d in filtered_news.items() if d["articles"]}

    if not stocks_with_news:
        return """
<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;">
  <h2 style="margin:0 0 16px;font-size:17px;color:#1a1a2e;">📰 Section 2 — News Feed</h2>
  <p style="color:#999;font-style:italic;">No articles found in the last 24 hours.</p>
</div>"""

    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    def sort_key(ticker):
        p = priority_order.get(priority_map.get(ticker, "LOW"), 2)
        m = 0 if market_map.get(ticker) == "IN" else 1   # Indian first
        i = -(invested_map.get(ticker) or 0)
        return (p, m, i)

    sorted_tickers = sorted(stocks_with_news.keys(), key=sort_key)

    blocks = []
    for ticker in sorted_tickers:
        data     = stocks_with_news[ticker]
        priority = priority_map.get(ticker, "LOW")
        blocks.append(
            build_collapsible_stock_news(data["stock"], data["articles"], priority)
        )

    total = sum(len(d["articles"]) for d in stocks_with_news.values())

    return f"""
<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;
     box-shadow:0 1px 4px rgba(0,0,0,0.07);">
  <h2 style="margin:0 0 4px;font-size:17px;color:#1a1a2e;">📰 Section 2 — News Feed</h2>
  <p style="margin:0 0 16px;font-size:12px;color:#999;">
    {total} articles across {len(stocks_with_news)} stocks.
    Tap any row to expand · Click headline to read full article.
    Ordered by priority → Indian first → investment size.
  </p>
  {"".join(blocks)}
</div>"""


# ─────────────────────────────────────────────
# Section 3 — Portfolio Pulse (pure math)
# ─────────────────────────────────────────────

def build_sector_sentiment(analysis_results: list[dict], portfolio: list[dict]) -> dict:
    """Aggregates sentiment + avg gain by sector."""
    gain_map = {s.get("ticker"): s.get("gain_pct") for s in portfolio}
    data = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0, "total": 0, "gains": []})

    for r in analysis_results:
        sector    = r.get("sector", "Unknown")
        sentiment = r.get("sentiment", "neutral")
        gain      = gain_map.get(r.get("ticker"))
        data[sector]["total"]    += 1
        data[sector][sentiment]  += 1
        if gain is not None:
            data[sector]["gains"].append(gain)

    result = {}
    for sector, d in data.items():
        avg_gain = sum(d["gains"]) / len(d["gains"]) if d["gains"] else None
        dominant = max({"positive": d["positive"], "negative": d["negative"], "neutral": d["neutral"]}, key=lambda k: {"positive": d["positive"], "negative": d["negative"], "neutral": d["neutral"]}[k])
        result[sector] = {**d, "avg_gain": avg_gain, "dominant": dominant}

    return result


def build_section3_html(
    analysis_results: list[dict],
    portfolio: list[dict],
    filtered_news: dict,
) -> str:
    """Section 3: Portfolio Pulse — sector bars, movers, no-news list."""

    # ── Sector sentiment ──────────────────────────────────
    sectors = build_sector_sentiment(analysis_results, portfolio)
    sorted_sectors = sorted(sectors.items(), key=lambda x: x[1]["total"], reverse=True)

    sector_rows = ""
    for sector, d in sorted_sectors:
        if d["total"] == 0:
            continue
        total = d["total"]
        pos, neg, neu = d["positive"], d["negative"], d["neutral"]
        avg  = d.get("avg_gain")
        dom  = d.get("dominant", "neutral")
        icon = SENTIMENT_ICON.get(dom, "➡️")
        avg_str   = f"{avg:+.1f}%" if avg is not None else "N/A"
        avg_color = "#2E7D32" if (avg or 0) >= 0 else "#C62828"

        # Mini bar — 80px wide
        pos_w = int((pos / total) * 80)
        neg_w = int((neg / total) * 80)
        neu_w = max(0, 80 - pos_w - neg_w)
        bar = (
            f'<span style="display:inline-block;width:{pos_w}px;height:6px;background:#4CAF50;border-radius:2px 0 0 2px;vertical-align:middle;"></span>'
            f'<span style="display:inline-block;width:{neu_w}px;height:6px;background:#BDBDBD;vertical-align:middle;"></span>'
            f'<span style="display:inline-block;width:{neg_w}px;height:6px;background:#F44336;border-radius:0 2px 2px 0;vertical-align:middle;"></span>'
        ) if total > 0 else ""

        sector_rows += f"""
<div style="padding:9px 0;border-bottom:1px solid #f5f5f5;">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px;">
    <div>
      <span style="font-size:13px;font-weight:600;">{icon} {sector}</span>
      <span style="font-size:11px;color:#aaa;margin-left:6px;">{total} stocks</span>
      <div style="margin-top:4px;">{bar}</div>
      <div style="font-size:11px;color:#aaa;margin-top:2px;">▲{pos} &nbsp;▶{neu} &nbsp;▼{neg}</div>
    </div>
    <span style="font-size:14px;font-weight:700;color:{avg_color};">{avg_str}</span>
  </div>
</div>"""

    # ── Top movers ────────────────────────────────────────
    has_gain = [s for s in portfolio if s.get("gain_pct") is not None]
    top5_up   = sorted(has_gain, key=lambda s: s.get("gain_pct", 0), reverse=True)[:5]
    top5_down = sorted(has_gain, key=lambda s: s.get("gain_pct", 0))[:5]

    def pill(s, color, bg):
        g = s.get("gain_pct", 0)
        t = s.get("ticker", "")
        return (
            f'<span style="display:inline-block;background:{bg};color:{color};'
            f'border-radius:12px;padding:4px 10px;font-size:12px;font-weight:600;margin:3px;">'
            f'{t} {g:+.1f}%</span>'
        )

    gainers_html = "".join(pill(s, "#2E7D32", "#E8F5E9") for s in top5_up)
    losers_html  = "".join(pill(s, "#C62828", "#FFEBEE") for s in top5_down)

    # ── No-news stocks ────────────────────────────────────
    no_news = sorted([t for t, d in filtered_news.items() if not d["articles"]])
    no_news_html = ""
    if no_news:
        pills = "".join(
            f'<span style="display:inline-block;background:#F5F5F5;color:#777;'
            f'border-radius:10px;padding:3px 9px;font-size:12px;margin:2px;">{t}</span>'
            for t in no_news
        )
        no_news_html = f"""
<div style="margin-top:20px;">
  <h3 style="font-size:13px;color:#aaa;margin:0 0 6px;font-weight:600;">
    🔇 No News Today ({len(no_news)} stocks)
  </h3>
  <div style="line-height:2;">{pills}</div>
</div>"""

    return f"""
<div style="background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;
     box-shadow:0 1px 4px rgba(0,0,0,0.07);">
  <h2 style="margin:0 0 4px;font-size:17px;color:#1a1a2e;">📡 Section 3 — Portfolio Pulse</h2>
  <p style="margin:0 0 16px;font-size:12px;color:#999;">Pure math — no AI.</p>

  <h3 style="font-size:13px;color:#555;margin:0 0 4px;font-weight:600;">🏭 Sector Sentiment</h3>
  <div style="margin-bottom:20px;">{sector_rows}</div>

  <h3 style="font-size:13px;color:#555;margin:0 0 8px;font-weight:600;">📈 Top Gainers</h3>
  <div style="margin-bottom:16px;line-height:2;">{gainers_html}</div>

  <h3 style="font-size:13px;color:#555;margin:0 0 8px;font-weight:600;">📉 Top Losers</h3>
  <div style="margin-bottom:4px;line-height:2;">{losers_html}</div>

  {no_news_html}
</div>"""


# ─────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────

def build_header(portfolio: list[dict], analysis_results: list[dict]) -> str:
    total      = len(portfolio)
    in_profit  = sum(1 for s in portfolio if (s.get("gain_pct") or 0) > 0)
    in_loss    = total - in_profit
    high_count = sum(1 for r in analysis_results if r.get("priority") == "HIGH")

    now    = datetime.now(timezone.utc)
    start  = now - timedelta(days=NEWS_DAYS_BACK)
    window = f"{start.strftime('%b %d %I:%M %p')} → {now.strftime('%b %d %I:%M %p')} UTC"
    today  = datetime.now().strftime("%A, %B %d, %Y")

    badge = (
        f'<span style="background:#E53935;color:#fff;padding:3px 10px;'
        f'border-radius:10px;font-size:11px;font-weight:700;margin-left:8px;">⚠️ {high_count} urgent</span>'
        if high_count > 0 else ""
    )

    return f"""
<div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);
     color:white;padding:22px 20px;border-radius:10px;margin-bottom:16px;">
  <div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:4px;">
    <h1 style="margin:0;font-size:19px;font-weight:700;">📊 Portfolio Intelligence</h1>
    {badge}
  </div>
  <p style="margin:0 0 14px;color:#aaa;font-size:12px;">{today}</p>
  <div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:12px;">
    <span style="font-size:13px;">Total: <strong>{total}</strong></span>
    <span style="color:#81C784;font-size:13px;">Profit: <strong>{in_profit}</strong></span>
    <span style="color:#EF9A9A;font-size:13px;">Loss: <strong>{in_loss}</strong></span>
  </div>
  <div style="background:rgba(255,255,255,0.08);border-radius:6px;padding:8px 12px;">
    <span style="font-size:11px;color:#90CAF9;">⏱ News window: {window}</span>
  </div>
</div>"""


# ─────────────────────────────────────────────
# Full Assembly
# ─────────────────────────────────────────────

def build_daily_email_html(
    analysis_results: list[dict],
    portfolio: list[dict],
    filtered_news: dict,
) -> str:
    header   = build_header(portfolio, analysis_results)
    section1 = build_section1_html(analysis_results)
    section2 = build_section2_html(filtered_news, analysis_results)
    section3 = build_section3_html(analysis_results, portfolio, filtered_news)
    footer   = """
<div style="text-align:center;padding:16px;font-size:11px;color:#bbb;">
  Portfolio Intelligence Pipeline &nbsp;•&nbsp;
  Yahoo Finance RSS &amp; Economic Times RSS &nbsp;•&nbsp; Google Gemini<br>
  <span style="color:#ddd;">⚠️ Personal tool — not financial advice.</span>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Portfolio Intelligence</title>
<style>
body{{margin:0;padding:0;background:#F0F2F5;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;}}
details>summary{{list-style:none;}}
details>summary::-webkit-details-marker{{display:none;}}
a{{color:#1565C0;}}
@media(max-width:600px){{
  .wrap{{padding:8px!important;}}
  h1{{font-size:16px!important;}}
  h2{{font-size:14px!important;}}
}}
</style>
</head>
<body>
<div class="wrap" style="max-width:680px;margin:0 auto;padding:16px;">
  {header}
  {section1}
  {section2}
  {section3}
  {footer}
</div>
</body>
</html>"""


def build_weekly_email_html(weekly_analysis: str, portfolio: list[dict]) -> str:
    today    = datetime.now().strftime("%B %d, %Y")
    body_html = weekly_analysis.replace("\n\n", "</p><p>").replace("\n", "<br>")
    return f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1.0">
</head><body style="max-width:680px;margin:0 auto;padding:16px;background:#F0F2F5;
font-family:-apple-system,Arial,sans-serif;">
<div style="background:linear-gradient(135deg,#0d3b66,#1565C0);color:#fff;
     padding:20px;border-radius:10px;margin-bottom:16px;">
  <h1 style="margin:0;font-size:19px;">📅 Weekly Portfolio Review</h1>
  <p style="margin:6px 0 0;color:#90CAF9;font-size:12px;">Week ending {today}</p>
</div>
<div style="background:#fff;border-radius:10px;padding:20px;line-height:1.8;
     color:#333;font-size:14px;">
  <p>{body_html}</p>
</div>
<p style="text-align:center;font-size:11px;color:#aaa;margin-top:12px;">
  ⚠️ Personal tool — not financial advice.
</p></body></html>"""


def build_monthly_email_html(monthly_analysis: str) -> str:
    month     = datetime.now().strftime("%B %Y")
    body_html = monthly_analysis.replace("\n\n", "</p><p>").replace("\n", "<br>")
    return f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1.0">
</head><body style="max-width:680px;margin:0 auto;padding:16px;background:#F0F2F5;
font-family:-apple-system,Arial,sans-serif;">
<div style="background:linear-gradient(135deg,#1b4332,#2D6A4F);color:#fff;
     padding:20px;border-radius:10px;margin-bottom:16px;">
  <h1 style="margin:0;font-size:19px;">📆 Monthly Portfolio Deep Dive</h1>
  <p style="margin:6px 0 0;color:#95D5B2;font-size:12px;">{month}</p>
</div>
<div style="background:#fff;border-radius:10px;padding:20px;line-height:1.8;
     color:#333;font-size:14px;">
  <p>{body_html}</p>
</div>
<p style="text-align:center;font-size:11px;color:#aaa;margin-top:12px;">
  ⚠️ Personal tool — not financial advice.
</p></body></html>"""


# ─────────────────────────────────────────────
# Sending
# ─────────────────────────────────────────────

def send_email(subject: str, html_body: str) -> bool:
    if not all([SENDER_EMAIL, RECIPIENT_EMAIL, GMAIL_APP_PASSWORD]):
        logger.error("Email credentials missing in .env")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        print(f"\n📧 Sending: {subject}")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print(f"  ✅ Sent to {RECIPIENT_EMAIL}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail auth failed — use App Password, not main password.")
        return False
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


def send_daily_digest(analysis_results, portfolio, filtered_news) -> bool:
    today      = datetime.now().strftime("%Y-%m-%d")
    high_count = sum(1 for r in analysis_results if r.get("priority") == "HIGH")
    subject    = f"📊 Portfolio Digest {today}"
    if high_count > 0:
        subject = f"🔴 [{high_count} urgent] Portfolio Digest {today}"
    html = build_daily_email_html(analysis_results, portfolio, filtered_news)
    return send_email(subject, html)


def send_weekly_digest(weekly_analysis: str, portfolio: list[dict]) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    html  = build_weekly_email_html(weekly_analysis, portfolio)
    return send_email(f"📅 Weekly Portfolio Review — {today}", html)


def send_monthly_digest(monthly_analysis: str) -> bool:
    month = datetime.now().strftime("%B %Y")
    html  = build_monthly_email_html(monthly_analysis)
    return send_email(f"📆 Monthly Portfolio Deep Dive — {month}", html)


# ─────────────────────────────────────────────
# Local preview
# ─────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import datetime, timezone, timedelta
    print("Building email preview...\n")

    now = datetime.now(timezone.utc)

    test_portfolio = [
        {"ticker": "NEWGEN",    "name": "Newgen Software",  "market": "IN", "sector": "IT",                 "gain_pct": -59,  "invested_inr": 26080, "avg_price_inr": 1304, "mkt_price_inr": 534},
        {"ticker": "NVDA",      "name": "Nvidia Corp.",     "market": "US", "sector": "Technology",         "gain_pct": 296,  "invested_inr": 65000, "avg_price_usd": 48,   "mkt_price_usd": 192},
        {"ticker": "HDFCBANK",  "name": "HDFC Bank",        "market": "IN", "sector": "Financial Services", "gain_pct": 21,   "invested_inr": 16740, "avg_price_inr": 761,  "mkt_price_inr": 924},
        {"ticker": "TCS",       "name": "TCS",              "market": "IN", "sector": "IT",                 "gain_pct": -13,  "invested_inr": 30830, "avg_price_inr": 3083, "mkt_price_inr": 2674},
        {"ticker": "PLTR",      "name": "Palantir",         "market": "US", "sector": "Technology",         "gain_pct": 343,  "invested_inr": 15240, "avg_price_usd": 29,   "mkt_price_usd": 130},
        {"ticker": "MNDY",      "name": "Monday.com",       "market": "US", "sector": "Technology",         "gain_pct": -74,  "invested_inr": 10000, "avg_price_usd": 274,  "mkt_price_usd": 72},
        {"ticker": "SUZLON",    "name": "Suzlon Energy",    "market": "IN", "sector": "Energy",             "gain_pct": -35,  "invested_inr": 6470,  "avg_price_inr": 68,   "mkt_price_inr": 44},
        {"ticker": "FEDERALBNK","name": "Federal Bank",     "market": "IN", "sector": "Financial Services", "gain_pct": 104,  "invested_inr": 52230, "avg_price_inr": 145,  "mkt_price_inr": 296},
    ]

    test_analysis = [
        {"ticker": "NEWGEN",    "name": "Newgen Software", "market": "IN", "sector": "IT",
         "priority": "HIGH",   "sentiment": "negative", "gain_pct": -59, "invested_inr": 26080,
         "summary": "Stock down 4% after weak Q3 guidance. Revenue growth slowing significantly.",
         "action_hint": "research_exit", "thesis_status": "weakened"},
        {"ticker": "MNDY",      "name": "Monday.com",     "market": "US", "sector": "Technology",
         "priority": "HIGH",   "sentiment": "negative", "gain_pct": -74, "invested_inr": 10000,
         "summary": "Analysts downgraded citing slowing enterprise growth. Guidance cut.",
         "action_hint": "research_exit", "thesis_status": "broken"},
        {"ticker": "NVDA",      "name": "Nvidia Corp.",   "market": "US", "sector": "Technology",
         "priority": "MEDIUM", "sentiment": "positive", "gain_pct": 296, "invested_inr": 65000,
         "summary": "Strong AI chip demand. New data center partnerships announced.",
         "action_hint": "hold", "thesis_status": "intact"},
        {"ticker": "PLTR",      "name": "Palantir",       "market": "US", "sector": "Technology",
         "priority": "MEDIUM", "sentiment": "positive", "gain_pct": 343, "invested_inr": 15240,
         "summary": "Government contract wins accelerating. AIP platform adoption growing.",
         "action_hint": "hold", "thesis_status": "intact"},
        {"ticker": "HDFCBANK",  "name": "HDFC Bank",      "market": "IN", "sector": "Financial Services",
         "priority": "LOW",    "sentiment": "neutral",  "gain_pct": 21,  "invested_inr": 16740,
         "summary": "No major news. Banking sector stable.",
         "action_hint": "hold", "thesis_status": "intact"},
        {"ticker": "TCS",       "name": "TCS",            "market": "IN", "sector": "IT",
         "priority": "LOW",    "sentiment": "neutral",  "gain_pct": -13, "invested_inr": 30830,
         "summary": "Sector-level IT caution. No company-specific news.",
         "action_hint": "watch", "thesis_status": "intact"},
        {"ticker": "FEDERALBNK","name": "Federal Bank",   "market": "IN", "sector": "Financial Services",
         "priority": "LOW",    "sentiment": "neutral",  "gain_pct": 104, "invested_inr": 52230,
         "action_hint": "no_news", "thesis_status": "unclear",
         "summary": "No recent news found."},
    ]

    test_news = {
        "NEWGEN":    {"stock": test_portfolio[0], "articles": [
            {"title": "Newgen Software Q3 revenue growth slows, guidance cut",
             "link": "https://economictimes.indiatimes.com", "source": "Economic Times",
             "published": now - timedelta(hours=3)},
            {"title": "Newgen faces rising competition from global SaaS players",
             "link": "https://moneycontrol.com", "source": "MoneyControl",
             "published": now - timedelta(hours=8)},
        ]},
        "NVDA":      {"stock": test_portfolio[1], "articles": [
            {"title": "Nvidia announces Blackwell Ultra chips for data centers",
             "link": "https://finance.yahoo.com", "source": "Yahoo Finance",
             "published": now - timedelta(hours=5)},
        ]},
        "MNDY":      {"stock": test_portfolio[5], "articles": [
            {"title": "Monday.com downgraded by Goldman Sachs, target cut to $180",
             "link": "https://finance.yahoo.com", "source": "Yahoo Finance",
             "published": now - timedelta(hours=2)},
        ]},
        "PLTR":      {"stock": test_portfolio[4], "articles": [
            {"title": "Palantir wins $480M US Army AI contract extension",
             "link": "https://finance.yahoo.com", "source": "Yahoo Finance",
             "published": now - timedelta(hours=6)},
        ]},
        "HDFCBANK":   {"stock": test_portfolio[2], "articles": []},
        "TCS":        {"stock": test_portfolio[3], "articles": []},
        "SUZLON":     {"stock": test_portfolio[6], "articles": []},
        "FEDERALBNK": {"stock": test_portfolio[7], "articles": []},
    }

    html = build_daily_email_html(test_analysis, test_portfolio, test_news)

    preview_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "output", "email_preview.html"
    )
    os.makedirs(os.path.dirname(preview_path), exist_ok=True)
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Preview saved: {preview_path}")
    print("Open in browser to check.")