import chromadb
from chromadb.utils import embedding_functions
import os
from langchain_groq import ChatGroq
import requests
from fastapi import HTTPException

from config import (
    GROQ_API_KEY,
    CHROMA_DB_PATH,
    LLM_MODEL_NAME,
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
    ALPHA_VANTAGE_API_KEY,
    ALPHA_VANTAGE_BASE_URL,
    REQUEST_TIMEOUT
)

def get_chat_llm():
    """Initialize and return ChatGroq LLM instance"""
    return ChatGroq(
        groq_api_key=GROQ_API_KEY,
        model_name=LLM_MODEL_NAME,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS
    )



def get_chroma_client():
    """Initialize and return ChromaDB client with consistent embedding function"""
    os.makedirs(CHROMA_DB_PATH, exist_ok=True)
    
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return client

def get_embedding_function():
    """Get consistent embedding function for both setup and retrieval"""
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-mpnet-base-v2"
    )


async def fetch_alpha_vantage_data(function: str, symbol: str, **params):
    """Fetch data from Alpha Vantage API"""
    try:
        base_url = ALPHA_VANTAGE_BASE_URL
        params_dict = {
            "function": function,
            "symbol": symbol,
            "apikey": ALPHA_VANTAGE_API_KEY,
            **params
        }
        
        print(f"Alpha Vantage API call: {function} for {symbol} with params: {params}")
        
        response = requests.get(base_url, params=params_dict, timeout=REQUEST_TIMEOUT)
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


async def get_stock_chart_data(symbol: str, range_period: str = None):
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


async def get_stock_quote_data(symbol: str):
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


async def get_multiple_quotes_data(symbols: str):
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


async def analyze_stock_data(request):
    """Analyze stock data using LLM with fundamental data context"""
    from tools import retrieve_fundamentals_context, classify_query_type
    
    try:
        symbol = request.symbol.upper()
        user_query = request.userQuery
        time_range = request.timeRange
        price_data = request.priceData
        
        start_date = time_range.get('startTime', '')
        end_date = time_range.get('endTime', '')
        
        if price_data:
            start_price = price_data[0].get('close', 0)
            end_price = price_data[-1].get('close', 0)
            prices = [point.get('close', 0) for point in price_data]
            high_price = max(prices)
            low_price = min(prices)
            volatility = round(((high_price - low_price) / low_price) * 100, 2) if low_price > 0 else 0
        else:
            start_price = end_price = high_price = low_price = volatility = 0
        
        def format_date(date_str):
            if isinstance(date_str, str) and len(date_str) == 10:
                parts = date_str.split('-')
                if len(parts) == 3:
                    return f"{parts[2]}-{parts[1]}-{parts[0]}"
            return str(date_str)
        
        formatted_start_date = format_date(start_date)
        formatted_end_date = format_date(end_date)
        
        # Detect comparison queries
        comparison_symbols = []
        query_lower = user_query.lower()
        if any(word in query_lower for word in ['vs', 'versus', 'compared to', 'compare', 'relative to']):
            # Extract comparison symbols (basic implementation)
            words = user_query.upper().split()
            common_symbols = ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META']
            for word in words:
                if word in common_symbols and word != symbol.upper():
                    comparison_symbols.append(word)
        
        fundamentals_context = retrieve_fundamentals_context(symbol, user_query, comparison_symbols)
        
        # Calculate price performance metrics
        price_change = ((end_price - start_price) / start_price * 100) if start_price > 0 else 0
        
        llm = get_chat_llm()
        
        if fundamentals_context.strip():
            query_categories = classify_query_type(user_query)
            
            prompt = _build_analysis_prompt(
                user_query, fundamentals_context, start_price, end_price, 
                price_change, volatility, query_categories, comparison_symbols, 
                symbol, formatted_start_date, formatted_end_date, high_price, low_price
            )
        else:
            prompt = _build_price_only_prompt(
                symbol, user_query, formatted_start_date, formatted_end_date,
                start_price, end_price, price_change, high_price, low_price, volatility
            )
        
        response = llm.invoke(prompt)
        analysis = response.content if hasattr(response, 'content') else str(response)
        
        return {"analysis": analysis}
        
    except Exception as e:
        print(f"Error in analyze_stock: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


def _build_analysis_prompt(user_query, fundamentals_context, start_price, end_price, 
                          price_change, volatility, query_categories, comparison_symbols, 
                          symbol, formatted_start_date, formatted_end_date, high_price, low_price):
    """Build LLM prompt based on query categories"""
    
    base_instructions = f"""You are a financial analyst. Answer the user's question directly and concisely.

USER QUESTION: {user_query}

FUNDAMENTAL DATA:
{fundamentals_context}

PRICE PERFORMANCE CONTEXT:
- Price range: ${start_price:.2f} to ${end_price:.2f} ({price_change:+.1f}% change)
- Volatility during period: {volatility}%

INSTRUCTIONS: 
1. Answer the user's question directly and honestly
2. You have structured AlphaVantage financial data with exact fiscal dates and report types
3. Data marked as "quarterly" corresponds to 10-Q filings, "annual" corresponds to 10-K filings
4. Each data point includes the fiscal date (fiscalDateEnding) and when available, the reported date
5. Use the fiscal dates and report types to answer questions about timing and filing periods
6. If you don't know something, say you don't know rather than guessing
7. Use simple formatting only for complex multi-metric analysis
8. Be concise and focus on what the user actually asked"""

    if comparison_symbols:
        return base_instructions.replace("FUNDAMENTAL DATA:", "COMPARATIVE FUNDAMENTAL DATA:").replace(
            f"- Price range: ${start_price:.2f} to ${end_price:.2f} ({price_change:+.1f}% change)",
            f"- {symbol} price range: ${start_price:.2f} to ${end_price:.2f} ({price_change:+.1f}% change)"
        )
    
    return base_instructions


def _build_price_only_prompt(symbol, user_query, formatted_start_date, formatted_end_date,
                            start_price, end_price, price_change, high_price, low_price, volatility):
    """Build prompt for price-only analysis"""
    return f"""You are analyzing {symbol} stock for the period {formatted_start_date} to {formatted_end_date}.

USER QUESTION: {user_query}

AVAILABLE PRICE DATA:
- Price range: ${start_price:.2f} to ${end_price:.2f} ({price_change:+.1f}% change)
- High: ${high_price:.2f}, Low: ${low_price:.2f}
- Volatility: {volatility}%

INSTRUCTIONS: 
1. Answer the user's question directly and honestly
2. You have structured AlphaVantage financial data with exact fiscal dates and report types
3. Data marked as "quarterly" corresponds to 10-Q filings, "annual" corresponds to 10-K filings
4. Each data point includes the fiscal date (fiscalDateEnding) and when available, the reported date
5. Use the fiscal dates and report types to answer questions about timing and filing periods
6. If you don't know something, say you don't know rather than guessing
7. Use simple formatting only for complex multi-metric analysis
8. Be concise and focus on what the user actually asked

Note that only price data is available - no fundamental data."""
