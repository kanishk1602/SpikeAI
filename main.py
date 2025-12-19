# main.py
import os

# Load .env as early as possible so subsequent imports see env vars.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:
    # If python-dotenv isn't installed or .env is missing, proceed with OS env vars.
    pass

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agents import AnalyticsAgent, SEOAgent
from llm_client import LiteLLMClient
from utils import detect_intent

app = FastAPI()

# Initialize agents with config (no hardcoded secrets)
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "gemini-2.5-flash")

llm = LiteLLMClient(api_key=LITELLM_API_KEY, base_url=LITELLM_BASE, model=LITELLM_MODEL)

analytics_agent = AnalyticsAgent(llm_client=llm)
seo_agent = SEOAgent(llm_client=llm)


class QueryIn(BaseModel):
    propertyId: str | None = None
    query: str


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "llm_enabled": getattr(llm, "enabled", False),
        "llm_base_url": (LITELLM_BASE or None),
        "llm_model": LITELLM_MODEL,
    }


@app.post("/query")
async def query_endpoint(payload: QueryIn):
    user_query = payload.query.strip()
    intent = detect_intent(user_query)

    if intent == "seo_only":
        return await seo_agent.handle_query(user_query)

    if intent in {"analytics_only", "multi"} and not payload.propertyId:
        raise HTTPException(status_code=400, detail="propertyId required for GA4 queries")

    if intent == "analytics_only":
        return await analytics_agent.handle_query(user_query, property_id=payload.propertyId)

    if intent == "multi":
        ga = await analytics_agent.handle_query(user_query, property_id=payload.propertyId, return_structured=True)
        seo = await seo_agent.handle_query(user_query, return_structured=True)
        fused = llm.summarize_fusion(ga, seo, user_query)
        return {"query": user_query, "ga4": ga, "seo": seo, "fusion": fused}

    # unknown default: attempt analytics if propertyId present else SEO
    if payload.propertyId:
        return await analytics_agent.handle_query(user_query, property_id=payload.propertyId)
    return await seo_agent.handle_query(user_query)

