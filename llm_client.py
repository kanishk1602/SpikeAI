# llm_client.py
import os
import time
import requests


class LiteLLMClient:
    def __init__(
        self,
        api_key: str | None,
        base_url: str = "http://3.110.18.218",
        model: str = "gemini-2.5-flash",
        request_timeout_s: float = 10.0,
        max_retries: int = 2,
        enabled: bool | None = None,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.request_timeout_s = request_timeout_s
        self.max_retries = max_retries

        # Enable LLM only if it is configured, unless explicitly forced.
        if enabled is None:
            self.enabled = bool(self.base_url) and (self.api_key != "")
        else:
            self.enabled = bool(enabled)

    def _post(self, payload):
        if not self.enabled:
            raise RuntimeError("LLM is disabled (missing LITELLM_API_KEY / LITELLM_BASE_URL)")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/chat/completions"
        last_err: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=self.request_timeout_s)
                if resp.status_code == 429:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                last_err = e
                time.sleep(min(1 + 2**attempt, 3))

        raise RuntimeError(f"LLM request failed after {self.max_retries} retries: {last_err}")

    def ask(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }

        try:
            out = self._post(payload)
            # adapter to many proxy responses
            return out.get("choices", [{}])[0].get("message", {}).get("content") or str(out)
        except Exception as e:
            # Hard fallback: return a deterministic string instead of hanging/failing the request.
            return f"[llm_unavailable] {type(e).__name__}: {e}"

    def summarize_fusion(self, ga_struct, seo_struct, user_query):
        # If the LLM is not configured, return a basic deterministic fused result.
        if not self.enabled:
            return {
                "summary": "LLM unavailable; returning a deterministic fusion.",
                "highlights": {
                    "ga4": ga_struct,
                    "seo": seo_struct,
                },
                "recommendations": [
                    "Set LITELLM_API_KEY and LITELLM_BASE_URL to enable LLM-based fusion summaries."
                ],
            }

        prompt = f"""You are a concise analytics+SEO assistant.
User query: {user_query}

GA4 result (structured): {ga_struct}
SEO result (structured): {seo_struct}

Produce a short fused JSON summary (top-level keys: summary, highlights, recommendations)."""
        return self.ask(prompt)
