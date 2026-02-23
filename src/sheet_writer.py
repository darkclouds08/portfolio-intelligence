"""
src/sheet_writer.py
====================
Writes daily analysis results back to your Google Sheet (DailyLog tab).
Also reads historical log data for weekly/monthly analysis.

The DailyLog tab schema:
  A: Date | B: Ticker | C: Name | D: Market | E: Sector |
  F: Sentiment | G: Priority | H: Action Hint | I: Thesis Status |
  J: Summary | K: Gain% at time | L: Profit(₹) | M: Weekly Used

If the DailyLog tab doesn't exist, this script will create it automatically.
"""

import sys
import os
import logging
from datetime import datetime, timedelta
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from tqdm import tqdm

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    SPREADSHEET_ID,
    LOG_TAB_NAME,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Need write scope this time
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

CREDENTIALS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "credentials.json"
)

# Column headers for the DailyLog tab
LOG_HEADERS = [
    "Date", "Ticker", "Name", "Market", "Sector",
    "Sentiment", "Priority", "Action Hint", "Thesis Status",
    "Summary", "Gain% At Time", "Profit (₹)", "Weekly Used"
]


def get_write_client() -> gspread.Client:
    """Auth with write scope."""
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_log_tab(sheet: gspread.Spreadsheet) -> gspread.Worksheet:
    """
    Gets the DailyLog tab or creates it if it doesn't exist.
    Adds header row if tab is newly created.
    """
    try:
        worksheet = sheet.worksheet(LOG_TAB_NAME)
        logger.debug(f"Found existing '{LOG_TAB_NAME}' tab.")
        return worksheet
    except gspread.WorksheetNotFound:
        print(f"  Creating '{LOG_TAB_NAME}' tab (first run)...")
        worksheet = sheet.add_worksheet(
            title=LOG_TAB_NAME,
            rows=5000,   # Pre-allocate rows for a couple years of data
            cols=len(LOG_HEADERS)
        )
        # Add headers
        worksheet.update("A1", [LOG_HEADERS])
        # Bold the header row
        worksheet.format("A1:M1", {"textFormat": {"bold": True}})
        print(f"  ✅ Created '{LOG_TAB_NAME}' tab with headers.")
        return worksheet


def write_daily_results(analysis_results: list[dict]) -> bool:
    """
    Appends today's analysis results to the DailyLog tab.

    Args:
        analysis_results: List of analysis dicts from gemini_analyzer.run_daily_analysis()

    Returns:
        True if successful
    """
    print("\n📝 Writing results to Google Sheet...")

    if not SPREADSHEET_ID:
        logger.error("SPREADSHEET_ID not set")
        return False

    try:
        client = get_write_client()
        sheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = get_or_create_log_tab(sheet)

        today = datetime.now().strftime("%Y-%m-%d")
        rows_to_append = []

        for result in tqdm(analysis_results, desc="  Preparing rows", unit="stock"):
            gain_pct = result.get("gain_pct")
            gain_str = f"{gain_pct:+.1f}%" if gain_pct is not None else ""

            profit = result.get("profit_inr")
            profit_str = f"₹{profit:+,.0f}" if profit is not None else ""

            row = [
                today,                                  # A: Date
                result.get("ticker", ""),               # B: Ticker
                result.get("name", ""),                 # C: Name
                result.get("market", ""),               # D: Market
                result.get("sector", ""),               # E: Sector
                result.get("sentiment", ""),            # F: Sentiment
                result.get("priority", ""),             # G: Priority
                result.get("action_hint", ""),          # H: Action Hint
                result.get("thesis_status", ""),        # I: Thesis Status
                result.get("summary", ""),              # J: Summary
                gain_str,                               # K: Gain% at time
                profit_str,                             # L: Profit
                "No",                                   # M: Weekly Used (not yet)
            ]
            rows_to_append.append(row)

        # Batch append all rows at once (single API call = efficient)
        worksheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")

        print(f"  ✅ Appended {len(rows_to_append)} rows to '{LOG_TAB_NAME}'")

        # Color-code priority cells for visual scanning
        _apply_priority_colors(worksheet, rows_to_append)

        return True

    except Exception as e:
        logger.error(f"Failed to write to sheet: {e}")
        return False


def _apply_priority_colors(worksheet: gspread.Worksheet, rows: list) -> None:
    """
    Applies green/yellow/red background to priority cells for easy scanning.
    Non-critical — failures here are just cosmetic.
    """
    try:
        # Find last N rows (the ones we just appended)
        all_values = worksheet.get_all_values()
        start_row = len(all_values) - len(rows) + 1  # 1-indexed

        priority_formats = {
            "HIGH":   {"backgroundColor": {"red": 1.0, "green": 0.8, "blue": 0.8}},
            "MEDIUM": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.7}},
            "LOW":    {"backgroundColor": {"red": 0.85, "green": 1.0, "blue": 0.85}},
        }

        for i, row in enumerate(rows):
            priority = row[6]  # Column G (0-indexed position 6)
            if priority in priority_formats:
                cell = f"G{start_row + i}"
                worksheet.format(cell, priority_formats[priority])

    except Exception as e:
        logger.debug(f"Could not apply priority colors: {e}")


def read_log_for_period(days_back: int = 7) -> str:
    """
    Reads the DailyLog tab for the past N days.
    Used to feed data to the weekly/monthly Gemini analysis.

    Returns formatted text string of all log entries in period.
    """
    print(f"\n📖 Reading log data for past {days_back} days...")

    try:
        client = get_write_client()
        sheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = get_or_create_log_tab(sheet)

        all_rows = worksheet.get_all_values()
        if len(all_rows) <= 1:
            print("  No log data found yet.")
            return "No historical data available."

        headers = all_rows[0]
        data_rows = all_rows[1:]

        # Filter to rows within the date range
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        recent_rows = [
            row for row in data_rows
            if row and row[0] >= cutoff  # String comparison works for YYYY-MM-DD
        ]

        print(f"  Found {len(recent_rows)} log entries in past {days_back} days")

        if not recent_rows:
            return f"No data found in the past {days_back} days."

        # Format as readable text for Gemini
        lines = []
        for row in recent_rows:
            if len(row) < 10:
                continue
            date = row[0]
            ticker = row[1]
            sentiment = row[5]
            priority = row[6]
            action = row[7]
            summary = row[9]
            gain = row[10]

            lines.append(
                f"[{date}] {ticker} | {sentiment.upper()} | {priority} priority | "
                f"Gain: {gain} | {action}\n"
                f"  → {summary}"
            )

        return "\n\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to read log: {e}")
        return f"Could not read log data: {e}"


def mark_rows_as_weekly_used(days_back: int = 7) -> None:
    """
    Marks rows as 'Yes' in the Weekly Used column after a weekly run.
    Prevents double-counting in the next weekly analysis.
    """
    print("  Marking rows as weekly used...")

    try:
        client = get_write_client()
        sheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = get_or_create_log_tab(sheet)

        all_rows = worksheet.get_all_values()
        if len(all_rows) <= 1:
            return

        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        updates = []

        for i, row in enumerate(all_rows[1:], start=2):  # 1-indexed, skip header
            if row and row[0] >= cutoff and len(row) >= 13:
                if row[12] == "No":  # Weekly Used column (M)
                    updates.append({"range": f"M{i}", "values": [["Yes"]]})

        if updates:
            worksheet.batch_update(updates)
            print(f"  ✅ Marked {len(updates)} rows as weekly used.")

    except Exception as e:
        logger.debug(f"Could not mark weekly used: {e}")


if __name__ == "__main__":
    # Test: Read recent log data
    print("Reading last 7 days of log data...\n")
    log_text = read_log_for_period(days_back=7)
    print(log_text[:1000] if len(log_text) > 1000 else log_text)
