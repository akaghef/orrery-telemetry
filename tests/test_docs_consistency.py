"""Facts that user docs state about the implementation, checked against it.

Each check exists because a reader found the docs contradicting the code
(2026-09-03 first-look review): the ORRERY Mail port in AGENTS.md, the number
of approval prompts in install.md, the hook count and guide list in the
English README. The compact README now routes standalone setup facts to
getting-started and the complete document lists to the indexes.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_pending_changelog_items_use_fragments() -> None:
    changelog = _read("CHANGELOG.md")
    import runpy

    validator = runpy.run_path(str(ROOT / "scripts/changelog-release.py"))["validate_changelog"]
    try:
        validator(changelog)
    except ValueError as error:
        raise AssertionError(str(error)) from error
    assert "[changelog.d/](changelog.d/)" in changelog
    assert (ROOT / "changelog.d/README.txt").is_file()


def test_pending_changelog_guard_rejects_direct_unreleased(monkeypatch) -> None:
    import pytest

    monkeypatch.setitem(globals(), "_read", lambda _: "## Unreleased\n\n### New\n")
    with pytest.raises(AssertionError, match="use changelog.d"):
        test_pending_changelog_items_use_fragments()


def _default_mail_port() -> str:
    # explicit > previous env.sh > this default (hooks/project-context.sh).
    match = re.search(
        r'^resolve_setting MCP_URL AGENTSTACK_MCP_URL http://127\.0\.0\.1:(\d+)/mcp kept$',
        INSTALLER,
        re.M,
    )
    assert match, "installer default MCP_URL not found"
    return match.group(1)


def test_agents_md_probes_the_installer_default_mail_port() -> None:
    port = _default_mail_port()
    agents = _read("AGENTS.md")
    assert f"lsof -i :{port}" in agents
    assert f"already listening on {port}" in agents
    stale = {"8765", "18765"} - {port}
    for other in stale:
        assert f":{other}" not in agents, f"AGENTS.md still names port {other}"


def _approval_prompt_count() -> int:
    # One `read -r reply` per confirm_* function, and the managed-setup
    # confirmation runs once per run_managed_setup call.
    confirms = len(re.findall(r"^confirm_\w+\(\) \{$", INSTALLER, re.M))
    managed_runs = len(re.findall(r'^\s*run_managed_setup "', INSTALLER, re.M))
    assert "confirm_managed_setup()" not in INSTALLER
    assert "The engine owns the single consent prompt" in INSTALLER
    return confirms + managed_runs


def test_docs_share_the_actual_conditional_consent_contract() -> None:
    assert _approval_prompt_count() == 4  # Maximum approval sites, not a fixed prompt count.
    for name in ('AGENTS.md', 'CLAUDE.md', 'docs/install.en.md', 'docs/getting-started.en.md'):
        assert 'instruction-migration.en.md#consent' in _read(name)
        assert 'four typed confirmations' not in _read(name)
        assert 'four approvals' not in _read(name)
    for name in ('docs/install.md','docs/getting-started.md'):
        assert 'instruction-migration.md#consent' in _read(name)
    contract = _read('docs/instruction-migration.md')
    assert '最大4つ' in contract and '`[Y/n]`、Enter は消す' in contract
    assert '新しい利用者 file を作るだけなら同意は不要' in contract


def _event_hook_count() -> int:
    template = json.loads(_read("hooks/settings.template.json"))
    commands = set()
    for matchers in template["hooks"].values():
        for matcher in matchers:
            for hook in matcher["hooks"]:
                commands.add(hook["command"])
    return len(commands)


def test_hook_count_matches_the_settings_template() -> None:
    count = _event_hook_count()
    assert f"event hook は{count}件" in _read("docs/hooks.md")
    assert f"Claude event hook {count}件" in _read("docs/getting-started.md")
    words = {8: "Eight"}
    assert f"{words[count]} Claude event hooks" in _read("docs/getting-started.en.md")


def test_english_readme_lists_every_guide_the_japanese_readme_lists() -> None:
    ja_pattern = re.compile(r"^\|[^\n]*\[[^\]]+\]\((docs/[\w-]+\.md)\) \|", re.M)
    en_pattern = re.compile(r"^\|[^\n]*\[[^\]]+\]\((docs/[\w-]+(?:\.en)?\.md)\) \|", re.M)
    ja = ja_pattern.findall(_read("README.md"))
    en = [path.replace(".en.md", ".md") for path in en_pattern.findall(_read("README.en.md"))]
    assert ja, "no guide table in README.md"
    assert set(ja) == set(en), sorted(set(ja) ^ set(en))


def test_both_readmes_route_to_guides_and_standalone_setup() -> None:
    for name, setup, index in (("README.md", "docs/getting-started.md", "docs/README.md"),
                               ("README.en.md", "docs/getting-started.en.md", "docs/README.en.md")):
        text = _read(name)
        for needle in ("https://raw.githubusercontent.com/gyroid-eth/orrery/master/scripts/get.sh",
                       "Your first flight", "Show help map", "Full tour", setup, index):
            assert needle in text, (name, needle)
        for needle in ("--dry-run", "agentstack-doctor", "agentstack-selftest", "/delegate", "http://127.0.0.1:8770/"):
            assert needle in _read(setup), (setup, needle)
    for image in ("img/deck.jpg", "img/network.jpg", "img/new-agent.jpg"):
        assert image in _read("docs/getting-started.md"), image
        assert image in _read("docs/getting-started.en.md"), image


def test_documentation_indexes_cover_the_actual_tree() -> None:
    import runpy

    checker = runpy.run_path(str(ROOT / "scripts/check_docs_index.py"))
    assert checker["check"](ROOT) == []


def test_api_docs_state_the_served_api_generation_and_when_it_changes() -> None:
    # 2026-09-30: ORRERY cockpit decides whether orrery-telemetry is new
    # enough from `api`, not from the release date, so the docs must show the
    # generation the server really serves and the rule for raising it.
    import ast
    source = _read("dashboard/server.py")
    definitions = [node.value.value for node in ast.parse(source).body
                   if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "API_GENERATION" for target in node.targets)
                   and isinstance(node.value, ast.Constant) and type(node.value.value) is int]
    assert len(definitions) == 1, "one API_GENERATION source is required"
    assert '"api": API_GENERATION' in source, "/api/version must use the shared generation"
    generation = definitions[0]
    for rel, rule in (("docs/api.md", "互換の世代"), ("docs/api.en.md", "compatibility generation")):
        doc = _read(rel)
        assert f'"api":{generation}}}' in doc, f"{rel} shows another api generation"
        assert rule in doc and "CHANGELOG" in doc, f"{rel} does not say when api goes up"
