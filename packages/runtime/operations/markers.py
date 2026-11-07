"""Pure bar outputs; positions stay on the source bar until presentation."""

from packages.contracts.markers import ARROW, ARROW_HEIGHTS, WINDOW, apis, presentation
from packages.contracts.colors import invoke
from packages.contracts.model import ValueType


def kernel(operation, PlotEvent, KernelError):
    parameters = {(v, len(api['parameters'])): tuple(p['name'] for p in api['parameters'])
                  for v in range(1,7) for api in apis(v) if api['operation'] == operation}
    kind = operation.removeprefix('markers.')

    def run(arguments, context):
        try:
            version = context.language_version
            values = dict(zip(parameters[version, len(arguments)], arguments, strict=True))
            transp = values.get('transp', 0)
            if type(transp) is not int or not 0<=transp<=100:
                raise ValueError('Transparency requires int 0..100.')
            raw = values.get('series')
            if kind == ARROW:
                value = None if raw is None else float(raw)
                color = values['colorup'] if raw and raw > 0 else values['colordown'] if raw else None
                location = 'relative'
            else:
                color = values['color']
                location = values.get('location', 'absolute')
                value = (None if raw is None or raw is False or (not raw and location!='absolute') else float(raw)) if kind.startswith('plot') else None
            textcolor = values.get('textcolor')
            if color is not None and color.endswith('FF'):
                color = invoke('colors.new', (color,transp))
            if textcolor is not None and textcolor.endswith('FF'):
                textcolor = invoke('colors.new', (textcolor,transp))
            meta = dict(color=color, textcolor=textcolor, linewidth=1, style=values.get('style',kind), kind=kind,
                        location=location, size=values.get('size','auto'), offset=values['offset'],
                        text=values.get('text',''), char=values.get('char',''))
            if kind == ARROW:
                meta.update((name,values[name]) for name in ARROW_HEIGHTS)
            if values.get(WINDOW) is not None:
                meta[WINDOW] = values[WINDOW]
            if values.get('force_overlay') is True:
                meta['force_overlay'] = True
            return PlotEvent(value, values.get('title',''), presentation(meta,version), output_type=ValueType('float','series'))
        except (ValueError,TypeError) as error:
            raise KernelError('VISUAL_SIGNAL', str(error), 'rule.visual-signals') from error

    return run
