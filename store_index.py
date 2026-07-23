"""
store_index.py — Offline ingestion pipeline.

Loads book PDFs, Indian export CSV, and trade laws knowledge base,
chunks them, generates embeddings,
and upserts everything into a Pinecone serverless index.

Usage:
    python store_index.py
"""
from dotenv import load_dotenv
import os
from src.helper import (
    load_pdf_file,
    load_csv_as_documents,
    load_text_file,
    filter_to_minimal_docs,
    text_split,
    download_hugging_face_embeddings,
)
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore


# ── Load environment variables ──────────────────────────────────────
load_dotenv()

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not PINECONE_API_KEY:
    raise ValueError("PINECONE_API_KEY not found in .env file")

os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY


# ── Step 1: Load data ──────────────────────────────────────────────
print("📚 Loading book PDFs...")
book_docs = load_pdf_file(data_dir="data/book/")
print(f"   → Loaded {len(book_docs)} pages from book PDF(s).")

print("📊 Loading export CSV...")
export_docs = load_csv_as_documents(csv_path="data/export/merged_country_wise.csv")
print(f"   → Loaded {len(export_docs)} export data entries.")

print("📜 Loading trade laws knowledge base...")
laws_docs = load_text_file(file_path="data/import_export_laws_knowledge_base.txt")
print(f"   → Loaded {len(laws_docs)} law document(s).")


# ── Step 2: Filter metadata ────────────────────────────────────────
print("🔧 Filtering metadata...")
all_docs = book_docs + export_docs + laws_docs
filtered_docs = filter_to_minimal_docs(all_docs)


# ── Step 3: Chunk text ─────────────────────────────────────────────
print("✂️  Chunking documents...")
text_chunks = text_split(filtered_docs)
print(f"   → Created {len(text_chunks)} chunks total.")


# ── Step 4: Initialize embeddings ──────────────────────────────────
print("🧠 Downloading HuggingFace embeddings model...")
embeddings = download_hugging_face_embeddings()


# ── Step 5: Create Pinecone index (if needed) ──────────────────────
index_name = "impexp-chatbot"
pc = Pinecone(api_key=PINECONE_API_KEY)

print(f"📌 Checking Pinecone index '{index_name}'...")
if not pc.has_index(index_name):
    print(f"   → Creating index '{index_name}' (384 dims, cosine, serverless)...")
    pc.create_index(
        name=index_name,
        dimension=384,  # all-MiniLM-L6-v2 output dimension
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    print(f"   → Index '{index_name}' created.")
else:
    print(f"   → Index '{index_name}' already exists.")


# ── Step 6: Upsert documents into Pinecone ─────────────────────────
print("⬆️  Upserting document chunks into Pinecone...")
docsearch = PineconeVectorStore.from_documents(
    documents=text_chunks,
    index_name=index_name,
    embedding=embeddings,
)
print("✅ Ingestion complete! All chunks embedded and stored in Pinecone.")


# ── Summary ─────────────────────────────────────────────────────────
index = pc.Index(index_name)
stats = index.describe_index_stats()
print(f"\n📈 Index stats: {stats}")
