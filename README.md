# BOM Squad 🎺

A multi-agent procurement crew. You describe a hardware build in plain English, and a band of agents turns it into an approved purchase order: an intake agent structures the request, a sourcing agent shortlists parts from a catalog, recruits a specialist into the room when a component needs real judgment, a budget agent verifies totals and compatibility, and a human gives the final sign-off.

Built for the **Band of Agents Hackathon** (lablab.ai, June 12–19, 2026). Born from a real problem: designing a drone is fun, sourcing its parts is days of vendor-hunting.

## Quickstart (zero API keys needed)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

That runs in **demo mode** — canned LLM replies scripted for the default request — so the full workflow runs instantly. Try your own request text later with `python main.py --request "..."` (custom requests need a real LLM, see below).

## Switching to a real LLM

Copy `.env.example` to `.env` and fill it. The client is OpenAI-compatible, so all of these work by changing two env values:

| Provider | LLM_BASE_URL | Notes |
|---|---|---|
| Groq | `https://api.groq.com/openai/v1` | free tier, fast |
| AI/ML API | `https://api.aimlapi.com/v1` | hackathon partner, $10 credits at kickoff — using it meaningfully qualifies for the $1,000 partner prize |
| Featherless | check partner setup guide at kickoff | hackathon partner, $25 credits |

Set `LLM_PROVIDER=live`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`.

## File map

| File | Job |
|---|---|
| `main.py` | wires the room, runs the workflow start to finish |
| `agents.py` | Intake, Sourcing, Specialist, Budget, Human Desk |
| `room.py` | the message room — **this is where the Band SDK plugs in** |
| `llm.py` | demo mode + OpenAI-compatible live mode |
| `catalog.py` | catalog search and price math (math stays in code, never in the LLM) |
| `data/parts.csv` | the mock vendor catalog |

## The Band integration (Day 1 job)

Band access codes drop at the June 12 kickoff stream. Once you have them:

1. Read the Band Hacker Guide + SDK setup (docs.band.ai).
2. Replace `Room.join / Room.post / Room.announce` internals with Band room calls. The agents never talk to each other directly — every message already flows through `Room` — so this is a transport swap, not a rewrite.
3. The recruitment moment (`request_specialist` → specialist joins) maps to Band's agent discovery/recruitment — make that the centerpiece, it is the platform's signature feature and the judging criteria reward exactly this.

Hackathon requirements to keep in sight: **at least 3 agents collaborating through Band**, Band as the real coordination layer (not a wrapper), original + MIT-licensed, public GitHub repo, demo URL, video, slides, cover image.

## 8-day plan

- **Jun 12** — kickoff stream, Band account + codes, wire `room.py` to Band
- **Jun 13–14** (weekend) — live LLM in, polish recruitment flow, end-to-end on Band
- **Jun 15–16** — second workflow pass, error handling, stretch: tiny web view of the room
- **Jun 17** — README polish, slides, cover image
- **Jun 18** — record 2–3 min video, draft submission page, **submit**
- **Jun 19** — buffer for disasters only

## Video script skeleton

1. Hook (10s): "I designed a drone. Then I lost days sourcing its parts."
2. Problem (20s): procurement = manual coordination across people who don't share context.
3. Demo (90s): request in → agents coordinate in the Band room → recruitment moment → budget review → human approval → purchase order out.
4. Why Band (20s): discovery, shared context, handoffs across frameworks.
5. Business value (15s): every hardware team, lab and factory does this weekly by hand.
