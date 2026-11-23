"""Document export: turn Markdown (a saved note, or content the agent writes) into a clean,
nicely formatted PDF in workspace/exports/.

Pure Python (fpdf2 + markdown), so it works offline on any OS. It uses a Unicode TTF font when
one is installed (Arial / DejaVu), with Arial Unicode as a fallback for symbols, and degrades to
the built-in Helvetica with character substitution otherwise.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from .context import workspace
from .notes import slugify
from .registry import Risk, ToolError, tool

EXPORT_DIR = "exports"

_FONT_DIRS = [Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"),
              Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/TTF"),
              Path("C:/Windows/Fonts")]
_FONTS = {
    "": ["Arial.ttf", "DejaVuSans.ttf", "arial.ttf"],
    "B": ["Arial Bold.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"],
    "I": ["Arial Italic.ttf", "DejaVuSans-Oblique.ttf", "ariali.ttf"],
    "BI": ["Arial Bold Italic.ttf", "DejaVuSans-BoldOblique.ttf", "arialbi.ttf"],
    "mono": ["Courier New.ttf", "DejaVuSansMono.ttf", "cour.ttf"],
    "unicode": ["Arial Unicode.ttf", "DejaVuSans.ttf", "seguisym.ttf"],
}
_ASCII = {"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "...", "•": "-",
          "✓": "v", "✔": "v", "✗": "x", "→": "->", "←": "<-", "≥": ">=", "≤": "<=", "×": "x",
          "\u00a0": " "}

INK = (14, 36, 66)          # abyss blue for text
ACCENT = (56, 132, 198)     # calm sky blue for headings and links
MUTED = (120, 140, 165)


def _find(names: list[str]) -> Path | None:
    for d in _FONT_DIRS:
        for n in names:
            if (d / n).is_file():
                return d / n
    return None


def _latin1(text: str) -> str:
    """For the built-in fonts only: map common symbols to ASCII, drop the rest."""
    for k, v in _ASCII.items():
        text = text.replace(k, v)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("latin-1", "ignore").decode("latin-1")


def markdown_to_pdf(markdown_text: str, title: str, out_path: Path) -> int:
    """Render Markdown to a PDF file. Returns the number of pages."""
    import markdown as md
    from fpdf import FPDF
    from fpdf.fonts import TextStyle

    class Doc(FPDF):
        def footer(self) -> None:
            self.set_y(-14)
            self.set_font(family, "", 8)
            self.set_text_color(*MUTED)
            self.cell(0, 8, f"{title_text}  ·  page {self.page_no()}/{{nb}}" if unicode_ok
                      else f"{title_text}  -  page {self.page_no()}/{{nb}}", align="C")

    pdf = Doc(format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=20)
    pdf.set_title(title)
    pdf.set_creator("AETHER")

    regular = _find(_FONTS[""])
    unicode_ok = regular is not None
    if unicode_ok:
        family = "Body"
        for style in ("", "B", "I", "BI"):
            path = _find(_FONTS[style]) or regular
            pdf.add_font(family, style, str(path))
        mono_path = _find(_FONTS["mono"])
        mono = "Mono" if mono_path else family
        if mono_path:
            pdf.add_font("Mono", "", str(mono_path))
        symbols = _find(_FONTS["unicode"])
        if symbols and symbols != regular:
            pdf.add_font("Symbols", "", str(symbols))
            pdf.set_fallback_fonts(["Symbols"])
    else:
        family, mono = "helvetica", "courier"
        markdown_text, title = _latin1(markdown_text), _latin1(title)
    title_text = title if len(title) <= 60 else title[:57] + "..."

    body = markdown_text.strip()
    if not re.match(r"^#\s", body):  # give the document a title if it has none
        body = f"# {title}\n\n{body}"
    html = md.markdown(body, extensions=["tables", "fenced_code", "sane_lists"])
    # fpdf2 renders <pre> best without the nested <code>.
    html = re.sub(r"<pre><code[^>]*>", "<pre>", html).replace("</code></pre>", "</pre>")

    pdf.add_page()
    pdf.set_text_color(*INK)
    pdf.write_html(
        html,
        font_family=family,
        li_prefix_color=ACCENT,
        table_line_separators=True,
        warn_on_tags_not_matching=False,
        tag_styles={
            "h1": TextStyle(font_family=family, font_style="B", font_size_pt=20, color=INK,
                            t_margin=2, b_margin=4),
            "h2": TextStyle(font_family=family, font_style="B", font_size_pt=15, color=ACCENT,
                            t_margin=6, b_margin=2),
            "h3": TextStyle(font_family=family, font_style="B", font_size_pt=12.5, color=INK,
                            t_margin=4, b_margin=1),
            "a": TextStyle(color=ACCENT, font_style="U"),
            "code": TextStyle(font_family=mono, color=(30, 64, 120)),
            "pre": TextStyle(font_family=mono, font_size_pt=9.5, color=(30, 50, 80)),
        },
    )
    pdf.ln(4)
    pdf.set_font(family, "I", 8)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 6, f"Created with AETHER on {datetime.now():%d %b %Y, %H:%M}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    return pdf.page_no()


def _source_path(source: str) -> Path:
    """Accept a workspace path, or just a note's title ("DBMS notes" → notes/dbms-notes.md)."""
    ws = workspace()
    candidates = [source]
    if not source.endswith((".md", ".txt", ".markdown")):
        candidates += [f"notes/{slugify(source)}.md", f"notes/{source}.md"]
    elif "/" not in source:
        candidates.append(f"notes/{source}")
    for c in candidates:
        try:
            p = ws.resolve(c)
        except Exception:
            continue
        if p.is_file():
            return p
    notes = sorted(p.name for p in (ws.root / "notes").glob("*.md"))[:30] if (ws.root / "notes").is_dir() else []
    raise ToolError(f"Couldn't find '{source}'. Notes available: {', '.join(notes) or 'none'}.")


def _output_path(title: str) -> Path:
    return workspace().resolve(f"{EXPORT_DIR}/{slugify(title)}.pdf")


def _pdf_risk(args: dict[str, Any]) -> Risk:
    try:
        return Risk.REQUIRES_APPROVAL if _output_path(str(args.get("title", ""))).exists() else Risk.SAFE
    except Exception:
        return Risk.SAFE


def _pdf_summary(args: dict[str, Any]) -> str:
    path = _output_path(str(args.get("title", "")))
    return f"Replace the existing PDF '{workspace().relative(path)}' with a new version."


def _pdf_precheck(args: dict[str, Any]) -> None:
    source, content = str(args.get("source", "")).strip(), str(args.get("content", "")).strip()
    if not source and not content:
        raise ToolError("Give either `source` (a note to convert, e.g. 'notes/dbms.md') or "
                        "`content` (the Markdown to put in the PDF).")
    if source and not content:
        _source_path(source)


@tool(description=(
    "Create a nicely formatted PDF document. Use this whenever the user wants a PDF. Never "
    "use run_python for it. Either convert a saved note (`source`, e.g. 'notes/dbms.md' or "
    "just the note's title), or write the Markdown yourself in `content`. Saved in exports/, "
    "and the user gets a download button."),
    params={"title": "Document title. It also becomes the file name",
            "source": "A note to convert, e.g. 'notes/rag-api-definitions.md'. Leave empty when "
                      "you provide `content`",
            "content": "Markdown to turn into the PDF. Leave empty when converting `source`"},
    category="documents", icon="file-down",
    risk_check=_pdf_risk, risk_summary=_pdf_summary, precheck=_pdf_precheck, timeout_s=60)
def create_pdf(title: str, source: str = "", content: str = "") -> str:
    text = content.strip()
    if not text:
        text = _source_path(source.strip()).read_text(encoding="utf-8", errors="replace")
    out = _output_path(title)
    pages = markdown_to_pdf(text, title, out)
    size_kb = max(1, round(out.stat().st_size / 1024))
    return (f"Created PDF {workspace().relative(out)} ({pages} page{'s' * (pages != 1)}, {size_kb} KB). "
            "The user can open or download it from the chat.")
