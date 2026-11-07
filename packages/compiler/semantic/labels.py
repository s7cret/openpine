"""Install versioned Pine drawing subsets."""
from packages.contracts import (
    box_borders, box_text, boxes, chart_points, drawing_xloc, drawing_xloc_mutations, label_styles, label_text, label_yloc,
    labels, linefills, lines, line_extensions,
    line_styles, polylines, tables,
)
from .colors import signature
from .specification import ROOT, SpecError


def install(catalog):
    if 'close' not in catalog.references:
        return
    version = catalog.language_version
    catalog.color_constants.update(line_extensions.constants(version))
    catalog.color_constants.update(tables.constants(version))
    catalog.color_constants.update(drawing_xloc.constants(version))
    catalog.color_constants.update(line_styles.constants(version))
    catalog.color_constants.update(label_styles.constants(version))
    catalog.color_constants.update(label_text.constants(version))
    catalog.color_constants.update(box_text.tokens(version))
    for profile in (labels, lines, line_extensions, boxes, tables, linefills, drawing_xloc,
                    chart_points, polylines, drawing_xloc_mutations, line_styles, box_borders,
                    label_styles, label_yloc, label_text, box_text):
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
                    or api['operation'] in chart_points.DRAWING_CONSTRUCTORS
                    or api['operation'] in (polylines.OVERLAY_CONSTRUCTOR, polylines.STYLED_CONSTRUCTOR, tables.OVERLAY_CONSTRUCTOR)
                    or api['operation'] in (*line_styles.CONSTRUCTORS, *box_borders.CONSTRUCTORS, *box_text.CONSTRUCTORS,
                                            *label_styles.CONSTRUCTORS, *label_yloc.CONSTRUCTORS, *label_text.CONSTRUCTORS)):
                catalog.signatures[api['name']].append(entry)
            else:
                catalog.signatures[api['name']] = [entry]
