# RC6 method, context, contract and tuple repair publication

The exact candidate `5b0a7318cda871bef12d835e8b074d0f3f6d23d2` (tree `4ee86715622404b31e174fb2e17d064a6ad579c9`) passed the coordinated Linux run [34206347276](https://github.com/s7cret/OpenPine/actions/runs/34206347276). Both Python versions executed all 16,787 selected tests with zero failures, errors or skips. Every previously accepted 10,683 test and every 16,745 failed-candidate test remains present. The original 12 Stage 1 cases, protected worker tests, architecture checks, package builds and frontend passed; frontend includes 152 Vitest tests and 22 Node tests.

This wave contains contextual constant/type repair, ordinary method selection, versioned float comparisons, modern string and map contracts, and independent array/string/map host corpora. The original failed run `34201053710` is retained: a tuple produced by a loop lost its lexical child types. The producer correction uses the existing child inference and loop target type owners, with 42 additional tests and no edits to older tuple tests.

There are 5,520 actual manual observations per Python (3,340 unique case/path pairs). Of these, 5,430 assigned observations pass. The remaining 90 remain unassigned: 40 array observations lack a complete target binding, and 50 string observations lack confirmed primary authority. Thirty of the latter differ from the preserved expected values; they remain explicit UNVERIFIED differences. No denominator, source-authority requirement or old expectation was weakened.

Three two-parent release integrations preserve the exact tested trees:

| Component | Tested source | Release integration |
|---|---|---|
| pine2ast | `05d7e61be58a184b211bc093f989527157005b92` | `dd49acc05f9a00778c37e76573880b7617842d57` |
| pinelib | `2148bca8370858ef8f7716ee0ba27d42960563b4` | `99d67b12bf26c7731ae0b923441d2a1b320b7edf` |
| OpenPine | `5b0a7318cda871bef12d835e8b074d0f3f6d23d2` | `88273b589a47fa8ad6424a7523d74f6a6856c413` |

The compiler pin remains `95a14be4be8987faafb3e7629c744e698fd9f134`; it is not an integration target. The lifecycle source pins retain the exact tested commits. This publication adds evidence and progress metadata after the exact-tree integrations.

Independent acceptance is recorded in `verification/tuple-repair-full-execution-independent-review.json`, SHA256 `38ec9ff8e37bb820424dc720066965934dff8f4ef16f89fc49eb262d96eaabf9`. The adjacent CI, archive, integration guard and actual readback receipts bind the evidence to source identities. Guarded cleanup completed in runs `34212529728`, `34212533539` and `34212537581`: six archive tags were verified and six exact temporary branches were deleted. Release and operations refs were preserved. The actual receipts and fresh remote readback are retained in `verification/tuple-repair-cleanup-final-execution-review.json`.

Full Stage 2 remains in progress: all four DoD classifications are unchanged. EMA/MACD seeding, TSI scaling, explicit receiver qualifiers, event-history occurrence metadata and matrix corpora belong to later candidates and are excluded from this run. This is bounded candidate acceptance, not full builtin parity or TradingView execution verification.
