"""Web research: search (several free sources, no API key) and readable page text.

read_webpage refuses private, loopback and link-local addresses, so a prompt-injected page
can't make the agent poke at your router or local services (SSRF protection).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .registry import ToolError, tool

MAX_PAGE_CHARS = 7000
SEARCH_UA = "Mozilla/5.0"  # DuckDuckGo's HTML endpoint serves a bot check to full browser UAs
USER_AGENT = "Mozilla/5.0 (Macintosh) AETHER-local-agent/0.1"
SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside",
             "form", "iframe", "button"}
BLOCK_TAGS = {"p", "div", "section", "article", "li", "br", "tr", "h1", "h2", "h3", "h4",
              "h5", "h6", "pre", "blockquote", "table"}


class _DDGResults(HTMLParser):
    """Parses DuckDuckGo's no-JavaScript results page (html.duckduckgo.com)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            href = a.get("href") or ""
            target = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            self.results.append({"title": "", "href": unquote(target), "body": ""})
            self._field = "title"
        elif "result__snippet" in classes and self.results:
            self._field = "body"

    def handle_endtag(self, tag: str) -> None:
        if tag in {"a", "td", "div"}:
            self._field = None

    def handle_data(self, data: str) -> None:
        if self._field and self.results:
            self.results[-1][self._field] += data


async def _search_ddg_html(query: str, n: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": SEARCH_UA}) as client:
        r = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        r.raise_for_status()
    if r.status_code != 200:  # 202 = a bot-check page, not results
        return []
    parser = _DDGResults()
    parser.feed(r.text)
    return [x for x in parser.results if x["href"].startswith("http")
            and "duckduckgo.com/y.js" not in x["href"]][:n]


async def _search_ddgs(query: str, n: int, backend: str) -> list[dict[str, str]]:
    from ddgs import DDGS
    return await asyncio.to_thread(
        lambda: list(DDGS(timeout=10).text(query, max_results=n, backend=backend)))


@tool(description=(
    "Search the web. Returns titles, URLs and snippets. Use for current events, "
    "facts you aren't sure about, and research. Then use read_webpage on the 1–3 best results, "
    "and cite URLs in your answer."),
    params={"query": "Search query, written like you'd type it into a search engine",
            "max_results": "How many results to return, 1–10"},
    category="research", icon="globe", timeout_s=45)
async def web_search(query: str, max_results: int = 5) -> str:
    n = max(1, min(max_results, 10))
    # Free search endpoints are flaky, so try several independent sources in turn.
    sources = [("ddgs", lambda: _search_ddgs(query, n, "auto")),
               ("duckduckgo-html", lambda: _search_ddg_html(query, n)),
               ("bing", lambda: _search_ddgs(query, n, "bing")),
               ("ddgs-retry", lambda: _search_ddgs(query, n, "auto"))]
    results: list[dict[str, str]] = []
    failures: list[str] = []
    for name, fetch in sources:
        try:
            results = await fetch()
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}")
            continue
        if results:
            break
        failures.append(f"{name}: no results")
    if not results:
        raise ToolError(
            "Web search is unavailable right now (" + "; ".join(failures) + "). "
            "Do NOT guess current facts from memory. Tell the user the search failed and "
            "they can try again in a minute.")
    lines = [f"Results for '{query}':"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {' '.join(r.get('title', '').split())}\n   {r.get('href', '')}\n"
                     f"   {' '.join(r.get('body', '').split())}")
    return "\n".join(lines)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line)


def _assert_public(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolError("Only http(s) URLs can be read.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ToolError(f"Couldn't resolve {parsed.hostname}. Are you online?") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolError(f"{parsed.hostname} points to a private/local address ({ip}); "
                            "reading it is blocked for safety.")


@tool(description="Download a web page and return its readable text (title, URL, content).",
      params={"url": "Full URL starting with http:// or https://"},
      category="research", icon="file-text", timeout_s=30)
async def read_webpage(url: str) -> str:
    await asyncio.to_thread(_assert_public, url)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20,
                                     headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url)
            r.raise_for_status()
            # Redirects could land on a private address; check the final URL too.
            if str(r.url) != url:
                await asyncio.to_thread(_assert_public, str(r.url))
    except httpx.HTTPStatusError as e:
        raise ToolError(f"{url} returned HTTP {e.response.status_code}.") from e
    except httpx.HTTPError as e:
        raise ToolError(f"Couldn't fetch {url}: {type(e).__name__}.") from e
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        raise ToolError(f"{url} isn't a web page (content type: {ctype or 'unknown'}).")
    parser = _TextExtractor()
    parser.feed(r.text)
    text = parser.text()
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + "\n…[page truncated]"
    title = " ".join(parser.title.split()) or url
    return f"{title}\n{r.url}\n\n{text or '(no readable text found)'}"
