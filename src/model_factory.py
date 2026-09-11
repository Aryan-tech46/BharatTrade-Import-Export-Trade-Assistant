"""
src/model_factory.py — Resilient multi-provider LLM factory with automatic failover.

Architecture:
1. Detects available API keys from environment (.env):
   - GROQ_API_KEY -> ChatGroq (auto-detects top model like openai/gpt-oss-120b + 20b backup)
   - GEMINI_API_KEY / GOOGLE_API_KEY -> ChatGoogleGenerativeAI (gemini-1.5-flash)
   - HUGGINGFACEHUB_ACCESS_TOKEN -> ChatHuggingFace (Qwen/Qwen2.5-72B-Instruct + 7B backup)
2. Validates credentials quickly to avoid long retry loops.
3. Chains using LangChain's RunnableWithFallbacks for zero-downtime failover.
"""

import os
import sys
from typing import List, Optional, Tuple
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(override=True)


def get_available_groq_models(api_key: str) -> List[str]:
    """Query Groq API to find which models are actually supported for this account."""
    try:
        from groq import Groq
        client = Groq(api_key=api_key, timeout=5.0)
        models_data = client.models.list().data
        available = {m.id for m in models_data}
        
        # Priority order of preferred models
        preferred = [
            "openai/gpt-oss-120b",
            "llama-3.3-70b-versatile",
            "openai/gpt-oss-20b",
            "llama-3.1-8b-instant",
            "groq/compound-mini",
        ]
        active = [m for m in preferred if m in available]
        return active if active else ["openai/gpt-oss-20b"]
    except Exception as e:
        print(f"ℹ️ Could not query Groq model list: {e}. Falling back to default list.")
        return ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def build_groq_model(
    model_name: str,
    api_key: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
):
    """Build a specific ChatGroq model instance."""
    key = api_key or os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    try:
        from langchain_groq import ChatGroq
        return ChatGroq(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            groq_api_key=key,
            timeout=10,
            max_retries=1,
        )
    except Exception as e:
        print(f"⚠️ Could not initialize Groq provider ({model_name}): {e}")
        return None


def build_gemini_model(
    api_key: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
):
    """Build ChatGoogleGenerativeAI model instance if key is valid."""
    key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return None
    
    # Valid Google Gemini API keys typically start with AIzaSy or AQ.
    if not key.startswith("AQ."):
        print(f"⚠️ GEMINI_API_KEY in .env format not recognized. Expected key starting with 'AIzaSy' or 'AQ.'. Skipping Gemini to prevent hanging.")
        return None

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model="gemini-3.6-flash",
            temperature=temperature,
            max_output_tokens=max_tokens,
            google_api_key=key,
            timeout=8,
            max_retries=1,
        )
    except Exception as e:
        print(f"⚠️ Could not initialize Gemini provider: {e}")
        return None


def build_huggingface_model(
    token: Optional[str] = None,
    repo_id: str = "Qwen/Qwen2.5-72B-Instruct",
    temperature: float = 0.3,
    max_tokens: int = 512,
):
    """Build ChatHuggingFace model instance."""
    tok = token or os.environ.get("HUGGINGFACEHUB_ACCESS_TOKEN") or os.environ.get("HF_TOKEN")
    if not tok:
        return None
    try:
        from langchain_huggingface import HuggingFaceEndpoint, ChatHuggingFace
        llm = HuggingFaceEndpoint(
            repo_id=repo_id,
            task="text-generation",
            max_new_tokens=max_tokens,
            temperature=temperature,
            huggingfacehub_api_token=tok,
            timeout=20,
        )
        return ChatHuggingFace(llm=llm)
    except Exception as e:
        print(f"⚠️ Could not initialize HuggingFace provider ({repo_id}): {e}")
        return None


def get_chat_model(temperature: float = 0.3, max_tokens: int = 2048):
    """
    Dynamically discover and chain all configured LLM providers with automatic fallback.
    """
    candidates = []
    names = []

    # 1. Groq (Ultra-Fast)
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        available_groq = get_available_groq_models(groq_key)
        for idx, model_id in enumerate(available_groq[:2]):
            groq_inst = build_groq_model(
                model_name=model_id,
                api_key=groq_key,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if groq_inst:
                candidates.append(groq_inst)
                names.append(f"Groq ({model_id})")

    # 2. Google Gemini (Fast & Generous Free Tier)
    gemini = build_gemini_model(temperature=temperature, max_tokens=max_tokens)
    if gemini:
        candidates.append(gemini)
        names.append("Gemini (gemini-3.6-flash)")

    # 3. Hugging Face Primary (72B)
    hf_primary = build_huggingface_model(
        repo_id="Qwen/Qwen2.5-72B-Instruct",
        temperature=temperature,
        max_tokens=min(max_tokens, 512),
    )
    if hf_primary:
        candidates.append(hf_primary)
        names.append("HuggingFace (Qwen2.5-72B)")

    # 4. Hugging Face Secondary Fallback (7B)
    hf_secondary = build_huggingface_model(
        repo_id="Qwen/Qwen2.5-7B-Instruct",
        temperature=temperature,
        max_tokens=min(max_tokens, 512),
    )
    if hf_secondary:
        candidates.append(hf_secondary)
        names.append("HuggingFace-Backup (Qwen2.5-7B)")

    if not candidates:
        raise ValueError(
            "No LLM provider available! Please set at least one of GROQ_API_KEY, "
            "GEMINI_API_KEY (or GOOGLE_API_KEY), or HUGGINGFACEHUB_ACCESS_TOKEN in your .env file."
        )

    primary = candidates[0]
    fallbacks = candidates[1:]

    print("\n" + "=" * 65)
    print("🤖 LLM INFERENCE PROVIDER CONFIGURATION:")
    print(f"   ⚡ Primary Provider  : {names[0]}")
    if fallbacks:
        print(f"   🛡️ Fallback Providers: {', '.join(names[1:])}")
    else:
        print("   ℹ️ No fallback providers configured.")
    print("=" * 65 + "\n")

    if fallbacks:
        return primary.with_fallbacks(fallbacks)
    return primary
