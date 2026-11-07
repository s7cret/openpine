# OpenPine: repository guidance

The R6 application lives in this repository. Keep changes here; legacy checkouts
and the local Pine Wiki are reference sources, not runtime dependencies.

## Project skills

Use the matching repository skill when its workflow helps the requested task:

- `openpine-debugging`: reproduce and diagnose a defect, failed check, restore
  divergence, resource problem, or platform difference.
- `openpine-verification`: match completion and publication claims to actual
  evidence; choose proportionate checks before committing or handing off.
- `openpine-adr`: record a consequential architectural decision already made in
  the task, or explain an existing decision.
- `openpine-acceptance`: run or audit R6 qualification and publish its evidence.
  A plan request or a small documentation correction does not require full R6 CI.

The skills are in `.agents/skills/`. Start with the selected `SKILL.md` and read
only the linked references needed for that task. Do not load the whole catalog.

## Engineering constraints

- Use Python 3.14 only; qualification requires exactly Python 3.14.8.
- Preserve package boundaries checked by `tools.verify_architecture`.
- Pine v1–v6 availability must be explicit; implemented bounded behavior and
  external TradingView observations are different kinds of evidence.
- Reuse the existing test, conformance, wheel and recovery tools. Do not replace
  them with a generic pytest/npm workflow or a second release gate.
- For a development batch, update the static function matrix and run diff-based
  feedback. Regenerate complete conformance reports during tagged acceptance.
  See [R6 batch development](docs/DEVELOPMENT_BATCHES.md).
- Agent instructions are project-local development documentation. They are not
  included in the application's current `source_identity()` or wheel payload.
  Review their own diff separately; do not present old CI as a test of new skills.

See [the skill selection and provenance record](docs/AGENT_SKILLS.md).
