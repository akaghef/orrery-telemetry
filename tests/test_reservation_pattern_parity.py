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
        "import runpy,sys; m=runpy.run_path(sys.argv[1]); assert m['patterns_overlap']('docs/**/*.md','docs/note.md'); assert not m['patterns_overlap']('other/**','docs/note.md')",
        module], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
