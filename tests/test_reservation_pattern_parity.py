"""Core guard semantics checked against the Mail package's real matcher."""
from pathlib import Path
import runpy

import pytest
from agentstack_mail.app import _patterns_overlap

ROOT = Path(__file__).resolve().parents[1]
overlap = runpy.run_path(str(ROOT / 'hooks/reservation-patterns.py'))['patterns_overlap']

PATTERNS = ['docs/**/*.md', 'docs/', '**/note.md', '/docs/*.md', 'docs\\*.md',
    'cafe\u0301/**', 'note.md', '*.md', '*', '**', '**/', '*/', 'docs/*',
    'docs/**', 'docs/**/**/note.md', 'docs/?ote.md', 'docs/[ab].md',
    'docs/[!a].md', 'docs/[^a].md', 'docs/[a-c].md', 'docs/[].md',
    'docs/**/deep/*.md', '#comment', '!docs/**', ' note.md', 'note.md ',
    './note.md', 'docs/a**b.md', 'docs/[]a].md']
TARGETS = ['note.md', ' note.md', './note.md', 'docs/note.md', 'docs/deep/note.md',
    'docs/deep/deeper/note.md', 'docs/note.txt', 'docs/', 'docs', 'other/note.md',
    'other/docs/note.md', 'docs/a.md', 'docs/b.md', 'docs/c.md', 'docs/d.md',
    'docs/].md', 'docs/[].md', 'docs/a/b.md', 'caf\u00e9/note.md', 'empty',
    'docs/aab.md', 'docs/ab.md', 'docs//note.md', 'docs/.md']


@pytest.mark.parametrize('pattern', PATTERNS)
@pytest.mark.parametrize('target', TARGETS)
def test_core_matches_mail_pathspec(pattern, target):
    assert overlap(pattern, target) == _patterns_overlap(pattern, target)


def test_virtual_and_filesystem_namespaces_are_separate():
    assert not overlap('tool://apply_patch', 'apply_patch')
    assert not overlap('tool://*', 'tool://apply_patch')
    assert overlap('tool://apply_patch', 'tool://apply_patch')


def test_core_matcher_needs_no_site_packages():
    import subprocess
    import sys
    module = str(ROOT / 'hooks/reservation-patterns.py')
    result = subprocess.run([sys.executable, '-S', '-c',
        "import runpy,sys; m=runpy.run_path(sys.argv[1]); assert m['patterns_overlap']('docs/**/*.md','docs/note.md'); assert not m['patterns_overlap']('other/**','docs/note.md'); assert m['patterns_overlap']('docs/[!x][!x][!x].md','docs/a/b.md')",
        module], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


# Replace every subset of the three filename positions with a class, then
# replace every subset of the target positions with slashes. Ranges containing
# slash and malformed/literal classes belong to the same oracle comparison.
CLASS_EXPRESSIONS = ['[!x]', '[^x]', '[.-0]', '[ab]', '[a-c]', '[]a]',
                     '[', '[!]', '[z-a]', '[a/b]']
CLASS_LOCATIONS = [('', ''), ('docs/', 'docs/'),
                   ('docs/**/', 'docs/deep/'), ('docs/pre', 'docs/pre')]


def class_slash_vectors():
    from itertools import product
    for expression, class_mask, location in product(
            CLASS_EXPRESSIONS, range(1, 8), CLASS_LOCATIONS):
        prefix, target_prefix = location
        pattern = prefix + ''.join(expression if class_mask & (1 << i) else c
                                   for i, c in enumerate('aab')) + '.md'
        # Malformed classes and classes containing literal slash can behave as
        # literal names. Checking the pattern itself prevents a false-negative
        # matrix made entirely of targets that could never match those names.
        yield pattern, pattern
        for characters, slash_mask in product(('aab', '000'), range(8)):
            target = target_prefix + ''.join('/' if slash_mask & (1 << i) else c
                                            for i, c in enumerate(characters)) + '.md'
            yield pattern, target


def matching_outcome(matcher, pattern, target):
    import re
    try:
        return matcher(pattern, target)
    except (ValueError, re.error):
        return 'invalid-pattern'


@pytest.mark.parametrize('pattern,target', list(class_slash_vectors()))
def test_classes_across_path_separators_match_mail(pattern, target):
    assert matching_outcome(overlap, pattern, target) == matching_outcome(_patterns_overlap, pattern, target)


@pytest.mark.parametrize('pattern,target', [('*', '[z-a]'), ('docs/**', 'docs/[z-a].md')])
def test_invalid_reverse_class_is_checked_before_a_successful_match(pattern, target):
    assert matching_outcome(_patterns_overlap, pattern, target) == 'invalid-pattern'
    assert matching_outcome(overlap, pattern, target) == 'invalid-pattern'


@pytest.mark.parametrize('expression', ['[!]', '[^]'])
@pytest.mark.parametrize('name', ['!', '^', ']', '[!]', '[^]'])
def test_unterminated_negated_classes_remain_literal(expression, name):
    pattern, target = 'docs/' + expression + '.md', 'docs/' + name + '.md'
    assert matching_outcome(overlap, pattern, target) == matching_outcome(_patterns_overlap, pattern, target)
