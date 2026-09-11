from langchain_community.document_loaders import PyPDFLoader, DirectoryLoader, TextLoader
from langchain_community.retrievers import BM25Retriever
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain.schema import Document
from typing import List
import pandas as pd
import os
import pickle
import re


# ---------- Book (PDF) Loading ----------

def load_pdf_file(data_dir: str) -> List[Document]:
    """
    Load all PDF files from a directory using LangChain's DirectoryLoader.
    Returns a list of Document objects with page_content and metadata.
    """
    loader = DirectoryLoader(
        data_dir,
        glob="*.pdf",
        loader_cls=PyPDFLoader
    )
    documents = loader.load()
    return documents


# ---------- Export CSV Loading ----------

def load_csv_as_documents(csv_path: str) -> List[Document]:
    """
    Read the merged_country_wise.csv and convert each row into a
    LangChain Document for embedding and retrieval.

    CSV columns: country_from, country_to, S.No., HSCode, Commodity,
                 2023-2024, 2024-2025, %Growth
    """
    df = pd.read_csv(csv_path)

    documents: List[Document] = []
    for _, row in df.iterrows():
        country_from = str(row.get("country_from", "India")).strip()
        country_to = str(row.get("country_to", "")).strip()
        hs_code = str(row.get("HSCode", "")).strip()
        commodity = str(row.get("Commodity", "")).strip()
        value_2023_24 = str(row.get("2023-2024", "n/a")).strip()
        value_2024_25 = str(row.get("2024-2025", "n/a")).strip()
        pct_growth = str(row.get("%Growth", "n/a")).strip()

        # Build human-readable text for embedding
        text = (
            f"Indian Export Data: {country_from} exports {commodity} "
            f"(HS Code: {hs_code}) to {country_to}. "
            f"Export value in 2023-2024: USD {value_2023_24} million. "
            f"Export value in 2024-2025: USD {value_2024_25} million. "
            f"Growth: {pct_growth}%."
        )

        metadata = {
            "source": "export_data",
            "country_from": country_from,
            "country_to": country_to,
            "hs_code": hs_code,
            "commodity": commodity,
        }

        documents.append(Document(page_content=text, metadata=metadata))

    return documents


# ---------- Metadata Filtering ----------

def filter_to_minimal_docs(docs: List[Document]) -> List[Document]:
    """
    Strip heavy metadata from documents, keeping only essential fields:
    source, chapter (for book), country_to (for export data).
    """
    minimal_docs: List[Document] = []
    for doc in docs:
        meta = {}
        # Preserve key metadata fields
        for key in ["source", "country_from", "country_to", "hs_code", "commodity", "page"]:
            if key in doc.metadata:
                meta[key] = doc.metadata[key]
        # Tag book documents
        if "source" not in meta:
            meta["source"] = "book"

        minimal_docs.append(
            Document(page_content=doc.page_content, metadata=meta)
        )
    return minimal_docs


# ---------- Text File Loading ----------

def load_text_file(file_path: str) -> List[Document]:
    """
    Load a plain text file and return it as a list of LangChain Documents.
    Each document is tagged with source='laws_kb' in metadata.
    """
    loader = TextLoader(file_path, encoding="utf-8")
    documents = loader.load()
    # Tag each document with a 'laws_kb' source
    for doc in documents:
        doc.metadata["source"] = "laws_kb"
    return documents


# ---------- Text Chunking ----------

def text_split(documents: List[Document]) -> List[Document]:
    """
    Split documents into chunks using RecursiveCharacterTextSplitter.
    Book documents get chunked (500 chars, 50 overlap).
    Laws/KB documents get chunked (800 chars, 100 overlap) to preserve section context.
    Export data documents are already compact per-row, so they pass through
    with a larger chunk size to avoid splitting single entries.
    """
    book_docs = [d for d in documents
                 if d.metadata.get("source") not in ("export_data", "laws_kb")]
    laws_docs = [d for d in documents if d.metadata.get("source") == "laws_kb"]
    export_docs = [d for d in documents if d.metadata.get("source") == "export_data"]

    # Chunk book documents
    book_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    book_chunks = book_splitter.split_documents(book_docs)

    # Chunk laws/KB documents — larger chunks to preserve structured sections
    laws_splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    laws_chunks = laws_splitter.split_documents(laws_docs)

    # Export rows are typically short, split only if very long
    export_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=50)
    export_chunks = export_splitter.split_documents(export_docs)

    return book_chunks + laws_chunks + export_chunks


def _sanitize_untrusted_text(text: str) -> str:
    """Neutralize XML delimiter injection attempts in user-supplied content."""
    return (
        text.replace("</user_uploaded_data>", "&lt;/user_uploaded_data&gt;")
            .replace("<user_uploaded_data>", "&lt;user_uploaded_data&gt;")
    )


def parse_uploaded_file(filepath: str, filename: str):
    """
    Parse an uploaded business dataset (CSV, Excel, PDF, or TXT).
    Returns:
      tuple: (statistical_profile_summary: str, row_documents: List[Document])
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(filepath)
        summary = _profile_dataframe(df, filename)
        docs = _dataframe_to_documents(df, filename)
        return _sanitize_untrusted_text(summary), docs
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(filepath)
        summary = _profile_dataframe(df, filename)
        docs = _dataframe_to_documents(df, filename)
        return _sanitize_untrusted_text(summary), docs
    elif ext == ".pdf":
        summary, docs = _pdf_to_summary_and_docs(filepath, filename)
        return _sanitize_untrusted_text(summary), docs
    elif ext == ".txt":
        summary, docs = _text_to_summary_and_docs(filepath, filename)
        return _sanitize_untrusted_text(summary), docs
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _profile_dataframe(df: pd.DataFrame, filename: str) -> str:
    """
    Build a comprehensive statistical profile across 100% of rows:
    - Overall dimensions (row count, columns)
    - Full numeric totals (sum, mean, median, min, max across all records)
    - Key categorical distributions & top contributors (by revenue/volume)
    - Sample records for structural context
    """
    lines: List[str] = []
    lines.append(f"DATASET OVERVIEW (File: {filename}):")
    lines.append(f"• Total Rows: {len(df):,} records | Total Columns: {len(df.columns)} ({', '.join(df.columns.tolist())})")

    # 1. Complete Numerical Totals across ALL rows
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if numeric_cols:
        lines.append("\nFINANCIAL & NUMERIC TOTALS (Calculated across 100% of rows):")
        for col in numeric_cols[:8]:
            total = df[col].sum()
            avg = df[col].mean()
            median = df[col].median()
            min_val = df[col].min()
            max_val = df[col].max()
            lines.append(
                f"• {col}: Total = {total:,.2f} | Avg = {avg:,.2f} | "
                f"Median = {median:,.2f} | Range = [{min_val:,.2f} to {max_val:,.2f}]"
            )

    # 2. Key Categorical Group-Bys & Top Contributors
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    target_keywords = ["country", "commodity", "product", "item", "customer", "buyer", "destination", "market", "hscode", "category"]
    priority_cat_cols = [c for c in cat_cols if any(kw in c.lower() for kw in target_keywords)]
    if not priority_cat_cols:
        priority_cat_cols = [c for c in cat_cols if 1 < df[c].nunique() <= 50][:3]

    # Find the primary value/revenue column for group-by aggregation
    value_col = None
    for col in numeric_cols:
        if any(kw in col.lower() for kw in ["value", "revenue", "amount", "sales", "usd", "inr", "price", "export"]):
            value_col = col
            break
    if not value_col and numeric_cols:
        value_col = numeric_cols[0]

    if priority_cat_cols:
        lines.append("\nKEY CATEGORIES & TOP CONTRIBUTORS:")
        for col in priority_cat_cols[:3]:
            unique_count = df[col].nunique()
            lines.append(f"• {col} Breakdown ({unique_count} distinct items):")
            if value_col:
                grouped = df.groupby(col)[value_col].sum().sort_values(ascending=False)
                total_val = grouped.sum()
                for rank, (cat, val) in enumerate(grouped.head(5).items(), 1):
                    share = (val / total_val * 100) if total_val else 0
                    lines.append(f"   {rank}. {cat}: {val:,.2f} ({share:.1f}% of {value_col})")
            else:
                counts = df[col].value_counts()
                for rank, (cat, cnt) in enumerate(counts.head(5).items(), 1):
                    lines.append(f"   {rank}. {cat}: {cnt} transactions")

    # 3. Sample Representative Rows (First 5)
    lines.append(f"\nSAMPLE RECORDS (First 5 of {len(df)} rows for data formatting context):")
    for idx, row in df.head(5).iterrows():
        row_str = " | ".join(f"{k}: {v}" for k, v in row.items())
        lines.append(f"• [Row {idx + 1}] {row_str}")

    return "\n".join(lines)


def _dataframe_to_documents(df: pd.DataFrame, filename: str) -> List[Document]:
    """Convert each row of a DataFrame into an individual searchable Document."""
    documents: List[Document] = []
    for idx, row in df.iterrows():
        parts = [f"{col}: {val}" for col, val in row.items() if pd.notna(val)]
        content = f"Uploaded Business Record (Row {idx + 1}, {filename}): " + ", ".join(parts)
        meta = {
            "source": "uploaded_data",
            "row": idx + 1,
            "filename": filename,
        }
        documents.append(Document(page_content=content, metadata=meta))
    return documents


def _pdf_to_summary_and_docs(filepath: str, filename: str):
    """Extract summary and document chunks from a PDF file."""
    loader = PyPDFLoader(filepath)
    pages = loader.load()
    full_text = "\n".join(page.page_content for page in pages)

    summary = f"USER'S BUSINESS DOCUMENT ({filename}):\n{full_text[:3500]}"
    if len(full_text) > 3500:
        summary += "\n... (profile continues in index)"

    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=100)
    chunks = splitter.split_documents(pages)
    for c in chunks:
        c.metadata["source"] = "uploaded_data"
        c.metadata["filename"] = filename
    return summary, chunks


def _text_to_summary_and_docs(filepath: str, filename: str):
    """Extract summary and document chunks from a plain text file."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    summary = f"USER'S BUSINESS DOCUMENT ({filename}):\n{content[:3500]}"
    if len(content) > 3500:
        summary += "\n... (profile continues in index)"

    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=100)
    chunks = splitter.create_documents(
        texts=[content],
        metadatas=[{"source": "uploaded_data", "filename": filename}],
    )
    return summary, chunks


def _clean_tokenize(text: str) -> List[str]:
    """Tokenize words, numbers, and hyphenated codes while stripping trailing punctuation."""
    return re.findall(r"\b[\w-]+\b", text.lower())


def create_upload_retriever(docs: List[Document], k: int = 4) -> BM25Retriever:
    """
    Build a fast, ephemeral in-memory BM25 retriever for the user's uploaded records.
    """
    retriever = BM25Retriever.from_documents(docs, preprocess_func=_clean_tokenize)
    retriever.k = k
    return retriever


# ---------- Embeddings ----------

def download_hugging_face_embeddings():
    """
    Download and return HuggingFace sentence-transformer embeddings.
    Model: all-MiniLM-L6-v2 (384 dimensions).
    On memory-constrained servers (like Render's 512MB free tier), uses the
    cloud-hosted Inference API to save ~200MB of RAM.
    """
    hf_token = os.environ.get("HUGGINGFACEHUB_ACCESS_TOKEN") or os.environ.get("HF_TOKEN")
    if (os.environ.get("RENDER") or os.environ.get("USE_HF_API", "").lower() == "true") and hf_token:
        try:
            from langchain_huggingface.embeddings import HuggingFaceEndpointEmbeddings
            print("🌐 Connecting to HuggingFace Serverless Inference API for embeddings (saving ~200MB RAM)...")
            return HuggingFaceEndpointEmbeddings(
                model="sentence-transformers/all-MiniLM-L6-v2",
                huggingfacehub_api_token=hf_token,
            )
        except Exception as e:
            print(f"⚠️ Remote embeddings unavailable ({e}), falling back to local model...")

    return HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )


# ---------- BM25 Sparse Keyword Retriever ----------

BM25_CACHE_PATH = os.path.join("data", "bm25_cache.pkl")

def build_or_load_bm25_retriever(k: int = 5) -> BM25Retriever:
    """
    Build or load from disk cache a BM25 sparse keyword retriever
    from all corpus chunks (Book, Export CSV, Laws KB).
    """
    if os.path.exists(BM25_CACHE_PATH):
        try:
            with open(BM25_CACHE_PATH, "rb") as f:
                retriever = pickle.load(f)
                retriever.k = k
                print(f"Loaded BM25 sparse retriever from cache ({BM25_CACHE_PATH})")
                return retriever
        except Exception as e:
            print(f"Warning: Failed to load BM25 cache: {e}. Rebuilding...")

    print("🔨 Building BM25 sparse index from corpus documents...")
    book_docs = load_pdf_file(data_dir="data/book/") if os.path.exists("data/book/") else []
    export_docs = load_csv_as_documents(csv_path="data/export/merged_country_wise.csv") if os.path.exists("data/export/merged_country_wise.csv") else []
    laws_docs = load_text_file(file_path="data/import_export_laws_knowledge_base.txt") if os.path.exists("data/import_export_laws_knowledge_base.txt") else []

    all_docs = filter_to_minimal_docs(book_docs + export_docs + laws_docs)
    chunks = text_split(all_docs)

    bm25 = BM25Retriever.from_documents(chunks)
    bm25.k = k

    # Cache to disk
    try:
        os.makedirs(os.path.dirname(BM25_CACHE_PATH), exist_ok=True)
        with open(BM25_CACHE_PATH, "wb") as f:
            pickle.dump(bm25, f)
        print(f"💾 BM25 retriever cached to {BM25_CACHE_PATH}")
    except Exception as e:
        print(f"⚠️ Could not cache BM25 retriever: {e}")

    return bm25
