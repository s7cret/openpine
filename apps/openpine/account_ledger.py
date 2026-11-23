"""Show settled cash, fees and marked exposure in the chosen account currency."""
import argparse
from pathlib import Path
import apsw

from apps.openpine.bootstrap import local_services
from packages.contracts.wire import canonical, read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('.runtime/jobs'))
    parser.add_argument('--id', required=True)
    parser.add_argument('--currency', required=True)
    parser.add_argument('--fx-rates', type=Path, help='Offline FX snapshot for jobs without a compiled FX request')
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args(argv)
    try:
        if not (args.state_dir/'catalog.sqlite3').is_file():
            raise ValueError('State directory has no existing catalogue.')
        with local_services(args.state_dir) as services:
            rates = read_json(args.fx_rates) if args.fx_rates else None
            value = services.queries.account_ledger(args.id, args.currency, args.start, args.limit, report_rates=rates)
    except (OSError, ValueError, apsw.Error) as error:
        print(canonical(dict(status='rejected', message=str(error))).decode())
        return 2
    print(canonical(value).decode())
    return 0
