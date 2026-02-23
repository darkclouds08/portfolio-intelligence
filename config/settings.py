"""
config/settings.py
==================
Central configuration for the Portfolio Intelligence Pipeline.
All tuneable parameters live here — don't scatter magic numbers in code.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Google Sheets
# ─────────────────────────────────────────────
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ISTOCK_TAB_NAME = os.getenv("ISTOCK_TAB_NAME", "iStocks")
USTOCK_TAB_NAME = os.getenv("USTOCK_TAB_NAME", "uStocks")
LOG_TAB_NAME = os.getenv("LOG_TAB_NAME", "DailyLog")

# Row where headers are (1-indexed). Row 3 based on your sheet screenshot.
ISTOCK_HEADER_ROW = 3
USTOCK_HEADER_ROW = 3

# ─────────────────────────────────────────────
# Gemini API
# ─────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash-lite"

# How many stocks to send in one Gemini call
GEMINI_BATCH_SIZE = 5

# ─────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# ─────────────────────────────────────────────
# News Fetching
# ─────────────────────────────────────────────

# 24 hour news window for daily runs
NEWS_DAYS_BACK = 1

# Max articles per stock before Gemini (token control)
MAX_ARTICLES_PER_STOCK = 5

# Max words per article body sent to Gemini (token control)
MAX_WORDS_PER_ARTICLE = 300

# Request timeout in seconds
REQUEST_TIMEOUT = 10

# Delay between requests (seconds)
REQUEST_DELAY = 0.5

# Fuzzy match threshold for article relevance check (0-100)
# 70 means article title/summary must share 70% of keywords with company name
FUZZY_MATCH_THRESHOLD = 70

# ─────────────────────────────────────────────
# Ticker → Aliases map
# Used by fuzzy matcher so "Infosys" matches NSE:INFY,
# "TSMC" matches TSM, etc.
# Add/edit freely — more aliases = better news matching.
# ─────────────────────────────────────────────
TICKER_ALIASES = {
    # ── Indian stocks ──────────────────────────
    "INFY":         ["Infosys", "Infy"],
    "NSE:INFY":     ["Infosys", "Infy"],
    "TCS":          ["Tata Consultancy", "TCS"],
    "NSE:TCS":      ["Tata Consultancy", "TCS"],
    "HCLTECH":      ["HCL Technologies", "HCL Tech", "HCL"],
    "NSE:BEL":      ["Bharat Electronics", "BEL"],
    "TMPV":         ["Tata Motors", "Tata Motor"],
    "TATAPOWER":    ["Tata Power"],
    "RELIANCE":     ["Reliance Industries", "Reliance Jio", "Reliance Retail", "RIL"],
    "INDIGO":       ["IndiGo", "Interglobe Aviation", "InterGlobe"],
    "NEWGEN":       ["Newgen Software", "Newgen"],
    "LT":           ["Larsen & Toubro", "L&T", "Larsen Toubro"],
    "AFFLE":        ["Affle India", "Affle"],
    "VBL":          ["Varun Beverages", "Varun Bev"],
    "FEDERALBNK":   ["Federal Bank"],
    "NSE:ITC":      ["ITC Limited", "ITC"],
    "MAPMYINDIA":   ["CE Info System", "MapmyIndia", "CE Info"],
    "IDFCFIRSTB":   ["IDFC First Bank", "IDFC First"],
    "KPITTECH":     ["KPIT Technologies", "KPIT Tech", "KPIT"],
    "NSE:UPL":      ["UPL Limited", "UPL"],
    "DIXON":        ["Dixon Technologies", "Dixon Tech"],
    "WIPRO":        ["Wipro"],
    "HDFCBANK":     ["HDFC Bank", "HDFC"],
    "TVSSCS":       ["TVS Supply Chain", "TVS SCS"],
    "SUZLON":       ["Suzlon Energy", "Suzlon"],
    "ARE&M":        ["Amara Raja", "Amara Raja Energy", "Amara Raja Batteries"],
    "BECTORFOOD":   ["Mrs Bectors Food", "Mrs Bectors", "Bectors"],
    "SBICARD":      ["SBI Cards", "SBI Card"],
    "DRREDDY":      ["Dr Reddys", "Dr. Reddy", "Dr Reddy Labs"],
    "IKIO":         ["IKIO Lighting", "IKIO"],
    "M&M":          ["Mahindra", "Mahindra & Mahindra", "M&M"],

    # ── US stocks ──────────────────────────────
    "AMZN":  ["Amazon", "Amazon.com"],
    "AAPL":  ["Apple"],
    "GOOGL": ["Google", "Alphabet"],
    "MSFT":  ["Microsoft"],
    "NVDA":  ["Nvidia", "NVIDIA"],
    "AMD":   ["AMD", "Advanced Micro Devices"],
    "ADBE":  ["Adobe"],
    "QQQ":   ["Invesco QQQ", "QQQ ETF", "Nasdaq ETF"],
    "CRWD":  ["CrowdStrike", "Crowdstrike"],
    "META":  ["Meta", "Meta Platforms", "Facebook"],
    "SOFI":  ["SoFi Technologies", "SoFi"],
    "JNJ":   ["Johnson & Johnson", "J&J"],
    "ARM":   ["Arm Holdings", "ARM Holdings"],
    "SNOW":  ["Snowflake"],
    "PLTR":  ["Palantir"],
    "INTC":  ["Intel"],
    "NU":    ["Nu Holdings", "Nubank"],
    "RDDT":  ["Reddit"],
    "MNDY":  ["Monday.com"],
    "NET":   ["Cloudflare"],
    "AVGO":  ["Broadcom"],
    "TSM":   ["TSMC", "Taiwan Semiconductor"],
    "UBER":  ["Uber"],
    "PYPL":  ["PayPal"],
    "QTUM":  ["Defiance Quantum", "QTUM ETF"],
    "DDOG":  ["Datadog"],
    "QCOM":  ["Qualcomm"],
    "SYM":   ["Symbotic"],
    "VXUS":  ["Vanguard Total International", "VXUS ETF"],
}

# ─────────────────────────────────────────────
# News Sources
# ─────────────────────────────────────────────
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
YAHOO_NSE_SUFFIX = ".NS"
YAHOO_BSE_SUFFIX = ".BO"

INDIAN_SECTOR_RSS = {
    "IT":                  "https://economictimes.indiatimes.com/tech/information-tech/rssfeeds/13357270.cms",
    "Financial Services":  "https://economictimes.indiatimes.com/industry/banking/finance/rssfeeds/13357271.cms",
    "FMCG":                "https://economictimes.indiatimes.com/industry/cons-products/fmcg/rssfeeds/13357395.cms",
    "Automobile":          "https://economictimes.indiatimes.com/industry/auto/rssfeeds/13357270.cms",
    "Energy":              "https://economictimes.indiatimes.com/industry/energy/rssfeeds/13357397.cms",
    "Infrastructure":      "https://economictimes.indiatimes.com/industry/indl-goods/svs/construction/rssfeeds/13357396.cms",
    "Healthcare":          "https://economictimes.indiatimes.com/industry/healthcare/biotech/rssfeeds/13357399.cms",
    "Chemicals":           "https://economictimes.indiatimes.com/industry/indl-goods/svs/chemicals/rssfeeds/13357270.cms",
    "Electronics":         "https://economictimes.indiatimes.com/industry/cons-products/electronics/rssfeeds/13357270.cms",
    "Services":            "https://economictimes.indiatimes.com/industry/services/rssfeeds/13357270.cms",
    "Aerospace & Defense": "https://economictimes.indiatimes.com/industry/indl-goods/svs/defence/rssfeeds/13357270.cms",
    "Oil and Gas":         "https://economictimes.indiatimes.com/industry/energy/oil-gas/rssfeeds/13357270.cms",
}

US_MARKET_RSS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
]

# ─────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")