"""
src/gemini_analyzer.py
=======================
Sends pre-filtered portfolio news to Gemini for analysis.

Changes from v1:
  - Auto-retry on 429 rate limit errors (waits the suggested retry delay)
  - Falls back to gemini-1.5-flash if 2.0-flash quota exhausted
    (1.5-flash has 1500 req/day free vs 50/day for 2.0-flash)
  - Larger batch size (8 stocks per call) to use fewer API calls total
  - Stocks sorted by invested amount so high-value positions get analyzed
    first in case we hit quota mid-way through
"""

import sys
import os
import json
import time
import re
import logging
from datetime import datetime

import google.generativeai as genai
from tqdm import tqdm

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_BATCH_SIZE,
)
from src.news_filter import build_llm_context_for_stock, estimate_token_count

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Model fallback order — try these in sequence if quota exceeded
MODEL_FALLBACK_ORDER = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",   # Smallest, highest free quota
]

# Max retries per batch on 429 errors
MAX_RETRIES = 3

# Delay between batches (seconds) — free tier allows 15 RPM
BATCH_DELAY = 6

# Current active model (may change during run if quota exceeded)
_active_model = None
_active_model_name = None


def init_gemini(model_name: str = None):
    """
    Initializes Gemini with the given model name.
    Falls back through MODEL_FALLBACK_ORDER if not specified.
    """
    global _active_model, _active_model_name

    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY not set in .env\n"
            "Get your free key from: https://aistudio.google.com/app/apikey"
        )

    genai.configure(api_key=GEMINI_API_KEY)

    # Use specified model or default from settings
    target = model_name or GEMINI_MODEL
    _active_model = genai.GenerativeModel(target)
    _active_model_name = target
    print(f"✅ Gemini initialized: {target}")
    return _active_model


def get_retry_delay_from_error(error_str: str) -> int:
    """
    Parses the suggested retry delay from Gemini's 429 error message.
    Returns the delay in seconds, defaulting to 60 if not found.
    """
    # Error message contains "retry_delay { seconds: 44 }" or "Please retry in 44s"
    match = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', str(error_str))
    if match:
        return int(match.group(1)) + 2  # Add 2s buffer
    match = re.search(r'Please retry in (\d+)', str(error_str))
    if match:
        return int(match.group(1)) + 2
    return 60  # Default wait


def call_gemini_with_retry(prompt: str, tickers: list[str]) -> tuple[str, bool]:
    """
    Calls Gemini API with automatic retry on 429 and model fallback.

    Returns:
        (response_text, success)
    """
    global _active_model, _active_model_name

    for attempt in range(MAX_RETRIES):
        try:
            response = _active_model.generate_content(prompt)
            return response.text.strip(), True

        except Exception as e:
            error_str = str(e)

            # ── Rate limit (429) — wait and retry ──────────
            if "429" in error_str:
                retry_delay = get_retry_delay_from_error(error_str)

                # Check if it's a daily quota issue (not just per-minute)
                daily_quota_hit = "PerDay" in error_str or "GenerateRequestsPerDay" in error_str

                if daily_quota_hit and attempt == 0:
                    # Try switching to next model in fallback list
                    current_idx = MODEL_FALLBACK_ORDER.index(_active_model_name) \
                        if _active_model_name in MODEL_FALLBACK_ORDER else -1
                    next_idx = current_idx + 1

                    if next_idx < len(MODEL_FALLBACK_ORDER):
                        new_model = MODEL_FALLBACK_ORDER[next_idx]
                        logger.warning(
                            f"Daily quota exhausted for {_active_model_name}. "
                            f"Switching to {new_model}..."
                        )
                        init_gemini(new_model)
                        continue  # Retry immediately with new model

                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"Rate limited (attempt {attempt+1}/{MAX_RETRIES}). "
                        f"Waiting {retry_delay}s before retry..."
                    )
                    # Show countdown so user knows it's not frozen
                    for remaining in range(retry_delay, 0, -5):
                        print(f"  ⏳ Retrying in {remaining}s...", end="\r")
                        time.sleep(min(5, remaining))
                    print(" " * 30, end="\r")  # Clear the countdown line
                    continue

            # ── Other errors — log and return failure ───────
            logger.error(f"Gemini API call failed for {tickers}: {e}")
            return "", False

    logger.error(f"Max retries exceeded for batch {tickers}")
    return "", False


# ─────────────────────────────────────────────
# Daily Analysis
# ─────────────────────────────────────────────

DAILY_SYSTEM_PROMPT = """You are a personal stock portfolio analyst.
Analyze the news for each stock and return ONLY a valid JSON array.
No markdown, no explanation, no backticks. Just the JSON array.

For each stock return an object with these exact keys:
{
  "ticker": "string",
  "sentiment": "positive" | "negative" | "neutral",
  "summary": "2 sentence max. What happened and why it matters to this investor.",
  "priority": "HIGH" | "MEDIUM" | "LOW",
  "priority_reason": "One line.",
  "action_hint": "hold" | "watch" | "research_exit" | "research_buy_more" | "no_news",
  "thesis_status": "intact" | "weakened" | "broken" | "unclear"
}

Priority guide:
  HIGH   = significant negative/positive news, stock moved >3%, fundamental change
  MEDIUM = minor news, sector movement, worth watching
  LOW    = no significant news, normal day

Action hints:
  hold             = neutral/positive, keep holding
  watch            = something changed, monitor closely
  research_exit    = negative fundamental news, consider cutting losses
  research_buy_more = strong positive signal, consider adding
  no_news          = nothing found
"""


def sort_by_investment(stock_list: list[tuple]) -> list[tuple]:
    """
    Sorts stocks by invested amount (INR) descending.
    Indian stocks prioritized over US within same invested bracket.
    This ensures highest-value positions get analyzed first
    if we hit quota mid-run.
    """
    def sort_key(item):
        ticker, data = item
        stock = data["stock"]
        invested = stock.get("invested_inr") or 0
        # Give Indian stocks a boost so they sort ahead of US at same investment level
        market_boost = 1.1 if stock.get("market") == "IN" else 1.0
        return -(invested * market_boost)

    return sorted(stock_list, key=sort_key)


def analyze_daily_batch(model, stock_contexts: list[str], tickers: list[str]) -> list[dict]:
    """
    Sends a batch of stock contexts to Gemini.
    Returns parsed list of analysis dicts.
    """
    combined = "\n\n" + ("─" * 40 + "\n\n").join(stock_contexts)
    prompt = f"""{DAILY_SYSTEM_PROMPT}

Stocks to analyze:
{combined}

Return JSON array with one object per stock."""

    logger.debug(f"Sending batch {tickers} (~{estimate_token_count(prompt)} tokens)")

    raw_text, success = call_gemini_with_retry(prompt, tickers)

    if not success or not raw_text:
        return _fallback_results(tickers, reason="API unavailable")

    # Clean any accidental markdown fences
    raw_text = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        results = json.loads(raw_text)
        if not isinstance(results, list):
            raise ValueError(f"Expected list, got {type(results)}")
        return results

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for batch {tickers}: {e}")
        logger.debug(f"Raw response: {raw_text[:300]}")
        return _fallback_results(tickers, reason="JSON parse error")


def _fallback_results(tickers: list[str], reason: str = "") -> list[dict]:
    """Returns placeholder results when Gemini fails."""
    return [
        {
            "ticker": t,
            "sentiment": "neutral",
            "summary": f"Analysis unavailable. {reason}".strip(),
            "priority": "LOW",
            "priority_reason": reason,
            "action_hint": "no_news",
            "thesis_status": "unclear",
        }
        for t in tickers
    ]


def run_daily_analysis(filtered_news: dict) -> list[dict]:
    """
    Runs daily analysis on all stocks.

    Key behaviors:
    - Sorts by invested amount (Indian stocks first within same bracket)
    - Batches GEMINI_BATCH_SIZE stocks per call
    - Retries on rate limits, falls back to smaller model if daily quota hit
    - Stocks with no news still get a result (action_hint: no_news)
    """
    print("\n" + "=" * 60)
    print("🤖 GEMINI DAILY ANALYSIS")
    print("=" * 60)

    model = init_gemini()

    # Only send stocks WITH news to Gemini — no point analyzing empty context
    items_with_news = {t: d for t, d in filtered_news.items() if d["articles"]}
    items_no_news   = {t: d for t, d in filtered_news.items() if not d["articles"]}

    print(f"   Stocks WITH news -> Gemini: {len(items_with_news)}")
    print(f"   Stocks with NO news skipped: {len(items_no_news)}")

    # Sort by investment value
    sorted_items = sort_by_investment(list(items_with_news.items()))

    print(f"   Batches: {max(1, -(-len(sorted_items) // GEMINI_BATCH_SIZE))} of {GEMINI_BATCH_SIZE}")
    print(f"   Order: highest invested (Indian first) first")
    all_results = []
    batches = [
        sorted_items[i:i + GEMINI_BATCH_SIZE]
        for i in range(0, len(sorted_items), GEMINI_BATCH_SIZE)
    ]

    for batch_idx, batch in enumerate(tqdm(batches, desc="Analyzing batches", unit="batch")):
        tickers_in_batch = [t for t, _ in batch]
        contexts = []

        for ticker, data in batch:
            context = build_llm_context_for_stock(data["stock"], data["articles"])
            contexts.append(context)

        logger.info(f"  Batch {batch_idx+1}/{len(batches)}: {tickers_in_batch}")
        batch_results = analyze_daily_batch(model, contexts, tickers_in_batch)

        # Merge stock financial data back into results
        ticker_to_stock = {t: data["stock"] for t, data in batch}
        for result in batch_results:
            rt = result.get("ticker")
            if rt in ticker_to_stock:
                s = ticker_to_stock[rt]
                result["name"]       = s.get("name", rt)
                result["market"]     = s.get("market", "")
                result["sector"]     = s.get("sector", "")
                result["gain_pct"]   = s.get("gain_pct")
                result["profit_inr"] = s.get("profit_inr")
                result["invested_inr"] = s.get("invested_inr")

        all_results.extend(batch_results)

        # Rate limit buffer between batches
        if batch_idx < len(batches) - 1:
            time.sleep(BATCH_DELAY)

    # Sort final output: HIGH → MEDIUM → LOW, then by invested amount within each tier
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_results.sort(key=lambda x: (
        priority_order.get(x.get("priority", "LOW"), 2),
        -(x.get("invested_inr") or 0)
    ))

    high   = sum(1 for r in all_results if r.get("priority") == "HIGH")
    medium = sum(1 for r in all_results if r.get("priority") == "MEDIUM")
    low    = sum(1 for r in all_results if r.get("priority") == "LOW")

    print(f"\n✅ Analysis complete: {len(all_results)} stocks")
    print(f"   🔴 HIGH: {high}  🟡 MEDIUM: {medium}  🟢 LOW: {low}")

    return all_results


# ─────────────────────────────────────────────
# Weekly / Monthly (unchanged logic, same retry wrapper)
# ─────────────────────────────────────────────

WEEKLY_PROMPT = """You are a portfolio analyst doing a weekly review.
Below are daily summaries from the past 7 days.

Provide:
1. Top 3 stocks needing urgent attention (with reasons)
2. Top 3 stocks showing positive momentum
3. Sector-level trends you notice
4. Overall sentiment: bullish / bearish / mixed

Keep under 600 words. Be direct and actionable.

Weekly data:
{weekly_data}
"""

MONTHLY_PROMPT = """You are a portfolio analyst doing a monthly review.
Below are weekly summaries and current portfolio snapshot.

Provide:
1. Portfolio Health Score (1-10) with explanation
2. Winners this month — what worked and why
3. Losers — what went wrong, is the thesis still valid?
4. Stocks to consider exiting — original buy reason broken?
5. Macro trends affecting your portfolio
6. Month ahead — what to watch

Be honest. Under 800 words.

Monthly data:
{monthly_data}

Portfolio snapshot:
{portfolio_summary}
"""


def run_weekly_analysis(weekly_log_data: str) -> str:
    init_gemini()
    prompt = WEEKLY_PROMPT.format(weekly_data=weekly_log_data)
    text, success = call_gemini_with_retry(prompt, ["weekly"])
    return text if success else "Weekly analysis unavailable."


def run_monthly_analysis(monthly_log_data: str, portfolio: list[dict]) -> str:
    init_gemini()
    total_invested = sum(s.get("invested_inr", 0) or 0 for s in portfolio)
    losers_20 = ", ".join(
        f"{s['ticker']} ({s.get('gain_pct', 0):.1f}%)"
        for s in portfolio if (s.get("gain_pct") or 0) < -20
    )
    summary = (
        f"Total stocks: {len(portfolio)}\n"
        f"Total invested: ₹{total_invested:,.0f}\n"
        f"Stocks >20% down: {losers_20 or 'None'}"
    )
    prompt = MONTHLY_PROMPT.format(monthly_data=monthly_log_data, portfolio_summary=summary)
    text, success = call_gemini_with_retry(prompt, ["monthly"])
    return text if success else "Monthly analysis unavailable."


if __name__ == "__main__":
    print("Testing Gemini connection...\n")
    test_filtered = {
        "AAPL": {
            "stock": {
                "ticker": "AAPL", "name": "Apple Inc.", "market": "US",
                "sector": "Technology", "gain_pct": 12.5,
                "avg_price_usd": 165, "mkt_price_usd": 185,
                "usd_invested": 825, "profit_usd": 100, "invested_inr": 65000,
            },
            "articles": [{
                "title": "Apple beats Q1 earnings on iPhone demand",
                "summary": "Apple reported $119.6B revenue, beating estimates.",
                "published": datetime.now(),
            }],
        }
    }
    results = run_daily_analysis(test_filtered)
    print("\nResult:")
    print(json.dumps(results, indent=2))