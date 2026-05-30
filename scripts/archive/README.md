# Archived Legacy Scripts

These scripts are archived because they use hardcoded paths, old TV export formats,
or duplicate the canonical batch runner (`tools/openpine_batch_1527_runner.py`).

**Do not use these scripts for new work.** They remain here for reference only.

## Why Archived

- Hardcoded `sys.path.insert` paths that assume one machine layout
- Use old TV export coordinate formats
- Duplicate logic that now exists in the canonical OpenPine CLI
- No test coverage

## Canonical Paths

- **Batch runner**: `tools/openpine_batch_1527_runner.py` (supports plan/ingest/compile/register/run phases)
- **TV export comparison**: `tools/openpine_compare_tv_exports.py`
- **OpenPine CLI**: `openpine` command (pip-installable)

## Archived Scripts

| Script | Reason |
|--------|--------|
| `batch_backtest_compare.py` | Duplicates `openpine_batch_1527_runner.py` |
| `batch_compare_tv.py` | Duplicates `openpine_compare_tv_exports.py` |
| `cli_tv_export_batch.py` | Legacy TV export format; replaced by batch runner |
| `extract_strategy_trades.py` | Duplicates `exports.export_trades()` |
| `run_backtest_compare.py` | Legacy one-off runner |
| `run_batch_compare.py` | Legacy one-off runner |
| `run_p092_comparison.py` | Case-specific one-off |

Archived: 2026-05-30 — per TZ v3 architecture cleanup.
