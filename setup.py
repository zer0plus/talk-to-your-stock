import os
from typing import List, Dict
import json
import time
from datetime import datetime

from config import ALPHA_VANTAGE_API_KEY, CHROMA_DB_PATH
from services import fetch_alpha_vantage_data, get_chroma_client, get_embedding_function

def create_comprehensive_report_text(symbol, report, fiscal_date, report_type, endpoint_name):
    """Store ALL raw data from report in readable format - no filtering"""
    
    # Convert entire report dictionary to readable text
    metrics_list = []
    for key, value in report.items():
        if value and value != "None" and value != "":
            # Format key to be more readable
            readable_key = key.replace('_', ' ').title()
            metrics_list.append(f"{readable_key}: {value}")
    
    # Join all metrics into comprehensive text
    all_metrics = ", ".join(metrics_list)
    period_text = "Annual" if report_type == "annual" else "Quarterly"
    
    return f"{symbol} {period_text} {endpoint_name.replace('_', ' ').title()} for {fiscal_date}: {all_metrics}"

async def get_alpha_vantage_data(function: str, symbol: str, **params):
    """Fetch data from Alpha Vantage API using services.py"""
    try:
        print(f"Fetching {function} for {symbol}...")
        data = await fetch_alpha_vantage_data(function, symbol, **params)
        return data
    except Exception as e:
        print(f"Error fetching {function} for {symbol}: {e}")
        return None

def create_raw_data_chunks(symbol: str, endpoint_data: Dict) -> List[Dict]:
    """Convert AlphaVantage endpoint data into structured chunks that preserve ALL data"""
    chunks = []
    current_date = datetime.now().strftime('%Y-%m-%d')
    
    for endpoint_name, data in endpoint_data.items():
        if not data:
            continue
            
        print(f"Processing {endpoint_name} data...")
        
        if endpoint_name == "OVERVIEW":
            # Store each key-value pair as a separate chunk for easy querying
            for key, value in data.items():
                if value and value != "None" and value != "":
                    chunks.append({
                        'text': f"{symbol} {key}: {value}",
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'OVERVIEW',
                            'data_type': 'overview_metric',
                            'metric_name': key,
                            'metric_value': str(value),
                            'last_updated': current_date,
                        }
                    })
        
        elif endpoint_name == "INCOME_STATEMENT":
            if 'annualReports' in data:
                for i, report in enumerate(data['annualReports']):
                    fiscal_date = report.get('fiscalDateEnding', f'annual_{i}')
                    
                    metrics_text = create_comprehensive_report_text(symbol, report, fiscal_date, 'annual', 'INCOME_STATEMENT')
                    
                    chunks.append({
                        'text': metrics_text,
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'INCOME_STATEMENT',
                            'data_type': 'annual_income',
                            'fiscal_date': fiscal_date,
                            'report_type': 'annual',
                            'last_updated': current_date
                        }
                    })
            
            if 'quarterlyReports' in data:
                for i, report in enumerate(data['quarterlyReports']):
                    fiscal_date = report.get('fiscalDateEnding', f'quarter_{i}')
                    
                    metrics_text = create_comprehensive_report_text(symbol, report, fiscal_date, 'quarterly', 'INCOME_STATEMENT')
                    
                    chunks.append({
                        'text': metrics_text,
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'INCOME_STATEMENT',
                            'data_type': 'quarterly_income',
                            'fiscal_date': fiscal_date,
                            'report_type': 'quarterly',
                            'last_updated': current_date
                        }
                    })
        
        elif endpoint_name == "BALANCE_SHEET":
            if 'annualReports' in data:
                for i, report in enumerate(data['annualReports']):
                    fiscal_date = report.get('fiscalDateEnding', f'annual_{i}')
                    
                    metrics_text = create_comprehensive_report_text(symbol, report, fiscal_date, 'annual', 'BALANCE_SHEET')
                    
                    chunks.append({
                        'text': metrics_text,
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'BALANCE_SHEET',
                            'data_type': 'annual_balance',
                            'fiscal_date': fiscal_date,
                            'report_type': 'annual',
                            'last_updated': current_date
                        }
                    })
            
            if 'quarterlyReports' in data:
                for i, report in enumerate(data['quarterlyReports']):
                    fiscal_date = report.get('fiscalDateEnding', f'quarter_{i}')
                    
                    metrics_text = create_comprehensive_report_text(symbol, report, fiscal_date, 'quarterly', 'BALANCE_SHEET')
                    
                    chunks.append({
                        'text': metrics_text,
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'BALANCE_SHEET',
                            'data_type': 'quarterly_balance',
                            'fiscal_date': fiscal_date,
                            'report_type': 'quarterly',
                            'last_updated': current_date
                        }
                    })
        
        elif endpoint_name == "CASH_FLOW":
            if 'annualReports' in data:
                for i, report in enumerate(data['annualReports']):
                    fiscal_date = report.get('fiscalDateEnding', f'annual_{i}')
                    
                    metrics_text = create_comprehensive_report_text(symbol, report, fiscal_date, 'annual', 'CASH_FLOW')
                    
                    chunks.append({
                        'text': metrics_text,
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'CASH_FLOW',
                            'data_type': 'annual_cashflow',
                            'fiscal_date': fiscal_date,
                            'report_type': 'annual',
                            'last_updated': current_date
                        }
                    })
            
            if 'quarterlyReports' in data:
                for i, report in enumerate(data['quarterlyReports']):
                    fiscal_date = report.get('fiscalDateEnding', f'quarter_{i}')
                    
                    metrics_text = create_comprehensive_report_text(symbol, report, fiscal_date, 'quarterly', 'CASH_FLOW')
                    
                    chunks.append({
                        'text': metrics_text,
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'CASH_FLOW',
                            'data_type': 'quarterly_cashflow',
                            'fiscal_date': fiscal_date,
                            'report_type': 'quarterly',
                            'last_updated': current_date
                        }
                    })
        
        elif endpoint_name == "EARNINGS":
            if 'annualEarnings' in data:
                for i, earnings in enumerate(data['annualEarnings']):
                    fiscal_date = earnings.get('fiscalDateEnding', f'annual_{i}')
                    
                    metrics_text = create_comprehensive_report_text(symbol, earnings, fiscal_date, 'annual', 'EARNINGS')
                    
                    chunks.append({
                        'text': metrics_text,
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'EARNINGS',
                            'data_type': 'annual_earnings',
                            'fiscal_date': fiscal_date,
                            'report_type': 'annual',
                            'last_updated': current_date
                        }
                    })
            
            if 'quarterlyEarnings' in data:
                for i, earnings in enumerate(data['quarterlyEarnings']):
                    fiscal_date = earnings.get('fiscalDateEnding', f'quarter_{i}')
                    
                    metrics_text = create_comprehensive_report_text(symbol, earnings, fiscal_date, 'quarterly', 'EARNINGS')
                    
                    chunks.append({
                        'text': metrics_text,
                        'metadata': {
                            'symbol': symbol,
                            'endpoint': 'EARNINGS',
                            'data_type': 'quarterly_earnings',
                            'fiscal_date': fiscal_date,
                            'report_type': 'quarterly',
                            'last_updated': current_date
                        }
                    })
    
    return chunks

async def setup_raw_fundamentals_data():
    """Download and store RAW AlphaVantage data with full structure preserved"""
    
    if not ALPHA_VANTAGE_API_KEY:
        print("ERROR: Alpha Vantage API key not found in config")
        return False
    
    client = get_chroma_client()
    
    # testing with AAPL
    symbol = 'AAPL'
    
    print(f"\n=== DOWNLOADING RAW DATA FOR {symbol} ===")
    
    endpoint_data = {}
    
    print(f"  Fetching OVERVIEW...")
    overview_data = await get_alpha_vantage_data("OVERVIEW", symbol)
    endpoint_data["OVERVIEW"] = overview_data
    time.sleep(1)
    
    print(f"  Fetching INCOME_STATEMENT...")
    income_data = await get_alpha_vantage_data("INCOME_STATEMENT", symbol)
    endpoint_data["INCOME_STATEMENT"] = income_data
    time.sleep(1)
    
    print(f"  Fetching BALANCE_SHEET...")
    balance_data = await get_alpha_vantage_data("BALANCE_SHEET", symbol)
    endpoint_data["BALANCE_SHEET"] = balance_data
    time.sleep(1)
    
    print(f"  Fetching CASH_FLOW...")
    cash_flow_data = await get_alpha_vantage_data("CASH_FLOW", symbol)
    endpoint_data["CASH_FLOW"] = cash_flow_data
    time.sleep(1)
    
    print(f"  Fetching EARNINGS...")
    earnings_data = await get_alpha_vantage_data("EARNINGS", symbol)
    endpoint_data["EARNINGS"] = earnings_data
    time.sleep(1)
    
    data_dir = "data/raw_financials"
    os.makedirs(data_dir, exist_ok=True)
    
    json_path = f"{data_dir}/{symbol}_raw_data.json"
    with open(json_path, "w") as f:
        json.dump(endpoint_data, f, indent=2)
    print(f"Saved raw data to {json_path}")
    
    valid_endpoints = {k: v for k, v in endpoint_data.items() if v is not None}
    
    if not valid_endpoints:
        print("ERROR: No valid data received from Alpha Vantage API!")
        return False
    
    print(f"Successfully fetched data from {len(valid_endpoints)} endpoints")
    
    all_chunks = create_raw_data_chunks(symbol, valid_endpoints)
    
    if not all_chunks:
        print("ERROR: No data was successfully processed into chunks!")
        return False
    
    print(f"\nTotal structured chunks created: {len(all_chunks)}")
    
    collection_name = "raw_stock_fundamentals"
    
    try:
        try:
            client.delete_collection(collection_name)
            print("Deleted existing collection")
        except:
            pass
        
        embedding_function = get_embedding_function()
        
        collection = client.create_collection(
            name=collection_name,
            embedding_function=embedding_function,
            metadata={"hnsw:space": "cosine"}
        )
        
        documents = [chunk['text'] for chunk in all_chunks]
        metadatas = [chunk['metadata'] for chunk in all_chunks]
        ids = [f"{chunk['metadata']['symbol']}_{chunk['metadata']['endpoint']}_{chunk['metadata'].get('fiscal_date', 'overview')}_{i}" 
               for i, chunk in enumerate(all_chunks)]
        
        batch_size = 100
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i+batch_size]
            batch_metas = metadatas[i:i+batch_size]
            batch_ids = ids[i:i+batch_size]
            
            collection.add(
                documents=batch_docs,
                metadatas=batch_metas,
                ids=batch_ids
            )
            print(f"Added batch {i//batch_size + 1} ({len(batch_docs)} documents)")
        
        print(f"\nSYS: Successfully stored {len(all_chunks)} raw data chunks in ChromaDB")
        print(f"Collection '{collection_name}' is ready for server use")
        
        print(f"\n=== SUMMARY FOR {symbol} ===")
        by_endpoint = {}
        for chunk in all_chunks:
            endpoint = chunk['metadata']['endpoint']
            data_type = chunk['metadata']['data_type']
            key = f"{endpoint} - {data_type}"
            by_endpoint[key] = by_endpoint.get(key, 0) + 1
        
        for key, count in sorted(by_endpoint.items()):
            print(f"{key}: {count} chunks")
        
        return True
        
    except Exception as e:
        print(f"ERROR storing in ChromaDB: {e}")
        return False

if __name__ == "__main__":
    import asyncio
    
    print("=" * 60)
    print("RAW Fundamentals Data Setup Script")
    print("=" * 60)
    
    success = asyncio.run(setup_raw_fundamentals_data())
    
    if success:
        print("\nSYS: Setup completed successfully!")
        print("📊 All AlphaVantage data is now stored with full structure preserved")
        print("📅 Dates, report types, and raw JSON are all available")
        print("🔧 Update main.py to use 'raw_stock_fundamentals' collection")
    else:
        print("\nSYS: Setup failed!")
        print("Please check the errors above and try again.")