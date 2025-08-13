from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import requests
from datetime import datetime, timedelta
from typing import Optional

app = FastAPI()

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

from dotenv import load_dotenv
import os

load_dotenv()

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_PREM_API_KEY")
if not ALPHA_VANTAGE_API_KEY:
    raise ValueError("ALPHA_VANTAGE_PREM_API_KEY not found in environment variables")

async def fetch_alpha_vantage_data(function: str, symbol: str, **params):
    """Fetch data from Alpha Vantage API"""
    try:
        base_url = "https://www.alphavantage.co/query"
        params_dict = {
            "function": function,
            "symbol": symbol,
            "apikey": ALPHA_VANTAGE_API_KEY,
            **params
        }
        
        print(f"Alpha Vantage API call: {function} for {symbol} with params: {params}")
        
        response = requests.get(base_url, params=params_dict, timeout=15)
        data = response.json()
        
        print(f"Alpha Vantage response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
        
        if "Error Message" in data:
            print(f"Alpha Vantage Error: {data['Error Message']}")
            raise HTTPException(status_code=404, detail=f"Stock symbol {symbol} not found")
        if "Note" in data:
            print(f"Alpha Vantage Note: {data['Note']}")
            raise HTTPException(status_code=429, detail="API rate limit exceeded")
        if "Information" in data:
            print(f"Alpha Vantage Information: {data['Information']}")
            raise HTTPException(status_code=429, detail="API call frequency limit reached")
            
        return data
        
    except requests.RequestException as e:
        print(f"Request error: {e}")
        raise HTTPException(status_code=503, detail="External API service unavailable")


@app.get("/")
async def read_root():
    return {"message": "Stock API Server with Alpha Vantage"}

@app.get("/api/stock/{symbol}/chart")
async def get_stock_chart(symbol: str, range_period: Optional[str] = None):
    """Get historical stock data for charting with range support"""
    print(f"\n=== CHART REQUEST ===")
    print(f"Symbol: {symbol}, Range: {range_period}")
    
    print("Using daily data...")
    data = await fetch_alpha_vantage_data("TIME_SERIES_DAILY_ADJUSTED", symbol.upper(), outputsize="full")
    
    if not data or "Time Series (Daily)" not in data:
        raise HTTPException(status_code=404, detail=f"No chart data found for {symbol}")
    
    time_series = data["Time Series (Daily)"]
    print(f"Daily data points: {len(time_series)}")
    
    candle_data = []
    volume_data = []
    
    all_dates = sorted(time_series.keys())
    
    for date in all_dates:
        row = time_series[date]
        
        split_coefficient = float(row.get('8. split coefficient', 1.0))
        adjusted_close = float(row['5. adjusted close'])
        raw_close = float(row['4. close'])
        
        if raw_close != 0:
            adjustment_factor = adjusted_close / raw_close
        else:
            adjustment_factor = split_coefficient
        
        candle = {
            "time": date,
            "open": round(float(row['1. open']) * adjustment_factor, 2),
            "high": round(float(row['2. high']) * adjustment_factor, 2),
            "low": round(float(row['3. low']) * adjustment_factor, 2),
            "close": round(adjusted_close, 2)
        }
        
        volume = {
            "time": date,
            "value": int(row['6. volume']),
            "color": 'rgba(38, 166, 154, 0.4)' if adjusted_close >= (float(row['1. open']) * adjustment_factor) else 'rgba(239, 83, 80, 0.4)'
        }
        
        candle_data.append(candle)
        volume_data.append(volume)
    
    print(f"Final data: {len(candle_data)} candles, {len(volume_data)} volume points")
    
    return {
        "candleData": candle_data, 
        "volumeData": volume_data,
        "requestedRange": range_period,
        "dataType": "daily"
    }


@app.get("/api/stock/{symbol}/quote")
async def get_stock_quote(symbol: str):
    """Get current stock quote"""
    print(f"Fetching quote for {symbol}")
    
    data = await fetch_alpha_vantage_data("GLOBAL_QUOTE", symbol.upper())
    
    if not data or "Global Quote" not in data:
        raise HTTPException(status_code=404, detail=f"No quote data found for {symbol}")
    
    quote = data["Global Quote"]
    
    if not quote or "01. symbol" not in quote:
        raise HTTPException(status_code=404, detail=f"Invalid quote data for {symbol}")
    
    current_price = float(quote["05. price"])
    change = float(quote["09. change"])
    change_percent = float(quote["10. change percent"].replace("%", ""))
    
    return {
        "symbol": symbol.upper(),
        "name": f"{symbol.upper()} Inc.",
        "sector": "Technology",
        "price": round(current_price, 2),
        "change": round(change, 2),
        "changePercent": round(change_percent, 2),
        "open": round(float(quote["02. open"]), 2),
        "high": round(float(quote["03. high"]), 2),
        "low": round(float(quote["04. low"]), 2),
        "volume": int(quote["06. volume"]),
        "marketCap": 1000000000000
    }

@app.get("/api/stocks/quotes")
async def get_multiple_quotes(symbols: str):
    """Get quotes for multiple symbols (comma-separated)"""
    symbol_list = [s.strip().upper() for s in symbols.split(',')]
    quotes = []
    
    print(f"Fetching quotes for symbols: {symbol_list}")
    
    for symbol in symbol_list:
        try:
            data = await fetch_alpha_vantage_data("GLOBAL_QUOTE", symbol)
            
            if data and "Global Quote" in data:
                quote = data["Global Quote"]
                if quote and "01. symbol" in quote:
                    current_price = float(quote["05. price"])
                    change = float(quote["09. change"])
                    change_percent = float(quote["10. change percent"].replace("%", ""))
                    
                    quotes.append({
                        "symbol": symbol,
                        "price": round(current_price, 2),
                        "change": round(change, 2),
                        "changePercent": round(change_percent, 2)
                    })
        except Exception as e:
            print(f"Error fetching {symbol}: {e}")
            continue
    
    if not quotes:
        raise HTTPException(status_code=404, detail="No valid quotes found for provided symbols")
    
    return {"quotes": quotes}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)