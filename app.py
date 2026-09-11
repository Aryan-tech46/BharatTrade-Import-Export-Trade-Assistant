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
from flask import Flask, render_template, jsonify, request, session, Response, stream_with_context
from src.helper import (
    download_hugging_face_embeddings,
    parse_uploaded_file,
    build_or_load_bm25_retriever,
    create_upload_retriever,
)
from langchain_pinecone import PineconeVectorStore
from src.model_factory import get_chat_model
from src.session_store import (
    get_session_history,
    save_session_history,
    clear_session_history,
    get_uploaded_data,
    save_uploaded_data,
    clear_uploaded_data,
)
from langchain.chains import create_retrieval_chain, create_history_aware_retriever
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.retrievers import EnsembleRetriever
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from dotenv import load_dotenv
from src.prompt import system_prompt, upload_system_prompt, contextualize_q_system_prompt
from werkzeug.utils import secure_filename
import os
import sys
import uuid
import tempfile
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ── Load environment variables early ─────────────────────────────────
load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

MAX_HISTORY_MESSAGES = 10  # Keep last 10 messages (5 user-bot conversation turns)

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".pdf", ".txt"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


# ── Validate Required API Keys ──────────────────────────────────────
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
if not PINECONE_API_KEY:
    raise ValueError("PINECONE_API_KEY not found in .env file")

HF_TOKEN = os.environ.get("HUGGINGFACEHUB_ACCESS_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

if not (HF_TOKEN or GROQ_KEY or GEMINI_KEY):
    raise ValueError(
        "No LLM API keys found in .env! Please provide at least one of: "
        "GROQ_API_KEY, GEMINI_API_KEY, or HUGGINGFACEHUB_ACCESS_TOKEN."
    )

os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY
if HF_TOKEN:
    os.environ["HF_TOKEN"] = HF_TOKEN
    os.environ["HUGGINGFACEHUB_API_TOKEN"] = HF_TOKEN
    os.environ["HUGGINGFACEHUB_ACCESS_TOKEN"] = HF_TOKEN


# ── Initialize embeddings & vector store ────────────────────────────
embeddings = download_hugging_face_embeddings()

index_name = "impexp-chatbot"
docsearch = PineconeVectorStore.from_existing_index(
    index_name=index_name,
    embedding=embeddings,
)


# ── Build Hybrid (Dense + Sparse) Retriever ────────────────────────
# Dense semantic retriever (Pinecone)
dense_retriever = docsearch.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 5},
)

# Sparse keyword retriever (BM25 for exact HS codes, commodities, laws)
bm25_retriever = build_or_load_bm25_retriever(k=5)

# Ensemble hybrid retriever (40% keyword match + 60% semantic similarity)
hybrid_retriever = EnsembleRetriever(
    retrievers=[bm25_retriever, dense_retriever],
    weights=[0.4, 0.6],
)

# ── Initialize Resilient Multi-Provider Chat Model (with Fallbacks) ─
model = get_chat_model(temperature=0.3, max_tokens=2048)

# ── Step 1: Contextualize Question Sub-Chain (History-Aware Retriever) ──
# Reformulates a follow-up question (e.g. "What about taxes on that?")
# into a standalone search query if chat history exists.
contextualize_q_prompt = ChatPromptTemplate.from_messages([
    ("system", contextualize_q_system_prompt),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])

history_aware_retriever = create_history_aware_retriever(
    model, hybrid_retriever, contextualize_q_prompt
)

# ── Step 2: Answer Generation Chains ──
# Standard RAG prompt (no upload, with chat history)
prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])

question_answer_chain = create_stuff_documents_chain(model, prompt)
rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)


# ── Helper: get or create session ID ───────────────────────────────
def _get_session_id():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


# ── Helper: get session chat history ───────────────────────────────
def _get_session_history(sid: str):
    """Retrieve chat history list for a session from session store."""
    return get_session_history(sid)


# ── Helper: add message turn to session history ────────────────────
def _add_to_session_history(sid: str, user_msg: str, bot_msg: str):
    """Append user message and bot response, maintaining sliding window and TTL."""
    history = get_session_history(sid)
    history.append(HumanMessage(content=user_msg))
    history.append(AIMessage(content=bot_msg))
    if len(history) > MAX_HISTORY_MESSAGES:
        history = history[-MAX_HISTORY_MESSAGES:]
    save_session_history(sid, history)


# ── Helper: build personalized RAG chain ───────────────────────────
def _build_personalized_chain(upload_info: dict):
    """Create a RAG chain that merges the user's ephemeral uploaded record retriever
    with the trade knowledge retriever and embeds the statistical profile into the prompt."""
    user_retriever = upload_info.get("retriever")

    if user_retriever:
        # Combine user file rows (50%) and official trade knowledge (50%)
        combined_retriever = EnsembleRetriever(
            retrievers=[user_retriever, hybrid_retriever],
            weights=[0.5, 0.5],
        )
    else:
        combined_retriever = hybrid_retriever

    personal_history_retriever = create_history_aware_retriever(
        model, combined_retriever, contextualize_q_prompt
    )

    personalized_prompt = ChatPromptTemplate.from_messages([
        ("system", upload_system_prompt.replace("{uploaded_data}", upload_info["summary"])),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])
    personal_qa_chain = create_stuff_documents_chain(model, personalized_prompt)
    return create_retrieval_chain(personal_history_retriever, personal_qa_chain)


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

            if source_info == "uploaded_data":
                row = doc.metadata.get("row", "")
                fname = doc.metadata.get("filename", "User File")
                if row:
                    sources.append(f"📊 Uploaded Record (Row {row} of {fname})")
                else:
                    sources.append(f"📊 Uploaded File ({fname})")
            elif source_info == "export_data":
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

    if has_upload and not any("Uploaded" in s for s in sources):
        sources.insert(0, "📊 Uploaded Business Profile")

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

        # Parse the file into a statistical summary and chunked documents
        summary, upload_docs = parse_uploaded_file(tmp_path, filename)

        # Clean up temp file
        os.remove(tmp_path)
        os.rmdir(tmp_dir)

        # Build ephemeral BM25 retriever for uploaded documents if any
        upload_retriever = create_upload_retriever(upload_docs, k=4)

        # Store in session store with 1-hour TTL
        sid = _get_session_id()
        upload_info = {
            "filename": filename,
            "summary": summary,
            "retriever": upload_retriever,
        }
        save_uploaded_data(sid, upload_info)

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
    clear_uploaded_data(sid)
    print(f"🗑️  Cleared upload for session: {sid}")
    return jsonify({"status": "cleared"})


@app.route("/clear-history", methods=["POST"])
def clear_history():
    """Clear conversation chat history for active session."""
    sid = _get_session_id()
    clear_session_history(sid)
    print(f"🧹 Cleared chat history for session: {sid}")
    return jsonify({"status": "cleared"})


@app.route("/get", methods=["GET", "POST"])
def chat():
    """Handle user messages — stream tokens and sources in real-time using Server-Sent Events (SSE)."""
    msg = request.form.get("msg", "") if request.method == "POST" else request.args.get("msg", "")
    if not msg:
        def empty_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Please enter a message.'})}\n\n"
        return Response(empty_gen(), mimetype="text/event-stream")

    print(f"🔍 User: {msg}")

    # Check session info, uploaded business data, and chat history
    sid = _get_session_id()
    upload_info = get_uploaded_data(sid)
    history = _get_session_history(sid)

    def generate_stream():
        full_answer = []
        collected_context = None
        has_upload = bool(upload_info)

        try:
            if upload_info:
                print(f"📊 Using personalized stream mode (file: {upload_info['filename']}, history turns: {len(history)//2})")
                chain = _build_personalized_chain(upload_info)
            else:
                print(f"💬 Standard RAG stream mode (history turns: {len(history)//2})")
                chain = rag_chain

            # Stream chunks from RAG chain
            for chunk in chain.stream({"input": msg, "chat_history": history}):
                # Retrieve context documents chunk
                if "context" in chunk and chunk["context"] and collected_context is None:
                    collected_context = chunk["context"]
                    sources = _extract_sources({"context": collected_context}, has_upload=has_upload)
                    yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"

                # Stream answer token
                if "answer" in chunk and chunk["answer"]:
                    token_text = chunk["answer"]
                    full_answer.append(token_text)
                    yield f"data: {json.dumps({'type': 'token', 'token': token_text})}\n\n"

            # If sources were not sent yet, extract and send them
            if collected_context is None:
                sources = _extract_sources({}, has_upload=has_upload)
                yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"

            final_answer = "".join(full_answer)
            print(f"💬 Bot (stream complete): {final_answer[:100]}...")

            # Save full turn to session history
            if final_answer:
                _add_to_session_history(sid, msg, final_answer)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = str(e)
            print(f"❌ Error in streaming /get: {err_msg}")

            if "403" in err_msg or "permissions" in err_msg or "Inference" in err_msg:
                user_friendly_err = (
                    "⚠️ **Hugging Face API Authentication Error (403 Forbidden)**\n\n"
                    "Your `HUGGINGFACEHUB_ACCESS_TOKEN` lacks permission to call Inference Providers.\n\n"
                    "**How to fix:**\n"
                    "1. Open [https://huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)\n"
                    "2. Click **Create new token** (or edit your existing token).\n"
                    "3. Under **Permissions**, select **Fine-grained** or **User permissions** and check:\n"
                    "   - ✅ **Make calls to Inference Providers**\n"
                    "   - ✅ **Make calls to the serverless Inference API**\n"
                    "4. Copy the new token into `.env` as `HUGGINGFACEHUB_ACCESS_TOKEN=hf_...` and restart `app.py`."
                )
            else:
                user_friendly_err = f"⚠️ An error occurred while generating response: {err_msg}"

            yield f"data: {json.dumps({'type': 'error', 'message': user_friendly_err})}\n\n"

    return Response(
        stream_with_context(generate_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("\n" + "=" * 65)
    print("🚀 IMPORT/EXPORT CHATBOT SERVER IS LIVE & READY!")
    print(f"👉 Click to open: http://localhost:{port}")
    print(f"👉 Network URL  : http://127.0.0.1:{port}")
    print("   (Press CTRL+C to stop)")
    print("=" * 65 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)