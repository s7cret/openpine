# INT05 current integration candidate

This working branch carries qualified INT08 source `f02dfb64eae8dd06221d207dc10f8043469265c6`, INT05 owner fixes through `3e76d87459f36e5f21bf666245e04b90b9a7accd`, and central policy change `90f4e9888245cc51780a88d0ba5354cf88900ffa`. No PR has been merged. INT04 work is excluded.

This isolated integration adds the approved negative-reader package through
`62189cf1d00c97b7b581143e006d6ab9cf1ef8be` on exact integration base
`0bbcfc08206efa8325a6148390fe228785247e5d`. Only those two INT05 commits are
cherry-picked; the host package identity, candidate branch locator and separate
INT05 owner inventory are refreshed here. Reserved policy and schemas are unchanged.

The host package tree identity and candidate branch locator are refreshed for this combined source. The central policy now declares PineLib as a BacktestEngine dependency, preserving transitive Optimizer selection. All seven library pins, the host inventory of 11998 nodes, its baseline aliases, and 130 reconciliation decisions remain unchanged.

`int05_tests` plus `int05_reader_tests` supplies a separate mandatory owner
contract gate: 115 collected nodes (the original 76 plus 39 reader contracts),
zero deselected, frozen in `verification/int05-owner-contract-inventory.json`
under suite `int05-owner-contract`. This gate is additional to the existing host
selectors; it does not reduce the 11998-node host inventory. Run it using the
exact candidate and pytest verification plugin with that suite and lock;
collection alone does not establish execution success.

Actual full and affected campaigns, their source binding, primary readback, and benchmark comparison remain pending. Qualification of the earlier INT08 SHA is historical evidence and does not qualify this integration SHA.

Existing automatic PR run `37539935508` at reader-review SHA `85a4cd95` built
eight wheels but correctly failed the installed API conformance assertion:
OpenPine actual package identity was `4a47fb84419867dfde033a01a86c5bd279032b8184a50e2d055d6f30d57738fa`
while its inherited lock still declared `062a7e0403b3d1fdff9bec16966c4f0eb848059482a334b2fb4489fec2cab62a`.
All seven library identities conformed. This candidate refreshes the host lock
from the actual reviewed package bytes; the assertion is retained. Full installed
preparation/conformance and the single baseline calibration still require raw
verification. The earlier 21 manifests bound to `0bbcfc0` remain historical
inputs; this new integration invalidates their source/collection/plan anchors.
