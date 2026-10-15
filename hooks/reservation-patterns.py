"""Dependency-free single-pattern gitignore matching for the legacy edit guard.

Mail remains the semantic authority. tests/test_reservation_pattern_parity.py
compares this small adapter with its actual PathSpec matcher; no service package
or dependency installation is required by an editing client's Python.
"""
from __future__ import annotations

import fnmatch
from functools import lru_cache
import re
import unicodedata


def normalize(value: str) -> str:
    return unicodedata.normalize('NFC', value.replace('\\', '/').lstrip('/'))


def virtual(value: str) -> bool:
    return value.startswith(('tool://', 'resource://', 'service://'))


@lru_cache(maxsize=1024)
def segment_matches(pattern: str, name: str) -> bool:
    # Git also accepts ^ as bracket negation; fnmatch uses ! only.
    pattern = re.sub(r'\[\^', '[!', pattern)
    return fnmatch.fnmatchcase(name, pattern)


def matches(pattern: str, target: str) -> bool:
    if target.startswith('./'):
        target = target[2:]
    pattern = pattern.rstrip()
    if not pattern or pattern.startswith(('#', '!')):
        return False
    parts = pattern.split('/')
    directory = not parts[-1]
    if len(parts) == 1 or (len(parts) == 2 and directory):
        if parts[0] != '**':
            parts.insert(0, '**')
    if directory:
        parts[-1] = '**'
    parts = [part for i, part in enumerate(parts) if part != '**' or i == 0 or parts[i - 1] != '**']
    if parts == ['**'] or parts == ['**', '*']:
        return '/' in target if directory else bool(target)
    if parts == ['**', '*', '**']:
        return '/' in target
    names = target.split('/')

    @lru_cache(maxsize=None)
    def visit(i: int, j: int) -> bool:
        if parts[i] == '**':
            if i == len(parts) - 1:
                # The preceding slash must exist (dir/** excludes dir itself).
                return j < len(names)
            return any((k == j or '/'.join(names[j:k])) and visit(i + 1, k)
                       for k in range(j, len(names)))
        if j >= len(names) or not segment_matches(parts[i], names[j]):
            return False
        if parts[i] == '*' and not names[j]:
            return False
        if i == len(parts) - 1:
            if parts[i] == '*':
                return j == len(names) - 1 or (j == len(names) - 2 and names[-1] == '')
            return True  # A named directory includes its descendants.
        return visit(i + 1, j + 1)

    return visit(0, 0)


def patterns_overlap(a: str, b: str) -> bool:
    if virtual(a) or virtual(b):
        return virtual(a) and virtual(b) and a.strip() == b.strip()
    a, b = normalize(a), normalize(b)
    return matches(a, b) or matches(b, a)
