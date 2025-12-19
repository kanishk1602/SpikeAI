# Technical Documentation

This document describes the Spike AI Builder backend system end-to-end: architecture, request flows, internals of each agent, configuration, error handling, and extensibility.

---

## 1. System Overview

Spike AI Builder is a FastAPI backend that answers natural language questions about:

- **Google Analytics 4 (GA4)** (via GA4 Data API)
- **SEO crawl exports** from Screaming Frog (ingested from Google Sheets)
- **Cross-domain insights** using a multi-agent orchestration layer

The system exposes:

- **POST `/query`**: single interface for all user questions
- **GET `/health`**: verification endpoint

---

## 2. High-Level Architecture

### Components

| Component | File | Responsibility |
|----------|------|----------------|
| API Server | `main.py` | FastAPI entrypoint, request/response schema |
| Intent Router | `utils.py` | Classifies query to analytics/seo/multi |
| Analytics Agent | `agents.py:AnalyticsAgent` | GA4 query planning + execution + summarization |
| SEO Agent | `agents.py:SEOAgent` | Sheets ingestion + filters + slim output |
| GA4 Client | `ga4_client.py` | GA4 Data API wrapper and validation |
| Sheets Client | `seo_client.py` | Google Sheets reading helpers |
| LLM Client | `llm_client.py` | LiteLLM proxy wrapper with retries |

### Request Flow

```
Client
  |
  | POST /query { propertyId?, query }
  v
FastAPI (main.py)
  |
  | detect_intent(query)
  v
┌───────────────────────────────────────────────┐
│ Intent: analytics_only | seo_only | multi     │
└───────────────────────────────────────────────┘
  |
  ├─ analytics_only → AnalyticsAgent.handle_query
  ├─ seo_only       → SEOAgent.handle_query
  └─ multi          → run both agents concurrently
                     then (optionally) LLM fusion

Response JSON
```

---

## 2.1 Architecture Diagram (ASCII)

```
┌─────────────────────────────────────────────────────────────────┐
│                        POST /query                               │
│                     (Natural Language)                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Intent Detector                             │
│              (Deterministic keyword routing)                     │
│                                                                  │
│   "page views last 14 days" → analytics_only                    │
│   "URLs without HTTPS"      → seo_only                          │
│   "top pages + title tags"  → multi (both agents)               │
└─────────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│  AnalyticsAgent   │ │    SEOAgent       │ │  Multi-Agent      │
│     (GA4)         │ │ (Screaming Frog)  │ │   Orchestrator    │
├───────────────────┤ ├───────────────────┤ ├───────────────────┤
│ • Fetch metadata  │ │ • Read Sheets     │ │ • Run agents      │
│ • Select fields   │ │ • Filter rules    │ │   concurrently    │
│   heuristics/LLM  │ │ • Slim output     │ │ • LLM fusion      │
│ • Apply filters   │ │   (cols/rows)     │ │   (optional)      │
│ • Run report      │ │ • Deduplicate     │ │ • Partial success │
└───────────────────┘ └───────────────────┘ └───────────────────┘
            │                 │                       │
            ▼                 ▼                       ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│   GA4 Data API    │ │  Google Sheets    │ │    LiteLLM        │
│   (Data API v1)   │ │      API          │ │   /chat/complet.  │
└───────────────────┘ └───────────────────┘ └───────────────────┘
```

---

## 2.2 Key Tradeoffs (Engineering Decisions)

This section documents the intentional choices made during implementation, and what was traded away.

| Area | Choice | Why it was chosen | Tradeoff / Cost | Mitigation |
|------|--------|-------------------|-----------------|------------|
| Intent routing | Deterministic keyword matching (`utils.py`) | Zero external dependency; fast; predictable; easy to debug | Can misclassify ambiguous queries | Expanded keyword set + multi-intent triggers ("corresponding", "correlate") |
| GA4 field mapping | Heuristic-first, LLM fallback | Most questions map cleanly; saves latency/cost | Requires maintaining heuristics | LLM fallback constrained by metadata; safe defaults |
| GA4 schema correctness | Runtime metadata fetch (`get_metadata`) | Works across any property including custom fields; avoids invalid requests | Extra API call (~100ms) per query | Can be cached later (Redis/in-memory) |
| Empty GA4 results | Date-range fallback (requested → 28d → 90d) | Improves UX; avoids "no data" dead-ends | More GA4 API calls in worst case | Stops early once non-empty; bounded to 3 calls |
| SEO datasets | Load all tabs by default (optional) | Screaming Frog exports are often split by report type | Potentially large in-memory DataFrame | Output slimming + row caps + dedupe |
| SEO output size | Slim records (priority columns + strip nulls + dedupe + max_rows) | Keeps responses usable and evaluator-friendly | Loses non-priority columns | Counts preserved; evolve priority columns over time |
| LLM dependency | Optional LLM; system still works without it | Prevents hard dependency on an LLM service | Less natural answers if disabled | Deterministic outputs remain; summaries are best-effort |
| Multi-agent fusion | LLM-generated synthesis | Produces human-friendly joined insights | LLM may hallucinate if unconstrained | Fusion prompt uses only agent outputs; temperature=0; fallback returns raw agent outputs |
| Error handling | Structured error + next_steps | Maximizes evaluator success and debuggability | Slightly larger payloads in errors | Still smaller than raw stack traces; clear guidance |

---
