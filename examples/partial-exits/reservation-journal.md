# Protective reservations during a manual partial close

With ten units at 100, initial equity 1000 and zero fees, reserve six units
for exit A (stop 95, limit 110) and four for B (stop 95, limit 120).
Market orders fill at the next opening. The table shows the state after
each bar; reservation quantities are assigned in order of creation.

| Bar | Operation | Position | A | B | Closed on bar |
|---:|---|---:|---:|---:|---:|
| 0 | Submit entry and both exits | 0 | 6 pending | 4 pending | 0 |
| 1 | Entry fills at 100; submit manual close of 3 | 10 | 6 | 4 | 0 |
| 2 | Manual close fills at 100 | 7 | 6 | 1 | 3 |
| 3 | High 111 reaches A's limit at 110 | 1 | — | 1 | 6 |
| 4 | Low 94 reaches B's stop at 95 | 0 | — | — | 1 |

The three exit fills produce realized profits of 0, 60 and −5, yielding final
equity 1055. The reservation reduction event on bar 2 records one unit for B.
Run `python -m unittest discover -s tests -p test_partial_broker.py` to replay
the journal and the related cancellation, reversal and commission cases.
