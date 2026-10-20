"""Verify the pinned local specification; never fetch remote data."""

import sys

from jsonschema.exceptions import ValidationError

from tools.specification import ROOT, SpecError, coverage, encode, load_snapshot


def main() -> int:
    try:
        snapshot = load_snapshot()
        report = coverage(snapshot)
        if (ROOT / "spec/coverage.json").read_bytes() != encode(report):
            raise SpecError("SPEC_COVERAGE", "committed coverage differs from snapshot")
    except (SpecError, ValidationError, OSError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"Specification verified: {report['reference_entries']} reference entries, Pine v1-v6.")
    for row in report["versions"]:
        print(f"v{row['language_version']}: {row['reference_entries']} references, "
              f"{row['admitted_assertions']} admitted assertions; OpenPine not implemented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
