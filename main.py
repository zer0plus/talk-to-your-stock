from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import requests
from typing import Optional, List
from pydantic import BaseModel
from langchain_groq import ChatGroq
import chromadb
from chromadb.config import Settings
from datetime import datetime, timedelta
import os


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

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found in environment variables")

def get_chat_llm():
    """Initialize and return ChatGroq LLM instance"""
    return ChatGroq(
        groq_api_key=GROQ_API_KEY,
        model_name="llama3-8b-8192",
        temperature=0.3,
        max_tokens=300
    )

llm = get_chat_llm()

def get_chroma_client():
    """Initialize and return ChromaDB client"""
    chroma_db_path = "./chroma_db"
    os.makedirs(chroma_db_path, exist_ok=True)
    
    client = chromadb.PersistentClient(path=chroma_db_path)
    return client

chroma_client = get_chroma_client()


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> List[str]:
    """Simple text chunking with overlap"""
    chunks = []
    start = 0
    text_len = len(text)
    
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end]
        chunks.append(chunk)
        
        if end >= text_len:
            break
            
        start = end - overlap
    
    return chunks

def process_sec_filing(filing_data: dict, symbol: str) -> List[dict]:
    """Process SEC filing data into chunks with metadata"""
    chunks = []
    filing_type = filing_data.get('filing_type', 'Unknown')
    filing_date = filing_data.get('filing_date', 'Unknown')
    content = filing_data.get('content', '')
    
    if content:
        text_chunks = chunk_text(content)
        
        for i, chunk in enumerate(text_chunks):
            chunks.append({
                'text': chunk,
                'metadata': {
                    'filing_type': filing_type,
                    'filing_date': filing_date,
                    'symbol': symbol.upper(),
                    'section': f'section_{i}',
                    'chunk_id': f'{filing_type}_{filing_date}_{i}'
                }
            })
    
    return chunks

class AnalyzeRequest(BaseModel):
    symbol: str
    userQuery: str
    timeRange: dict
    priceData: list

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


def retrieve_sec_context(symbol: str, start_date: str, end_date: str, query: str) -> str:
    """Retrieve relevant SEC filing context for the given time range and query"""
    try:
        collection_name = "aapl_sec_filings"  # Created by setup_sec_data.py script
        print(f"DEBUG: Looking for collection: {collection_name}")
        
        try:
            collection = chroma_client.get_collection(collection_name)
            print(f"DEBUG: Found collection with {collection.count()} documents")
        except Exception as e:
            print(f"DEBUG: Collection not found: {e}")
            return ""
        
        try:
            if isinstance(start_date, str) and len(start_date) == 10:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            else:
                start_dt = datetime.now() - timedelta(days=365)
                
            if isinstance(end_date, str) and len(end_date) == 10:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            else:
                end_dt = datetime.now()
        except:
            start_dt = datetime.now() - timedelta(days=365)
            end_dt = datetime.now()
        
        print(f"DEBUG: Date range: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}")
        print(f"DEBUG: Query: '{query}' for symbol: {symbol.upper()}")
        
        results = collection.query(
            query_texts=[query],
            n_results=15,  
            where={
                "symbol": symbol.upper()
            }
        )
        
        print(f"DEBUG: Query returned {len(results['documents'][0]) if results['documents'] else 0} documents")
        
        if not results['documents'] or not results['documents'][0]:
            print("DEBUG: No documents found in query results")
            return ""
        
        relevant_docs = []
        print(f"DEBUG: Processing {len(results['documents'][0])} documents")
        
        for i, doc in enumerate(results['documents'][0]):
            metadata = results['metadatas'][0][i]
            filing_date_str = metadata.get('filing_date', '')
            filing_type = metadata.get('filing_type', '')
            
            print(f"DEBUG: Document {i}: {filing_type} from {filing_date_str}")
            
            try:
                filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                
                is_recent = filing_date >= datetime(2020, 1, 1)
                
                relevant_docs.append({
                    'content': doc[:800],
                    'filing_type': metadata.get('filing_type', ''),
                    'filing_date': filing_date_str,
                    'is_recent': is_recent,
                    'filing_year': filing_date.year
                })
                
                print(f"DEBUG: Document {i} ADDED ({'recent' if is_recent else 'older'})")
                
            except Exception as e:
                print(f"DEBUG: Document {i} EXCLUDED - date parsing error: {e}")
                continue
        
        relevant_docs.sort(key=lambda x: (x['is_recent'], x['filing_year']), reverse=True)
        relevant_docs = relevant_docs[:5]
        
        print(f"DEBUG: Using {len(relevant_docs)} documents for context")
        
        if not relevant_docs:
            print("DEBUG: No relevant docs found")
            return ""
        
        context_parts = []
        for doc in relevant_docs[:3]: 
            context_parts.append(f"[{doc['filing_type']} - {doc['filing_date']}]: {doc['content']}")
        
        return "\n\n".join(context_parts)
        
    except Exception as e:
        print(f"Error retrieving SEC context: {e}")
        return ""

@app.post("/api/analyze")
async def analyze_stock(request: AnalyzeRequest):
    """Analyze stock data using LLM with SEC filing context"""
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
        
        sec_context = retrieve_sec_context(symbol, start_date, end_date, user_query)
        
        if sec_context.strip():
            prompt = f"""You are analyzing {symbol} stock for the period {formatted_start_date} to {formatted_end_date}.

USER QUESTION: {user_query}

RELEVANT SEC FILING DATA:
{sec_context}

PRICE DATA CONTEXT:
- Price range: ${start_price:.2f} to ${end_price:.2f} 
- High: ${high_price:.2f}, Low: ${low_price:.2f}
- Volatility: {volatility}%

INSTRUCTIONS: Answer the user's specific question directly using the SEC filing data provided. If they asked about revenue, focus on revenue. If they asked about earnings, focus on earnings. Only mention price movements if directly relevant to their question."""
        else:
            prompt = f"""You are analyzing {symbol} stock for the period {formatted_start_date} to {formatted_end_date}.

USER QUESTION: {user_query}

AVAILABLE DATA:
- Price range: ${start_price:.2f} to ${end_price:.2f} 
- High: ${high_price:.2f}, Low: ${low_price:.2f}
- Volatility: {volatility}%

INSTRUCTIONS: Answer the user's specific question as best you can with the available price data. Focus on what they actually asked about."""
        
        response = llm.invoke(prompt)
        analysis = response.content if hasattr(response, 'content') else str(response)
        
        return {"analysis": analysis}
        
    except Exception as e:
        print(f"Error in analyze_stock: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)