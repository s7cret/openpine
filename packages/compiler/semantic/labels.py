"""Install versioned Pine drawing subsets."""
from packages.contracts import boxes, chart_points, drawing_xloc, labels, linefills, lines, line_extensions, polylines, tables
from .colors import signature
from .specification import ROOT, SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    catalog.color_constants.update(line_extensions.constants(version))
    catalog.color_constants.update(tables.constants(version))
    catalog.color_constants.update(drawing_xloc.constants(version))
    for profile in (labels, lines, line_extensions, boxes, tables, linefills, drawing_xloc, chart_points, polylines):
        try:
            profile.admit(ROOT, catalog.snapshot, version)
        except ValueError as error:
            raise SpecError('SPEC_EVIDENCE', str(error)) from error
        registered = profile.contracts(version)
        for api in profile.apis(version):
            contract = registered[api['id']]
            catalog.contracts[contract.overload_id] = contract
            entry = (signature(api, contract), contract)
            if (api['operation'] == 'line.new_extended' or api['operation'] in drawing_xloc.OPERATIONS
                    or api['operation'] in chart_points.DRAWING_CONSTRUCTORS):
                catalog.signatures[api['name']].append(entry)
            else:
                catalog.signatures[api['name']] = [entry]
