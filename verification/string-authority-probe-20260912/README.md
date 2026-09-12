# Pine v5 numerical authority investigation — 2026-09-12

No numerical oracle was obtained from the unauthenticated endpoints tested here. Do not turn these successful HTTP/compiler responses into confirmed expected values.

Candidate `da413b1a9f6c8f62de590adfad94db5254f357c9` and all production/expected/corpus/gate files are unchanged by this investigation. Stage 2 and PR17 merge acceptance remain unconfirmed.

| Attempt | GitHub run | Observed result | Archive SHA256 |
|---|---:|---|---|
| Literal input defaults | 34691150016 | All five requests return the same input-float type metadata; no numerical values. | 8af2bd994e610b31d0ad54a06e9db98d9fe4af892ce0b517625890cfa5333106 |
| Valid/invalid declaration limits | 34691276254 | Responses to max_labels_count=100 and 501 are identical; remaining conditional probes were not sent. | 69eec10e006a4dce22496d6ad556a62d9bf05da229e2fbcea6d387754737c763 |
| True/false title predicates | 34691409304 | Both responses contain result:null; conversion predicates were not sent. | 96f1baa56e60c7b25b34a2bf238e654b07400eb4e59b07be3d2299bdf74ff8f9 |
| Anonymous full translation | 34691527078 | The first control request returns HTTP 404; stopped without trying account access. | 91e398e781c2af8fd0a0b164dcc18e430f9ef7da2840a5599f38262c81a2e2a3 |

Each archive records exact public probe sources, responses, timestamps and SHA256. No credentials, repository implementation or user account data were sent. No scripts were saved to any TradingView account.

The attached `probe.pine` is for actual TradingView execution, not an expected-output file. It displays all seven literal and series-string conversions, including independent decimal/invalid controls and the three disputed strings. It places no orders or alerts. Capture the entire table including P5-CONV / 20260912-A and preserve the source. The source SHA256 is `f654eca00b7a75e1d225874af258ba9b19b21153c535c43e483b25a2c42b1981`.

This can resolve only the observed lexical cases and qualifiers. It does not prove the entire builtin surface or every historical TradingView build. A current successful lightweight compilation is syntax evidence only. The existing v5 authority review and unresolved numeric expectations have not been rewritten.
