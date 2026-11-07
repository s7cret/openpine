"""Pure bar outputs; positions stay on the source bar until presentation."""
from packages.contracts.markers import apis,presentation
from packages.contracts.colors import invoke
from packages.contracts.model import ValueType

def kernel(operation,PlotEvent,KernelError):
    def run(arguments,context):
        try:
            v=context.language_version;a=next(a for a in apis(v) if a['operation']==operation and len(a['parameters'])==len(arguments))
            values=dict(zip((p['name'] for p in a['parameters']),arguments,strict=True));kind=a['name']
            color=values['color'];textcolor=values.get('textcolor');transp=values.get('transp',0)
            if type(transp) is not int or not 0<=transp<=100:raise ValueError('Transparency requires int 0..100.')
            if color is not None and color.endswith('FF'):color=invoke('colors.new',(color,transp))
            if textcolor is not None and textcolor.endswith('FF'):textcolor=invoke('colors.new',(textcolor,transp))
            location=values.get('location','absolute');raw=values.get('series')
            value=(None if raw is None or raw is False or (not raw and location!='absolute') else float(raw)) if kind.startswith('plot') else None
            meta=dict(color=color,textcolor=textcolor,linewidth=1,style=values.get('style',kind),kind=kind,
                location=location,size=values.get('size','auto'),offset=values['offset'],text=values.get('text',''),char=values.get('char',''))
            if values.get('force_overlay') is True:meta['force_overlay']=True
            return PlotEvent(value,values.get('title',''),presentation(meta,v),output_type=ValueType('float','series'))
        except (ValueError,TypeError) as e:raise KernelError('VISUAL_SIGNAL',str(e),'rule.visual-signals') from e
    return run
