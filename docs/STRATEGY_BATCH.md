# Several strategies on one primary snapshot

Compile each strategy through `POST /api/compile` and retain its `input_ref`.
Upload the primary candles once through `POST /api/snapshots` and retain the
returned `sha256`. Submit the following body to `POST /api/job-batches`,
substituting the three returned identifiers.

```json
{
  "snapshot_id": "SHARED_SNAPSHOT",
  "requests": [
    {"schema_version":"openpine.job-request.2","input_ref":"LONG_PROGRAM","timeout_seconds":60},
    {"schema_version":"openpine.job-request.2","input_ref":"SHORT_PROGRAM","timeout_seconds":60}
  ]
}
```

A successful submission returns HTTP 202 and one job ID per request, in input
order. Each job has its own execution state, fills, checkpoint and result.
Use the normal job endpoints to inspect results or to cancel one member with
`POST /api/jobs/JOB_ID/cancel`. The coordinator admits 1–32 members in one
transaction, subject to the available queue capacity. A retry of a successful
submission creates another group of jobs.
