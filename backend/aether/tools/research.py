"""Research tools for study: YouTube transcripts, arXiv papers and Wikipedia.

They need the internet; without it they say so plainly ("you're offline") instead of failing
with a stack trace. Requests carry a generic app name, nothing about you.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

from .registry import ToolError, tool

UA = "AETHER/0.1 (local study assistant)"
OFFLINE = "You're offline (or the site can't be reached), so this can't be looked up right now."
TRANSCRIPT_LIMIT = 24_000
_transport: Any = None  # tests inject a mock HTTP transport


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}, transport=_transport, follow_redirects=True)


# --------------------------------------------------------------- YouTube
def video_id(url_or_id: str) -> str | None:
    s = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else None


def _stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _fetch_transcript(vid: str, languages: list[str]) -> tuple[str, list[tuple[float, str]]]:
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(vid, languages=languages)
    except Exception:
        # Not in the wanted language: take whatever exists (manual first, then auto-generated).
        listing = api.list(vid)
        first = next(iter(listing))
        fetched = first.fetch()
    lang = getattr(fetched, "language", "") or getattr(fetched, "language_code", "")
    return lang, [(sn.start, sn.text) for sn in fetched]


@tool(description=(
    "Get the transcript of a YouTube video (with timestamps) to summarise it, take notes or answer "
    "questions about it. Works for lectures, talks and tutorials that have captions."),
    params={"url": "The video's URL (youtube.com/watch?v=…, youtu.be/…) or its 11-character id",
            "language": "Preferred caption language code, e.g. en, hi (default en)"},
    category="research", icon="youtube", timeout_s=45)
async def youtube_transcript(url: str, language: str = "en") -> str:
    vid = video_id(url)
    if not vid:
        raise ToolError("That doesn't look like a YouTube link. Give a youtube.com/watch?v=… or youtu.be/… URL.")
    try:
        lang, lines = await asyncio.to_thread(_fetch_transcript, vid, [language, "en"])
    except Exception as e:  # noqa: BLE001 - the library raises many kinds
        name = type(e).__name__
        if any(k in name for k in ("ConnectionError", "ConnectError", "Timeout")) or "Max retries" in str(e):
            raise ToolError(OFFLINE) from e
        if "Disabled" in name or "NoTranscript" in name or "StopIteration" in name:
            raise ToolError("This video has no captions, so there's no transcript to read.") from e
        if "Unavailable" in name:
            raise ToolError("That video is unavailable (private, removed or region-locked).") from e
        if "Blocked" in name or "IpBlocked" in name:
            raise ToolError("YouTube is refusing transcript requests from this network right now. Try again later.") from e
        raise ToolError(f"Couldn't get the transcript: {name}") from e
    if not lines:
        raise ToolError("The transcript is empty.")
    text = "\n".join(f"[{_stamp(t)}] {line.strip()}" for t, line in lines if line.strip())
    cut = ""
    if len(text) > TRANSCRIPT_LIMIT:
        text, cut = text[:TRANSCRIPT_LIMIT], f"\n… (transcript cut at {TRANSCRIPT_LIMIT:,} characters)"
    return (f"Transcript of https://youtu.be/{vid} ({lang or 'captions'}, {_stamp(lines[-1][0])} long):\n"
            f"{text}{cut}")


# --------------------------------------------------------- arXiv + Wikipedia
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _arxiv_queries(query: str) -> list[str]:
    """Most to least precise: the exact phrase in a title, then all the words, then any of them."""
    q = re.sub(r'["\\]', " ", query).strip()
    words = [w for w in re.findall(r"[\w-]+", q) if len(w) > 1]
    out = [f'ti:"{q}"'] if len(words) > 1 else []
    if words:
        out.append(" AND ".join(f"all:{w}" for w in words))
        out.append(f"all:{q}")
    return out


async def _arxiv(client: httpx.AsyncClient, query: str, n: int) -> list[str]:
    entries: list[ET.Element] = []
    seen: set[str] = set()
    for search in _arxiv_queries(query):
        r = await client.get("https://export.arxiv.org/api/query",
                             params={"search_query": search, "start": 0, "max_results": n, "sortBy": "relevance"})
        r.raise_for_status()
        for e in ET.fromstring(r.text).findall("a:entry", ATOM):
            key = e.findtext("a:id", "", ATOM) or ""
            if key not in seen:
                seen.add(key)
                entries.append(e)
        if len(entries) >= n:
            break
    out = []
    for e in entries[:n]:
        title = " ".join((e.findtext("a:title", "", ATOM) or "").split())
        summary = " ".join((e.findtext("a:summary", "", ATOM) or "").split())
        authors = [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)]
        published = (e.findtext("a:published", "", ATOM) or "")[:10]
        link = e.findtext("a:id", "", ATOM)
        who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        out.append(f"- **{title}** ({who}, {published})\n  {link}\n  {summary[:600]}{'…' if len(summary) > 600 else ''}")
    return out


async def _wikipedia(client: httpx.AsyncClient, query: str, n: int, lang: str) -> list[str]:
    r = await client.get(f"https://{lang}.wikipedia.org/w/api.php",
                         params={"action": "query", "list": "search", "srsearch": query, "srlimit": n,
                                 "format": "json"})
    r.raise_for_status()
    titles = [h["title"] for h in r.json().get("query", {}).get("search", [])]
    out = []
    for title in titles:
        s = await client.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}")
        if s.status_code != 200:
            continue
        d = s.json()
        url = (d.get("content_urls") or {}).get("desktop", {}).get("page", "")
        out.append(f"- **{d.get('title', title)}**: {d.get('extract', '').strip()}\n  {url}")
    return out


@tool(description=(
    "Look up academic papers on arXiv and/or encyclopedia articles on Wikipedia, with links. "
    "Good for studying a concept, finding research papers, or getting a reliable definition."),
    params={"query": "What to look up, e.g. 'attention is all you need' or 'database normalization'",
            "source": "arxiv, wikipedia, or both (default)",
            "max_results": "How many results per source (1–8, default 4)",
            "language": "Wikipedia language code (default en)"},
    category="research", icon="graduation-cap", timeout_s=40)
async def arxiv_wiki_lookup(query: str, source: str = "both", max_results: int = 4, language: str = "en") -> str:
    source = source.strip().lower()
    if source not in ("both", "arxiv", "wikipedia", "wiki"):
        raise ToolError("source must be arxiv, wikipedia or both.")
    n = max(1, min(int(max_results), 8))
    lang = language.strip().lower() if re.fullmatch(r"[a-z]{2,3}", language.strip().lower()) else "en"
    parts: list[str] = []
    try:
        async with _client() as client:
            if source in ("both", "arxiv"):
                papers = await _arxiv(client, query, n)
                parts.append("## arXiv papers\n" + ("\n".join(papers) if papers else "No papers found."))
            if source in ("both", "wikipedia", "wiki"):
                articles = await _wikipedia(client, query, n if source != "both" else min(n, 3), lang)
                parts.append("## Wikipedia\n" + ("\n".join(articles) if articles else "No articles found."))
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        raise ToolError(OFFLINE) from e
    except httpx.HTTPStatusError as e:
        raise ToolError(f"The service answered HTTP {e.response.status_code}. Try again in a moment.") from e
    except ET.ParseError as e:
        raise ToolError("arXiv sent back something unreadable. Try again in a moment.") from e
    return "\n\n".join(parts)
