# Expanded stage 24 on Windows

Python 3.14.8, SQLite 3.54.1 and DuckDB 2.0.0.dev2610192346. The scale report records elapsed seconds, peak worker RSS in bytes, snapshot and result identities. The 100,000-bar case used two concurrent jobs. The larger cases used one each. The complete scan verifies timestamps and closes. Process read calls and bytes include cached reads.

- [Scale and backup restoration](evidence/expanded-stage24-scale.json)
- [Interrupted strategy and queued indicator](evidence/expanded-stage24-recovery.json)

The worker was stopped with 655,131 journal bytes on disk. Recovery resumed from bar 1,000 and yielded exactly 4,000 distinct fills and 2,000 distinct closed trades. The independent indicator completed in one attempt.

Measured script SHA-256 (local file bytes):

- storage_benchmark.py — c4799a16af5da767d5635b4e19c9cfc76f0aba5ce9990525ac8884c58c517da6
- mixed_trade_recovery.py — 9531b3b636f88fc658bcdb8e8afb058d2e2f91967e4cd7a890ee1e7a211906da
