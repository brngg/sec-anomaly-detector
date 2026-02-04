import json
from edgar import *

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

for ticker in tickers:
    try:
        company = Company(ticker)
        
        print("-----")
        # Using f-strings avoids the concatenation errors entirely
        print(f"Name: {company.name}")
        print(f"Tickers: {company.tickers}")
        print(f"CIK: {company.cik}")
        print(f"Industry: {company.industry}")
        
        # Some properties might be missing for certain companies, 
        # f-strings handle 'None' gracefully as well.
        print(f"Fiscal Year End: {company.fiscal_year_end}")
        print("-----")
        
    except Exception as e:
        print(f"Error fetching {ticker}: {e}")