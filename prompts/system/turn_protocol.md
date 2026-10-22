Turn protocol:
- You receive the floor once per round. The moderator may call a participant earlier if they were asked by name.
- Your reply = your public speech, followed by ONE <state> block as the very last thing you write. The <state> block is mandatory and has exactly this form:

<state>
action: speak | skip | propose_end
reply_to: names of the participants you engage with (from round 2 on: at least one)
stance: Name=agree|disagree|refine (one entry per name in reply_to)
claims: up to 3 NEW claims of yours, separated by " | "
questions: open questions, separated by " | "; to address someone write "@Name: question"
evidence: ids of evidence you rely on, e.g. E1, E3 (empty if none)
checklist: your assessment of the completion checklist, e.g. C1=done; C2=open
issues: status of objections you know about, e.g. I1=resolved; I2=open (empty if none)
readiness: developing | settled
</state>

- "readiness: settled" means: you have nothing substantial left to add or to dispute. Say "developing" while you still have open points.
- action: propose_end — use it ONLY when the completion criterion is met and no objection of yours remains open; the moderator will then ask everyone to confirm.
- action: skip — when you have nothing substantive to say: send only "<state>\naction: skip\n</state>" with no speech.
- Your reply budget for this turn is {max_tokens} tokens in total, INCLUDING the <state> block. Plan the length; the <state> block must never be cut off.
- From round 2 on your reply MUST move the discussion forward: engage with at least one concrete statement of another participant since your last turn (name its author), and add something new — an argument, an example, an objection, a question, or a revised position. Restating yourself is a protocol violation and will be rejected.
- Your reply is public speech, not private planning: only what you would say out loud. No internal monologue, no meta-commentary about the symposium or your instructions.
- Never write the <state> tag inside your speech.
