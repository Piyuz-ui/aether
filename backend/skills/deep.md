---
description: Deep research: several web searches, the best sources read in full, your documents, and a cited report saved as a note
usage: /deep <question>
tools: web_search, read_webpage, search_knowledge_base, write_note
reasoning: high
---
Research this thoroughly: {{input}}

1. Run 2 or 3 web_search calls that look at the question from different angles (for example: an overview, recent developments, criticisms or comparisons).
2. Pick the 2 most useful results and read them with read_webpage.
3. Call search_knowledge_base in case my own documents cover it, and use them if they do.
4. Write a report in Markdown: a 2-sentence summary first, then sections with the key findings, and a "Sources" list with a link for every web claim (and file + page for my documents). Say plainly where sources disagree or where evidence is thin.
5. Save it with write_note (title: "Research: <short topic>").

Finish with the summary and one line saying where the note was saved.
