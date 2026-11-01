---
name: openpine-adr
description: Record a consequential OpenPine architecture or ambiguous Pine semantic decision made in the current task, or explain an existing decision from its evidence. Avoid ADRs for routine edits.
license: MIT
metadata:
  origin: Adapted from affaan-m/ECC architecture-decision-records
  upstream-commit: b38be6a7f6350964d59328b52f0624e1d1d552d1
---

# OpenPine architecture decisions

Preserve why a consequential choice was made, its alternatives, compatibility
boundary and cost. Use the language of the task and keep the record brief.

Resolve repository paths from the OpenPine Git root, `openpine-r6/` under
the parent workspace, rather than from a junction exposing this skill.

## Find existing evidence

Check `docs/adr/` and its index, then relevant design/semantic documentation and
the implementation. When asked "why", answer from those records. If no record
exists, distinguish an inference from a documented decision; do not invent a
historical rationale or retroactive user approval.

Record decisions affecting package ownership, public contracts, IR/checkpoint
compatibility, persistent identity, storage or execution policy. A conflicting
Pine source interpretation warrants a record when it has lasting behavioral
consequences. Routine naming/formatting fixes and each individual function do not.

## Write the decision

Use [the ADR template](references/adr-template.md) as a guide; omit irrelevant
fields. Read the next number from existing numbered records without overwriting
them. Create `docs/adr/` and its index when the first relevant authorized record
is needed, and update the index in the same change.

Use `accepted` for a choice already authorized in the task or an implementation
choice within that scope. Use `proposed` for an unresolved recommendation.
Recording a choice does not authorize implementing its alternatives. Ask only
if a material unresolved decision blocks the requested work.

Include source URLs and revisions when they resolve a dispute. State separately
what the artifacts establish, what OpenPine chooses to implement, and what
requires external TradingView comparison. Note version availability and any
migration, performance or resource consequences that actually apply.

When backfilling, give today's recording date and the known original decision
date, or say that the original date is unknown. Preserve old rationale when a
decision is superseded; link the replacement in both the record and the index.
Do not create a record for every discussion or replay the whole chat history.

No separate confirmation is needed just to write documentation within the
already authorized task. This skill does not dispatch agents or require the
ECC framework, an external service or an additional template in `docs/adr/`.

Source: [pinned upstream procedure](https://github.com/affaan-m/ECC/blob/b38be6a7f6350964d59328b52f0624e1d1d552d1/skills/architecture-decision-records/SKILL.md).
The project adaptation record is `docs/AGENT_SKILLS.md` under the Git root.
The upstream MIT notice is retained in [LICENSE](LICENSE).
