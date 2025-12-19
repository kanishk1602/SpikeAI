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

## 3. API Specification

### 3.1 POST `/query`

#### Request Body

| Field | Type | Required | Notes |
|------|------|----------|------|
| `query` | string | Yes | Natural language question |
| `propertyId` | string | For GA4 / multi | GA4 numeric property ID; if absent system may use `GA4_PROPERTY_ID` env var |

#### Typical Response Shapes

**Analytics only**
```json
{
  "query": "...",
  "report": {
    "dimensionHeaders": ["date"],
    "metricHeaders": ["screenPageViews"],
    "rows": [...],
    "notes": {...}
  },
  "summary": "..."
}
```

**SEO only**
```json
{
  "query": "...",
  "results": {
    "long_title_tags": [{"Address": "...", "Title 1": "..."}],
    "long_title_count": 30
  }
}
```

**Multi-agent**
```json
{
  "query": "...",
  "ga4": {"report": {...}, "summary": "..."},
  "seo": {"results": {...}},
  "fusion": "..." 
}
```

#### Error Response Format
Errors are returned as structured JSON:
```json
{
  "query": "...",
  "error": "Permission denied for GA4 property",
  "details": "403 ...",
  "next_steps": ["..."]
}
```

### 3.2 GET `/health`

Returns:
- server status
- LLM configuration availability

---

## 4. Intent Detection (`utils.py`)

### Implementation Summary

Intent is detected via deterministic keyword matching:

- **analytics_only**: GA4-related words (sessions, users, views, top pages, etc.)
- **seo_only**: SEO words (https, title tag, meta, indexable, screaming frog, etc.)
- **multi**: combination of GA4 + SEO or cross-reference language (corresponding, correlate, fusion)

### Tradeoffs

- ✅ Predictable and fast
- ✅ No LLM/routing dependency
- ⚠️ Keyword list must be maintained over time

---

## 5. Analytics Agent (`agents.py:AnalyticsAgent`)

### Responsibilities

1. Load service account credentials
2. Fetch GA4 metadata for the property
3. Choose metrics and dimensions
4. Apply filters (e.g., pagePath contains)
5. Execute GA4 report
6. Generate summary using LLM (optional)

### 5.1 Field Selection

**Step 1: Heuristics**
Maps query words → GA4 metrics/dimensions.

Examples:
- "pageviews" → `screenPageViews`
- "sessions" → `sessions`
- "daily" → `date`
- "pages" → `pagePath`

**Step 2: LLM fallback**
If heuristics fail, it asks the LLM to select from allowed fields.

**Validation**
All selected fields are validated against metadata to avoid invalid GA4 requests.

### 5.2 Date Range Resolution

`_coerce_date_range()` maps:
- today/yesterday
- last 7/14/30/90 days

If result is empty, the agent broadens date range:
- requested range → 28 days → 90 days

### 5.3 Top-N Queries

Detects intent like:
- "top 10 pages"

Applies:
- `limit = N`
- `order_by_metric = first metric` descending

### 5.4 Page Hint Extraction

Detects explicit paths like `/pricing` and applies a dimension filter:
- `pagePath CONTAINS /pricing`

---

## 6. SEO Agent (`agents.py:SEOAgent`)

### Responsibilities

1. Load Screaming Frog exports via Google Sheets
2. Run filters based on query (HTTPS, title tags length, etc.)
3. Return optimized results

### 6.1 Sheet Loading

Supports:
- Single tab read
- All tabs merge (if `SEO_SHEET_USE_ALL_TABS=true` or title is `*`)

If multiple tabs are loaded, a `__sheet` column is added to keep provenance.

### 6.2 Query → Filters

Supports:
- Non-HTTPS URLs
- Long title tags (>60 chars)
- Intersection filtering: non-HTTPS AND long title
- Indexability count summary

### 6.3 Output Optimization

To prevent enormous responses:

- **priority columns** only
- strip null/NaN
- deduplicate by URL
- limit rows (default 20)
- include total counts (e.g., `long_title_count`)

---

## 7. Multi-Agent Orchestration

### Flow

When intent is `multi`:

1. Run GA4 and SEO agents concurrently
2. Return both payloads in response
3. If LLM is enabled, create a fusion summary/recommendations

### Failure Behavior

- If GA4 fails but SEO succeeds → still return SEO results + GA4 error
- If SEO fails but GA4 succeeds → still return GA4 report + SEO error
- If fusion fails → still return both agent outputs

---

## 8. GA4 Client (`ga4_client.py`)

### Responsibilities

- Instantiate GA4 Data API client from service account file
- Fetch and parse metadata
- Validate requested fields against metadata
- Construct RunReportRequest:
  - metrics
  - dimensions
  - order_bys
  - dimension filter expressions

---

## 9. LLM Client (`llm_client.py`)

### Responsibilities

- Wrap LiteLLM proxy `/chat/completions`
- Add Authorization header
- Retry with backoff for 429 / transient failures

### Runtime Configuration

LLM is enabled only when:
- `LITELLM_BASE_URL` exists
- `LITELLM_API_KEY` exists

---

## 10. Configuration

Configuration is via environment variables (supports `.env`).

See `.env.example` for all available settings.

### Required
- `GOOGLE_APPLICATION_CREDENTIALS` (service account file)
- `SEO_SHEET_URL`

### Optional
- `LITELLM_API_KEY`, `LITELLM_BASE_URL`, `LITELLM_MODEL`
- `GA4_PROPERTY_ID`

---

## 11. Security Practices

- `.gitignore` prevents committing:
  - `.env`
  - `credentials.json`
  - keys/certs
- Only service account JSON is supported for Google APIs

---

## 12. Extensibility Guide

### Adding a New Data Source Agent

1. Create a new agent class with:
   - `async handle_query(query: str, ...)` method
2. Add keywords in `utils.py:detect_intent`
3. Update router logic in `main.py`

### Suggested future agents
- Google Search Console agent
- PageSpeed Insights agent
- Paid ads agent

---

## 13. Operational Notes

- Output sizes are controlled in SEOAgent to avoid huge payloads.
- GA4 agent already limits preview rows for summaries.
- For production, consider:
  - caching GA4 metadata
  - request tracing/logging
  - per-user rate limiting

---

## 14. Known Limitations

- Keyword-based intent detection can misclassify some ambiguous queries.
- GA4 metadata is fetched per request (can be cached in production).
- SEO filters are rule-based; more complex analysis would benefit from schema-aware planning.

---

## 15. Appendix: Files and Key Functions

| File | Key functions/classes |
|------|------------------------|
| `main.py` | FastAPI app, `/query`, `/health` |
| `utils.py` | `detect_intent()` |
| `agents.py` | `AnalyticsAgent`, `SEOAgent` |
| `ga4_client.py` | `load_client_from_service_account`, `get_metadata`, `run_report` |
| `seo_client.py` | `open_sheet_by_url`, `open_all_worksheets_by_url` |
| `llm_client.py` | `LiteLLMClient.ask()` |
