"""
src/sheet_reader.py
====================
Reads portfolio data from Google Sheets.

KEY CHANGE: Reads by HEADER NAME not column index position.
This means adding/moving/renaming columns in your sheet won't break things.
The code finds each column by looking for its name in the header row.

Handles both:
  - iStocks tab: Indian stocks (NSE/BSE)
  - uStocks tab: US stocks (NASDAQ/NYSE)
"""

import sys
import os
import logging
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from tqdm import tqdm

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    SPREADSHEET_ID,
    ISTOCK_TAB_NAME,
    USTOCK_TAB_NAME,
    ISTOCK_HEADER_ROW,
    USTOCK_HEADER_ROW,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

CREDENTIALS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "credentials.json"
)


def get_gspread_client() -> gspread.Client:
    """Authenticates and returns a gspread client."""
    print("🔐 Authenticating with Google Sheets API...")
    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"credentials.json not found at {CREDENTIALS_FILE}"
        )
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    print("✅ Authenticated successfully.")
    return client


def clean_number(value: str) -> Optional[float]:
    """
    Converts '₹873', '$205', '-₹5,322', '13%' etc. to float.
    Returns None if empty or non-numeric.
    """
    if not value or str(value).strip() in ("#N/A", "N/A", "-", "", "#VALUE!", "#REF!"):
        return None
    cleaned = (
        str(value)
        .replace("₹", "").replace("$", "")
        .replace(",", "").replace("%", "")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        return None


def build_col_map(headers: list[str]) -> dict[str, int]:
    """
    Builds a dict mapping lowercase header name → column index.
    e.g. {"ticker": 0, "name": 1, "shares": 2, ...}
    Strips whitespace and lowercases for fuzzy matching.
    """
    return {h.strip().lower(): i for i, h in enumerate(headers)}


def get_col(row: list, col_map: dict, *possible_names: str) -> str:
    """
    Gets a cell value from a row by trying multiple possible column names.
    Returns empty string if none found.

    e.g. get_col(row, col_map, "avg price", "avg. price", "average price")
    will try each name until one matches.
    """
    for name in possible_names:
        idx = col_map.get(name.lower())
        if idx is not None and idx < len(row):
            return row[idx]
    return ""


def read_indian_stocks(sheet: gspread.Spreadsheet) -> list[dict]:
    """
    Reads the iStocks tab. Uses header names to find columns,
    so extra/reordered columns don't matter.
    """
    print(f"\n📊 Reading Indian stocks from '{ISTOCK_TAB_NAME}' tab...")

    try:
        worksheet = sheet.worksheet(ISTOCK_TAB_NAME)
    except gspread.WorksheetNotFound:
        logger.error(f"Tab '{ISTOCK_TAB_NAME}' not found.")
        return []

    all_rows = worksheet.get_all_values()

    # Debug: show what we're working with
    print(f"   Total rows in sheet (including headers/totals): {len(all_rows)}")

    # Header is at ISTOCK_HEADER_ROW (1-indexed) → 0-indexed = ISTOCK_HEADER_ROW - 1
    header_idx = ISTOCK_HEADER_ROW - 1
    if header_idx >= len(all_rows):
        logger.error(f"Sheet has {len(all_rows)} rows but header expected at row {ISTOCK_HEADER_ROW}")
        return []

    headers = all_rows[header_idx]
    data_rows = all_rows[header_idx + 1:]  # Everything after the header row

    # Build column name → index map
    col_map = build_col_map(headers)

    print(f"   Headers found: {headers}")
    print(f"   Column map: {col_map}")
    print(f"   Data rows to parse: {len(data_rows)}")

    stocks = []
    skipped = 0

    for row in tqdm(data_rows, desc="   Parsing Indian stocks", unit="stock"):
        # Skip completely empty rows
        if not any(cell.strip() for cell in row):
            skipped += 1
            continue

        # Get ticker — try common column names
        ticker_raw = get_col(row, col_map, "ticker", "symbol", "stock")
        ticker = ticker_raw.strip()

        # Skip total rows, empty rows, or header repeats
        if not ticker or ticker.lower() in ("ticker", "total", "symbol", ""):
            skipped += 1
            continue

        # Clean ticker — strip NSE: prefix for Yahoo Finance
        yahoo_ticker = ticker.replace("NSE:", "").replace("BSE:", "").strip()

        # Read each field by header name — handles any column order
        name         = get_col(row, col_map, "name", "company name", "company")
        shares_str   = get_col(row, col_map, "shares", "qty", "quantity", "units")
        avg_price_str= get_col(row, col_map, "avg price", "avg. price", "average price", "buy price", "avg")
        mkt_price_str= get_col(row, col_map, "mkt price", "market price", "current price", "ltp", "price")
        invested_str = get_col(row, col_map, "invested", "amount invested", "investment", "cost")
        cur_val_str  = get_col(row, col_map, "current value", "value", "cur value", "market value")
        gain_str     = get_col(row, col_map, "gain %", "gain%", "return %", "return%", "returns %", "gain ", "gain")
        profit_str   = get_col(row, col_map, "profit", "p&l", "pnl", "unrealized p&l")
        d_chg_pct_str= get_col(row, col_map, "daily change %", "daily chg %", "day change %", "1d change %")
        d_chg_str    = get_col(row, col_map, "daily change", "daily chg", "day change")
        sector       = get_col(row, col_map, "sector", "industry", "category")
        mkt_cap      = get_col(row, col_map, "market cap", "cap", "market cap category", "cap category")

        stock = {
            "ticker":            ticker,
            "yahoo_ticker":      yahoo_ticker,
            "name":              name.strip() if name else ticker,
            "exchange":          "NSE",
            "market":            "IN",
            "shares":            clean_number(shares_str),
            "avg_price_inr":     clean_number(avg_price_str),
            "mkt_price_inr":     clean_number(mkt_price_str),
            "invested_inr":      clean_number(invested_str),
            "current_value_inr": clean_number(cur_val_str),
            "gain_pct":          clean_number(gain_str),
            "profit_inr":        clean_number(profit_str),
            "daily_change_pct":  clean_number(d_chg_pct_str),
            "daily_change_inr":  clean_number(d_chg_str),
            "sector":            sector.strip() if sector else "Unknown",
            "market_cap_category": mkt_cap.strip() if mkt_cap else "Unknown",
        }

        # Skip rows where we couldn't find avg_price (means the row is probably empty/invalid)
        if stock["avg_price_inr"] is None:
            logger.debug(f"   Skipping {ticker} — avg_price not found. Row: {row}")
            skipped += 1
            continue

        stocks.append(stock)

    print(f"   ✅ Loaded {len(stocks)} Indian stocks. (Skipped {skipped} rows)")
    return stocks


def read_us_stocks(sheet: gspread.Spreadsheet) -> list[dict]:
    """
    Reads the uStocks tab. Uses header names to find columns.
    """
    print(f"\n📊 Reading US stocks from '{USTOCK_TAB_NAME}' tab...")

    try:
        worksheet = sheet.worksheet(USTOCK_TAB_NAME)
    except gspread.WorksheetNotFound:
        logger.error(f"Tab '{USTOCK_TAB_NAME}' not found.")
        return []

    all_rows = worksheet.get_all_values()
    print(f"   Total rows in sheet: {len(all_rows)}")

    header_idx = USTOCK_HEADER_ROW - 1
    if header_idx >= len(all_rows):
        logger.error(f"Sheet has {len(all_rows)} rows but header expected at row {USTOCK_HEADER_ROW}")
        return []

    headers = all_rows[header_idx]
    data_rows = all_rows[header_idx + 1:]

    col_map = build_col_map(headers)

    print(f"   Headers found: {headers}")
    print(f"   Column map: {col_map}")
    print(f"   Data rows to parse: {len(data_rows)}")

    stocks = []
    skipped = 0

    for row in tqdm(data_rows, desc="   Parsing US stocks", unit="stock"):
        if not any(cell.strip() for cell in row):
            skipped += 1
            continue

        ticker_raw = get_col(row, col_map, "ticker", "symbol", "stock")
        ticker = ticker_raw.strip()

        if not ticker or ticker.lower() in ("ticker", "total", "symbol", ""):
            skipped += 1
            continue

        name          = get_col(row, col_map, "name", "company name", "company")
        qty_str       = get_col(row, col_map, "quantity", "qty", "shares", "units")
        avg_price_str = get_col(row, col_map, "avg. price", "avg price", "average price", "buy price", "avg")
        mkt_price_str = get_col(row, col_map, "mkt price", "market price", "current price", "ltp", "price")
        rs_inv_str    = get_col(row, col_map, "rs invested", "rs. invested", "inr invested", "invested (inr)", "invested")
        usd_inv_str   = get_col(row, col_map, "usd invested", "$ invested", "invested (usd)")
        value_str     = get_col(row, col_map, "value", "current value", "market value")
        profit_str    = get_col(row, col_map, "profit", "p&l", "pnl")
        gain_str      = get_col(row, col_map, "gain ", "gain%", "gain %", "return %", "gain")
        today_gain_str= get_col(row, col_map, "today gain", "today gain %", "1d gain %", "daily gain")

        stock = {
            "ticker":        ticker,
            "yahoo_ticker":  ticker,   # US tickers work directly with Yahoo
            "name":          name.strip() if name else ticker,
            "exchange":      "NASDAQ",
            "market":        "US",
            "shares":        clean_number(qty_str),
            "avg_price_usd": clean_number(avg_price_str),
            "mkt_price_usd": clean_number(mkt_price_str),
            "invested_inr":  clean_number(rs_inv_str),
            "usd_invested":  clean_number(usd_inv_str),
            "current_value_usd": clean_number(value_str),
            "profit_usd":    clean_number(profit_str),
            "gain_pct":      clean_number(gain_str),
            "daily_change_pct": clean_number(today_gain_str),
            "sector":        _infer_us_sector(ticker),
            "market_cap_category": "Unknown",
        }

        if stock["avg_price_usd"] is None:
            logger.debug(f"   Skipping {ticker} — avg_price not found. Row: {row}")
            skipped += 1
            continue

        stocks.append(stock)

    print(f"   ✅ Loaded {len(stocks)} US stocks. (Skipped {skipped} rows)")
    return stocks


def _infer_us_sector(ticker: str) -> str:
    """Quick sector lookup for US tickers."""
    sector_map = {
        "AAPL": "Technology",  "MSFT": "Technology",   "GOOGL": "Technology",
        "AMZN": "Consumer Discretionary", "META": "Technology",
        "NVDA": "Technology",  "AMD":  "Technology",   "INTC": "Technology",
        "ADBE": "Technology",  "SNOW": "Technology",   "NET":  "Technology",
        "PLTR": "Technology",  "CRWD": "Technology",   "ARM":  "Technology",
        "MNDY": "Technology",  "DDOG": "Technology",   "TSM":  "Technology",
        "AVGO": "Technology",  "QCOM": "Technology",   "SYM":  "Technology",
        "RDDT": "Technology",
        "QQQ":  "ETF",  "QTUM": "ETF",  "VXUS": "ETF",
        "JNJ":  "Healthcare",
        "SOFI": "Financial Services",  "PYPL": "Financial Services",
        "NU":   "Financial Services",
        "UBER": "Consumer Discretionary",
    }
    return sector_map.get(ticker.upper(), "Technology")


def read_full_portfolio() -> list[dict]:
    """
    Main entry point — reads both tabs and returns unified stock list.
    """
    print("\n" + "=" * 60)
    print("📋 PORTFOLIO READER")
    print("=" * 60)

    if not SPREADSHEET_ID:
        raise ValueError("SPREADSHEET_ID not set in .env")

    client = get_gspread_client()
    print(f"\n📂 Opening spreadsheet: {SPREADSHEET_ID}")
    sheet = client.open_by_key(SPREADSHEET_ID)

    indian_stocks = read_indian_stocks(sheet)
    us_stocks = read_us_stocks(sheet)
    all_stocks = indian_stocks + us_stocks

    print(f"\n{'=' * 60}")
    print(f"✅ Total portfolio: {len(all_stocks)} stocks")
    print(f"   🇮🇳 Indian: {len(indian_stocks)} | 🇺🇸 US: {len(us_stocks)}")
    print(f"{'=' * 60}\n")

    return all_stocks


if __name__ == "__main__":
    portfolio = read_full_portfolio()
    print("\n📌 Sample (first 5 stocks):")
    for s in portfolio[:5]:
        gain = s.get("gain_pct")
        gain_str = f"{gain:+.1f}%" if gain is not None else "N/A"
        print(f"  [{s['market']}] {s['ticker']:15s} {s['name']:35s} Gain: {gain_str}")