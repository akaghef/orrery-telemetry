"""Dependency-free single-pattern gitignore matching for the legacy edit guard.

Mail remains the semantic authority. tests/test_reservation_pattern_parity.py
compares this small adapter with its actual PathSpec matcher; no service package
or dependency installation is required by an editing client's Python.
"""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata


def normalize(value: str) -> str:
    return unicodedata.normalize('NFC', value.replace('\\', '/').lstrip('/'))


def virtual(value: str) -> bool:
    return value.startswith(('tool://', 'resource://', 'service://'))


_CLASS = re.compile(r'\[([!^]?)(\]?[^]]*)\]')


def segment_regex(segment: str) -> str:
    """Render basic gitignore tokens; a class may match a path separator.

    Unlike * and ?, PathSpec's bracket expressions do not exclude slash.
    Keep their regex ranges intact, including invalid ranges that must fail
    closed. Backslashes have already become separators in normalize().
    """
    result = []
    position = 0
    while position < len(segment):
        token = segment[position]
        bracket = _CLASS.match(segment, position) if token == '[' else None
        if bracket is not None and bracket[2]:
            result.append('[' + ('^' if bracket[1] else '') + bracket[2] + ']')
            position = bracket.end()
            continue
        result.append({'*': '[^/]*', '?': '[^/]'}.get(token, re.escape(token)))
        position += 1
    return ''.join(result)


@lru_cache(maxsize=1024)
def compile_pattern(pattern: str) -> re.Pattern[str] | None:
    pattern = pattern.rstrip()
    if not pattern or pattern.startswith('#'):
        return None
    # Negation suppresses a match, but Mail still parses and compiles its
    # body. Malformed excluded patterns must reach the same fail-closed path.
    negated = pattern.startswith('!')
    if negated:
        pattern = pattern[1:]
    parts = pattern.split('/')
    directory = pattern != '/' and not parts[-1]
    if not parts[0]:
        parts.pop(0)
    elif len(parts) == 1 or (len(parts) == 2 and directory):
        if parts[0] != '**':
            parts.insert(0, '**')
    if not parts:
        raise ValueError('invalid gitignore pattern: empty body')
    if not parts[-1]:
        parts[-1] = '**'
    parts = [part for i, part in enumerate(parts) if part != '**' or i == 0 or parts[i - 1] != '**']
    if parts == ['**']:
        expression = '/' if directory else '.'
    elif parts == ['**', '*']:
        expression = '.'
    elif parts == ['**', '*', '**']:
        expression = '/'
    else:
        expression = ''
        separator = False
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            if part == '**':
                if index == 0:
                    expression += '^(?:.+/)?'
                elif last:
                    expression += '/'
                else:
                    expression += '(?:/.+)?'
                    separator = True
                continue
            if index == 0:
                expression += '^'
            if separator:
                expression += '/'
            expression += '[^/]+' if part == '*' else segment_regex(part)
            if last:
                expression += '/?$' if part == '*' else '(?:/|$)'
            separator = True
    try:
        compiled = re.compile(expression)
    except re.error as exc:
        raise ValueError('invalid gitignore pattern: ' + str(exc)) from exc
    return None if negated else compiled


def matches(pattern: str, target: str) -> bool:
    if target.startswith('./'):
        target = target[2:]
    compiled = compile_pattern(pattern)
    return compiled is not None and compiled.search(target) is not None


def patterns_overlap(a: str, b: str) -> bool:
    if virtual(a) or virtual(b):
        return virtual(a) and virtual(b) and a.strip() == b.strip()
    a, b = normalize(a), normalize(b)
    # Mail compiles both sides before cross-matching, even if the first
    # match would succeed. Invalid reverse patterns must not be skipped.
    compile_pattern(a)
    compile_pattern(b)
    return matches(a, b) or matches(b, a)
