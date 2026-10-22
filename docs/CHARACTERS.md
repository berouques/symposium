# Characters

A character is a reusable participant. When a symposium is created, each chosen character is **copied** into the symposium (later edits do not change past symposia); any participant can then be fine-tuned during the symposium.

| field | notes |
|---|---|
| `name`, `title`, `description` | how the participant is introduced to the others |
| `prompt` | the persona: who they are, how they reason and argue |
| `speech_style` | one line: formal, vivid, terse… |
| `avatar` | `{hair, hair_color, face, bg, outfit}` — four independent controls; unset values derive from a seed |
| `api_profile_id`, `profile_fallback` | own LLM profile; if unavailable: fall back to the symposium's profile (ON) or stop the symposium and recommend opening the participant's settings (OFF) |
| `temperature` | 0.0–1.0 in 11 named steps (Determinist … Dreamer) |
| `max_tokens` | visible reply budget, default 350 (reasoning is counted separately) |
| `thinking_mode`, `thinking_depth` | reasoning on/off + effort; ignored if the profile's reasoning is off |
| `tools` | permitted subagents; default `["ask_human"]` |
| `rag_folder_ids` | private library folders — only this participant can search them |
| `subagent_limit` | calls per turn (0 — global setting) |

## Built-in characters: `prompts/characters/*.json`

Edit the files — changes are picked up live (modification times are checked on access). Built-in characters are read-only in the UI; use **Create based on this** to make your own copy.

```json
{
  "key": "oleg", "name": "Oleg", "title": "Street philosopher",
  "description": "Cynical, blunt, but very witty interlocutor…",
  "prompt": "You are …", "speech_style": "",
  "temperature": 0.9, "max_tokens": 350,
  "thinking_mode": null, "thinking_depth": null,
  "tools": ["ask_human"],
  "avatar": {"hair": "buzz", "hair_color": "#34231a", "face": "stubble", "bg": "#2b3a4a", "outfit": "#555b66"},
  "i18n": {"ru": {"title": "Уличный философ", "description": "…"}}
}
```

Hair styles: `bald buzz short side long curly bun`. Face: `none mustache stubble beard_short beard_full goatee lipstick makeup_light makeup_bold` (beards and mustaches take the hair colour). Colours are `#rrggbb`; the UI offers curated swatches.
