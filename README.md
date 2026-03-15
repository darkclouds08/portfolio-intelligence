# Portfolio Intelligence — Agentic LLM System

Multi-agent workflow for automated portfolio analysis and rebalancing recommendations.

## How It Works

1. **News Agent**: Scrapes financial news, fuzzy-matches tickers
2. **YouTube Agent**: Extracts insights from creator content via transcripts
3. **Fundamentals Agent**: Fetches PE, debt, revenue growth from Yahoo/Screener
4. **Technical Agent**: Calculates DMAs, Bollinger Bands, RSI
5. **Rebalancing Agent**: Synthesizes all data → recommendations
6. **Email Report**: Daily automated delivery via Gmail SMTP

## Tech Stack
- Google Gemini API (LLM)
- DSPy (prompt optimization)
- Yahoo Finance API, Screener.in
- GitHub Actions (automation)

## Why This Matters
Traditional portfolio rebalancing requires manual research across 5+ sources. This system automates the full workflow while maintaining decision transparency through structured agent outputs.

---

*Note: Source code is proprietary. This repo contains architecture documentation only.*
