"""Admit the fixed-symbol subset of pinned corporate-action signatures."""
from dataclasses import replace
import re

from packages.compiler.frontend import parser
from packages.contracts import CallContract, ValueType
from packages.contracts import financial
from packages.contracts.corporate import FIELDS, PARAMETERS, field_names
from .specification import value_sha


def install(catalog):
    version = catalog.language_version
    if version < 4:
        return
    families = (*FIELDS, 'financial', 'currency_rate') if version >= 5 else (*FIELDS, 'financial')
    for family in families:
        name = family if version == 4 else 'request.' + family
        row, = [r for r in catalog.snapshot.references[version]['entries'] if r['name'] == name and r['category'] == 'functions']
        names = [p['name'] for p in row['parameters']]
        expected = (*PARAMETERS, 'currency') if version >= 5 and family != 'splits' else PARAMETERS
        if family == 'financial':
            expected = (*financial.PARAMETERS, 'currency') if version >= 5 else financial.PARAMETERS
        if family == 'currency_rate':
            expected = ('from','to','ignore_invalid_currency')
        if tuple(names) != expected:
            raise ValueError('Pinned corporate-action signature changed.')
        operation = 'corporate.' + family
        oid = f'overload.openpine.v{version}.{operation}'
        defaults = {'gaps':'off','lookahead':'off','ignore_invalid_symbol':False,'currency':''}
        if family == 'currency_rate':
            defaults = {'ignore_invalid_currency':False}
        elif family == 'dividends':
            defaults['field'] = 'dividends.gross'
        elif family == 'earnings':
            defaults['field'] = 'earnings.actual'
        params = [{'name':n,'parameter_id':oid+'.'+n,
                   'requiredness':{'state':'optional' if n in defaults else 'required'},
                   'default':{'kind':'literal','value':defaults[n]} if n in defaults else {'kind':'none'}} for n in names]
        contract = CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,
                                tuple(p['parameter_id'] for p in params),
                                tuple(('bool',) if n in ('ignore_invalid_symbol','ignore_invalid_currency') else ('string',) for n in names),
                                ('simple', *('const' for n in names[1:])),None,(),ValueType('float','series'))
        catalog.contracts[oid] = contract
        catalog.signatures[name] = [({'parameters':params,'binding':{'named':True},'effects':[]},contract)]


def bind(binder, call, signature, contract, top_level):
    family = contract.operation.split('.')[1]
    names = [p['name'] for p in signature['parameters']]
    args = []
    options = {}
    for i, arg in enumerate(call.arguments):
        key = arg.name or (names[i] if i < len(names) else None)
        expr = arg.expression
        if family == 'financial' and key in ('period', 'financial_id'):
            if not isinstance(expr, parser.Literal) or type(expr.value) is not str:
                binder.fail('PROFILE_UNCOVERED','ACTION_ARGUMENT','Use a literal financial metric and period.',arg.span)
            options[key] = expr.value
        if key in ('field','gaps','lookahead'):
            allowed = ([family+'.'+f for f in field_names(family, binder.catalog.language_version)] if key=='field'
                       else ['barmerge.'+key+'_'+suffix for suffix in ('on','off')])
            if not isinstance(expr, parser.Reference) or expr.name not in allowed:
                binder.fail('PROFILE_UNCOVERED','ACTION_ARGUMENT',f'Expected one of {allowed}.',arg.span)
            expr = parser.Literal(expr.name if key=='field' else expr.name.rsplit('_',1)[1],expr.span)
        elif key == 'currency' and isinstance(expr,parser.Reference) and expr.name == 'syminfo.currency':
            expr = parser.Literal('',expr.span)
        elif key in ('from','to','currency') and isinstance(expr,parser.Reference) and re.fullmatch(r'currency\.[A-Z]{3,4}',expr.name):
            expr = parser.Literal(expr.name.split('.')[1],expr.span)
        if key in ('from','to','ignore_invalid_currency','ignore_invalid_symbol','currency') and not isinstance(expr,parser.Literal):
            binder.fail('PROFILE_UNCOVERED','ACTION_ARGUMENT','Use a literal corporate-action option.',arg.span)
        args.append(replace(arg,expression=expr))
    if ('financial_id' in options and 'period' in options and
            not financial.supported(options['financial_id'], options['period'], binder.catalog.language_version)):
        binder.fail('PROFILE_UNCOVERED','ACTION_ARGUMENT','Unsupported financial metric and reporting period.',call.span)
    call = replace(call,arguments=tuple(args))
    return binder.call(call,tuple(binder.expression(a.expression) for a in args),signature,contract,top_level)
