from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
from pydantic import BaseModel

from services import (
    get_stock_chart_data,
    get_stock_quote_data, 
    get_multiple_quotes_data,
    analyze_stock_data
)

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

class AnalyzeRequest(BaseModel):
    symbol: str
    userQuery: str
    timeRange: dict
    priceData: list

@app.get("/")
async def read_root():
    return {"message": "Stock API Server with Alpha Vantage"}

@app.get("/api/stock/{symbol}/chart")
async def get_stock_chart(symbol: str, range_period: Optional[str] = None):
    """Get historical stock data for charting with range support"""
    return await get_stock_chart_data(symbol, range_period)

@app.get("/api/stock/{symbol}/quote")
async def get_stock_quote(symbol: str):
    """Get current stock quote"""
    return await get_stock_quote_data(symbol)

@app.get("/api/stocks/quotes")
async def get_multiple_quotes(symbols: str):
    """Get quotes for multiple symbols (comma-separated)"""
    return await get_multiple_quotes_data(symbols)

@app.post("/api/analyze")
async def analyze_stock(request: AnalyzeRequest):
    """Analyze stock data using LLM with fundamental data context"""
    return await analyze_stock_data(request)