# Repeat an experiment offline

Export a completed experiment from a stopped local server's catalogue.
Choose a new bundle directory outside the catalogue.

```sh
openpine portable-experiment export --id EXPERIMENT_ID --state-dir original --bundle portable
```

Copy the entire bundle directory to the destination. Install the same OpenPine
wheel and its platform dependencies on Python 3.14.8. To rerun all variants,
choose a new state directory.

```sh
openpine portable-experiment repeat --bundle portable --state-dir reproduced
```

The bundle contains the selected experiment's frozen plans, program,
referenced candle blocks, context datasets and the expected hashes of its
chart windows and ordered simulation journal. Each variant starts afresh
with its recorded settings. The returned local experiment ID opens the
recomputed results. A checksum error identifies the damaged bundle member.
For CI the Linux producer exports two partial-exit variants. Windows and
Linux consumers install the same wheel and independently rerun that export.
