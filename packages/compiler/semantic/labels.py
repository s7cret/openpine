"""Install versioned Pine drawing subsets."""
from packages.contracts import boxes, labels, lines, line_extensions, tables
from .colors import signature
from .specification import ROOT, SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    catalog.color_constants.update(line_extensions.constants(version))
    catalog.color_constants.update(tables.constants(version))
    for profile in (labels, lines, line_extensions, boxes, tables):
        try:
            profile.admit(ROOT, catalog.snapshot, version)
        except ValueError as error:
            raise SpecError('SPEC_EVIDENCE', str(error)) from error
        registered = profile.contracts(version)
        for api in profile.apis(version):
            contract = registered[api['id']]
            catalog.contracts[contract.overload_id] = contract
            entry = (signature(api, contract), contract)
            if api['operation'] == 'line.new_extended':
                catalog.signatures['line.new'].append(entry)
            else:
                catalog.signatures[api['name']] = [entry]
