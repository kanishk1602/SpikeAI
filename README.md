# Spike AI Builder — Hackathon Submission

> **A production-ready multi-agent system for natural language analytics and SEO queries**

---

## 🎯 Problem Statement

Build a backend system that answers natural-language questions about:
1. **Google Analytics 4 (GA4)** data — traffic, page views, sessions, etc.
2. **SEO crawl data** (Screaming Frog exports) — title tags, HTTPS status, indexability
3. **Cross-referencing both** — correlating top pages with their SEO attributes

The system must expose a **single POST API endpoint** that intelligently routes queries to the appropriate agent(s).

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        POST /query                               │
│                     (Natural Language)                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Intent Detector                             │
│              (Keyword-based routing logic)                       │
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
│ • Metadata fetch  │ │ • Google Sheets   │ │ • Parallel exec   │
│ • Heuristic field │ │   reader          │ │ • LLM fusion      │
│   selection       │ │ • Column filtering│ │ • Cross-reference │
│ • LLM fallback    │ │ • Deduplication   │ │   insights        │
│ • Date range      │ │ • Result slimming │ │                   │
│   coercion        │ │                   │ │                   │
└───────────────────┘ └───────────────────┘ └───────────────────┘
            │                 │                       │
            ▼                 ▼                       ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│   GA4 Data API    │ │  Google Sheets    │ │    LiteLLM        │
│   (Beta v1)       │ │      API          │ │   (Gemini 2.5)    │
└───────────────────┘ └───────────────────┘ └───────────────────┘
```

---

## 🧠 Design Decisions & Thinking

### 1. **Single Endpoint Design**
**Why:** The hackathon required one unified API. Instead of separate endpoints for GA4/SEO, I built an intelligent router that detects intent from the query text.

```python
# utils.py - Intent detection
def detect_intent(query: str) -> str:
    # Keywords like "page views", "sessions" → analytics_only
    # Keywords like "HTTPS", "title tag" → seo_only  
    # Both or "correlate", "corresponding" → multi
```

**Tradeoff:** Simple keyword matching vs LLM-based intent detection
- ✅ Chose keywords for **speed** (no LLM call overhead)
- ✅ Deterministic and debuggable
- ⚠️ May misroute edge cases (mitigated by comprehensive keyword lists)

---

### 2. **Heuristic-First Field Selection (GA4)**
**Why:** GA4 has 100+ metrics/dimensions. Asking the LLM every time is slow and expensive.

```python
# agents.py - AnalyticsAgent
def _heuristic_fields(self, query_l: str):
    # "page views" → screenPageViews
    # "daily" → date dimension
    # "by country" → country dimension
```

**Strategy:**
1. **First:** Try keyword heuristics (instant, no API call)
2. **Fallback:** Ask LLM to pick from property's allowed fields
3. **Validate:** Always check against property metadata

**Tradeoff:** 
- ✅ 80% of queries handled without LLM
- ⚠️ Custom properties may need LLM fallback

---

### 3. **Metadata-Driven Validation**
**Why:** Different GA4 properties have different custom dimensions/metrics. Hardcoding would break.

```python
# ga4_client.py
def get_metadata(client, property_id):
    # Fetches ALL available metrics/dimensions for THIS property
    return {"dimensions": [...], "metrics": [...]}
```

**Flow:**
1. Fetch property metadata on every request
2. Validate selected fields against metadata
3. Strip invalid fields, add notes explaining what was dropped

**Tradeoff:**
- ✅ Works with any GA4 property (no assumptions)
- ⚠️ Extra API call per request (~100ms)

---

### 4. **Progressive Date Range Fallback**
**Why:** Users often ask for data in ranges with no traffic. Instead of returning empty, we try broader ranges.

```python
fallback_ranges = [
    (start_date, end_date),    # What user asked
    ("28daysAgo", "today"),    # Broader
    ("90daysAgo", "today"),    # Even broader
]
for sd, ed in fallback_ranges:
    rep = run_report(...)
    if rep.get("rows"):
        break  # Found data!
```

**Tradeoff:**
- ✅ More helpful responses
- ⚠️ Up to 3 GA4 API calls worst case

---

### 5. **Output Optimization (SEO Agent)**
**Why:** Screaming Frog exports have 50+ columns per row. Raw output was 100KB+ per response.

**Solution:**
```python
# agents.py - SEOAgent
priority_columns = ["Address", "Title 1", "Status Code", ...]
max_rows = 20

def _slim_records(self, records):
    # 1. Keep only priority columns
    # 2. Strip null/NaN values
    # 3. Deduplicate by URL
    # 4. Limit to 20 rows
```

**Result:** 100KB → 2KB responses

---

### 6. **Graceful LLM Degradation**
**Why:** LLM may be unavailable, rate-limited, or slow. System should still work.

```python
# llm_client.py
class LiteLLMClient:
    def ask(self, prompt: str) -> str:
        if not self.enabled:
            return ""  # Graceful fallback
        # Retry with exponential backoff
```

**If LLM fails:**
- GA4 Agent: Returns raw data without summary
- SEO Agent: Returns filtered results
- Multi-Agent: Skips fusion, returns individual agent outputs

---

### 7. **Error Handling Philosophy**
**Why:** APIs fail. Users need actionable guidance, not stack traces.

```python
# Structured error responses
{
    "error": "Permission denied for GA4 property",
    "propertyId": "123456789",
    "details": "403 User does not have sufficient permissions...",
    "next_steps": [
        "Verify the propertyId is correct",
        "Grant service account Viewer access in GA4 Admin"
    ]
}
```

**Covered scenarios:**
- ❌ Invalid credentials
- ❌ Property permission denied
- ❌ Empty data (suggests broadening query)
- ❌ Google Sheets access denied
- ❌ LLM timeout/failure

---

## ⚖️ Key Tradeoffs

| Decision | Chose | Alternative | Why |
|----------|-------|-------------|-----|
| Intent detection | Keywords | LLM classification | Speed & predictability |
| Field selection | Heuristics + LLM fallback | Always use LLM | Cost & latency |
| Metadata fetch | Per-request | Cache | Freshness over speed |
| Date fallback | 3 attempts | Single attempt | Better UX |
| Output format | Slim JSON | Full data | Response size |
| Error handling | Structured + next_steps | Generic errors | Developer experience |

---

## 📁 Project Structure

```
├── main.py              # FastAPI app + /query endpoint
├── agents.py            # AnalyticsAgent + SEOAgent classes
├── ga4_client.py        # GA4 Data API wrapper
├── seo_client.py        # Google Sheets reader (gspread)
├── llm_client.py        # LiteLLM client with retries
├── utils.py             # Intent detection
├── credentials.json     # Service account (gitignored)
├── .env                 # Environment config (gitignored)
├── .env.example         # Template
├── requirements.txt     # Dependencies
├── deploy.sh            # One-command deployment
└── README.md            # You are here
```

---

## 🚀 Quick Start

```bash
# 1. Clone and setup
git clone <repo>
cd SpikeAI

# 2. Add credentials
cp .env.example .env
# Edit .env with your LITELLM_API_KEY
# Place credentials.json (Google service account)

# 3. Run
bash deploy.sh

# 4. Test
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"propertyId": "YOUR_GA4_ID", "query": "top 10 pages by views last 7 days"}'
```

---

## 📡 API Reference

### POST `/query`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `propertyId` | string | For GA4 | GA4 property ID (numeric) |
| `query` | string | Yes | Natural language question |

### GET `/health`
Returns system status and LLM configuration.

---

## 🧪 Example Queries

### Tier 1 — Analytics Agent (GA4)
```json
{
  "propertyId": "516777993",
  "query": "Give me a daily breakdown of page views and sessions for the last 14 days"
}
```

### Tier 2 — SEO Agent
```json
{
  "query": "Which URLs do not use HTTPS and have title tags longer than 60 characters?"
}
```

### Tier 3 — Multi-Agent
```json
{
  "propertyId": "516777993", 
  "query": "What are the top 10 pages by views and their corresponding title tags?"
}
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LITELLM_API_KEY` | — | LiteLLM API key |
| `LITELLM_BASE_URL` | `http://3.110.18.218` | LiteLLM proxy URL |
| `LITELLM_MODEL` | `gemini-2.5-flash` | LLM model name |
| `GOOGLE_APPLICATION_CREDENTIALS` | `credentials.json` | Path to service account JSON |
| `GA4_PROPERTY_ID` | — | Default GA4 property ID |
| `SEO_SHEET_URL` | — | Screaming Frog Google Sheet URL |
| `SEO_SHEET_WORKSHEET_TITLE` | `*` | Worksheet tab (`*` = all tabs) |
| `SEO_SHEET_USE_ALL_TABS` | `true` | Read all sheet tabs |

---

