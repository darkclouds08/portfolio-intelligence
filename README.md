# 📈 Portfolio Intelligence Pipeline

An end-to-end agentic news monitoring system for your stock portfolio.
Fetches news for all your Indian + US stocks, runs LLM analysis via Gemini,
and delivers daily email digests + weekly/monthly deep dives.

## Features
- Reads portfolio from Google Sheets (iStocks + uStocks tabs)
- Fetches news from Yahoo Finance, MoneyControl, Economic Times RSS
- Pre-filters and deduplicates news before hitting Gemini (token efficient)
- Daily email digest with priority-ranked stocks needing attention
- Appends structured logs to Google Sheet for history
- Weekly & monthly deep dives using accumulated history

## File Structure
```
portfolio-intelligence/
├── README.md
├── requirements.txt
├── .env                        # API keys (never commit this)
├── .gitignore
├── config/
│   └── settings.py             # All config in one place
├── src/
│   ├── sheet_reader.py         # Read portfolio from Google Sheets
│   ├── news_fetcher.py         # Fetch news from all sources
│   ├── news_filter.py          # Pre-filter before sending to Gemini
│   ├── gemini_analyzer.py      # LLM analysis via Gemini API
│   ├── sheet_writer.py         # Write daily logs back to sheet
│   ├── email_sender.py         # Send daily digest via Gmail
│   └── pipeline.py             # Main orchestrator — run this
├── logs/                       # Runtime logs (gitignored)
└── output/
    ├── daily/                  # Daily JSON outputs (gitignored)
    ├── weekly/                 # Weekly reports
    └── monthly/                # Monthly reports
```

## Setup
1. `pip install -r requirements.txt`
2. Set up Google Cloud project, enable Sheets API + Gmail API
3. Download `credentials.json` to project root
4. Copy `.env.example` to `.env` and fill in values
5. Run once manually: `python src/pipeline.py --mode daily`
6. Schedule via cron or n8n

## Scheduling (cron example)
```
# Daily at 7:30 AM
30 7 * * 1-5 cd /path/to/portfolio-intelligence && python src/pipeline.py --mode daily

# Weekly on Sunday at 8 AM  
0 8 * * 0 cd /path/to/portfolio-intelligence && python src/pipeline.py --mode weekly

# Monthly on 1st at 8 AM
0 8 1 * * cd /path/to/portfolio-intelligence && python src/pipeline.py --mode monthly
```
