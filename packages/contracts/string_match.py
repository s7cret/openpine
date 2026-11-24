"""Leftmost greedy matching of a bounded sequence of single-character atoms."""
import re

from .scalar_functions import ScalarError

MAX_PATTERN = 256
META = frozenset(r'\.^$[](){}*+?|-')

class PatternError(ScalarError):
    code = 'REGEX_PATTERN'


def character(pattern, index):
    char = pattern[index]
    if char != '\\':
        return re.escape(char), index + 1
    if index + 1 == len(pattern):
        raise PatternError('Trailing escape in regex.')
    char = pattern[index + 1]
    if char == 'd':
        return r'\d', index + 2
    if char not in META:
        raise PatternError('Unsupported regex escape.')
    return re.escape(char), index + 2


def character_class(pattern, index):
    start = index
    index += 1
    negate = pattern.startswith('^', index)
    index += int(negate)
    items = []
    while index < len(pattern) and pattern[index] != ']':
        if pattern[index] == '[':
            raise PatternError('Nested regex character classes are unsupported.')
        atom, index = character(pattern, index)
        if pattern.startswith('-', index) and index + 1 < len(pattern) and pattern[index + 1] != ']':
            end, index = character(pattern, index + 1)
            atom += '-' + end
        items.append(atom)
    if not items or index == len(pattern):
        raise PatternError('Empty or unclosed regex character class.')
    try:
        matcher = re.compile('[' + ('^' if negate else '') + ''.join(items) + ']', re.ASCII)
    except re.error as error:
        raise PatternError('Invalid regex character range.') from error
    return matcher.fullmatch, index + 1, index - start


def search(source, pattern, budget):
    if len(pattern) > MAX_PATTERN:
        raise ScalarError('Regex exceeds 256 code points.')
    # Anchors are admitted only at the two ends of the sequence.
    anchored = pattern.startswith('^')
    index = int(anchored)
    end_anchor = False
    atoms = []
    while index < len(pattern):
        char = pattern[index]
        if char == '$' and index + 1 == len(pattern):
            end_anchor = True
            break
        cost = 1
        if char == '[':
            matches, index, cost = character_class(pattern, index)
        elif char == '.':
            matches = lambda c: c != '\n'
            index += 1
        else:
            if char in '^$]()*+?{}|':
                raise PatternError('Unsupported regex syntax or misplaced quantifier.')
            literal, index = character(pattern, index)
            matches = re.compile(literal, re.ASCII).fullmatch
        quantifier = ''
        if index < len(pattern) and pattern[index] in '?*+':
            quantifier = pattern[index]
            index += 1
        atoms.append((matches, quantifier, cost))

    size = len(source)
    budget.charge((size + 1) * (1 + sum(cost for _, _, cost in atoms)))
    # Each cell holds the preferred end of a match starting at this position.
    # A consuming branch wins whenever the remaining suffix can match.
    suffix = [-1] * size + [size] if end_anchor else list(range(size + 1))
    for matches, quantifier, _ in reversed(atoms):
        row = [-1] * (size + 1)
        optional = quantifier in ('?', '*')
        if optional:
            row[size] = suffix[size]
        for pos in range(size - 1, -1, -1):
            end = -1
            if matches(source[pos]):
                end = suffix[pos + 1]
                if quantifier in ('*', '+') and row[pos + 1] >= 0:
                    end = row[pos + 1]
            row[pos] = suffix[pos] if end < 0 and optional else end
        suffix = row
    for start in range(1 if anchored else size + 1):
        if suffix[start] >= 0:
            budget.output(suffix[start] - start)
            return source[start:suffix[start]]
    return ''
