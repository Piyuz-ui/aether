"""PowerPoint (.pptx) in the knowledge vault: slides, bullets, tables, groups, speaker notes."""
from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from aether.memory.knowledge import KnowledgeError, read_slides
from aether.tools import REGISTRY


def make_deck(path: Path) -> Path:
    deck = Presentation()
    s1 = deck.slides.add_slide(deck.slide_layouts[0])
    s1.shapes.title.text = "Operating Systems: Unit 3"
    s1.placeholders[1].text = "Memory management"

    s2 = deck.slides.add_slide(deck.slide_layouts[1])
    s2.shapes.title.text = "Paging"
    body = s2.placeholders[1].text_frame
    body.text = "Memory is split into fixed-size frames"
    p = body.add_paragraph()
    p.text = "Pages map to frames through a page table"
    p.level = 1
    s2.notes_slide.notes_text_frame.text = "Exam tip: paging removes external fragmentation."

    s3 = deck.slides.add_slide(deck.slide_layouts[5])
    s3.shapes.title.text = "Scheduling algorithms"
    table = s3.shapes.add_table(3, 2, Inches(1), Inches(2), Inches(6), Inches(1.5)).table
    for r, (a, b) in enumerate([("Algorithm", "Preemptive"), ("Round Robin", "Yes"), ("FCFS", "No")]):
        table.cell(r, 0).text, table.cell(r, 1).text = a, b
    group = s3.shapes.add_group_shape()
    box = group.shapes.add_textbox(Inches(1), Inches(4), Inches(4), Inches(1))
    box.text_frame.text = "Round robin uses a time quantum"
    deck.save(str(path))
    return path


def test_read_slides_extracts_everything(tmp_path):
    slides = read_slides(make_deck(tmp_path / "os.pptx"))
    assert len(slides) == 3
    assert slides[0].startswith("Slide 1: Operating Systems: Unit 3") and "Memory management" in slides[0]
    assert "- Pages map to frames" in slides[1]  # nested bullet kept as a sub-point
    assert "Speaker notes: Exam tip: paging removes external fragmentation." in slides[1]
    assert "Round Robin | Yes" in slides[2]  # table row
    assert "time quantum" in slides[2]  # text inside a grouped shape


async def test_pptx_ingest_search_and_slide_citations(mem, tmp_path):
    doc = await mem.knowledge.ingest(make_deck(tmp_path / "os.pptx"))
    assert doc["status"] == "ready" and doc["pages"] == 3
    hits = await mem.knowledge.search("what does the exam tip say about paging fragmentation?")
    assert hits[0].page == 2 and hits[0].cite() == "os.pptx slide 2"
    res = await REGISTRY.execute(REGISTRY.get("search_knowledge_base"),
                                 {"query": "is round robin preemptive"}, timeout_s=10, max_output_chars=4000)
    assert "[os.pptx slide 3]" in res.output and "Round Robin | Yes" in res.output


def test_old_ppt_and_broken_files_get_clear_errors(mem, tmp_path):
    old = tmp_path / "lecture.ppt"
    old.write_bytes(b"\xd0\xcf\x11\xe0 legacy")
    with pytest.raises(KnowledgeError, match="Save it as .pptx"):
        mem.knowledge.register(old)
    broken = tmp_path / "broken.pptx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(KnowledgeError, match="Couldn't read the PowerPoint"):
        read_slides(broken)
