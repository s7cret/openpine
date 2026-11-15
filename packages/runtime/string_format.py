"""Pine positional templates and scalar number formats."""

import re

from packages.contracts.scalar_functions import MAX_STRING, ScalarError, format_decimal
from packages.runtime.series import KernelError


SPECIAL = re.compile(r"['{}]")
MASK = re.compile(r"(?P<currency>'\$')?(?P<whole>#?,###|#*0*)(?:\.(?P<fraction>0*#*))?(?P<percent>%)?")
DEFAULT = '#,###.###'
PRESETS = {'integer': '#,###', 'currency': "'$'#,###.00", 'percent': '#,###%'}


class FormatError(ValueError):
    pass


def number(value, pattern, budget):
    pattern = PRESETS.get(pattern, pattern)
    match = MASK.fullmatch(pattern)
    if match is None or not match['whole'] or len(match['whole']) > 10:
        raise FormatError('Unsupported str.format number pattern.')
    fraction = match['fraction']
    if fraction is not None and not 1 <= len(fraction) <= 10:
        raise FormatError('Numeric masks allow one to ten fractional placeholders.')
    fraction = fraction or ''
    if value is None:
        return 'NaN'
    if type(value) not in (int, float):
        raise FormatError('The number modifier requires an int or float value.')

    text = format_decimal(value, len(fraction), fraction.count('0'), budget, percent=bool(match['percent']))
    negative = text.startswith('-')
    whole, dot, tail = text.removeprefix('-').partition('.')
    if ',' in match['whole']:
        whole = format(int(whole), ',')
    else:
        whole = whole.zfill(match['whole'].count('0'))
    sign = '-' if negative else ''
    prefix = '$' if match['currency'] else ''
    suffix = '%' if match['percent'] else ''
    return sign + prefix + whole + dot + tail + suffix


def placeholder(text, values, budget):
    index_text, *options = (item.strip() for item in text.split(',', 2))
    if not index_text.isascii() or not index_text.isdecimal():
        raise FormatError('A str.format placeholder requires a nonnegative integer index.')
    index_text = index_text.lstrip('0') or '0'
    if len(index_text) > 2 or int(index_text) >= len(values):
        return '{' + text + '}'
    value = values[int(index_text)]
    if options:
        if options[0] != 'number':
            raise FormatError('Only the number modifier is supported in str.format.')
        return number(value, options[1] if len(options) == 2 else DEFAULT, budget)
    if value is None:
        return 'NaN'
    if type(value) is bool:
        return 'true' if value else 'false'
    if type(value) is str:
        return value
    return number(value, DEFAULT, budget)


def render(template, values, budget):
    budget.charge(1 + len(values))
    for value in (template, *values):
        if type(value) is str:
            if len(value) > MAX_STRING:
                raise ScalarError('Scalar input string exceeds 4096 code points.')
            budget.charge(len(value))
    if template is None:
        return None

    parts = []
    size = 0

    def append(piece):
        nonlocal size
        size += len(piece)
        if size > MAX_STRING:
            raise ScalarError('Scalar string exceeds 4096 code points before allocation.')
        budget.charge(len(piece))
        parts.append(piece)

    quoted = False
    position = 0
    while match := SPECIAL.search(template, position):
        index = match.start()
        append(template[position:index])
        character = match[0]
        position = index + 1
        if character == "'":
            if template.startswith("'", position):
                append("'")
                position += 1
            else:
                quoted = not quoted
        elif quoted:
            append(character)
        elif character == '{':
            end = template.find('}', position)
            if end < 0 or template.find('{', position, end) >= 0:
                raise FormatError('Unbalanced braces in str.format template.')
            append(placeholder(template[position:end], values, budget))
            position = end + 1
        else:
            raise FormatError('Unbalanced braces in str.format template.')
    if quoted:
        raise FormatError('Unclosed apostrophe quote in str.format template.')
    append(template[position:])
    return ''.join(parts)


def kernel(arguments, context):
    try:
        return render(arguments[0], arguments[1:], context.scalar_budget)
    except ScalarError as error:
        raise KernelError('SCALAR_LIMIT', str(error), 'rule.scalar') from error
    except FormatError as error:
        raise KernelError('STRING_FORMAT', str(error), 'rule.scalar') from error
