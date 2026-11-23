# From the script catalogue to a portable portfolio

List the versioned scripts with `openpine corpus catalogue`. Use the case IDs
to select entries for a portfolio. This example runs a Pine v4 partial close
and a Pine v6 strategy that uses an enum to decide when to enter and exit.

```sh
openpine corpus-portfolio export --case partial-v4 --case enum-strategy-v6 --state-dir local-runs --portfolio results.json
openpine corpus-portfolio repeat --state-dir fresh-runs --portfolio results.json
```

The JSON file contains the selected catalogue records, source and dataset
hashes, compilation outcomes, historical chart data and complete simulation
journals. Each executable case first passes the catalogue's numeric expectations
and its source, artifact and checkpoint comparisons. A catalogue case that
expects a rejected program carries the compiler diagnostic.

Copy the portfolio file to another installation of the same release. Repeat
uses the case IDs and the installed catalogue in a new state directory. It
computes the results again and checks the hash of the full portfolio.
