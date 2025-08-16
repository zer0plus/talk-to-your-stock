import datamule
import chromadb
from chromadb.config import Settings
from datetime import datetime, timedelta
import os
from typing import List

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

def setup_aapl_sec_data():
    """Download AAPL SEC filings and store in ChromaDB"""
    print("Starting AAPL SEC data setup...")
    
    chroma_db_path = "./chroma_db"
    os.makedirs(chroma_db_path, exist_ok=True)
    client = chromadb.PersistentClient(path=chroma_db_path)
    
    index = datamule.Index()
    
    print(f"Searching for recent AAPL filings (2020 onwards)")
    
    filing_types = ['10-K', '10-Q']
    all_chunks = []
    
    for filing_type in filing_types:
        print(f"Downloading {filing_type} filings for AAPL...")
        
        try:
            search_results = index.search_submissions(
                ticker='AAPL',
                submission_type=filing_type,
                filing_date="2020-01-01:2025-12-31",
                quiet=False
            )
            
            print(f"Found {len(search_results)} recent {filing_type} filings")
            
            if not search_results:
                print(f"No recent results, getting all {filing_type} filings...")
                search_results = index.search_submissions(
                    ticker='AAPL',
                    submission_type=filing_type,
                    quiet=False
                )
                print(f"Found {len(search_results)} total {filing_type} filings")
            
            recent_filings = []
            for filing in search_results:
                source = filing.get('_source', {})
                filing_date_str = source.get('file_date', '')
                try:
                    filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                    if filing_date >= datetime(2020, 1, 1):
                        recent_filings.append((filing, filing_date))
                except:
                    continue
            
            recent_filings.sort(key=lambda x: x[1], reverse=True)
            recent_filings = recent_filings[:10]
            
            print(f"Processing {len(recent_filings)} recent {filing_type} filings (2020+)")
            
            for filing, filing_dt in recent_filings:
                try:
                    source = filing.get('_source', {})
                    accession_num = source.get('adsh', '')
                    filing_date_str = source.get('file_date', '')
                    cik = source.get('ciks', [''])[0]
                    
                    print(f"Processing {filing_type} filing: {accession_num} from {filing_date_str}")
                    
                    if accession_num and cik:
                        submission = datamule.Submission(
                            url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_num.replace('-', '')}/{accession_num}.txt"
                        )
                        
                        content_parts = []
                        doc_count = 0
                        for doc in submission:
                            if doc_count >= 3:
                                break
                            try:
                                if hasattr(doc, 'text_content') and doc.text_content:
                                    content_parts.append(doc.text_content[:15000])
                                elif hasattr(doc, 'content') and doc.content:
                                    content_parts.append(str(doc.content)[:15000])
                                doc_count += 1
                            except Exception as doc_error:
                                print(f"Warning: Error extracting content from document: {doc_error}")
                                continue
                        
                        content = " ".join(content_parts)[:50000]
                        
                        if content:
                            filing_data = {
                                'filing_type': filing_type,
                                'filing_date': filing_date_str,
                                'content': content
                            }
                            
                            chunks = process_sec_filing(filing_data, 'AAPL')
                            all_chunks.extend(chunks)
                            print(f"Added {len(chunks)} chunks from {filing_type} filing {accession_num}")
                        else:
                            print(f"Warning: No content extracted from {filing_type} filing {accession_num}")
                            
                except Exception as e:
                    print(f"Error processing {filing_type} filing: {e}")
                    continue
                    
        except Exception as e:
            print(f"Error searching {filing_type} filings: {e}")
            continue
    
    if not all_chunks:
        print("ERROR: No SEC filing content was successfully extracted!")
        return False
    
    print(f"Total chunks created: {len(all_chunks)}")
    
    collection_name = "aapl_sec_filings"
    
    try:
        try:
            client.delete_collection(collection_name)
            print("Deleted existing collection")
        except:
            pass
        
        collection = client.create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        
        documents = [chunk['text'] for chunk in all_chunks]
        metadatas = [chunk['metadata'] for chunk in all_chunks]
        ids = [chunk['metadata']['chunk_id'] for chunk in all_chunks]
        
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
        
        print(f"✅ Successfully stored {len(all_chunks)} AAPL SEC filing chunks in ChromaDB")
        print(f"✅ Collection '{collection_name}' is ready for server use")
        return True
        
    except Exception as e:
        print(f"ERROR storing in ChromaDB: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("AAPL SEC Data Setup Script")
    print("=" * 60)
    
    success = setup_aapl_sec_data()
    
    if success:
        print("\n🎉 Setup completed successfully!")
        print("You can now start the server with: python main.py")
        print("The AAPL SEC data is ready for RAG retrieval.")
    else:
        print("\n❌ Setup failed!")
        print("Please check the errors above and try again.")