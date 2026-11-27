"""Install named order comments against the pinned function record."""
from dataclasses import replace
from packages.contracts.trade_identity import COMMENTS
from packages.contracts.wire import digest
from .specification import SpecError


def add_comments(catalog, name, signature, base, reference):
    version = catalog.language_version
    comment_names = COMMENTS if name == 'strategy.exit' else COMMENTS[:1]
    available_names = {item['name'] for item in reference['parameters']}
    if not set(comment_names) <= available_names:
        raise SpecError('SPEC_EVIDENCE', f'{name} comment fields differ in v{version}.')
    oid = base.overload_id + '+trade-comments'
    ids = tuple(f'parameter.{oid}.{field}' for field in comment_names)
    params = [{'name': field, 'parameter_id': pid, 'requiredness': {'state':'optional'},
               'default': {'kind':'literal','value':''}} for field,pid in zip(comment_names,ids,strict=True)]
    annotated = replace(base, overload_id=oid, assertion_id=base.assertion_id+'+trade-comments',
        signature_sha256=digest({'base':base.signature_sha256,'reference':digest(reference),'comments':comment_names}),
        parameter_ids=(*base.parameter_ids,*ids), parameter_types=(*base.parameter_types,*(('string',),)*len(ids)),
        qualifier_limits=(*base.qualifier_limits,*('series',)*len(ids)))
    extended = {**signature, 'parameters':[*signature['parameters'],*params]}
    catalog.contracts[oid] = annotated
    catalog.comment_calls[base.overload_id] = extended, annotated
    return extended, annotated
