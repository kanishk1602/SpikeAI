# agents.py
import os
import asyncio
from ga4_client import load_client_from_service_account, run_report
from ga4_client import get_metadata
from seo_client import open_sheet_by_url
from seo_client import open_all_worksheets_by_url
from llm_client import LiteLLMClient

class AnalyticsAgent:
    def __init__(self, llm_client: LiteLLMClient, credential_path="credentials.json"):
        self.llm = llm_client
        self.credential_path = credential_path

    def _coerce_date_range(self, q: str) -> tuple[str, str]:
        ql = q.lower()
        if "today" in ql:
            return "today", "today"
        if "yesterday" in ql:
            return "yesterday", "yesterday"
        if "7 days" in ql or "last week" in ql:
            return "7daysAgo", "today"
        if "14 days" in ql or "two weeks" in ql:
            return "14daysAgo", "today"
        if "30 days" in ql or "last month" in ql:
            return "30daysAgo", "today"
        if "90 days" in ql or "last 3 months" in ql:
            return "90daysAgo", "today"
        return "14daysAgo", "today"

    def _heuristic_fields(self, query_l: str) -> tuple[list[str], list[str]]:
        metrics: list[str] = []
        dims: list[str] = []

        # Metrics
        if any(w in query_l for w in ["active users", "active user"]):
            metrics.append("activeUsers")
        if "new users" in query_l:
            metrics.append("newUsers")
        if "users" in query_l and "new users" not in query_l and "active users" not in query_l:
            metrics.append("totalUsers")
        if "sessions" in query_l:
            metrics.append("sessions")
        if any(w in query_l for w in ["engaged", "engagement"]):
            metrics.append("engagedSessions")
        if any(w in query_l for w in ["avg session", "average session", "session duration"]):
            metrics.append("averageSessionDuration")
        if any(w in query_l for w in ["pageview", "page view", "pageviews", "page views", "views"]):
            # GA4 Data API uses screenPageViews (not legacy pageviews)
            metrics.append("screenPageViews")

        # Dimensions
        if any(w in query_l for w in ["daily", "by day", "day-wise", "per day", "trend"]):
            dims.append("date")
        if any(w in query_l for w in ["page", "pages", "path", "landing"]):
            dims.append("pagePath")
        if "title" in query_l:
            dims.append("pageTitle")
        if any(w in query_l for w in ["country"]):
            dims.append("country")
        if any(w in query_l for w in ["city"]):
            dims.append("city")
        if any(w in query_l for w in ["device", "mobile", "desktop", "tablet"]):
            dims.append("deviceCategory")
        if any(w in query_l for w in ["source/medium", "source", "medium"]):
            if "source" in query_l:
                dims.append("source")
            if "medium" in query_l:
                dims.append("medium")
        if any(w in query_l for w in ["channel", "channel group"]):
            dims.append("channelGroup")

        return metrics, dims

    def _llm_pick_fields(self, query: str, allowed_metrics: list[str], allowed_dims: list[str]) -> tuple[list[str], list[str]]:
        """Ask LLM for a small set of GA4 fields. Output must be JSON."""
        prompt = f"""
You map natural-language questions to GA4 Data API fields.

Return STRICT JSON ONLY with keys: metrics, dimensions.
- metrics: 1-3 strings from allowed list
- dimensions: 0-2 strings from allowed list (use [] if none)

Allowed metrics: {allowed_metrics}
Allowed dimensions: {allowed_dims}

User question: {query}
""".strip()

        raw = self.llm.ask(prompt)
        try:
            import json as _json
            obj = _json.loads(raw)
            m = obj.get("metrics", []) or []
            d = obj.get("dimensions", []) or []
            return [str(x) for x in m], [str(x) for x in d]
        except Exception:
            return [], []

    # NEW
    def _detect_top_query(self, ql: str) -> bool:
        return any(w in ql for w in ["top ", "top pages", "top page", "highest", "most ", "best "]) and any(
            w in ql for w in ["page", "pages", "country", "countries", "city", "cities", "source", "medium", "channel", "device", "user", "users"]
        )

    # NEW
    def _parse_top_n(self, ql: str, default: int = 10, max_n: int = 100) -> int:
        import re
        m = re.search(r"\btop\s+(\d+)\b", ql)
        if not m:
            return default
        try:
            n = int(m.group(1))
            return max(1, min(max_n, n))
        except Exception:
            return default

    # NEW
    def _extract_page_hint(self, query: str) -> tuple[str | None, str | None]:
        """Return (dimension, value) hint for page-scoped questions."""
        ql = query.lower()
        import re

        # explicit /path
        m = re.search(r"\B(/[^\s,?.]+)", query)
        if m:
            return "pagePath", m.group(1)

        # common english references
        if "homepage" in ql or "home page" in ql or "home" in ql:
            # GA4 homepage can be '/' or sometimes '(not set)' patterns; use contains '/' is too broad.
            return "pagePath", "/"

        if "pricing" in ql:
            return "pagePath", "/pricing"

        return None, None

    async def handle_query(self, query: str, property_id: str, return_structured: bool=False):
        ql = query.lower()

        try:
            client = load_client_from_service_account(self.credential_path)
        except Exception as e:
            return {
                "query": query,
                "error": "Failed to load GA4 credentials",
                "details": str(e),
                "next_steps": ["Ensure credentials.json exists and is a valid service account key file."],
            }

        try:
            metadata = get_metadata(client, property_id)
        except Exception as e:
            err_str = str(e).lower()
            if "permission" in err_str or "403" in err_str:
                return {
                    "query": query,
                    "error": "Permission denied for GA4 property",
                    "propertyId": property_id,
                    "details": str(e),
                    "next_steps": [
                        "Verify the propertyId is correct (numeric ID, not 'UA-' or 'G-' prefixed).",
                        "Grant the service account Viewer access in GA4 Admin > Property Access Management.",
                    ],
                }
            return {
                "query": query,
                "error": "Failed to fetch GA4 metadata",
                "propertyId": property_id,
                "details": str(e),
            }

        allowed_metrics = sorted(metadata.get("metrics", []))
        allowed_dims = sorted(metadata.get("dimensions", []))

        metrics, dims = self._heuristic_fields(ql)

        if not metrics:
            lm, ld = self._llm_pick_fields(query, allowed_metrics, allowed_dims)
            metrics = lm or metrics
            dims = ld or dims

        if not metrics:
            metrics = allowed_metrics[:1]
        if not dims:
            dims = ["date"] if "date" in allowed_dims else (allowed_dims[:1] if allowed_dims else [])

        metrics = [m for m in metrics if m in allowed_metrics]
        dims = [d for d in dims if d in allowed_dims]

        if not metrics:
            return {"query": query, "error": "Unable to select any valid GA4 metrics for this property"}
        if not dims:
            return {"query": query, "error": "Unable to select any valid GA4 dimensions for this property"}

        # build dimension filters if user is asking about a specific page/path
        dimension_filters = []
        page_dim, page_val = self._extract_page_hint(query)
        if page_dim and page_val and page_dim in allowed_dims:
            # contains is safer for '/pricing' vs '/pricing/'
            dimension_filters.append({"field": page_dim, "op": "CONTAINS", "value": page_val})
            # ensure page dimension is included if user asked "for /pricing page"
            if page_dim not in dims:
                dims.append(page_dim)

        is_top = self._detect_top_query(ql)
        if is_top:
            report_limit = self._parse_top_n(ql, default=10)
        else:
            report_limit = 10000

        order_metric = metrics[0] if metrics else None

        # Date range handling
        start_date, end_date = self._coerce_date_range(query)

        # Special case: period-over-period comparisons
        wants_compare = any(w in ql for w in ["compare", "previous", "vs", "versus", "period over period"])
        wants_avg = "average" in ql and "daily" in ql

        def _run(sd: str, ed: str):
            return run_report(
                client,
                property_id,
                metrics,
                dims,
                start_date=sd,
                end_date=ed,
                metadata=metadata,
                validate_with_metadata=True,
                limit=report_limit,
                order_by_metric=order_metric if is_top else None,
                order_desc=True,
                dimension_filters=dimension_filters or None,
            )

        last_report = None
        last_err = None

        if wants_compare:
            # Compare last 30 days vs previous 30 days if not specified.
            # If user specified 30 days, keep that; otherwise default to 30.
            cur_sd, cur_ed = ("30daysAgo", "today") if "30" in ql else (start_date, end_date)
            # Previous period: 60-31 days ago
            prev_sd, prev_ed = ("60daysAgo", "31daysAgo")

            try:
                cur = _run(cur_sd, cur_ed)
                prev = _run(prev_sd, prev_ed)
                last_report = {"current": cur, "previous": prev}
            except Exception as e:
                last_err = str(e)
                last_report = None
        else:
            fallback_ranges = [(start_date, end_date), ("28daysAgo", "today"), ("90daysAgo", "today")]
            for sd, ed in fallback_ranges:
                try:
                    rep = _run(sd, ed)
                    last_report = rep
                    if rep.get("rows"):
                        break
                except Exception as e:
                    last_err = str(e)

        if last_report is None:
            return {"query": query, "error": "GA4 query failed", "details": last_err or "Unknown error"}

        # Build summary
        if wants_compare and isinstance(last_report, dict) and "current" in last_report:
            cur_rows = last_report["current"].get("rows", [])
            prev_rows = last_report["previous"].get("rows", [])
            summary_prompt = f"""
You are a careful GA4 analyst. Only use the provided rows.

User query: {query}
GA4 propertyId: {property_id}

CURRENT period report: {last_report['current']}
PREVIOUS period report: {last_report['previous']}

Tasks:
- If asked for average daily page views: compute average per day for current and previous periods.
- Compare and state direction (increasing/decreasing) and approximate percent change.
- If data is empty, say so and suggest next steps.

Return a concise answer.
""".strip()
            summary = self.llm.ask(summary_prompt)
            if return_structured:
                return {"report": last_report, "summary": summary}
            return {"query": query, "report": last_report, "summary": summary}

        rows_preview = last_report.get("rows", [])[:20]
        summary_prompt = f"""
You are a careful GA4 analyst. Only use the provided rows.
If rows are empty, say so and suggest how to broaden the query.

User query: {query}
GA4 propertyId: {property_id}
Dimensions: {last_report.get('dimensionHeaders')}
Metrics: {last_report.get('metricHeaders')}
Filters applied: {dimension_filters}
Rows (up to 20): {rows_preview}
Notes: {last_report.get('notes', {})}

Write a concise answer with:
- direct answer
- 2-4 bullet highlights
- if empty: what was attempted (date range) and next steps
""".strip()
        summary = self.llm.ask(summary_prompt)

        if return_structured:
            return {"report": last_report, "summary": summary}
        return {"query": query, "report": last_report, "summary": summary}


class SEOAgent:
    def __init__(self, llm_client, credential_path="credentials.json"):
        self.llm = llm_client
        self.credential_path = credential_path
        self.sheet_url = os.environ.get(
            "SEO_SHEET_URL",
            (
                "https://docs.google.com/spreadsheets/d/"
                "1zzf4ax_H2WiTBVrJigGjF2Q3Yz-qy2qMCbAMKvl6VEE/edit"
            ),
        )

        # Optional selectors
        self.sheet_gid = os.environ.get("SEO_SHEET_GID")
        self.sheet_title = os.environ.get("SEO_SHEET_WORKSHEET_TITLE")
        self.use_all_tabs = os.environ.get("SEO_SHEET_USE_ALL_TABS", "").strip().lower() in {"1", "true", "yes"}
        
        # Output limits
        self.max_rows = 20
        self.priority_columns = [
            "Address", "Title 1", "Title 1 Length", "Meta Description 1", 
            "Status Code", "Status", "Indexability", "Crawl Depth",
            "Word Count", "H1-1", "Inlinks", "Outlinks", "__sheet"
        ]

    def _wants_all_tabs(self) -> bool:
        t = (self.sheet_title or "").strip().lower()
        return self.use_all_tabs or t in {"*", "all", "all_tabs", "all tabs"}

    def _json_sanitize(self, obj):
        """Recursively replace NaN/Infinity floats with None so FastAPI can JSON-encode."""
        import math

        if obj is None:
            return None
        if isinstance(obj, float):
            return None if (math.isnan(obj) or math.isinf(obj)) else obj
        if isinstance(obj, dict):
            return {k: self._json_sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._json_sanitize(v) for v in obj]
        return obj

    def _slim_records(self, records: list[dict], limit: int | None = None, dedupe_by: str = "Address") -> list[dict]:
        """Return records with only priority columns, stripped of null values, deduplicated, limited in count."""
        limit = limit or self.max_rows
        seen = set()
        slim = []
        for rec in records:
            # Deduplicate by key field (e.g., Address)
            key = rec.get(dedupe_by)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            
            # Keep only non-null, non-empty values from priority columns
            filtered = {}
            for col in self.priority_columns:
                val = rec.get(col)
                # Skip None, empty strings, and NaN floats
                if val is None or val == "" or val == "null":
                    continue
                if isinstance(val, float):
                    import math
                    if math.isnan(val) or math.isinf(val):
                        continue
                filtered[col] = val
            # If no priority columns found, keep first few non-null fields
            if not filtered:
                for k, v in rec.items():
                    if v is not None and v != "" and len(filtered) < 5:
                        filtered[k] = v
            if filtered:
                slim.append(filtered)
            
            if len(slim) >= limit:
                break
        return slim

    async def handle_query(self, query: str, return_structured: bool = False):
        q = query.lower()

        # Load data (catch permissions / connectivity issues)
        try:
            if self._wants_all_tabs():
                tabs = open_all_worksheets_by_url(self.credential_path, self.sheet_url)
                if not tabs:
                    return {"query": query, "error": "No readable worksheets found in SEO sheet"}

                all_frames = []
                for title, tdf in tabs.items():
                    if tdf is None or tdf.empty:
                        continue
                    tdf = tdf.copy()
                    tdf["__sheet"] = title
                    all_frames.append(tdf)

                if not all_frames:
                    return {"query": query, "error": "All worksheets are empty"}

                import pandas as _pd

                df = _pd.concat(all_frames, ignore_index=True, sort=False)
            else:
                gid = int(self.sheet_gid) if self.sheet_gid and self.sheet_gid.isdigit() else None
                df = open_sheet_by_url(
                    self.credential_path,
                    self.sheet_url,
                    gid=gid,
                    worksheet_title=self.sheet_title,
                )
        except PermissionError:
            return {
                "query": query,
                "error": "Permission denied reading the SEO Google Sheet",
                "next_steps": [
                    "Share the Google Sheet with the service account email from credentials.json (Viewer is enough).",
                    "Or set SEO_SHEET_URL to a sheet the service account can access.",
                ],
            }
        except Exception as e:
            return {
                "query": query,
                "error": "Failed to read the SEO Google Sheet",
                "details": str(e),
            }

        # Make dataframe JSON-safe (replace NaN/Inf with None)
        try:
            import pandas as _pd

            df = df.replace([_pd.NA], None)
            df = df.where(df.notna(), None)
        except Exception:
            pass

        results = {}

        # Detect combined queries ("and" / "&" between conditions)
        wants_https = "https" in q and "Address" in df.columns
        wants_title = "title" in q and "Title 1" in df.columns
        wants_intersection = wants_https and wants_title and ("and" in q or "&" in q)

        if wants_intersection:
            # Intersection: URLs that are non-HTTPS AND have title > 60
            non_https_mask = ~df["Address"].str.startswith("https://", na=False)
            long_title_mask = df["Title 1"].astype(str).str.len() > 60
            combined = df[non_https_mask & long_title_mask]
            raw = combined.to_dict(orient="records")
            results["non_https_and_long_title"] = self._slim_records(raw)
            results["total_count"] = len(raw)
        else:
            if wants_https:
                not_https = df[~df["Address"].str.startswith("https://", na=False)]
                raw = not_https.to_dict(orient="records")
                results["non_https_urls"] = self._slim_records(raw)
                results["non_https_count"] = len(raw)

            if wants_title:
                long_titles = df[df["Title 1"].astype(str).str.len() > 60]
                raw = long_titles.to_dict(orient="records")
                results["long_title_tags"] = self._slim_records(raw)
                results["long_title_count"] = len(raw)

        if "indexable" in q and "Indexability" in df.columns:
            results["indexability_summary"] = df.groupby("Indexability").size().to_dict()

        if not results:
            prompt = f"""
Given the following SEO crawl columns: {list(df.columns)},
summarize major SEO issues and priorities.

If a column named __sheet exists, it indicates which sheet tab each row came from.
""".strip()
            summary = self.llm.ask(prompt)
            return {"summary": summary}

        return self._json_sanitize({"query": query, "results": results})
