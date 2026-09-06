"""Optional web search client. Disabled unless explicitly configured."""

from __future__ import annotations

import httpx


class DisabledWebSearchClient:
    """No-effect search adapter used while the explicit search gate is closed."""

    enabled = False

    async def search(self, query: str) -> str:
        del query
        return ""


class WebSearchClient:
    def __init__(
        self,
        *,
        enabled: bool,
        api_base: str,
        api_key: str,
        timeout_seconds: float = 20,
    ) -> None:
        self.enabled = enabled and bool(api_base) and bool(api_key)
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds

    async def search(self, query: str) -> str:
        if not self.enabled:
            return ""
        query = query.strip()
        if not query:
            return ""
        url = f"{self._api_base}/search"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                json={"query": query, "max_results": 3},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
        if isinstance(payload, dict):
            answer = payload.get("answer")
            if isinstance(answer, str) and answer.strip():
                return answer.strip()[:2000]
            results = payload.get("results")
            if isinstance(results, list):
                snippets = []
                for item in results[:3]:
                    if not isinstance(item, dict):
                        continue
                    text = str(
                        item.get("content") or item.get("snippet") or item.get("title") or ""
                    )
                    if text:
                        snippets.append(text[:400])
                return "\n".join(snippets)
        return ""
