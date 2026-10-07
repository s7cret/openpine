# INT05 current integration candidate

This working branch carries qualified INT08 source `f02dfb64eae8dd06221d207dc10f8043469265c6`, INT05 owner fixes through `3e76d87459f36e5f21bf666245e04b90b9a7accd`, and central policy change `90f4e9888245cc51780a88d0ba5354cf88900ffa`. No PR has been merged. INT04 work is excluded.

The host package tree identity and candidate branch locator are refreshed for this combined source. The central policy now declares PineLib as a BacktestEngine dependency, preserving transitive Optimizer selection. All seven library pins, the host inventory of 11998 nodes, its baseline aliases, and 130 reconciliation decisions remain unchanged.

`int05_tests` supplies a separate mandatory owner contract gate: 76 collected nodes, zero deselected, frozen in `verification/int05-owner-contract-inventory.json` under suite `int05-owner-contract`. This gate is additional to the existing host selectors. Run it using the exact installed candidate and the pytest verification plugin with that suite and lock; collection alone does not establish execution success.

Actual full and affected campaigns, their source binding, primary readback, and benchmark comparison remain pending. Qualification of the earlier INT08 SHA is historical evidence and does not qualify this integration SHA.
