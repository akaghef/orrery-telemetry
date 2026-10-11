"""Load the installed common client; never keep another owner credential."""
from pathlib import Path
import os
import runpy
import subprocess


def selected_client():
    library = Path(__file__).resolve().parents[4] / 'bin/lib/runtime_client.py'
    if not library.exists():
        library = Path(os.environ.get('AGENTSTACK_HOME', str(Path.home()/'.agentstack'))) / 'bin/lib/runtime_client.py'
    if not library.exists():
        loader = library.with_name('global-client-loader.sh')
        if loader.exists():
            result = subprocess.run(['/bin/bash', str(loader), 'select'],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 125:
                return None
            raise RuntimeError('GLOBAL_ENTRY_UNAVAILABLE')
        if os.environ.get('AGENTSTACK_CLIENT_CONFIG'):
            raise RuntimeError('GLOBAL_ENTRY_UNAVAILABLE')
        return None
    api = runpy.run_path(str(library))
    path = api['selected']([])
    return (api, api['RuntimeClient'](path)) if path is not None else None
