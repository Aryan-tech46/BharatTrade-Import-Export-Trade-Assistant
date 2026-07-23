from langchain_community.document_loaders import PyPDFLoader, DirectoryLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.schema import Document
from typing import List
import pandas as pd
import os


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


# ---------- Uploaded File Parsing ----------

def parse_uploaded_file(filepath: str, filename: str) -> str:
    """
    Parse an uploaded business-stats file and return a structured text
    summary suitable for LLM context injection.

    Supported formats: .csv, .xlsx, .xls, .pdf, .txt
    Returns a string capped at ~2000 characters.
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".csv":
        return _summarize_dataframe(pd.read_csv(filepath), filename)
    elif ext in (".xlsx", ".xls"):
        return _summarize_dataframe(pd.read_excel(filepath), filename)
    elif ext == ".pdf":
        return _summarize_pdf(filepath, filename)
    elif ext == ".txt":
        return _summarize_text(filepath, filename)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _summarize_dataframe(df: pd.DataFrame, filename: str) -> str:
    """Build a concise text summary from a pandas DataFrame."""
    lines: List[str] = []
    lines.append(f"USER'S BUSINESS DATA (uploaded file: {filename}):")
    lines.append(f"- Columns: {', '.join(df.columns.tolist())}")
    lines.append(f"- Total rows: {len(df)}")

    # Numeric column statistics
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if numeric_cols:
        lines.append("- Numeric summary:")
        for col in numeric_cols[:5]:  # limit to 5 numeric columns
            total = df[col].sum()
            mean = df[col].mean()
            lines.append(f"  • {col}: total={total:,.2f}, avg={mean:,.2f}, "
                         f"min={df[col].min():,.2f}, max={df[col].max():,.2f}")

    # Top rows preview
    preview_rows = min(15, len(df))
    lines.append(f"- First {preview_rows} rows:")
    for idx, row in df.head(preview_rows).iterrows():
        row_str = " | ".join(str(v) for v in row.values)
        lines.append(f"  Row {idx + 1}: {row_str}")

    summary = "\n".join(lines)
    # Cap at ~2000 chars to fit within LLM context
    if len(summary) > 2000:
        summary = summary[:1950] + "\n  ... (truncated)"
    return summary


def _summarize_pdf(filepath: str, filename: str) -> str:
    """Extract and summarize text from a PDF file."""
    loader = PyPDFLoader(filepath)
    pages = loader.load()
    full_text = "\n".join(page.page_content for page in pages)

    summary = f"USER'S BUSINESS DATA (uploaded file: {filename}):\n{full_text}"
    if len(summary) > 2000:
        summary = summary[:1950] + "\n... (truncated)"
    return summary


def _summarize_text(filepath: str, filename: str) -> str:
    """Read and summarize a plain text file."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    summary = f"USER'S BUSINESS DATA (uploaded file: {filename}):\n{content}"
    if len(summary) > 2000:
        summary = summary[:1950] + "\n... (truncated)"
    return summary


# ---------- Embeddings ----------

def download_hugging_face_embeddings():
    """
    Download and return HuggingFace sentence-transformer embeddings.
    Model: all-MiniLM-L6-v2 (384 dimensions).
    """
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )
    return embeddings
