# Protocol v2: reply formats

## A participant's reply

Free speech, then **one** `<state>` block — the very last thing the participant writes:

```
<state>
action: speak | skip | propose_end
reply_to: Alice, Bob                      # from round 2: at least one author
stance: Alice=agree; Bob=disagree         # agree | disagree | refine
claims: new claim 1 | new claim 2         # up to 3 NEW claims
questions: @Bob: what would refute this? | a general question
evidence: E1, E3
checklist: C1=done; C2=open
issues: I1=resolved
readiness: developing | settled           # neutral "my position is complete"
</state>
```

To pass: send only `<state>\naction: skip\n</state>` with no speech. There is no terminal marker: the block is the end of the reply. The parser is tolerant (case, list bullets, an unclosed tag); legacy `[END_OF_TURN]`, `[SKIP_TURN]`, `[END_SYMPOSIUM]` are understood as synonyms.

What the engine derives: stagnation (no `claims`), ignoring colleagues (no `reply_to`), consensus on the checklist, resolved objections (`issues`, only by their owner), who was asked (`@Name:` → speaks earlier), readiness to conclude (`readiness`, `propose_end`), the round digest without an LLM.

## Subagent calls

A message with `<call>` blocks has no speech and no `<state>`; several blocks run in order:

```
<call agent="web_explorer">search query</call>
<call agent="web_fetch">https://example.com/data.csv</call>   # download into workspace/downloads
<call agent="archivarius">what to look for</call>
<call agent="coder" language="python" filename="a.py">
code as is — no JSON escaping
</call>
<call agent="coder" mode="shell" timeout="120">pip install numpy</call>
<call agent="ask_human">{"question": "…", "options": ["…", "…"]}</call>
```

The result is shared evidence `[E#]` (an `evidence` message in the transcript and a row in `evidence`). Participants cite it as `[E#]` and list ids in `evidence:`. Only the tools permitted to the participant are advertised and accepted.

## Ballot (blind voting)

```
<vote>agree | reservations | object</vote>
<reason>one line</reason>
<unresolved>what remains (for reservations/object)</unresolved>
<satisfied_if>what would satisfy me (for object)</satisfied_if>
```

Ballots are revealed only at tally. An objection becomes an `issues` record shown in every briefing until its owner resolves it. Policies: `unanimous` (no objections), `supermajority` (≥ 2/3), `majority` (> 1/2); `reservations` count as "for" and go into the report.

## When confirmation is requested

1. a participant's `action: propose_end` — remembered and considered at the END of the round (rejected while objections are open or during cool-down);
2. the checklist is closed by consensus and every participant is `readiness: settled`;
3. stagnation: `stall_rounds` rounds in a row without new claims;
4. the round limit — final.

## Discussion forms

`free`, `disputatio` (videtur quod → sed contra → respondeo → ad argumenta), `scientific` (claim → basis → falsifier → alternatives). Files `prompts/system/form_*.md`; a new form is just a new file.
