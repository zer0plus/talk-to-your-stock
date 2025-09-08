from typing import List
from services import get_chroma_client, get_embedding_function
def retrieve_fundamentals_context(symbol: str, query: str, comparison_symbols: List[str] = None) -> str:
    """Retrieve relevant fundamental data context based on query and symbol"""
    
    chroma_client = get_chroma_client()
    try:
        collection_name = "raw_stock_fundamentals"
        print(f"DEBUG: Looking for collection: {collection_name}")
        
        try:
            embedding_function = get_embedding_function()
            collection = chroma_client.get_collection(collection_name, embedding_function=embedding_function)
            print(f"DEBUG: Found collection with {collection.count()} documents")
        except Exception as e:
            print(f"DEBUG: Collection not found: {e}")
            return ""
        
        query_lower = query.lower()
        
        target_data_types = []
        
        income_keywords = [
            'revenue', 'sales', 'income', 'profit', 'gross', 'operating', 'expenses', 
            'cost', 'depreciation', 'amortization', 'ebit', 'ebitda', 'tax', 'margin',
            'total revenue', 'gross profit', 'cost of revenue', 'operating income', 
            'selling general administrative', 'research and development', 'operating expenses',
            'net interest income', 'interest income', 'interest expense', 'depreciation and amortization',
            'income before tax', 'income tax expense', 'net income from continuing operations',
            'net income', 'comprehensive income'
        ]
        
        balance_keywords = [
            'assets', 'liabilities', 'equity', 'debt', 'cash', 'inventory', 'receivables',
            'investments', 'property', 'goodwill', 'intangible', 'payable', 'shareholders',
            'total assets', 'current assets', 'cash and cash equivalents', 'net receivables',
            'non current assets', 'property plant equipment', 'long term debt', 'short term debt',
            'total liabilities', 'current liabilities', 'accounts payable',
            'shareholder equity', 'common stock', 'retained earnings'
        ]
        
        cashflow_keywords = [
            'cash flow', 'capex', 'financing', 'investing', 'operating', 'capital expenditures',
            'operating cash flow', 'investment cash flow', 'financing cash flow',
            'depreciation depletion amortization', 'change in receivables', 'change in inventory',
            'proceeds from operating', 'payments for operating', 'free cash flow'
        ]
        
        earnings_keywords = [
            'eps', 'earnings', 'reported eps', 'estimated eps', 'surprise', 'surprise percentage',
            'earnings per share', 'earnings surprise'
        ]
        
        overview_keywords = [
            'pe', 'ratio', 'valuation', 'market', 'cap', 'beta', 'dividend', 'yield',
            'pe ratio', 'peg ratio', 'book value', 'dividend per share', 'dividend yield',
            'revenue per share', 'profit margin', 'operating margin', 'return on assets',
            'return on equity', 'revenue ttm', 'gross profit ttm', 'diluted eps',
            'analyst rating', 'trailing pe', 'forward pe', 'price to sales', 'price to book',
            'ev to revenue', 'ev to ebitda', '52 week high', '52 week low',
            'market capitalization', 'shares outstanding', 'shares float'
        ]
        
        if any(word in query_lower for word in income_keywords):
            target_data_types.extend(['quarterly_income', 'annual_income'])
        
        if any(word in query_lower for word in balance_keywords):
            target_data_types.extend(['quarterly_balance', 'annual_balance'])
            
        if any(word in query_lower for word in cashflow_keywords):
            target_data_types.extend(['quarterly_cashflow', 'annual_cashflow'])
            
        if any(word in query_lower for word in earnings_keywords):
            target_data_types.extend(['quarterly_earnings', 'annual_earnings'])
            
        if any(word in query_lower for word in overview_keywords):
            target_data_types.append('overview_metric')
        
        overlapping_terms = ['netincome', 'depreciation', 'amortization']
        if any(word in query_lower for word in overlapping_terms):
            target_data_types.extend(['quarterly_income', 'annual_income', 'quarterly_cashflow', 'annual_cashflow'])
        
        broad_terms = ['10-k', '10-q', 'quarterly', 'annual', 'financial', 'performance', 'analysis']
        if any(word in query_lower for word in broad_terms) and not target_data_types:
            target_data_types = ['quarterly_earnings', 'annual_earnings', 'quarterly_income', 'annual_income', 'overview_metric']
            
        if not target_data_types:
            target_data_types = ['quarterly_earnings', 'annual_earnings', 'overview_metric']
        
        target_data_types = list(dict.fromkeys(target_data_types))
        
        print(f"DEBUG: Target data types: {target_data_types}")
        
        all_results = []
        for data_type in target_data_types:
            results = collection.query(
                query_texts=[query],
                n_results=20,
                where={"data_type": data_type}
            )
            
            if results['documents'] and results['documents'][0]:
                for i, doc in enumerate(results['documents'][0]):
                    metadata = results['metadatas'][0][i]
                    if metadata.get('symbol', '').upper() == symbol.upper():
                        all_results.append((doc, metadata))
        
        print(f"DEBUG: Query returned {len(all_results)} documents for {symbol}")
        print(f"DEBUG: Data types found: {[meta.get('data_type') for doc, meta in all_results]}")
        for i, (doc, meta) in enumerate(all_results[:5]):  
            print(f"DEBUG: Doc {i+1}: {doc[:100]}...")
        
        context_parts = []
        
        if all_results:
            sorted_results = sorted(all_results, key=lambda x: x[1].get('fiscal_date', ''), reverse=True)
            
            context_parts.append(f"=== {symbol.upper()} FINANCIAL DATA ===")
            
            by_type = {}
            for doc, metadata in sorted_results[:15]: 
                data_type = metadata.get('data_type', 'unknown')
                report_type = metadata.get('report_type', '')
                fiscal_date = metadata.get('fiscal_date', '')
                
                if data_type not in by_type:
                    by_type[data_type] = []
                by_type[data_type].append({
                    'content': doc,
                    'fiscal_date': fiscal_date,
                    'report_type': report_type,
                    'metadata': metadata
                })
            
            for data_type, docs in by_type.items():
                type_name = data_type.replace('_', ' ').title()
                context_parts.append(f"\n[{type_name}]:")
                for doc_info in docs[:5]:  
                    fiscal_date = doc_info['fiscal_date']
                    report_type = doc_info['report_type']
                    content = doc_info['content']
                    context_parts.append(f"  {fiscal_date} ({report_type}): {content}")
        
        # Add comparison data if requested
        if comparison_symbols:
            relevant_categories = classify_query_type(query)
            for comp_symbol in comparison_symbols[:2]:  
                comp_results = collection.query(
                    query_texts=[query],
                    n_results=10,
                    where={"symbol": comp_symbol.upper()}
                )
                
                comp_filtered = []
                if comp_results['documents'] and comp_results['documents'][0]:
                    for i, doc in enumerate(comp_results['documents'][0]):
                        comp_metadata = comp_results['metadatas'][0][i]
                        comp_category = comp_metadata.get('category', '')
                        if comp_category in relevant_categories:
                            comp_filtered.append(doc)
                
                if comp_filtered:
                    context_parts.append(f"\n=== {comp_symbol.upper()} COMPARISON ===")
                    for doc in comp_filtered[:3]:
                        context_parts.append(doc[:200] + "...")
        
        if not context_parts:
            print("DEBUG: No fundamental context found")
            return ""
        
        final_context = "\n\n".join(context_parts)
        print(f"DEBUG: Generated context length: {len(final_context)} characters")
        
        return final_context
        
    except Exception as e:
        print(f"Error retrieving fundamentals context: {e}")
        return ""


def classify_query_type(query: str) -> List[str]:
    """Classify query to determine relevant fundamental categories"""
    query_lower = query.lower()
    
    query_categories = {
        'valuation_metrics': ['pe ratio', 'valuation', 'multiple', 'expensive', 'cheap', 'overvalued', 'undervalued', 'price to earnings', 'p/e', 'market cap', 'price to book'],
        'profitability_metrics': ['revenue', 'earnings', 'profit', 'margin', 'growth', 'sales', 'eps', 'income'], 
        'balance_sheet_metrics': ['debt', 'equity', 'assets', 'balance sheet', 'leverage', 'roe', 'roa', 'book value', 'return on'],
        'dividend_metrics': ['dividend', 'yield', 'payout', 'income'],
        'trading_metrics': ['price', 'target', 'analyst', '52 week', 'moving average', 'beta', 'technical'],
        'company_info': ['company', 'business', 'sector', 'industry', 'description', 'what does'],
        'revenue_trends': ['revenue growth', 'sales growth', 'quarterly revenue', 'annual revenue', 'revenue trend'],
        'profitability_analysis': ['gross margin', 'operating margin', 'net margin', 'profitability', 'margin analysis'],
        'balance_sheet_strength': ['financial strength', 'balance sheet', 'debt ratio', 'cash position', 'assets'],
        'cash_flow_analysis': ['cash flow', 'free cash flow', 'operating cash flow', 'capex', 'capital expenditure'],
        'earnings_trends': ['eps growth', 'earnings growth', 'earnings surprise', 'quarterly earnings', 'annual earnings', 'eps reported', 'all eps', '10-k', '10-q', 'quarterly eps', 'annual eps']
    }
    
    relevant_categories = []
    for category, keywords in query_categories.items():
        if any(keyword in query_lower for keyword in keywords):
            relevant_categories.append(category)
    
    if not relevant_categories:
        relevant_categories = ['valuation_metrics', 'profitability_metrics', 'balance_sheet_metrics', 'revenue_trends', 'earnings_trends']
    
    return relevant_categories
