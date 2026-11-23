# AETHER: 90-second demo video

**Goal:** in 90 seconds, prove AETHER is a real *agent*. It plans, uses tools, asks permission, remembers, sees, and speaks, all locally. Each scene shows one capability a chatbot doesn't have.

**Format:** 1440×900 screen recording, voice-over, burned-in captions. End on the symbiote.

---

## Before you record (10 minutes of prep)

- [ ] `./start.sh` running; **Ollama warm**: send one throwaway message first, so the first reply in the video isn't a cold start.
- [ ] Brain: `qwen2.5:7b` (fast). Check that `qwen3.5:4b` is installed for the vision scene.
- [ ] **Settings → AETHER's voice**: George, speed 1.05×, **Auto-speak on**. Tap George once to warm up the voice model.
- [ ] Calm mode **off** (the motion is the point). Side panel **open** on **Tasks**.
- [ ] Knowledge Vault has one real lecture PDF or `.pptx` (the Normalization notes work well).
- [ ] Have ready: a timetable screenshot (`timetable.png`) and a note to delete.
- [ ] Start a **New chat** so the empty state with the big symbiote shows.
- [ ] Hide the browser chrome (full screen ⌃⌘F) and turn on Do Not Disturb.
- [ ] Record with **⇧⌘5**, "Record selected portion", with internal audio (e.g. BlackHole) or record the voice separately.

---

## The script

| Time | On screen | Voice-over / caption |
|---|---|---|
| **0:00–0:06** | Empty state. The symbiote breathes; move the cursor slowly near it: it reaches a pseudopod toward the cursor while the light drifts (parallax). | *"This is AETHER: an AI agent that runs entirely on my laptop. No cloud, no API keys."* |
| **0:06–0:20** | Drag `timetable.png` onto the chat. Type **"Save my classes."** Send. The pill flips to *"Looking at your image with qwen3.5:4b"*, then three `add_class` pills tick ✓. The Tasks panel fills with classes. | *"I drop in a screenshot of my timetable. It switches to a vision model, reads it, and calls a tool for every class. Look, they're in my planner now."* |
| **0:20–0:34** | Type **"Quiz me on my uploaded notes."** The `search_knowledge_base` pill appears, then glass quiz cards. Answer one wrong (coral shake), then one right (seafoam glow). | *"It searches my own lecture notes and turns them into an interactive quiz. Get one wrong and it remembers that topic as a weak spot."* |
| **0:34–0:46** | Type **"Research what's new in Python 3.15 and save a note with sources."** Pills: `web_search` → `read_webpage` → `write_note`. Click **Open**: the preview slides in with the cited note. | *"Multi-step research: it searches, reads the sources, writes a cited note, and I can preview it right here."* |
| **0:46–0:58** | Type **"Delete that note."** The **safety modal** springs in (amber wave, "can't be undone"). Pause for a beat, then click **Approve**. | *"Anything risky pauses the agent and asks me first. The loop literally waits until I decide."* |
| **0:58–1:10** | Back on the reply: auto-speak is talking. **Zoom in on the symbiote** as its membrane ripples with the voice. | *"And it talks back with a neural voice that's also generated locally. The creature is reacting to the actual audio."* |
| **1:10–1:20** | Expand the **telemetry chip** under a reply (steps, tool chain, tokens, context bar, *local*). Quick flash of the Memory Bank with learned facts. | *"Every answer shows exactly what it did, how long it took, and what it learned about me."* |
| **1:20–1:30** | Hold on the empty state, symbiote centred. Title card: **AETHER · local-first autonomous agent · github.com/Piyuz-ui/aether** | *"AETHER: tools, memory, vision, voice and safety, running on my own machine."* |

**Total:** about 88 seconds. If you're short on time, cut the research scene (0:34–0:46), since the delete scene can reuse the note from the quiz or vision scenes.

---

## Shot list for the README GIFs

Record each as a short clip (3–8 s), convert, and save into `docs/media/` with these exact names:

| File | What to capture |
|---|---|
| `hero.gif` | The empty state symbiote, then send a message (watch it grow tendrils while thinking): pills stream in, then the answer (≈8 s) |
| `safety-gate.gif` | Ask to delete a file: the modal springs in, then click Deny |
| `rag-citations.gif` | Ask about your PDF: the `search_knowledge_base` pill, then an answer with `[file p.N]` |
| `vision-timetable.gif` | Drop the screenshot: the vision note, then 3× `add_class` ✓ |
| `quiz-cards.gif` | A wrong answer (shake), then a right one (glow), then a card flip |
| `voice-orb.gif` | The symbiote rippling while AETHER speaks, and reaching for a tool |
| `telemetry.gif` | Expanding the telemetry chip |

**Convert a screen recording into a small GIF:**

```bash
ffmpeg -i clip.mov -vf "fps=15,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer" -loop 0 docs/media/hero.gif
```

Keep each GIF under about 5 MB so GitHub loads it quickly. Install the tool with `brew install ffmpeg` if you don't have it.

---

## Talking points for judges and interviews

- **"Why not LangChain?"** The engine is one ~400-line, readable file, so every step is inspectable and testable. That made it possible to fix small-model failures, such as the runaway generation and the false success claims, at the engine level.
- **"How is it safe?"** A per-call risk level (some tools are risky only sometimes, e.g. overwriting), pre-checks before asking, an approval broker built on `asyncio.Future`, a path jail for files, SSRF protection on web fetches, and an AST calculator instead of `eval`.
- **"How good is the RAG?"** Hybrid: semantic + BM25, weighted Reciprocal Rank Fusion, deduplicated passages, page and slide citations, and an overview mode for "quiz me on my notes".
- **"What did you learn?"** Small models need *engineering*, not just prompting. Each row of the "Making a 7B model behave" table in the README came from a bug found in live testing.
- **Numbers:** 20 tools + MCP · 148 automated tests · voice ~6× faster than real time · the first spoken word in under 1 s.
