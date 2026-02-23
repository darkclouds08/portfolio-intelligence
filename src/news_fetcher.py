"""
src/news_fetcher.py
====================
Fetches news for every stock in your portfolio from free sources:

  For US stocks:
    - Yahoo Finance RSS per ticker (direct, no API key)

  For Indian stocks:
    - Yahoo Finance RSS using NSE suffix (e.g. RELIANCE.NS)
    - Economic Times sector RSS feeds
    - MoneyControl general RSS as fallback

  Also fetches sector-level news to give market context beyond just the company.

No paid APIs used. Respects rate limits with small delays.
"""

import sys
import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    YAHOO_RSS_URL,
    YAHOO_NSE_SUFFIX,
    INDIAN_SECTOR_RSS,
    US_MARKET_RSS,
    NEWS_DAYS_BACK,
    REQUEST_TIMEOUT,
    REQUEST_DELAY,
    MAX_ARTICLES_PER_STOCK,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Core RSS Fetching
# ─────────────────────────────────────────────

def fetch_rss_feed(url: str) -> list[dict]:
    """
    Fetches and parses an RSS feed URL.
    Returns a list of article dicts with title, link, summary, published.
    Returns empty list on any failure (we don't want one bad feed to kill the run).
    """
    try:
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            logger.debug(f"Failed to parse RSS feed: {url}")
            return []

        articles = []
        for entry in feed.entries:
            # Parse published date — feedparser gives us a time struct
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

            articles.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": published,
                "source": feed.feed.get("title", url),
            })

        return articles

    except Exception as e:
        logger.debug(f"Exception fetching RSS {url}: {e}")
        return []


def is_recent(article: dict, days_back: int = NEWS_DAYS_BACK) -> bool:
    """Returns True if article was published within the last N days."""
    if article.get("published") is None:
        return True  # If we can't determine age, include it (better safe than miss)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    return article["published"] >= cutoff


def fetch_article_body(url: str, max_words: int = 300) -> str:
    """
    Attempts to scrape the full article body from a URL.
    Falls back to empty string if blocked or fails.
    We keep it to max_words to control Gemini token usage.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove nav, footer, ads, script tags
        for tag in soup(["nav", "footer", "script", "style", "aside", "header", "form"]):
            tag.decompose()

        # Try to find article body — common patterns across news sites
        body_candidates = (
            soup.find("article") or
            soup.find("div", class_=lambda c: c and "article" in c.lower()) or
            soup.find("div", class_=lambda c: c and "content" in c.lower()) or
            soup.find("main") or
            soup.body
        )

        if body_candidates:
            text = body_candidates.get_text(separator=" ", strip=True)
        else:
            text = soup.get_text(separator=" ", strip=True)

        # Truncate to max_words
        words = text.split()
        if len(words) > max_words:
            text = " ".join(words[:max_words]) + "..."

        return text

    except Exception as e:
        logger.debug(f"Could not scrape {url}: {e}")
        return ""


# ─────────────────────────────────────────────
# Stock-Specific News Fetchers
# ─────────────────────────────────────────────

def fetch_news_for_us_stock(stock: dict, days_back: int = NEWS_DAYS_BACK) -> list[dict]:
    """
    Fetches recent news for a US stock via Yahoo Finance RSS.
    Ticker used directly (e.g. AAPL, NVDA, META).
    """
    ticker = stock["yahoo_ticker"]
    url = YAHOO_RSS_URL.format(ticker=ticker)

    logger.debug(f"Fetching US news for {ticker}: {url}")
    articles = fetch_rss_feed(url)

    # Filter to recent articles only
    recent = [a for a in articles if is_recent(a, days_back)]
    logger.debug(f"  {ticker}: {len(articles)} total → {len(recent)} recent")

    return recent[:MAX_ARTICLES_PER_STOCK]


def fetch_news_for_indian_stock(stock: dict, days_back: int = NEWS_DAYS_BACK) -> list[dict]:
    """
    Fetches recent news for an Indian stock.

    Strategy:
    1. Try Yahoo Finance with .NS suffix (e.g. RELIANCE.NS)
    2. Try Yahoo Finance with cleaned ticker
    3. Fall back to sector RSS and filter by company name
    """
    ticker = stock["yahoo_ticker"]
    company_name = stock.get("name", "")
    sector = stock.get("sector", "")
    all_articles = []

    # Strategy 1: Yahoo Finance with .NS suffix
    ns_ticker = ticker + YAHOO_NSE_SUFFIX
    url_ns = YAHOO_RSS_URL.format(ticker=ns_ticker)
    logger.debug(f"Fetching Indian news for {ticker} via Yahoo .NS: {url_ns}")
    articles_ns = fetch_rss_feed(url_ns)
    all_articles.extend(articles_ns)
    time.sleep(REQUEST_DELAY)

    # Strategy 2: Yahoo Finance without suffix (some Indian stocks have US listings)
    if len(all_articles) < 2:
        url_plain = YAHOO_RSS_URL.format(ticker=ticker)
        logger.debug(f"Fallback: trying plain ticker {ticker}")
        articles_plain = fetch_rss_feed(url_plain)
        all_articles.extend(articles_plain)
        time.sleep(REQUEST_DELAY)

    # Strategy 3: Sector RSS — filter articles mentioning company name
    if len(all_articles) < 2 and sector in INDIAN_SECTOR_RSS:
        logger.debug(f"Fallback: trying sector RSS for {sector}")
        sector_url = INDIAN_SECTOR_RSS[sector]
        sector_articles = fetch_rss_feed(sector_url)

        # Only keep articles that mention the company name or ticker
        keywords = [company_name.lower(), ticker.lower()]
        # Also try short versions of company name (e.g. "Federal Bank" → "federal")
        name_words = [w.lower() for w in company_name.split() if len(w) > 3]
        keywords.extend(name_words)

        relevant = [
            a for a in sector_articles
            if any(kw in (a.get("title", "") + a.get("summary", "")).lower()
                   for kw in keywords)
        ]
        all_articles.extend(relevant)

    # Filter to recent and deduplicate by title
    seen_titles = set()
    unique_recent = []
    for article in all_articles:
        if not is_recent(article, days_back):
            continue
        title_key = article["title"].lower()[:60]  # First 60 chars as dedup key
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_recent.append(article)

    logger.debug(f"  {ticker}: {len(unique_recent)} unique recent articles")
    return unique_recent[:MAX_ARTICLES_PER_STOCK]


def fetch_sector_news(sector: str, market: str = "IN", days_back: int = NEWS_DAYS_BACK) -> list[dict]:
    """
    Fetches sector-level news to provide market context.
    Used for the weekly/monthly analysis to understand sector trends.
    """
    articles = []

    if market == "IN" and sector in INDIAN_SECTOR_RSS:
        url = INDIAN_SECTOR_RSS[sector]
        articles = fetch_rss_feed(url)
    elif market == "US":
        # For US, we use general market RSS and the Yahoo feed for sector ETFs
        sector_etf_map = {
            "Technology": "QQQ",
            "Financial Services": "XLF",
            "Healthcare": "XLV",
            "Consumer Discretionary": "XLY",
            "Energy": "XLE",
        }
        etf = sector_etf_map.get(sector)
        if etf:
            url = YAHOO_RSS_URL.format(ticker=etf)
            articles = fetch_rss_feed(url)

    recent = [a for a in articles if is_recent(a, days_back)]
    return recent[:5]  # Just 5 sector articles for context


# ─────────────────────────────────────────────
# Main Portfolio News Fetcher
# ─────────────────────────────────────────────

def fetch_news_for_portfolio(
    portfolio: list[dict],
    days_back: int = NEWS_DAYS_BACK,
    fetch_bodies: bool = False,    # Set True for weekly/monthly (more detail, more time)
) -> dict[str, dict]:
    """
    Main entry point. Fetches news for every stock in the portfolio.

    Args:
        portfolio: List of stock dicts from sheet_reader
        days_back: How many days back to look for news
        fetch_bodies: Whether to scrape full article bodies (slower, more tokens)

    Returns:
        Dict mapping ticker → { "stock": stock_dict, "articles": [article_dicts] }
    """
    print("\n" + "="*60)
    print(f"📰 NEWS FETCHER  (looking back {days_back} days)")
    print("="*60)

    results = {}
    total_articles = 0

    # Separate Indian and US stocks
    indian = [s for s in portfolio if s["market"] == "IN"]
    us = [s for s in portfolio if s["market"] == "US"]

    print(f"\n🇺🇸 Fetching news for {len(us)} US stocks...")
    for stock in tqdm(us, desc="US stocks", unit="stock"):
        ticker = stock["ticker"]
        try:
            articles = fetch_news_for_us_stock(stock, days_back)

            # Optionally fetch full article bodies
            if fetch_bodies and articles:
                for article in tqdm(articles, desc=f"  {ticker} bodies", leave=False):
                    if article.get("link") and not article.get("body"):
                        article["body"] = fetch_article_body(article["link"])
                        time.sleep(REQUEST_DELAY)

            results[ticker] = {"stock": stock, "articles": articles}
            total_articles += len(articles)

            if articles:
                logger.debug(f"  ✓ {ticker}: {len(articles)} articles")
            else:
                logger.debug(f"  ⚠ {ticker}: no recent news found")

        except Exception as e:
            logger.warning(f"  ✗ Failed to fetch news for {ticker}: {e}")
            results[ticker] = {"stock": stock, "articles": []}

        time.sleep(REQUEST_DELAY)

    print(f"\n🇮🇳 Fetching news for {len(indian)} Indian stocks...")
    for stock in tqdm(indian, desc="Indian stocks", unit="stock"):
        ticker = stock["ticker"]
        try:
            articles = fetch_news_for_indian_stock(stock, days_back)

            if fetch_bodies and articles:
                for article in tqdm(articles, desc=f"  {ticker} bodies", leave=False):
                    if article.get("link") and not article.get("body"):
                        article["body"] = fetch_article_body(article["link"])
                        time.sleep(REQUEST_DELAY)

            results[ticker] = {"stock": stock, "articles": articles}
            total_articles += len(articles)

        except Exception as e:
            logger.warning(f"  ✗ Failed to fetch news for {ticker}: {e}")
            results[ticker] = {"stock": stock, "articles": []}

        time.sleep(REQUEST_DELAY)

    # Summary
    stocks_with_news = sum(1 for v in results.values() if v["articles"])
    stocks_no_news = len(results) - stocks_with_news

    print(f"\n{'='*60}")
    print(f"✅ News fetch complete")
    print(f"   📰 Total articles: {total_articles}")
    print(f"   ✓  Stocks with news: {stocks_with_news}")
    print(f"   ⚠  Stocks with no news: {stocks_no_news}")
    print(f"{'='*60}\n")

    return results


def format_articles_for_llm(articles: list[dict], max_words_per_article: int = 300) -> str:
    """
    Formats article list into clean text for Gemini prompt.
    Uses title + summary (or body if available) and truncates to token budget.
    """
    if not articles:
        return "No recent news found."

    lines = []
    for i, article in enumerate(articles, 1):
        title = article.get("title", "").strip()
        # Prefer scraped body, fall back to RSS summary
        body = article.get("body") or article.get("summary", "")

        # Truncate body
        words = body.split()
        if len(words) > max_words_per_article:
            body = " ".join(words[:max_words_per_article]) + "..."

        date_str = ""
        if article.get("published"):
            date_str = f" [{article['published'].strftime('%Y-%m-%d')}]"

        lines.append(f"[{i}]{date_str} {title}\n{body}")

    return "\n\n".join(lines)


if __name__ == "__main__":
    # Quick test — run this file directly to test news fetching
    # Creates a fake mini-portfolio to test both markets
    test_portfolio = [
        {
            "ticker": "AAPL", "yahoo_ticker": "AAPL", "name": "Apple Inc.",
            "market": "US", "exchange": "NASDAQ", "sector": "Technology",
            "shares": 5, "avg_price_usd": 165, "gain_pct": 12.5,
        },
        {
            "ticker": "NVDA", "yahoo_ticker": "NVDA", "name": "Nvidia Corp.",
            "market": "US", "exchange": "NASDAQ", "sector": "Technology",
            "shares": 1, "avg_price_usd": 48, "gain_pct": 296,
        },
        {
            "ticker": "RELIANCE", "yahoo_ticker": "RELIANCE", "name": "Reliance Industries",
            "market": "IN", "exchange": "NSE", "sector": "Oil and Gas",
            "shares": 2, "avg_price_inr": 1273, "gain_pct": 12,
        },
        {
            "ticker": "HDFCBANK", "yahoo_ticker": "HDFCBANK", "name": "HDFC Bank",
            "market": "IN", "exchange": "NSE", "sector": "Financial Services",
            "shares": 22, "avg_price_inr": 761, "gain_pct": 21,
        },
    ]

    print("Running news fetch test on 4 sample stocks...\n")
    news_data = fetch_news_for_portfolio(test_portfolio, days_back=3)

    # Print sample results
    for ticker, data in news_data.items():
        articles = data["articles"]
        print(f"\n{'─'*50}")
        print(f"  {ticker} — {len(articles)} articles")
        for a in articles[:2]:
            print(f"  • {a['title'][:80]}")
            if a.get("published"):
                print(f"    Published: {a['published'].strftime('%Y-%m-%d %H:%M')}")
