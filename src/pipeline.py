"""
src/pipeline.py
================
Main orchestrator for the Portfolio Intelligence Pipeline.

Run modes:
  python src/pipeline.py --mode daily     → Full run: fetch, analyze, email, log
  python src/pipeline.py --mode weekly    → 7-day deep dive
  python src/pipeline.py --mode monthly   → 30-day full analysis

Flags:
  --dry-run    Run everything but skip email + sheet write
  --no-email   Skip email only
  --no-sheet   Skip sheet write only
  --verbose    Print detailed filter/fetch logs
  --days-back  Override news window (default: 1 day)

Test run:
  python src/pipeline.py --mode daily --dry-run --verbose
"""

import sys
import os
import json
import argparse
import logging
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from src.sheet_reader import read_full_portfolio
from src.news_fetcher import fetch_news_for_portfolio
from src.news_filter import filter_portfolio_news
from src.gemini_analyzer import run_daily_analysis, run_weekly_analysis, run_monthly_analysis
from src.sheet_writer import write_daily_results, read_log_for_period, mark_rows_as_weekly_used
from src.email_sender import send_daily_digest, send_weekly_digest, send_monthly_digest
from config.settings import OUTPUT_DIR, LOGS_DIR

# Set up logging to both console and file
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(LOGS_DIR, f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"),
            encoding="utf-8"
        ),
    ]
)
logger = logging.getLogger(__name__)


def setup_directories():
    """Ensure all output/log dirs exist."""
    for d in [
        OUTPUT_DIR, LOGS_DIR,
        os.path.join(OUTPUT_DIR, "daily"),
        os.path.join(OUTPUT_DIR, "weekly"),
        os.path.join(OUTPUT_DIR, "monthly"),
    ]:
        os.makedirs(d, exist_ok=True)


def save_output(data, mode: str, subdir: str) -> str:
    """Saves analysis output JSON for debugging and archive."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filepath = os.path.join(OUTPUT_DIR, subdir, f"{mode}_{timestamp}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        if isinstance(data, str):
            json.dump({"analysis": data, "generated_at": timestamp}, f, indent=2, default=str)
        else:
            json.dump(data, f, indent=2, default=str)
    print(f"  💾 Output saved: {filepath}")
    return filepath


def run_daily_pipeline(
    dry_run: bool = False,
    skip_email: bool = False,
    skip_sheet: bool = False,
    verbose: bool = False,
    days_back: int = 1,
):
    """
    Full daily pipeline:
    1. Read portfolio from Google Sheet
    2. Fetch news for all stocks (Yahoo Finance + ET RSS)
    3. Filter + deduplicate news (fuzzy matching, recency)
    4. Analyze with Gemini in batches
    5. Write results to DailyLog sheet tab
    6. Send 3-section email digest
    """
    print("\n" + "=" * 60)
    print("🚀 DAILY PIPELINE")
    print(f"   News window: last {days_back} day(s)")
    print(f"   Dry run: {dry_run}")
    print("=" * 60)

    start_time = datetime.now()

    # ── Step 1: Portfolio ──────────────────────────────────
    print("\n[1/5] Reading portfolio from Google Sheet...")
    portfolio = read_full_portfolio()
    if not portfolio:
        logger.error("No portfolio data. Check sheet credentials and tab names in .env")
        return

    # ── Step 2: Fetch News ─────────────────────────────────
    print(f"\n[2/5] Fetching news...")
    raw_news = fetch_news_for_portfolio(portfolio, days_back=days_back)

    # ── Step 3: Filter ─────────────────────────────────────
    print("\n[3/5] Filtering news...")
    filtered_news = filter_portfolio_news(raw_news, days_back=days_back, verbose=verbose)

    # ── Step 4: Gemini Analysis ────────────────────────────
    print("\n[4/5] Running Gemini analysis...")
    analysis_results = run_daily_analysis(filtered_news)
    save_output(analysis_results, "daily", "daily")

    # Print console summary
    print("\n📋 TODAY'S PRIORITY SUMMARY:")
    print("─" * 58)
    for r in analysis_results:
        icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(r.get("priority"), "⚪")
        ticker = r.get("ticker", "?")
        gain = r.get("gain_pct")
        gain_str = f"{gain:+.1f}%" if gain is not None else "  N/A"
        action = r.get("action_hint", "?")
        sentiment = r.get("sentiment", "?")[:3].upper()
        print(f"  {icon} {r.get('priority','?'):6s} | {ticker:15s} | {gain_str:8s} | {sentiment} | {action}")

    # ── Step 5a: Sheet ──────────────────────────────────────
    if not skip_sheet and not dry_run:
        print("\n[5a/5] Writing to DailyLog sheet...")
        write_daily_results(analysis_results)
    else:
        print("\n[5a/5] Skipping sheet write.")

    # ── Step 5b: Email ──────────────────────────────────────
    # NOTE: filtered_news is passed here so Section 2 (news feed) can
    # display raw articles with links — no AI involved in that section.
    if not skip_email and not dry_run:
        print("\n[5b/5] Sending email digest...")
        send_daily_digest(analysis_results, portfolio, filtered_news)
    else:
        print("\n[5b/5] Skipping email send.")
        # Still save the HTML preview locally so you can check it
        from src.email_sender import build_daily_email_html
        html = build_daily_email_html(analysis_results, portfolio, filtered_news)
        preview_path = os.path.join(OUTPUT_DIR, "email_preview.html")
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  📄 Email preview saved: {preview_path}")

    elapsed = (datetime.now() - start_time).seconds
    print(f"\n✅ Daily pipeline complete in {elapsed}s")
    print("=" * 60)


def run_weekly_pipeline(dry_run: bool = False, skip_email: bool = False):
    """
    Weekly deep dive:
    1. Read 7 days of DailyLog from sheet
    2. Gemini weekly synthesis
    3. Send weekly email
    """
    print("\n" + "=" * 60)
    print("📅 WEEKLY PIPELINE")
    print("=" * 60)

    print("\n[1/3] Reading 7-day log...")
    weekly_data = read_log_for_period(days_back=7)

    if "No data" in weekly_data or "No historical" in weekly_data:
        print("  ⚠ Not enough log data. Run daily pipeline for a few days first.")
        return

    print("\n[2/3] Running Gemini weekly analysis...")
    weekly_analysis = run_weekly_analysis(weekly_data)
    save_output(weekly_analysis, "weekly", "weekly")

    print("\n📋 WEEKLY ANALYSIS PREVIEW:")
    print("─" * 58)
    print(weekly_analysis[:600] + ("..." if len(weekly_analysis) > 600 else ""))

    portfolio = read_full_portfolio()

    if not skip_email and not dry_run:
        print("\n[3/3] Sending weekly email...")
        send_weekly_digest(weekly_analysis, portfolio)
        mark_rows_as_weekly_used(days_back=7)
    else:
        print("\n[3/3] Skipping email.")

    print("\n✅ Weekly pipeline complete.")


def run_monthly_pipeline(dry_run: bool = False, skip_email: bool = False):
    """
    Monthly deep dive:
    1. Read 30 days of DailyLog
    2. Gemini monthly synthesis
    3. Send monthly email
    """
    print("\n" + "=" * 60)
    print("📆 MONTHLY PIPELINE")
    print("=" * 60)

    print("\n[1/3] Reading 30-day log...")
    monthly_data = read_log_for_period(days_back=30)

    print("\n[2/3] Reading portfolio...")
    portfolio = read_full_portfolio()

    print("\n[3/3] Running Gemini monthly analysis...")
    monthly_analysis = run_monthly_analysis(monthly_data, portfolio)
    save_output(monthly_analysis, "monthly", "monthly")

    print("\n📋 MONTHLY ANALYSIS PREVIEW:")
    print("─" * 58)
    print(monthly_analysis[:800] + ("..." if len(monthly_analysis) > 800 else ""))

    if not skip_email and not dry_run:
        from src.email_sender import send_monthly_digest
        send_monthly_digest(monthly_analysis)
    else:
        print("\nSkipping email.")

    print("\n✅ Monthly pipeline complete.")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Portfolio Intelligence Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/pipeline.py --mode daily
  python src/pipeline.py --mode daily --dry-run
  python src/pipeline.py --mode daily --no-email --verbose
  python src/pipeline.py --mode weekly
  python src/pipeline.py --mode monthly
        """
    )
    parser.add_argument("--mode", choices=["daily", "weekly", "monthly"], default="daily")
    parser.add_argument("--dry-run",  action="store_true", help="Skip email + sheet write")
    parser.add_argument("--no-email", action="store_true", help="Skip email only")
    parser.add_argument("--no-sheet", action="store_true", help="Skip sheet write only")
    parser.add_argument("--verbose",  action="store_true", help="Detailed filter logs")
    parser.add_argument("--days-back", type=int, default=1, help="News window in days (default: 1)")

    args = parser.parse_args()
    setup_directories()

    print(f"\n{'='*60}")
    print(f"  Portfolio Intelligence — {args.mode.upper()} MODE")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if args.mode == "daily":
        run_daily_pipeline(
            dry_run=args.dry_run,
            skip_email=args.no_email,
            skip_sheet=args.no_sheet,
            verbose=args.verbose,
            days_back=args.days_back,
        )
    elif args.mode == "weekly":
        run_weekly_pipeline(dry_run=args.dry_run, skip_email=args.no_email)
    elif args.mode == "monthly":
        run_monthly_pipeline(dry_run=args.dry_run, skip_email=args.no_email)


if __name__ == "__main__":
    main()