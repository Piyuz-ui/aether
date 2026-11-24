# AETHER: 90-second demo video

**Goal:** in 90 seconds, prove AETHER is a real *agent* on a real home-built stack. It plans, uses tools, asks permission, remembers, thinks visibly and runs on a GPU across the network, and it never needs the cloud. Each scene shows one thing a chatbot can't do.

**Format:** 1440×900 screen recording, voice-over, burned-in captions. End on the symbiote.

---

## Before you record (10 minutes of prep)

- [ ] **PC:** on, plugged in, Ollama running; `gpt-oss:20b` warmed with one throwaway message. Settings → Network & hardware shows it **online**.
- [ ] **Mac:** `cd ~/Downloads/"ai agent" && ./start.sh`. Brain: **`gpt-oss:20b@pc`**, Reasoning **Med**.
- [ ] **Settings → AETHER's voice**: George, speed 1.05×, **Auto-speak on** (tap George once to warm it up).
- [ ] Calm mode **off**. Chats sidebar **open** on the left; right panel closed.
- [ ] Knowledge Vault has one real lecture PDF or `.pptx`. A few tasks and classes in the planner (so `/brief` has something to say).
- [ ] 👁 **Hide addresses** on (Settings → Network & hardware) so no IPs appear on screen.
- [ ] Start a **New chat** so the empty state with the big symbiote shows. Full screen (⌃⌘F), Do Not Disturb on.
- [ ] Record with **⇧⌘5**, "Record selected portion", with internal audio (e.g. BlackHole) or the voice recorded separately.

---

## The script

| Time | On screen | Voice-over / caption |
|---|---|---|
| **0:00–0:06** | Empty state, the symbiote breathing. The line under the input box reads *"Runs on your PC over your own network"*. | *"This is AETHER, an AI agent I built. The brain is a 20-billion-parameter model on my gaming PC's RTX 5080; the app runs on my MacBook."* |
| **0:06–0:20** | Type **/** → the skills menu appears. Pick **/brief**, send. Pills: `manage_timetable` → `manage_tasks` → `recall_memory`. Auto-speak reads the briefing while the symbiote ripples. | *"Skills are just Markdown playbooks. /brief checks my classes, deadlines and goals and talks me through my day."* |
| **0:20–0:34** | Click the **Data & plot** card, send. The **approval modal** for `python_sandbox` springs in; approve. A bar chart appears in a glass card; the GPU line under the reply shows **~95% · 13.8/15.9 GB**. | *"It writes and runs Python in a sandbox, but only after I approve it, and the chart lands right in the chat. That's the PC's GPU working live."* |
| **0:34–0:46** | Set **Reasoning: High**. Ask *"A bat and a ball cost 1.10. The bat costs 1.00 more than the ball. How much is the ball?"* The **Thought for…** card fills, then *"$0.05"*. Open the card. | *"For hard problems I turn reasoning up. You can open its thinking and see why it didn't fall for the trick."* |
| **0:46–0:58** | Type **/quiz normalisation**. `search_knowledge_base` → `make_quiz` → glass quiz cards. Answer one wrong (shake), one right (glow). | *"It quizzes me from my own lecture notes, with citations, and remembers what I keep getting wrong."* |
| **0:58–1:10** | Put the PC to sleep (or unplug its network). Ask anything. A note: *"pc isn't reachable right now… this answer runs on qwen3:14b on this Mac"*; the footer flips to *"Runs locally on Ollama"*. | *"If the PC goes away (say I'm at college without it) AETHER quietly falls back to the Mac and tells me. Nothing breaks."* |
| **1:10–1:20** | Expand the **telemetry chip** (steps, tool chain, tok/s, context bar). Flash **Settings → Network & hardware**: RTX 5080 card, *Excellent link*. | *"Every answer shows what it did, how fast, and on which machine."* |
| **1:20–1:30** | Back to the empty state, symbiote centred. Title card: **AETHER · local-first autonomous agent · github.com/Piyuz-ui/aether** | *"AETHER: my own agent, on my own hardware."* |

**Total:** about 90 seconds. Short on time? Cut the quiz scene (0:46–0:58).

---

## Shot list for the README GIFs

Record each as a short clip (3–8 s), convert, and save into `docs/media/` with these exact names:

| File | What to capture |
|---|---|
| `hero.gif` | The empty state symbiote, then send a message (tendrils while thinking): pills stream in, then the answer (≈8 s) |
| `safety-gate.gif` | Ask to delete a file: the modal springs in, then click Deny |
| `rag-citations.gif` | Ask about your PDF: the `search_knowledge_base` pill, then an answer with `[file p.N]` |
| `vision-timetable.gif` | Drop the screenshot: the vision note, then 3× `add_class` ✓ |
| `quiz-cards.gif` | A wrong answer (shake), then a right one (glow), then a card flip |
| `voice-orb.gif` | The symbiote rippling while AETHER speaks |
| `telemetry.gif` | Expanding the telemetry chip |
| `chart.gif` | Data & plot: approval, then the chart card with Download, and the live GPU line |
| `reasoning.gif` | Reasoning: High, then opening the "Thought for …" card |
| `skills.gif` | Typing `/`, arrowing to `/brief`, Enter |

**Convert a screen recording into a small GIF:**

```bash
ffmpeg -i clip.mov -vf "fps=15,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer" -loop 0 docs/media/hero.gif
```

Keep each GIF under about 5 MB so GitHub loads it quickly. Install the tool with `brew install ffmpeg` if you don't have it.

---

## Talking points for judges and interviews

- **"Why not LangChain?"** The engine is one readable file, so every step is inspectable and testable. That made it possible to fix small-model failures, such as runaway generation and false success claims, at the engine level.
- **"How does a 20B model use 22 tools well?"** It doesn't see them all: a router picks the 6–8 relevant ones per message from tags and packs (core, research, code & data, MCP).
- **"How is it safe?"** Per-call risk levels (some tools are risky only sometimes), pre-checks before asking, an approval broker built on `asyncio.Future`, a path jail for files, SSRF protection, a sandboxed Python process without API keys, and an AST calculator instead of `eval`.
- **"How does the PC part work?"** The Mac's model router knows each computer's home and Tailscale addresses, uses whichever answers, and falls back to a Mac model per answer when neither does. Ollama is never exposed to the internet: Tailscale lets only my own devices in.
- **"How good is the RAG?"** Hybrid: semantic + BM25, weighted Reciprocal Rank Fusion, deduplicated passages, page and slide citations.
- **"What did you learn?"** Small models need *engineering*, not just prompting. Each row of the "Making a 7B model behave" table in the README came from a bug found in live testing.
- **Numbers:** 22 tools + MCP · 277 automated tests · 11 optional cloud providers · RTX 5080 at ~95% GPU while answering · voice ~6× faster than real time.
