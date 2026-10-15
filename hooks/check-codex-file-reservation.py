#!/usr/bin/env python3
"""Adapt Codex apply_patch input to the shared Edit/Write reservation guard."""
from __future__ import annotations

from contextlib import suppress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def patch_paths(command: str) -> list[str]:
    lines = command.strip().splitlines()
    if not lines or lines[0].strip() != '*** Begin Patch' or lines[-1].strip() != '*** End Patch':
        raise ValueError('expected a complete apply_patch document')
    paths = []
    for line in lines[1:-1]:
        for prefix in ('*** Add File: ', '*** Update File: ', '*** Delete File: ', '*** Move to: '):
            if line.startswith(prefix):
                path = line[len(prefix):].strip()
                if not path or '\x00' in path:
                    raise ValueError('empty or invalid patch path')
                paths.append(path)
                break
    if not paths:
        raise ValueError('no patch targets found')
    return list(dict.fromkeys(paths))


def run_shared_guard(guard: Path, document: dict, cwd: str, timeout: float):
    process = subprocess.Popen(['/bin/bash', str(guard)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd=cwd, start_new_session=True)
    try:
        stdout, stderr = process.communicate(json.dumps(document), timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill only this invocation's unreaped process group, including a
        # helper retaining the pipes. Killing just bash can leave communicate
        # waiting for a descendant, past Codex's fail-open hook timeout.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def main() -> int:
    try:
        raw = sys.stdin.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('hook input exceeds 1 MiB')
        payload = json.loads(raw)
        if payload.get('tool_name') != 'apply_patch':
            raise ValueError('expected apply_patch hook input')
        command = payload['tool_input']['command']
        if not isinstance(command, str):
            raise ValueError('tool_input.command must be patch text')
        paths = patch_paths(command)
        cwd = payload.get('cwd')
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ValueError('absolute, existing payload cwd required')
        guard = Path(__file__).with_name('check-file-reservation.sh')
        if not guard.is_file():
            raise ValueError('shared reservation guard unavailable')
        deadline = time.monotonic() + 25
        messages = []
        for path in paths:
            # Resolve symlink aliases and .. before the shared protected-root check.
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = Path(cwd) / candidate
            candidate = candidate.resolve()
            document = dict(payload, tool_name='Edit', tool_input={'file_path': str(candidate)})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('reservation checks exceeded their time budget')
            result = run_shared_guard(guard, document, cwd, remaining)
            if result.stderr:
                print(result.stderr, file=sys.stderr, end='')
            if result.returncode:
                return 2
            if result.stdout.strip():
                # Preserve shared warn-open diagnostics as one valid hook JSON object.
                for line in result.stdout.splitlines():
                    try:
                        warning = json.loads(line).get('systemMessage')
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(warning, str) and warning not in messages:
                        messages.append(warning)
        if messages:
            print(json.dumps({'systemMessage': '\n'.join(messages)}))
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'CODEX RESERVATION GUARD: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
