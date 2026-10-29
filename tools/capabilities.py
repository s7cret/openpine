"""Compatibility CLI for the application capability catalogue."""
from apps.openpine.capabilities import report

def main(argv=None):
    import argparse,json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='print the full machine-readable inventory')
    args = parser.parse_args(argv)
    value = report()
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value['meaning'])
        for row in value['versions']:
            case_count = sum(len(corpus['case_ids']) for corpus in row['fixtures'])
            print(f"Pine v{row['language_version']}: {len(row['apis'])} admitted overloads, "
                  f"{len(row['references'])+len(row['computed_references'])} series references, {case_count} fixture cases")
        print('Special-form constraints and verification links: docs/CAPABILITIES.md')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
