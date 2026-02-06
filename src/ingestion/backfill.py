import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from edgar import *
from src.db import db_utils
from datetime import datetime, timedelta

set_identity("Brandon Cheng chengbr3@gmail.com")

tickers = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT", "AMZN", 
    "AVGO", "AXP", "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "BRK.B", "C", 
    "CAT", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS", "CVX", 
    "DE", "DHR", "DIS", "DUK", "EMR", "FDX", "GD", "GE", "GILD", "GM", 
    "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU", "ISRG", "JNJ", 
    "JPM", "KO", "LIN", "LLY", "LMT", "LOW", "MA", "MCD", "MDLZ", "MDT", 
    "MET", "META", "MMM", "MO", "MRK", "MS", "MSFT", "NEE", "NFLX", "NKE", 
    "NOW", "NVDA", "ORCL", "PEP", "PFE", "PG", "PLTR", "PM", "PYPL", "QCOM", 
    "RTX", "SBUX", "SCHW", "SO", "SPG", "T", "TGT", "TMO", "TMUS", "TSLA", 
    "TXN", "UBER", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WFC", "WMT", "XOM"
]

six_months_ago = datetime.now() - timedelta(days=180)
date_filter = f"{six_months_ago.strftime('%Y-%m-%d')}:"

total = len(tickers)

# 3. OPEN ONCE: Moving the guard outside the loop for speed
with db_utils.get_conn() as conn:
    for i, ticker in enumerate(tickers, 1):
        # Progress Trackingç
        sys.stdout.write(f"\r🚀 [{i}/{total}] Syncing {ticker}...          ")
        sys.stdout.flush()

        try:
            company = Company(ticker)
            
            # Upsert company info
            db_utils.upsert_company(
                conn, 
                company.cik, 
                company.name, 
                ticker, 
                company.industry
            )
            
            # 4. FILTER AT SOURCE: Fetch only 8-Ks and 10-Ks/Qs in the date window
            # The colon in "date_filter" means "from that date to now"
            filings = company.get_filings(form=["8-K", "10-K", "10-Q"]).filter(date=date_filter)
            
            for filing in filings:
                # Direct insertion (SEC already filtered the date for us!)
                db_utils.insert_filing(
                    conn,
                    filing.accession_no,
                    company.cik,
                    filing.form,
                    filing.acceptance_datetime,
                    filing.filing_date,
                    filing.primary_document or None
                )
            
            # Respect SEC Rate Limit (10 requests/sec)
            time.sleep(0.1) 

        except Exception as e:
            print(f"\n❌ Error with {ticker}: {e}")

print("\n\n✅ Backfill complete!")
                
                