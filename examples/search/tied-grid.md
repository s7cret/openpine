# Reproducible selection with tied scores

For the example selection and holdout windows use a grid of `n = 1, 2, 3`.
Plot `close * (n == 1 ? 1 : 2)`. The selection mean of the source closes
is 12. All three ordinals have distinct parameter values.

| Candidate ordinal | n | Selection score | Rank |
|---:|---:|---:|---:|
| 0 | 1 | 12 | 3 |
| 1 | 2 | 24 | 1 |
| 2 | 3 | 24 | 2 |

The lower ordinal wins the tie. On the separate holdout window the winning
candidate scores 29. Run the scenario with:

```sh
python -m unittest discover -s tests -p test_searches.py -k resumed_tied_grid
```

The scenario runs the grid sequentially, then starts a fresh search, pauses
after the first successful candidate and closes the coordinator. Reopening
the state directory and resuming preserves that result ID and attempt count.
The three ranked ordinals, scores and holdout result match in both runs.
