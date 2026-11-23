"""Research tools: web search and page reading. These are the only tools that need internet."""
from __future__ import annotations

import requests

from .base import tool

MAX_CHARS = 8000
HEADERS = {"User-Agent": "Mozilla/5.0 (local-ai-agent)"}


@tool("Search the web. Use for current events, facts you're unsure of, or research. "
      "Then use fetch_webpage on the best results and cite the URLs.",
      params={"query": "Search query", "max_results": "How many results (1-10)"},
      category="research")
def web_search(query: str, max_results: int = 5) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # older package name
        except ImportError:
            return "Web search isn't installed. Run: pip install ddgs"
    try:
        results = list(DDGS().text(query, max_results=max(1, min(max_results, 10))))
    except Exception as e:  # offline, rate-limited, etc.
        return f"Web search failed ({e}). You may be offline."
    if not results:
        return "No results."
    return "\n\n".join(f"{i}. {r.get('title')}\n{r.get('href')}\n{r.get('body')}"
                       for i, r in enumerate(results, 1))


@tool("Download a web page and return its readable text.",
      params={"url": "Full URL starting with http"}, category="research")
def fetch_webpage(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "Error: URL must start with http:// or https://"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        return f"Couldn't fetch {url}: {e}"
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n[truncated]"
    return f"{title}\n{url}\n\n{text}"
