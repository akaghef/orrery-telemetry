"""Ephemeral tmux transports and legacy target admission from existing truth.

No socket registry, mode selector, SQLite reads or new client journal. The CLI
is deliberately useful to an older consumer before global launch is enabled.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import time

SPEC = json.loads(Path(__file__).with_name('runtime_tmux_contract.json').read_text())
CONTRACT = SPEC['contract']
CAPABILITIES = SPEC['capabilities']
OPERATIONS = tuple(SPEC['operations'])


class TargetError(RuntimeError):
    pass


def result_schema(name):
    fields = SPEC['result_fields'][name]
    return {'type': 'object', 'additionalProperties': False, 'required': fields,
            'properties': {key: SPEC['result_properties'][key] for key in fields}}


def print_result(value, name):
    root = Path(__file__).resolve().parents[2]
    installed = Path(__file__).with_name('schema_contract.py')
    validator = runpy.run_path(str(installed if installed.is_file() else
        root / 'packages/agentstack_mail/src/agentstack_mail/schema_contract.py'))
    validator['validate_schema'](value, result_schema(name))
    print(json.dumps(value, ensure_ascii=False))


def private_directory(path, *, create=False):
    path = Path(path)
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or path.resolve() != path):
        raise TargetError('TMUX_DIRECTORY_UNSAFE')
    return path


def global_socket(client_home, mail_instance_id, *, base=None, create=False):
    """One deterministic endpoint; ``base`` is an in-process fixture seam only."""
    home = Path(client_home)
    if not home.is_absolute() or home.resolve() != home:
        raise TargetError('TMUX_CLIENT_HOME_INVALID')
    if not isinstance(mail_instance_id, str) or not mail_instance_id:
        raise TargetError('TMUX_INSTANCE_REQUIRED')
    directory = (Path('/tmp').resolve() / ('orrery-tmux-' + str(os.getuid()))
                 if base is None else Path(base))
    digest = hashlib.sha256(json.dumps([str(home), mail_instance_id],
                                      separators=(',', ':')).encode()).hexdigest()
    path = directory / (digest + '.sock')
    # macOS sockaddr_un is the narrower supported boundary (104 bytes incl NUL).
    if len(os.fsencode(path)) >= 104:
        raise TargetError('TMUX_SOCKET_PATH_TOO_LONG')
    if create or directory.exists() or directory.is_symlink():
        private_directory(directory, create=create)
    if path.exists() or path.is_symlink():
        validate_socket(path)
    return path


def validate_socket(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise TargetError('TMUX_SOCKET_UNSAFE')
    private_directory(path.parent)
    info = path.lstat()
    # tmux itself creates 0660 sockets. The UID-owned 0700 parent prevents
    # group traversal; never chmod somebody's existing server socket.
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o007 or info.st_mode & 0o600 != 0o600):
        raise TargetError('TMUX_SOCKET_UNSAFE')
    return info.st_dev, info.st_ino


class Transport:
    def __init__(self, socket_path, *, binary=None, run=None, allow_absent=False):
        self.path = Path(socket_path)
        self.binary = binary or shutil.which('tmux')
        if not self.binary:
            raise TargetError('TMUX_UNAVAILABLE')
        self.run = subprocess.run if run is None else run
        if allow_absent and not self.path.exists() and not self.path.is_symlink():
            if not self.path.is_absolute() or self.path.resolve() != self.path:
                raise TargetError('TMUX_SOCKET_UNSAFE')
            if self.path.parent.exists() or self.path.parent.is_symlink():
                private_directory(self.path.parent)
            self.identity = None
        else:
            self.identity = validate_socket(self.path)

    @property
    def argv(self):
        return [self.binary, '-S', str(self.path)]

    def command(self, *args, deadline=None):
        if self.identity is None:
            raise TargetError('TMUX_TARGET_UNAVAILABLE')
        if validate_socket(self.path) != self.identity:
            raise TargetError('TMUX_SOCKET_CHANGED')
        timeout = 5 if deadline is None else min(5, deadline - time.monotonic())
        if timeout <= 0:
            raise TargetError('TMUX_TARGET_UNAVAILABLE')
        env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_PANE')}
        try:
            result = self.run([*self.argv, *args], capture_output=True, text=True,
                              timeout=timeout, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TargetError('TMUX_TARGET_UNAVAILABLE') from exc
        if validate_socket(self.path) != self.identity:
            raise TargetError('TMUX_SOCKET_CHANGED')
        return result

    def public(self):
        # A delta, not inherited credentials or a second environment record.
        return {'transport': 'legacy-default', 'socket_path': str(self.path),
                'argv': self.argv, 'environment_unset': ['TMUX', 'TMUX_PANE'],
                'server_pid': getattr(self, 'server_pid', None)}


def legacy_transport(*, run=None, binary=None, environ=None):
    """Resolve once at consumer bootstrap; subsequent calls use explicit -S.

    An inherited custom endpoint must prove its exact socket/server PID. A
    dedicated global endpoint can never become a legacy consumer endpoint.
    """
    env = os.environ if environ is None else environ
    inherited = env.get('TMUX')
    server_pid = None
    if inherited:
        parts = inherited.rsplit(',', 2)
        if len(parts) != 3 or not parts[1].isdigit():
            raise TargetError('LEGACY_TMUX_TRANSPORT_UNAVAILABLE')
        path = Path(parts[0])
        server_pid = parts[1]
    else:
        base = Path(env.get('TMUX_TMPDIR') or '/tmp').resolve()
        path = base / ('tmux-' + str(os.getuid())) / 'default'
    # Resolve the /tmp alias, while retaining no-symlink validation below the
    # trusted temp directory. Do not silently resolve a substituted socket.
    if str(path).startswith('/tmp/'):
        path = Path('/tmp').resolve() / path.relative_to('/tmp')
    global_parent = Path('/tmp').resolve() / ('orrery-tmux-' + str(os.getuid()))
    if path.parent == global_parent:
        raise TargetError('GLOBAL_TMUX_LEGACY_CONSUMER_REFUSED')
    if not inherited and not path.exists() and not path.is_symlink():
        # A fresh legacy cockpit can display an empty roster before any tmux
        # server exists. Pin its destination without creating a server.
        return Transport(path, binary=binary, run=run, allow_absent=True)
    transport = Transport(path, binary=binary, run=run)
    result = transport.command('display-message', '-p', '#{socket_path}\t#{pid}')
    fields = result.stdout.strip().split('\t')
    if (result.returncode or len(fields) != 2 or not fields[1].isdigit()
            or Path(fields[0]) != path or (server_pid and fields[1] != server_pid)):
        raise TargetError('LEGACY_TMUX_TRANSPORT_UNAVAILABLE')
    transport.server_pid = int(fields[1])
    return transport


def panes(transport, *, session=None, pane=None, deadline=None):
    if (session is None) == (pane is None):
        raise TargetError('TMUX_TARGET_INPUT_INVALID')
    if session is not None:
        if not isinstance(session, str) or not session or any(c in session for c in '\r\n\0:'):
            raise TargetError('TMUX_TARGET_INPUT_INVALID')
        args = ['list-panes', '-s', '-t', '=' + session]
    else:
        if not isinstance(pane, str) or re.fullmatch(r'%[0-9]+', pane) is None:
            raise TargetError('TMUX_TARGET_INPUT_INVALID')
        args = ['display-message', '-p', '-t', pane]
    fmt = '#{session_id}\t#{session_name}\t#{pane_id}\t#{pane_pid}'
    result = transport.command(*args, '-F', fmt, deadline=deadline) if session is not None else transport.command(*args, fmt, deadline=deadline)
    if result.returncode:
        raise TargetError('TMUX_TARGET_UNAVAILABLE')
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split('\t')
        if (len(fields) != 4 or re.fullmatch(r'\$[0-9]+', fields[0]) is None
                or re.fullmatch(r'%[0-9]+', fields[2]) is None
                or not fields[3].isdigit() or int(fields[3]) < 1
                or (session is not None and fields[1] != session)
                or (pane is not None and fields[2] != pane)):
            raise TargetError('TMUX_TARGET_UNAVAILABLE')
        rows.append(dict(session_id=fields[0], session=fields[1], pane_id=fields[2], pid=int(fields[3])))
    if not rows:
        raise TargetError('TMUX_TARGET_UNAVAILABLE')
    return rows


def global_proofs(client_home, *, api=None, deadline=None):
    """Read fixed client truth through the existing noncreating snapshot entry.

    A partial/unknown root is not proof of absence. No calls authenticate as a
    child, no database is opened, and no new operation mutex is created.
    """
    if api is None:
        api = runpy.run_path(str(Path(__file__).with_name('runtime_client.py')))
    home = Path(client_home)
    parent = home / 'clients'
    # Even without clients, an installed explicit context/config is a global
    # hint. Its absence alone cannot prove that the clients directory is empty.
    hints = [home / 'runtime-client.json']
    for key in ('AGENTSTACK_CLIENT_CONFIG', 'AGENTSTACK_GLOBAL_SERVER_CONFIG'):
        if os.environ.get(key):
            hints.append(Path(os.environ[key]))
    if not home.exists() and not home.is_symlink():
        if any(p.exists() or p.is_symlink() for p in hints):
            raise TargetError('GLOBAL_TARGET_OWNERSHIP_UNKNOWN')
        return []
    # Released pure-legacy installs may have a 0755 stack home. Reading the
    # absence of fixed global roles must not require changing their mode.
    info = home.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022 or home.resolve() != home):
        raise TargetError('GLOBAL_TARGET_OWNERSHIP_UNKNOWN')
    if not parent.exists() and not parent.is_symlink():
        if any(p.exists() or p.is_symlink() for p in hints):
            raise TargetError('GLOBAL_TARGET_OWNERSHIP_UNKNOWN')
        return []
    api['safe_private_directory'](parent)
    operator_api = runpy.run_path(str(Path(__file__).with_name('runtime_operator.py')))
    observation_api = runpy.run_path(str(Path(__file__).with_name('runtime_observation.py')))
    proofs = []
    for root in sorted(parent.iterdir()):
        api['remaining'](deadline)
        api['validate_client_name'](root.name)
        api['safe_private_directory'](root)
        context = api['read_json'](root / api['CLIENT_LAYOUT']['context'])
        identity = context.get('identity')
        if not isinstance(identity, dict) or not api['integer'](identity.get('agent_id'), 1):
            raise TargetError('GLOBAL_TARGET_OWNERSHIP_UNKNOWN')
        runtime = api['absolute'](context.get('runtime_root'))
        operator = operator_api['RuntimeOperator'](runtime / 'server-config.json', api=api)
        if operator.clients_parent != parent:
            raise TargetError('GLOBAL_TARGET_OWNERSHIP_UNKNOWN')
        snapshots = observation_api['Observations'](operator, lambda _: None, deadline=deadline)
        value, digest = snapshots.snapshot(root, identity['agent_id'])
        launch = value['launch']
        for live in value['lives']:
            if live.get('launch_id') is not None and (not launch or any(
                    launch.get(k) != live.get(k) for k in ('launch_id', 'receipt_nonce'))):
                raise TargetError('GLOBAL_TARGET_OWNERSHIP_UNKNOWN')
            proofs.append({'live': live, 'digest': digest, 'agent_id': identity['agent_id']})
    if not proofs and any(p.exists() or p.is_symlink() for p in hints):
        # Config plus no usable roots is still a partial global placement.
        raise TargetError('GLOBAL_TARGET_OWNERSHIP_UNKNOWN')
    return proofs


def legacy_admission(transport, client_home, *, session=None, pane=None,
                     operation='capture', api=None, proof_reader=None, deadline=None):
    if operation not in OPERATIONS:
        raise TargetError('TMUX_OPERATION_INVALID')
    if api is None:
        api = runpy.run_path(str(Path(__file__).with_name('runtime_client.py')))
    deadline = time.monotonic() + 5 if deadline is None else deadline
    targets = panes(transport, session=session, pane=pane, deadline=deadline)
    # A session-wide destructive operation protects every constituent pane.
    if operation == 'kill-session' and pane is not None:
        targets = panes(transport, session=targets[0]['session'], deadline=deadline)
    reader = global_proofs if proof_reader is None else proof_reader
    proofs = reader(client_home, api=api, deadline=deadline)
    for target in targets:
        start = api['process_start'](target['pid'], deadline=deadline)
        if not start or start == 'absent':
            raise TargetError('TMUX_TARGET_UNAVAILABLE')
        target['process_start'] = start
        for proof in proofs:
            live = proof['live']
            if (live.get('pane_id') == target['pane_id'] and live.get('pid') == target['pid']
                    and live.get('process_start') == start):
                raise TargetError('LEGACY_TARGET_IS_GLOBAL')
    # Re-read socket and actual pane membership after the root snapshots.
    after = panes(transport, session=session, pane=pane, deadline=deadline)
    if operation == 'kill-session' and pane is not None:
        after = panes(transport, session=after[0]['session'], deadline=deadline)
    expected = [{k: v for k, v in row.items() if k != 'process_start'} for row in targets]
    if after != expected:
        raise TargetError('TMUX_TARGET_CHANGED')
    for target in targets:
        if api['process_start'](target['pid'], deadline=deadline) != target['process_start']:
            raise TargetError('TMUX_TARGET_CHANGED')
    digest = hashlib.sha256(json.dumps([transport.public(), targets], sort_keys=True).encode()).hexdigest()
    return {'contract': CONTRACT, 'ok': True, 'allowed': True, 'operation': operation,
            **transport.public(), 'panes': targets, 'state_sha256': digest}


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('capabilities')
    sub.add_parser('legacy-transport')
    admit = sub.add_parser('legacy-target')
    selection = admit.add_mutually_exclusive_group(required=True)
    selection.add_argument('--session')
    selection.add_argument('--pane')
    admit.add_argument('--operation', choices=OPERATIONS, required=True)
    options = parser.parse_args(argv)
    try:
        if options.command == 'capabilities':
            value = {'contract': CONTRACT, 'capabilities': CAPABILITIES}
        else:
            transport = legacy_transport()
            if options.command == 'legacy-transport':
                value = {'contract': CONTRACT, 'ok': True, **transport.public()}
            else:
                home = Path(os.environ.get('AGENTSTACK_HOME', str(Path.home() / '.agentstack')))
                value = legacy_admission(transport, home, session=options.session,
                                         pane=options.pane, operation=options.operation)
        print_result(value, options.command)
        return 0
    except (TargetError, OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, (TargetError, RuntimeError)) and re.fullmatch(r'[A-Z][A-Z_]{2,80}', str(exc)) else 'GLOBAL_TARGET_OWNERSHIP_UNKNOWN'
        print_result({'contract': CONTRACT, 'ok': False, 'allowed': False, 'reason': reason}, 'error')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
