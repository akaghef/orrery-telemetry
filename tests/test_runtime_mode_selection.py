"""Mode-only admission and ownerless service routing over existing truth."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def selector(tmp_path, monkeypatch):
    api = runpy.run_path(str(ROOT / 'bin/lib/runtime_client.py'))
    monkeypatch.setitem(api['selected'].__globals__, 'ROOT', tmp_path)
    for key in ('AGENTSTACK_RUNTIME_MODE','AGENTSTACK_CLIENT_CONFIG',
                'AGENTSTACK_GLOBAL_SERVER_CONFIG','AGENT_NAME','AGENTSTACK_SESSION_ID'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('AGENTSTACK_HOME', str(tmp_path))
    # Selection must not construct a client or issue a remote call.
    def forbidden(*args, **kwargs): pytest.fail('mode selection reached owner/network IO')
    monkeypatch.setitem(api['selected'].__globals__, 'RuntimeClient', forbidden)
    monkeypatch.setitem(api['selected'].__globals__, 'management_exchange', forbidden)
    return api


def private(path, value):
    path.write_text(json.dumps(value)); path.chmod(0o600)

@pytest.mark.parametrize('mode,expected', [(None,'legacy'),('legacy','legacy'),('global','global')])
def test_mode_without_context_never_constructs_an_owner(selector, monkeypatch, mode, expected):
    if mode is not None: monkeypatch.setenv('AGENTSTACK_RUNTIME_MODE', mode)
    choice = selector['selected']([])
    assert choice['mode'] == expected and choice['context'] is None
    if mode == 'global':
        with pytest.raises(selector['ClientError'], match='^CONTEXT_UNAVAILABLE$'):
            selector['configured'](selection=choice)

@pytest.mark.parametrize('mode', ['GLOBAL','invalid',''])
def test_unknown_or_empty_mode_is_not_legacy(selector, monkeypatch, mode):
    monkeypatch.setenv('AGENTSTACK_RUNTIME_MODE', mode)
    with pytest.raises(selector['ClientError'], match='^RUNTIME_MODE_INVALID$'):
        selector['selected']([])


def test_explicit_mode_and_context_conflict_before_owner_io(selector, tmp_path):
    path = tmp_path/'runtime-client.json'
    private(path, {'kind':'orrery-runtime-client-s1','mode':'global'})
    assert selector['selected']([])['mode'] == 'global'
    with pytest.raises(selector['ClientError'], match='^RUNTIME_MODE_CONTEXT_CONFLICT$'):
        selector['selected'](['--runtime-mode','legacy'])
    path.unlink(); path.symlink_to(tmp_path/'absent')
    with pytest.raises(selector['ClientError'], match='^PRIVATE_FILE_UNAVAILABLE$'):
        selector['selected']([])


def test_bootstrap_default_is_read_once_but_inherited_mode_stays_pinned(selector, tmp_path, monkeypatch):
    env = tmp_path/'env.sh'
    env.write_text('export AGENTSTACK_RUNTIME_MODE=global\n')
    assert selector['selected'](bootstrap=True)['mode'] == 'global'
    monkeypatch.setenv('AGENTSTACK_RUNTIME_MODE','legacy')
    assert selector['selected'](bootstrap=True)['mode'] == 'legacy'
    assert selector['selected'](bootstrap=False)['mode'] == 'legacy'
    monkeypatch.delenv('AGENTSTACK_RUNTIME_MODE')
    assert selector['selected'](bootstrap=False)['mode'] == 'legacy'


def test_existing_legacy_session_requires_self_proof_not_project_hint(selector, tmp_path, monkeypatch):
    (tmp_path/'env.sh').write_text('export AGENTSTACK_RUNTIME_MODE=global\n')
    monkeypatch.setenv('AGENT_NAME','LegacyOwner')
    monkeypatch.setenv('AGENTSTACK_SESSION_ID','old-session')
    monkeypatch.setenv('AGENTSTACK_PROJECT_KEY','/irrelevant/project')
    with pytest.raises(selector['ClientError'], match='^LEGACY_SESSION_MODE_UNRESOLVED$'):
        selector['selected'](bootstrap=True)
    directory=tmp_path/'runtime/session_index'; directory.mkdir(parents=True)
    private(directory/'7.json', {'schema_version':2,'binding_kind':'self',
        'agent_id':7,'agent_name':'LegacyOwner','registered_by':'LegacyOwner','session_id':'old-session'})
    choice=selector['selected'](bootstrap=True)
    assert choice['mode']=='legacy' and choice['source']=='legacy-session-proof'
    with pytest.raises(selector['ClientError'], match='^RUNTIME_MODE_CONTEXT_CONFLICT$'):
        selector['selected'](['--runtime-mode','global'])

@pytest.mark.parametrize('hint', [('AGENTSTACK_RUNTIME_MODE','global'),('AGENTSTACK_RUNTIME_MODE','unknown'),('AGENTSTACK_RUNTIME_MODE',''),
                                 ('AGENTSTACK_GLOBAL_SERVER_CONFIG','/missing/server-config.json')])
def test_partial_loader_global_hint_cannot_continue_legacy(tmp_path, hint):
    import shutil
    lib=tmp_path/'bin/lib';lib.mkdir(parents=True)
    shutil.copyfile(ROOT/'bin/lib/global-client-loader.sh',lib/'global-client-loader.sh')
    env={**os.environ,hint[0]:hint[1]}
    result=subprocess.run(['/bin/bash',str(lib/'global-client-loader.sh'),'select'],env=env,capture_output=True,text=True)
    assert result.returncode==2 and 'GLOBAL_ENTRY_UNAVAILABLE' in result.stderr


def test_contextless_global_mode_cli_does_not_open_legacy_database(tmp_path):
    env={**os.environ,'AGENTSTACK_RUNTIME_MODE':'global','AGENTSTACK_HOME':str(tmp_path),'AGENTSTACK_LABEL_PREFIX':'org.agentstack.test.r1-mode',
         'AGENTSTACK_MAIL_DB':str(tmp_path/'must-not-exist.sqlite3')}
    for key in ('AGENT_NAME','AGENTSTACK_SESSION_ID','AGENTSTACK_CLIENT_CONFIG'):
        env.pop(key,None)
    result=subprocess.run([sys.executable,str(ROOT/'bin/lib/runtime_client.py'),'mode'],env=env,capture_output=True,text=True)
    assert result.returncode==0 and result.stdout=='global\n'
    result=subprocess.run([sys.executable,str(ROOT/'bin/lib/runtime_client.py'),'observe'],env=env,capture_output=True,text=True)
    assert result.returncode==2 and 'CONTEXT_UNAVAILABLE' in result.stderr
    assert not (tmp_path/'must-not-exist.sqlite3').exists()


def test_config_only_selection_is_global_and_unknown_kind_never_falls_back(selector, tmp_path):
    path=tmp_path/'server-config.json'
    private(path,{'kind':'orrery-global-server-s2c-v1'})
    result=selector['selected'](['--server-config',str(path)])
    assert result['mode']=='global' and result['context'] is None
    with pytest.raises(selector['ClientError'],match='^RUNTIME_MODE_CONTEXT_CONFLICT$'):
        selector['selected'](['--runtime-mode','legacy','--server-config',str(path)])
    private(path,{'kind':'foreign'})
    with pytest.raises(selector['ClientError'],match='^ISOLATED_CONFIG_REQUIRED$'):
        selector['selected'](['--server-config',str(path)])


def test_installed_default_does_not_override_explicit_global_context(selector,tmp_path):
    (tmp_path/'env.sh').write_text('export AGENTSTACK_RUNTIME_MODE=legacy\n')
    private(tmp_path/'runtime-client.json',{'kind':'orrery-runtime-client-s1','mode':'global'})
    assert selector['selected'](bootstrap=True)['mode']=='global'


def test_bootstrap_pinned_legacy_ignores_changed_global_defaults_without_python(tmp_path):
    import shutil
    lib=tmp_path/'bin/lib';lib.mkdir(parents=True)
    shutil.copy2(ROOT/'bin/lib/global-client-loader.sh',lib/'global-client-loader.sh')
    fake=tmp_path/'fake';fake.mkdir();marker=tmp_path/'python-ran'
    executable=fake/'python3'
    executable.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n');executable.chmod(0o755)
    default=tmp_path/'env.sh'
    default.write_text('export AGENTSTACK_RUNTIME_MODE=global\nexport AGENTSTACK_GLOBAL_SERVER_CONFIG=/must-not-read.json\n')
    script='. "$1"; ags_client_bootstrap "$2"; printf "%s:%s" "$AGENTSTACK_RUNTIME_MODE" "${AGENTSTACK_GLOBAL_SERVER_CONFIG-unset}"'
    env={**os.environ,'PATH':str(fake)+':/usr/bin:/bin','AGENTSTACK_RUNTIME_MODE':'legacy'}
    env.pop('AGENTSTACK_GLOBAL_SERVER_CONFIG',None);env.pop('AGENTSTACK_CLIENT_CONFIG',None)
    result=subprocess.run(['/bin/bash','-c',script,'test',str(lib/'global-client-loader.sh'),str(default)],
                          env=env,capture_output=True,text=True)
    assert result.returncode==0 and result.stdout=='legacy:unset'
    assert not marker.exists()


@pytest.mark.parametrize('entry',['bin/agentstack-enroll','bin/agentstack-mailctl','dashboard/agentctl.sh'])
def test_new_operator_controller_missing_loader_never_invokes_legacy(tmp_path,entry):
    import shutil
    destination=tmp_path/entry;destination.parent.mkdir(parents=True)
    shutil.copy2(ROOT/entry,destination)
    result=subprocess.run(['/bin/bash',str(destination),'status'],
                          env={**os.environ,'AGENTSTACK_HOME':str(tmp_path),'AGENTSTACK_LABEL_PREFIX':'org.agentstack.test.r1-mode','AGENTSTACK_RUNTIME_MODE':'global'},
                          capture_output=True,text=True)
    assert result.returncode==2 and 'GLOBAL_ENTRY_UNAVAILABLE' in result.stderr
    assert not (tmp_path/'mail').exists()


def test_global_installer_is_rejected_before_legacy_service_or_database(tmp_path):
    env={**os.environ,'HOME':str(tmp_path),'AGENTSTACK_HOME':str(tmp_path/'stack'),
         'AGENTSTACK_PROJECT_KEY':str(tmp_path/'project'),'AGENTSTACK_RUNTIME_MODE':'global',
         'AGENTSTACK_LABEL_PREFIX':'org.agentstack.test.r1-mode',
         'AGENTSTACK_MAIL_DB':str(tmp_path/'must-not-create.sqlite3')}
    for key in ('AGENT_NAME','AGENTSTACK_SESSION_ID','AGENTSTACK_CLIENT_CONFIG','AGENTSTACK_GLOBAL_SERVER_CONFIG'):
        env.pop(key,None)
    result=subprocess.run(['/bin/bash',str(ROOT/'scripts/install.sh'),'--dashboard-only','--dry-run'],
                          env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==2 and 'GLOBAL_INSTALL_REQUIRES_PR7' in result.stderr
    assert not (tmp_path/'must-not-create.sqlite3').exists() and not (tmp_path/'stack').exists()


@pytest.mark.parametrize('assignment,mode,reason',[
    ('export AGENTSTACK_RUNTIME_MODE=legacy\n','legacy',None),
    ('export AGENTSTACK_RUNTIME_MODE=global\n','global',None),
    ("export AGENTSTACK_RUNTIME_MODE=''\n",None,'RUNTIME_MODE_INVALID'),
    ('export AGENTSTACK_RUNTIME_MODE=unknown\n',None,'RUNTIME_MODE_INVALID'),
    ('export AGENTSTACK_RUNTIME_MODE="$(touch ATTACK_MARKER)"\n',None,'RUNTIME_MODE_INVALID'),
    ('export AGENTSTACK_RUNTIME_MODE="unterminated\n',None,'RUNTIME_DEFAULT_UNAVAILABLE'),
])
def test_installer_bootstrap_reads_only_literal_settings(tmp_path,assignment,mode,reason):
    # Other assignments are data, never shell evidence or code to execute.
    env_file=tmp_path/'env.sh'
    marker=tmp_path/'must-not-execute'
    env_file.write_text(assignment.replace('ATTACK_MARKER',str(marker))+
        'export AGENTSTACK_MAIL_ENV="$MISSING_RENDER/service.env"\n')
    script='. "$1"; . "$2"; ags_client_bootstrap "$3" --literal || exit $?; printf "%s" "$AGENTSTACK_RUNTIME_MODE"'
    env={k:v for k,v in os.environ.items() if not k.startswith('AGENTSTACK_') and k not in ('AGENT_NAME','PARENT_AGENT')}
    env.update(HOME=str(tmp_path),AGENTSTACK_LABEL_PREFIX='org.agentstack.test.r1-literal')
    result=subprocess.run(['/bin/bash','-uc',script,'fixture',str(ROOT/'hooks/project-context.sh'),
        str(ROOT/'bin/lib/global-client-loader.sh'),str(env_file)],env=env,capture_output=True,text=True)
    if reason:
        assert result.returncode==2 and reason in result.stderr,result.stderr
    else:
        assert result.returncode==0 and result.stdout==mode,result.stderr
    assert not marker.exists()


def test_literal_default_reader_failure_never_becomes_legacy(tmp_path):
    fake=tmp_path/'fake';fake.mkdir()
    python=fake/'python3';python.write_text('#!/bin/sh\nexit 1\n');python.chmod(0o755)
    env_file=tmp_path/'env.sh';env_file.write_text('export AGENTSTACK_RUNTIME_MODE=global\n')
    script='. "$1"; . "$2"; ags_client_bootstrap "$3" --literal'
    env={k:v for k,v in os.environ.items() if not k.startswith('AGENTSTACK_') and k not in ('AGENT_NAME','PARENT_AGENT')}
    env.update(HOME=str(tmp_path),PATH=str(fake)+':/usr/bin:/bin',AGENTSTACK_LABEL_PREFIX='org.agentstack.test.r1-reader')
    result=subprocess.run(['/bin/bash','-c',script,'fixture',str(ROOT/'hooks/project-context.sh'),
        str(ROOT/'bin/lib/global-client-loader.sh'),str(env_file)],env=env,capture_output=True,text=True)
    assert result.returncode==2 and 'RUNTIME_DEFAULT_UNAVAILABLE' in result.stderr
