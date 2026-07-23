"""
app.py — Flask web server with RAG pipeline.

Serves the chat UI and handles user queries by:
1. Embedding the query
2. Retrieving top-k relevant chunks from Pinecone
3. Generating an answer with HuggingFace LLM + source citations

Includes file-upload support for personalized business suggestions.

Usage:
    python app.py
"""
from flask import Flask, render_template, jsonify, request, session
from src.helper import download_hugging_face_embeddings, parse_uploaded_file
from langchain_pinecone import PineconeVectorStore
from langchain_huggingface import HuggingFaceEndpoint, ChatHuggingFace
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv
from src.prompt import system_prompt, upload_system_prompt
from werkzeug.utils import secure_filename
import os
import uuid
import tempfile


app = Flask(__name__)
app.secret_key = os.urandom(24)

# ── In-memory store for uploaded data (keyed by session ID) ─────────
# For a single-user / demo app this is sufficient.
# For production, use Redis or a database.
uploaded_data_store = {}

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".pdf", ".txt"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


# ── Load environment variables ──────────────────────────────────────
load_dotenv()

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
HF_TOKEN = os.environ.get("HUGGINGFACEHUB_ACCESS_TOKEN")

if not PINECONE_API_KEY:
    raise ValueError("PINECONE_API_KEY not found in .env file")
if not HF_TOKEN:
    raise ValueError("HUGGINGFACEHUB_ACCESS_TOKEN not found in .env file")

os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
os.environ["HUGGINGFACEHUB_API_TOKEN"] = HF_TOKEN


# ── Initialize embeddings & vector store ────────────────────────────
embeddings = download_hugging_face_embeddings()

index_name = "impexp-chatbot"
docsearch = PineconeVectorStore.from_existing_index(
    index_name=index_name,
    embedding=embeddings,
)


# ── Build RAG chain ────────────────────────────────────────────────
retriever = docsearch.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 5},
)

llm = HuggingFaceEndpoint(
    repo_id="Qwen/Qwen2.5-72B-Instruct",
    task="text-generation",
    max_new_tokens=512,
    temperature=0.3,
    huggingfacehub_api_token=HF_TOKEN,
)
model = ChatHuggingFace(llm=llm)

# Standard RAG prompt (no upload)
prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{input}"),
])

question_answer_chain = create_stuff_documents_chain(model, prompt)
rag_chain = create_retrieval_chain(retriever, question_answer_chain)


# ── Helper: get or create session ID ───────────────────────────────
def _get_session_id():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


# ── Helper: build personalized RAG chain ───────────────────────────
def _build_personalized_chain(uploaded_data_text: str):
    """Create a RAG chain that includes the user's uploaded data in the
    system prompt alongside the retrieved trade-knowledge context."""
    personalized_prompt = ChatPromptTemplate.from_messages([
        ("system", upload_system_prompt.replace("{uploaded_data}", uploaded_data_text)),
        ("human", "{input}"),
    ])
    personal_qa_chain = create_stuff_documents_chain(model, personalized_prompt)
    return create_retrieval_chain(retriever, personal_qa_chain)


# ── Helper: extract sources from response ──────────────────────────
def _extract_sources(response, has_upload=False):
    """Pull source citations from the RAG response documents."""
    sources = []
    if "context" in response:
        print(f"📋 Retrieved {len(response['context'])} context documents:")
        for i, doc in enumerate(response["context"]):
            source_info = doc.metadata.get("source", "unknown")
            print(f"   [{i+1}] source={source_info}, metadata={doc.metadata}")
            print(f"       content preview: {doc.page_content[:120]}...")

            if source_info == "export_data":
                country = doc.metadata.get("country_to", "")
                commodity = doc.metadata.get("commodity", "")
                hs_code = doc.metadata.get("hs_code", "")
                label = f"📊 Export Data: India → {country}"
                if commodity:
                    label += f" ({commodity}"
                    if hs_code:
                        label += f", HS {hs_code}"
                    label += ")"
                sources.append(label)
            elif source_info == "laws_kb":
                sources.append("📜 Trade Laws & Regulations KB")
            elif source_info == "book":
                page = doc.metadata.get("page", "")
                if page:
                    sources.append(f"📖 Import/Export Book — Page {int(page) + 1}")
                else:
                    sources.append("📖 Import/Export Business Book")
            else:
                sources.append("📄 Knowledge Base")

    if has_upload:
        sources.insert(0, "📊 Uploaded Business Data")

    # Deduplicate while preserving order
    unique = list(dict.fromkeys(sources))
    print(f"🏷️  Final source tags: {unique}")
    return unique



# ── Routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the chat UI."""
    return render_template("chat.html")


@app.route("/upload", methods=["POST"])
def upload_file():
    """Handle file upload — parse and store summary in memory."""
    try:
        file = request.files.get("file")
        if not file or file.filename == "":
            return jsonify({"status": "error", "message": "No file selected."}), 400

        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()

        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({
                "status": "error",
                "message": f"Unsupported file type: {ext}. "
                           f"Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
            }), 400

        # Save to a temp file for parsing
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, filename)
        file.save(tmp_path)

        # Check file size
        file_size = os.path.getsize(tmp_path)
        if file_size > MAX_FILE_SIZE:
            os.remove(tmp_path)
            return jsonify({
                "status": "error",
                "message": f"File too large ({file_size // 1024}KB). Max: 5MB.",
            }), 400

        # Parse the file into a text summary
        summary = parse_uploaded_file(tmp_path, filename)

        # Clean up temp file
        os.remove(tmp_path)
        os.rmdir(tmp_dir)

        # Store in memory keyed by session
        sid = _get_session_id()
        uploaded_data_store[sid] = {
            "filename": filename,
            "summary": summary,
        }

        print(f"📂 File uploaded: {filename} (session: {sid})")
        print(f"📊 Summary preview: {summary[:200]}...")

        return jsonify({
            "status": "ok",
            "filename": filename,
            "preview": summary[:300],
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": f"Failed to process file: {str(e)}",
        }), 500


@app.route("/clear-upload", methods=["POST"])
def clear_upload():
    """Clear the uploaded file from session memory."""
    sid = _get_session_id()
    if sid in uploaded_data_store:
        del uploaded_data_store[sid]
        print(f"🗑️  Cleared upload for session: {sid}")
    return jsonify({"status": "cleared"})


@app.route("/get", methods=["GET", "POST"])
def chat():
    """Handle user messages — run RAG pipeline and return answer + sources."""
    try:
        msg = request.form.get("msg", "")
        if not msg:
            return jsonify({"answer": "Please enter a message.", "sources": []})

        print(f"🔍 User: {msg}")

        # Check if user has uploaded business data
        sid = _get_session_id()
        upload_info = uploaded_data_store.get(sid)

        if upload_info:
            # ── Personalized mode: include uploaded data in prompt ──
            print(f"📊 Using personalized mode (file: {upload_info['filename']})")
            personalized_chain = _build_personalized_chain(upload_info["summary"])
            response = personalized_chain.invoke({"input": msg})
        else:
            # ── Standard mode: existing RAG chain ──
            response = rag_chain.invoke({"input": msg})

        answer = response["answer"]
        print(f"💬 Bot: {answer}")

        sources = _extract_sources(response, has_upload=bool(upload_info))

        return jsonify({
            "answer": answer,
            "sources": sources,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error in /get route: {e}")
        return jsonify({
            "answer": f"Sorry, an error occurred: {str(e)}",
            "sources": [],
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)