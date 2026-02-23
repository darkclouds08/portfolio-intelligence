"""
src/news_filter.py
===================
Pre-filters and deduplicates news BEFORE sending to Gemini.
This is the token-saver layer.

Filtering steps:
  1. Recency check — drop articles older than N days
  2. Relevance check — fuzzy match article text against company name + aliases
     Uses rapidfuzz for proper fuzzy string matching, not just keyword contains.
     This catches "Infosys" for NSE:INFY, "TSMC" for TSM, etc.
  3. Deduplication — same story from multiple outlets dropped via title similarity
  4. Cap at MAX_ARTICLES_PER_STOCK
  5. Truncate body to MAX_WORDS_PER_ARTICLE

NOTE: Only articles from sector RSS feeds go through the relevance check.
      Yahoo Finance per-ticker RSS is already filtered by Yahoo — we trust those.
"""

import sys
import os
import re
import logging
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    NEWS_DAYS_BACK,
    MAX_ARTICLES_PER_STOCK,
    MAX_WORDS_PER_ARTICLE,
    FUZZY_MATCH_THRESHOLD,
    TICKER_ALIASES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def get_aliases_for_stock(stock: dict) -> list[str]:
    """
    Returns all known name aliases for a stock ticker.
    Falls back to company name if no aliases defined.

    e.g. NSE:INFY → ["Infosys", "Infy", "INFY"]
         TSM      → ["TSMC", "Taiwan Semiconductor", "TSM"]
    """
    ticker = stock.get("ticker", "")
    name = stock.get("name", "")
    yahoo_ticker = stock.get("yahoo_ticker", ticker)

    # Collect all possible names to search for
    aliases = set()
    aliases.add(ticker.lower())
    aliases.add(yahoo_ticker.lower())

    # Add from our defined alias map
    for key in [ticker, yahoo_ticker, ticker.replace("NSE:", ""), ticker.replace("BSE:", "")]:
        if key in TICKER_ALIASES:
            for alias in TICKER_ALIASES[key]:
                aliases.add(alias.lower())

    # Always include the actual company name
    if name:
        aliases.add(name.lower())
        # Also add first significant word (e.g. "HDFC" from "HDFC Bank Ltd")
        words = [w for w in name.split() if len(w) >= 4]
        if words:
            aliases.add(words[0].lower())

    return list(aliases)


def is_article_relevant(article: dict, stock: dict, from_sector_feed: bool = False) -> bool:
    """
    Checks if an article is relevant to a stock using fuzzy matching.

    For Yahoo Finance per-ticker RSS: always returns True (Yahoo already filtered it).
    For sector RSS feeds: uses fuzzy matching against all aliases.

    Fuzzy matching means:
      - "Infosys Q3 results" will match stock "NSE:INFY" via alias "Infosys"
      - "HDFC Bank posts profit" will match "HDFCBANK"
      - Minor typos and partial names still match above threshold
    """
    # Yahoo Finance per-ticker feeds are pre-filtered — trust them
    if not from_sector_feed:
        return True

    text = (
        article.get("title", "") + " " + article.get("summary", "")
    ).lower()

    aliases = get_aliases_for_stock(stock)

    for alias in aliases:
        if not alias or len(alias) < 3:
            continue

        # Strategy 1: Direct substring check (fast, catches exact matches)
        if alias in text:
            return True

        # Strategy 2: Fuzzy partial match using rapidfuzz
        # partial_ratio checks if alias appears as a substring with fuzzy tolerance
        score = fuzz.partial_ratio(alias, text)
        if score >= FUZZY_MATCH_THRESHOLD:
            logger.debug(
                f"  Fuzzy match: '{alias}' in article '{article['title'][:50]}' "
                f"(score: {score})"
            )
            return True

    return False


def normalize_for_dedup(title: str) -> str:
    """Normalizes title for deduplication — lowercase, no punctuation."""
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def is_duplicate_title(title: str, seen_titles: list[str], threshold: int = 70) -> bool:
    """
    Checks if a title is too similar to any already-seen title.
    Uses fuzzy token_sort_ratio which handles word order differences.
    e.g. "HDFC Bank Q3 beats estimates" ≈ "Q3 estimates beaten by HDFC Bank"
    """
    norm = normalize_for_dedup(title)
    for seen in seen_titles:
        score = fuzz.token_sort_ratio(norm, seen)
        if score >= threshold:
            return True
    return False


def truncate_text(text: str, max_words: int = MAX_WORDS_PER_ARTICLE) -> str:
    """Truncates text to max_words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def filter_news_for_stock(
    articles: list[dict],
    stock: dict,
    days_back: int = NEWS_DAYS_BACK,
    max_articles: int = MAX_ARTICLES_PER_STOCK,
    from_sector_feed: bool = False,
    verbose: bool = False,
) -> list[dict]:
    """
    Filters articles for a specific stock.

    Returns filtered list with truncated text, ready for Gemini or email.
    """
    ticker = stock.get("ticker", "UNKNOWN")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    stats = {"too_old": 0, "irrelevant": 0, "duplicate": 0, "kept": 0}

    filtered = []
    seen_normalized_titles = []

    for article in articles:

        # ── 1. Recency ─────────────────────────────────────
        pub = article.get("published")
        if pub is not None and pub < cutoff:
            stats["too_old"] += 1
            continue

        # ── 2. Relevance (sector feeds only) ───────────────
        if not is_article_relevant(article, stock, from_sector_feed=from_sector_feed):
            stats["irrelevant"] += 1
            if verbose:
                logger.debug(f"[{ticker}] IRRELEVANT: {article['title'][:60]}")
            continue

        # ── 3. Deduplication ───────────────────────────────
        title = article.get("title", "")
        if is_duplicate_title(title, seen_normalized_titles):
            stats["duplicate"] += 1
            if verbose:
                logger.debug(f"[{ticker}] DUPLICATE: {title[:60]}")
            continue

        seen_normalized_titles.append(normalize_for_dedup(title))

        # ── 4. Truncate text for token control ─────────────
        processed = article.copy()
        if processed.get("body"):
            processed["body"] = truncate_text(processed["body"])
        if processed.get("summary"):
            processed["summary"] = truncate_text(processed["summary"], max_words=150)

        filtered.append(processed)
        stats["kept"] += 1

        if len(filtered) >= max_articles:
            break

    if verbose:
        logger.info(
            f"[{ticker}] {len(articles)} → {stats['kept']} kept "
            f"(old:{stats['too_old']} irrelevant:{stats['irrelevant']} dup:{stats['duplicate']})"
        )

    return filtered


def filter_portfolio_news(
    raw_news: dict,
    days_back: int = NEWS_DAYS_BACK,
    verbose: bool = False,
) -> dict:
    """
    Filters news for all stocks in the portfolio.
    Returns same structure as input but with filtered articles.
    """
    print(f"\n🔍 Filtering news (fuzzy matching, {days_back}d window)...")

    total_before = sum(len(v["articles"]) for v in raw_news.values())
    filtered_news = {}

    for ticker, data in raw_news.items():
        filtered = filter_news_for_stock(
            articles=data["articles"],
            stock=data["stock"],
            days_back=days_back,
            verbose=verbose,
        )
        filtered_news[ticker] = {
            "stock": data["stock"],
            "articles": filtered,
        }

    total_after = sum(len(v["articles"]) for v in filtered_news.values())
    reduction = ((total_before - total_after) / max(total_before, 1)) * 100
    print(f"  {total_before} articles → {total_after} kept ({reduction:.0f}% reduction)")

    return filtered_news


def estimate_token_count(text: str) -> int:
    """Rough token estimate: ~1 token per 4 chars."""
    return len(text) // 4


def build_llm_context_for_stock(stock: dict, articles: list[dict]) -> str:
    """
    Builds the text block for a single stock sent to Gemini.
    Only the analysis-relevant content — no links, no fluff.
    Links are handled separately in the email builder (no AI needed for that).
    """
    ticker = stock.get("ticker", "")
    name = stock.get("name", ticker)
    sector = stock.get("sector", "Unknown")
    market = stock.get("market", "")

    if market == "IN":
        avg_price = stock.get("avg_price_inr")
        mkt_price = stock.get("mkt_price_inr")
        invested = stock.get("invested_inr")
        profit = stock.get("profit_inr")
        currency = "₹"
    else:
        avg_price = stock.get("avg_price_usd")
        mkt_price = stock.get("mkt_price_usd")
        invested = stock.get("usd_invested")
        profit = stock.get("profit_usd")
        currency = "$"

    gain_pct = stock.get("gain_pct")

    # Build context header
    lines = [f"STOCK: {ticker} | {name} | {sector}"]

    if avg_price and mkt_price:
        gain_str = f"{gain_pct:+.1f}%" if gain_pct is not None else "N/A"
        lines.append(f"Price: {currency}{avg_price:.2f} → {currency}{mkt_price:.2f} ({gain_str})")

    if invested is not None and profit is not None:
        lines.append(f"P&L: {currency}{profit:+.0f} on {currency}{invested:.0f} invested")

    # Add news — title + summary only (no links, Gemini doesn't need them)
    if articles:
        lines.append("RECENT NEWS:")
        for i, a in enumerate(articles, 1):
            title = a.get("title", "")
            body = a.get("body") or a.get("summary", "")
            date = a["published"].strftime("%Y-%m-%d") if a.get("published") else "recent"
            lines.append(f"  [{i}] ({date}) {title}")
            if body:
                lines.append(f"  {body[:200]}")
    else:
        lines.append("RECENT NEWS: None found.")

    return "\n".join(lines)