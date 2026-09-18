# Stage 2 F0 reconciliation — 2026-09-18/19

`full_stage2_accepted` remains false. This is inventory of the working base, not acceptance.

## Input ZIP

- Inner archive `OpenPine_Stage2_Continuation_2026-09-18.zip`
- SHA-256: `d96ccc7453b56da5fa0b0888c04144af0eb5255e5dd5d9078a511b5798516581`
- Matches completion spec E.1.

## Environment

- Python 3.11.15 and Python 3.13.5 are installed on the build host.
- `continuation-work/` is not treated as an applied patch. The scalar `version_compatibility.py` proof is rejected as mixed-version support.

## Git `release/5.0.0rc6` after continuation-pack merges

| Component | SHA |
|---|---|
| openpine | `501e5df5eb05ddd189f87d7aafc56a024415b543` plus open continuation PR #18 |
| pine2ast | `218ba8ec1696d6f530338dd565068a1d5a1031b5` plus F2 import-matrix PR |
| pinelib | `84043d168062d5f1f87efc6e0a2b3e96ad20156c` |
| ast2python | `3d9f3649fb91d62018a5a36b093a7e7d4a5dc1c7` |

Lifecycle pins in `docs/RC6_LIFECYCLE_SOURCES.json` still point at older cumulative SHAs. They must be updated together with `verification/inventory.json` hashes; changing pins alone makes RC6 native CI fail closed.

## Open remainder (spec S2-13/14/15/23)

- F1 catalog authority including CAT-04
- F2 origin-preserving execution (admission matrix is started; not full semantic origin)
- F3 independent oracle including STATE-03/04
- F6 eight-component campaign on one lock
