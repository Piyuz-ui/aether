---
description: Quiz yourself with interactive cards made from your uploaded documents
usage: /quiz <topic>   (e.g. /quiz normalisation)
tools: search_knowledge_base, list_documents, make_quiz
---
Quiz me on: {{input}}

1. If I named a topic, call search_knowledge_base for it. If I didn't, call list_documents and use the most recently uploaded document, then search it for its main ideas.
2. Write 5 multiple-choice questions only from what the documents actually say: 4 options each, exactly one correct, a one-line explanation that cites the file and page or slide.
3. Call make_quiz with a short title and the questions, so they appear as interactive cards. Do not write the questions as text.

After the tool call, reply with one short line inviting me to start. My answers are recorded, and topics I keep missing show up as weak spots.
