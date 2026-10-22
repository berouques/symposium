# Changelog

## 0.5.0

- **Roles removed.** A character carries its own persona prompt and title; `prompts/roles` is gone.
- **Characters**: LLM profile with an explicit *fallback to the symposium's profile* flag; if the flag is off, an unavailable or deleted profile stops the symposium with a recommendation to open the participant's settings. Default tools: only `ask_human` (the Erudite adds web/library, the Python programmer and the data scientist get the coder). Default reply budget 350 tokens. «Create based on this» clones any character. Gender removed from users and characters.
- **17 built-in characters** in `prompts/characters/*.json` (Moderator, Erudite, Sofia, Petronius, Ludomir, Fabulon, Visual, Ilmari, Suvi, Aino, Annette, Electra, Protagoras, Plato, Epicurus, Friedrich, Oleg), prompts derived from the former roles, **reloaded live** when a file changes.
- **Avatar editor**: four independent controls — hair (style + colour from white to black and ginger), face (mustache, beards, stubble, goatee, makeup; facial hair follows the hair colour), background, outfit + frame.
- **Reasoning**: the profile has a single master switch; effort is per character; a profile with reasoning off overrides every character.
- **Fixed**: LLM profiles added in the admin panel did not appear in symposium/character pickers (stale list).
- **UI**: sliding symposia panel with search and incremental rendering; four themes with light/dark variants (Symposium, Academus' Garden, Sorbonne Theological College, Solvay Congress); English by default with a Russian translation (user menu → Appearance and language).
- **Safety**: destructive shell/Python commands need the human's confirmation; workspace rules added to the symposium rules.
- **Docs**: README and documentation in English; author and licence metadata.

## 0.4.x

- Protocol v2 (structured `<state>` replies), explicit state machine, blind voting with graded ballots, shared evidence, per-profile request limiter with provider presets, self-recovery, characters, user profile, file attachments, `web_fetch`, local per-symposium Python workspaces, message footers (time, tokens, copy), code blocks, usage views, full technical dump export, new React UI. Earlier history: see the git log.
