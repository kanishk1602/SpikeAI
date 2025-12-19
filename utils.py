# utils.py
import re

def detect_intent(query: str) -> str:
    q = query.lower()
    has_analytics_words = any(
        w in q for w in [
            "page view", "pageviews", "sessions", "users",
            "ga4", "property", "traffic", "top by", "top pages",
            "views", "top 10 pages", "top pages by"
        ]
    )
    has_seo_words = any(
        w in q for w in [
            "https", "title tag", "meta", "screaming frog",
            "indexable", "meta description", "duplicate",
            "seo"
        ]
    )
    # "title tags" or "corresponding title" suggests cross-referencing SEO data
    has_seo_cross_ref = any(w in q for w in ["title tag", "title tags", "corresponding title"])

    if has_analytics_words and not has_seo_words and not has_seo_cross_ref:
        return "analytics_only"
    if has_seo_words and not has_analytics_words:
        return "seo_only"
    if (has_analytics_words and has_seo_words) or (has_analytics_words and has_seo_cross_ref) or any(w in q for w in ["correlate", "corresponding", "fusion"]):
        return "multi"
    if re.search(r"\blast\b|\blast \d+ days|\bprevious\b|\b30 days\b|\b14 days\b", q):
        return "analytics_only"
    return "unknown"
