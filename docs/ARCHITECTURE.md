# Architecture

```
web/ → frontend/dist          React + Vite + TypeScript: top menu | participants + registries | chat | dock
        │  REST + SSE (/events) + WebSocket (/terminal)
app/main.py · app/api/*       routes: symposia, characters, admin, RAG, workspace, terminal
app/engine/                   THE SYMPOSIUM LOGIC (below)
app/llm/                      client, limiter, presets, reasoning control, fake LLM for tests
app/subagents/                web_explorer (SearXNG), web_fetch, archivarius (RAG), coder (workspace)
app/rag/ · app/workspace/     indexing (Ollama embeddings) · per-symposium workspace and files
app/characters.py · portrait.py   presets of participants · editable SVG portraits
app/db.py                     SQLite, numbered migrations
prompts/                      system/*.md (protocol, rules, forms, secretary), characters/*.json
```

## Engine (`app/engine/`)

| module | responsibility |
|---|---|
| `protocol/` | **pure functions**: `envelope` (a reply: speech + `<state>`), `ballot`, `calls` (`<call>`), `nudges`, `repetition` |
| `fsm.py` | **explicit state machine**: `decide(Snapshot) → Action`, `pick_speaker` — testable without DB or LLM |
| `convergence.py` | when to ask for confirmation: checklist consensus, stagnation, cool-down |
| `state.py` | typed `TurnState` / `VoteState` (JSON in the DB, survive restarts) |
| `store.py` | all SQL of one symposium + SSE events |
| `llmcall.py` | LLM calls on behalf of a participant: personal profile → fallback or stop with a recommendation |
| `turn.py` | one turn: context → LLM → parse → subagents / corrective request / record → effects |
| `vote.py` | blind voting, objections registry, outcome by policy |
| `secretary.py` | opening (note + checklist, with a no-LLM fallback), round digest, final report |
| `context/` | system prompts, transcript projection, briefing, secretary tasks |
| `symposium.py` | orchestrator: loop `snapshot → fsm.decide → execute`, pauses, auto-recovery, rounds, finish |

## Life cycle

`OPEN` (secretary: note + checklist) → rounds: `START_TURN` for every participant → `CLOSE_ROUND` (deterministic digest) → convergence check → optional `VOTE` → finish (`finished`, `end_reason`).

Order of speech inside a round: authors of open objections → participants addressed by `@Name:` → whoever has waited longest (fair queue). **Everyone speaks every round** (a participant may pass, virtually staying silent); an `action: propose_end` is considered at the END of the round.

State lives in the database, so restarts are safe: `symposia.pending_turn` (a turn), `symposia.voting_state` (a vote).

## Principles

- **A format error never stops a symposium.** Missing `<state>` → one request, then the reply is accepted with a mark. Pauses come from LLM errors, empty replies or invalid ballots after a retry.
- **Evidence is shared.** A subagent result is a public record `E#` in the transcript.
- **The round limit is hard.** The vote at the limit is final: the symposium closes whatever the outcome; dissent goes into the report.
- **Frugal with calls.** Round digests need no LLM (`summary_mode=llm` brings the secretary back); a ballot is one short call.
- **Reasoning budget.** Reasoning tokens count against `max_tokens`; models that think by default get an automatic reserve so the visible reply is not cut off.
- **Self-recovery.** Non-critical failures resume automatically with growing delays; critical ones (no profile, quota, payment, a participant's profile without fallback) wait for you.

## Participants and profiles

`characters` are templates; `members` hold a snapshot of the character plus personal settings: `api_profile_id` (own model) with `profile_fallback`, `tools` (permitted subagents), `rag_folder_ids` (private folders), `subagent_limit`. If a participant's own profile fails: fallback ON → the symposium's profile serves the call (the reply is marked); fallback OFF → the symposium stops with an explicit recommendation.

## Limiter (`app/llm/limits.py`, `rate.py`)

Per-profile policy (JSON): concurrency, pause, requests per second/minute/hour/day, tokens per minute/day/month, behaviour on exhaustion (`pause`/`wait`), `Retry-After` handling, retry parameters. A slot is held for the duration of a call. Off by default; presets are starting points.

## Safety (`app/subagents/safety.py`)

Pattern-based interception of destructive shell/Python actions → a confirmation card (same mechanism as `ask_human`). Not a sandbox.

## Scripts (`./sym`)

`setup · build · start · stop · restart · status · logs · dev · test · e2e · admin · backup · restore · reset-data · release · clean` — see `./sym help`.
