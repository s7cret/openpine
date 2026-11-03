"""Retain the pinned coordinate overload and version boundary for lines."""
from packages.contracts.lines import OPERATIONS, apis
from tools.labels_spec import main


if __name__ == '__main__':
    main(kind='line', operations=OPERATIONS, api_for=apis)
