"""Legacy consumer safety against the shared transport/admission implementation."""
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = runpy.run_path(str(ROOT / 'bin/lib/runtime_tmux.py'))
ERROR = API['TargetError']


@pytest.fixture
def endpoint():
    # Short isolated socket, never the operator's default tmux server.
    with tempfile.TemporaryDirectory(prefix='r2t-', dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as value:
        path = Path(value) / 'fixture.sock'
        with socket.socket(socket.AF_UNIX) as stream:
            stream.bind(str(path))
            path.chmod(0o600)
            yield path


def fake_transport(path, rows, *, changed=None):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[1:3] == ['-S', str(path)]
        assert 'TMUX' not in kwargs['env'] and 'TMUX_PANE' not in kwargs['env']
        output = rows if changed is None or len(calls) == 1 else changed
        return subprocess.CompletedProcess(argv, 0, output, '')
    return API['Transport'](path, binary='/fixture/tmux', run=run), calls


@pytest.mark.parametrize('operation', ['input', 'split', 'close', 'kill-session', 'capture', 'attach'])
def test_legacy_action_rejects_global_standalone_without_name_prefix(endpoint, operation):
    transport, calls = fake_transport(endpoint, '$1\tAlpha\t%4\t400\n')
    proof = {'live': {'pane_id': '%4', 'pid': 400, 'process_start': 'start', 'ended': False}}
    with pytest.raises(ERROR, match='^LEGACY_TARGET_IS_GLOBAL$'):
        API['legacy_admission'](transport, endpoint.parent, session='Alpha', operation=operation,
            api={'process_start': lambda pid, **_: 'start'}, proof_reader=lambda *_, **__: [proof])
    assert len(calls) == 1  # No input/split/kill command was issued.


def test_normal_legacy_target_continues_beside_rejected_global_target(endpoint):
    proof = {'live': {'pane_id': '%4', 'pid': 400, 'process_start': 'start'}}
    transport, calls = fake_transport(endpoint, '$2\tGamma\t%5\t500\n')
    result = API['legacy_admission'](transport, endpoint.parent, session='Gamma', operation='input',
        api={'process_start': lambda pid, **_: 'start'}, proof_reader=lambda *_, **__: [proof])
    assert result['allowed'] is True and result['panes'][0]['session'] == 'Gamma'
    assert result['argv'] == ['/fixture/tmux', '-S', str(endpoint)]
    assert len(calls) == 2


def test_same_pane_number_on_other_server_is_not_owner_evidence(endpoint):
    transport, _ = fake_transport(endpoint, '$2\tGamma\t%4\t500\n')
    result = API['legacy_admission'](transport, endpoint.parent, pane='%4', operation='capture',
        api={'process_start': lambda pid, **_: 'start'},
        proof_reader=lambda *_, **__: [{'live': {'pane_id': '%4', 'pid': 400, 'process_start': 'start'}}])
    assert result['allowed'] is True


def test_session_kill_checks_global_standalone_in_another_pane(endpoint):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        output = ('$1\tAlpha\t%1\t100\n' if len(calls) == 1 else
                  '$1\tAlpha\t%1\t100\n$1\tAlpha\t%4\t400\n')
        return subprocess.CompletedProcess(argv, 0, output, '')
    transport = API['Transport'](endpoint, binary='/fixture/tmux', run=run)
    with pytest.raises(ERROR, match='^LEGACY_TARGET_IS_GLOBAL$'):
        API['legacy_admission'](transport, endpoint.parent, pane='%1', operation='kill-session',
            api={'process_start': lambda pid, **_: 'start'},
            proof_reader=lambda *_, **__: [{'live': {'pane_id': '%4', 'pid': 400, 'process_start': 'start'}}])
    assert all('kill-session' not in args for args in calls)


@pytest.mark.parametrize('changed', ['$1\tAlpha\t%4\t401\n', '$2\tAlpha\t%4\t400\n'])
def test_pane_or_session_replaced_during_proof_scan_is_refused(endpoint, changed):
    transport, _ = fake_transport(endpoint, '$1\tAlpha\t%4\t400\n', changed=changed)
    with pytest.raises(ERROR, match='^TMUX_TARGET_CHANGED$'):
        API['legacy_admission'](transport, endpoint.parent, session='Alpha', operation='input',
            api={'process_start': lambda pid, **_: 'start'}, proof_reader=lambda *_, **__: [])


def test_server_pid_must_match_inherited_custom_transport(endpoint):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, str(endpoint) + '\t123\n', '')
    with pytest.raises(ERROR, match='^LEGACY_TMUX_TRANSPORT_UNAVAILABLE$'):
        API['legacy_transport'](run=run, binary='/fixture/tmux', environ={'TMUX': str(endpoint) + ',124,0'})
    result = API['legacy_transport'](run=run, binary='/fixture/tmux', environ={'TMUX': str(endpoint) + ',123,0'})
    assert result.public()['server_pid'] == 123


def test_global_endpoint_is_stable_across_epoch_and_deep_home(tmp_path):
    home = tmp_path / ('a' * 100) / ('b' * 100)
    base = Path('/private/tmp' if Path('/private/tmp').is_dir() else tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix='r2g-', dir=base) as value:
        path = API['global_socket'](home, 'instance', base=Path(value))
        assert path == API['global_socket'](home, 'instance', base=Path(value))
        assert len(os.fsencode(path)) < 104
        assert path != API['global_socket'](home, 'other-instance', base=Path(value))


def test_legacy_bootstrap_with_no_server_is_readonly_and_target_is_unavailable(endpoint):
    base=endpoint.parent
    def never_run(*args, **kwargs):
        raise AssertionError('No server should be started or queried')
    transport=API['legacy_transport'](run=never_run,binary='/fixture/tmux',environ={'TMUX_TMPDIR':str(base)})
    assert transport.public()['server_pid'] is None
    assert transport.argv==['/fixture/tmux','-S',str(base/('tmux-'+str(os.getuid()))/'default')]
    assert not (base/('tmux-'+str(os.getuid()))).exists()
    with pytest.raises(ERROR,match='^TMUX_TARGET_UNAVAILABLE$'):
        transport.command('list-sessions')


def test_socket_symlink_and_replacement_are_refused(endpoint):
    link = endpoint.with_name('alias.sock')
    link.symlink_to(endpoint)
    with pytest.raises(ERROR, match='^TMUX_SOCKET_UNSAFE$'):
        API['Transport'](link, binary='/fixture/tmux')
    transport, _ = fake_transport(endpoint, '')
    endpoint.unlink()
    with socket.socket(socket.AF_UNIX) as replacement:
        replacement.bind(str(endpoint)); endpoint.chmod(0o600)
        with pytest.raises(ERROR, match='^TMUX_SOCKET_CHANGED$'):
            transport.command('list-sessions')


def test_pure_legacy_home_needs_no_global_config_or_mutex(tmp_path, monkeypatch):
    for key in ('AGENTSTACK_CLIENT_CONFIG', 'AGENTSTACK_GLOBAL_SERVER_CONFIG'):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / 'absent'
    assert API['global_proofs'](home) == []
    assert not home.exists()
    home.mkdir(mode=0o755)
    home.chmod(0o755)
    assert API['global_proofs'](home) == []
    assert not list(home.iterdir())


def test_partial_global_root_is_not_proof_of_absence(tmp_path, monkeypatch):
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    clients = home / 'clients'; clients.mkdir(mode=0o700)
    (clients / 'partial').mkdir(mode=0o700)
    with pytest.raises(RuntimeError):
        API['global_proofs'](home)
    assert not list((clients / 'partial').iterdir())  # No new mutex or recovery journal.


def test_capability_cli_does_not_select_an_owner_or_echo_secrets(monkeypatch, capsys):
    client = runpy.run_path(str(ROOT / 'bin/lib/runtime_client.py'))
    monkeypatch.setenv('CHILD_REGISTRATION_TOKEN', 'not-public')
    assert client['main'](['tmux-capabilities']) == 0
    value = json.loads(capsys.readouterr().out)
    assert value == {'contract': 'orrery-tmux-target-v1',
                     'capabilities': ['legacy-transport', 'legacy-target-admission']}
    assert 'not-public' not in json.dumps(value)


def test_actual_isolated_label_resolves_to_explicit_socket():
    binary = __import__('shutil').which('tmux')
    if binary is None: pytest.skip('tmux unavailable')
    with tempfile.TemporaryDirectory(prefix='r2l-', dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as value:
        env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_PANE')}
        env['TMUX_TMPDIR'] = value
        command = [binary, '-L', 'fixture', '-f', '/dev/null']
        subprocess.run([*command, 'new-session', '-d', '-s', 'Alpha', 'sleep 60'], env=env, check=True, capture_output=True)
        try:
            proof = subprocess.run([*command, 'display-message', '-p', '#{socket_path},#{pid},0'], env=env, check=True, capture_output=True, text=True).stdout.strip()
            transport = API['legacy_transport'](environ={'TMUX': proof}, binary=binary)
            assert transport.command('has-session', '-t', '=Alpha').returncode == 0
            assert transport.argv[1] == '-S'
        finally:
            subprocess.run([*command, 'kill-server'], env=env, capture_output=True, check=False)
