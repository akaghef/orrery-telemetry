"""Actual S2c management socket and isolated tmux; shell fixture only."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import pytest
from test_global_runtime_client_s2a import api,context
from test_global_runtime_delivery import server

ROOT=Path(__file__).resolve().parents[1]
TMUX=runpy.run_path(str(ROOT/'bin/lib/runtime_tmux.py'))
TARGET=runpy.run_path(str(ROOT/'bin/lib/runtime_target.py'))

@pytest.fixture
def target(server,monkeypatch):
    binary=shutil.which('tmux')
    if binary is None:pytest.skip('tmux unavailable')
    client=context(server,window=False)
    client.save_metadata('claude-code','fixture-model')
    base=tempfile.TemporaryDirectory(prefix='r2ta-',dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
    directory=Path(base.name).resolve();socket=directory/'fixture.sock'
    command=[binary,'-S',str(socket),'-f','/dev/null']
    env={k:v for k,v in os.environ.items() if k not in ('TMUX','TMUX_PANE')}
    try:
        created=subprocess.run([*command,'new-session','-d','-s','Beta','sleep 60'],env=env,capture_output=True,text=True)
        assert created.returncode==0,created.stderr
        fields=subprocess.check_output([*command,'display-message','-p','#{pid}\t#{pane_id}\t#{pane_pid}\t#{session_id}'],env=env,text=True).strip().split('\t')
        monkeypatch.setenv('TMUX_TMPDIR',str(directory))
        monkeypatch.setenv('TMUX',str(socket)+','+fields[0]+',0')
        live=dict(kind='orrery-global-live-session-v1',**client.binding,agent_id=2,session_id='fixture-session',pid=int(fields[2]),process_start='fixture-start',pane_id=fields[1])
        (client.runtime_dir/'live-sessions').mkdir(mode=0o700)
        api['atomic_json'](client.runtime_dir/'live-sessions/fixture-session.json',live)
        runtime_api={**api,'process_start':lambda pid,**kw:'fixture-start'}
        operator=TMUX['module_api']('runtime_operator')['RuntimeOperator'](server['config_path'],api=runtime_api)
        targets=TARGET['Targets'](operator,api=runtime_api,transport_api=TMUX)
        yield dict(client=client,targets=targets,transport=TMUX['Transport'](socket,binary=binary),command=command,env=env,live=live,server=server,pane=fields[1],session=fields[3],monkeypatch=monkeypatch)
    finally:
        subprocess.run([*command,'kill-server'],env=env,capture_output=True,check=False)
        base.cleanup()


def root_bytes(client):
    return {str(p.relative_to(client.client_root)):p.read_bytes() for p in client.client_root.rglob('*') if p.is_file()}


def test_operator_standalone_actual_socket_is_readonly_and_owner_attach_is_refused(target):
    before=root_bytes(target['client'])
    value,name=target['targets'].execute('target',2,operator=True)
    assert name=='target.success'
    assert value['target']['socket_path']==str(target['transport'].path)
    assert value['target']['agent_name']=='Beta' and value['target']['launch'] is None
    assert value['target']['kind']=='standalone-tmux'
    assert root_bytes(target['client'])==before
    target['targets'].emit(value,name)
    with pytest.raises(TMUX['TargetError'],match='^OPERATOR_CONFIRMATION_REQUIRED$'):
        target['targets'].execute('attach',2,context=target['client'].path,print_only=True)


def test_capture_limit_boundaries_and_truncation_are_literal(target):
    with pytest.raises(TMUX['TargetError'],match='^TMUX_TARGET_INPUT_INVALID$'):
        target['targets'].execute('capture',2,operator=True,lines=1001)
    value,name=target['targets'].execute('capture',2,operator=True,lines=1)
    assert name=='capture.success' and value['capture']['max_lines']==1
    assert value['capture']['truncated'] is True and len(value['capture']['text'].splitlines())<=1
    target['targets'].emit(value,name)


def test_owner_target_requires_existing_same_token_and_parent_proof(target):
    value,name=target['targets'].execute('target',2,context=target['client'].path)
    assert value['principal']['kind']=='owner' and 'attach' not in value['next_operations']
    assert 'fixture-owner-two' not in json.dumps(value)
    with pytest.raises(TMUX['TargetError'],match='^TMUX_TARGET_NOT_IDLE$'):
        target['targets'].execute('send-compact',2,context=target['client'].path,expected=value['state_sha256'])


def test_missing_and_busy_operation_mutex_never_creates_a_replacement(target):
    lock=target['client'].outputs['operation_lock'];lock.unlink()
    with pytest.raises(api['ClientError'],match='^OBSERVATION_MUTEX_UNAVAILABLE$'):
        target['targets'].execute('target',2,operator=True)
    assert not lock.exists()


def test_duplicate_identity_roots_are_not_selected_by_name(target):
    client=target['client'];other=client.client_root.with_name('second');other.mkdir(mode=0o700)
    api['atomic_json'](other/'runtime-client.json',client.config)
    with pytest.raises(TMUX['TargetError'],match='^GLOBAL_TARGET_OWNERSHIP_UNKNOWN$'):
        target['targets'].execute('target',2,operator=True)


def test_attach_print_matches_exec_and_stderr_admission(target,capsys):
    value,name=target['targets'].execute('attach',2,operator=True,read_only=True,print_only=True)
    assert value['target']['argv']==[*target['transport'].argv,'attach-session','-t',target['session'],'-r']
    calls=[]
    target['targets'].execute('attach',2,operator=True,read_only=True,exec_fn=lambda *args:calls.append(args))
    output=capsys.readouterr()
    assert output.out==''
    assert json.loads(output.err)['phase']=='admitted'
    assert calls[0][1]==value['target']['argv'] and 'TMUX' not in calls[0][2]


def test_same_pane_number_from_wrong_endpoint_is_a_negative_control(target,monkeypatch):
    resolver=target['targets'];original=resolver.endpoints
    class Wrong:
        def command(self,*a,**kw):return subprocess.CompletedProcess(a,0,'$9\tWrong\t'+target['pane']+'\t999999\n','')
    monkeypatch.setattr(resolver,'endpoints',lambda *args:[('legacy-default',Wrong())])
    with pytest.raises(TMUX['TargetError'],match='^TMUX_TARGET_UNAVAILABLE$'):
        resolver.execute('target',2,operator=True)
    monkeypatch.setattr(resolver,'endpoints',original)
    assert resolver.execute('target',2,operator=True)[0]['target']['pane_id']==target['pane']


@pytest.mark.parametrize('text,reason',[
 ('✻ Worked for 6s · done 6:39 PM\n❯\nctx: 14% used',None),
 ('✳ Stewing… (18s · ↓ 1.1k tokens)\n❯\nctx: 33% used','TMUX_TARGET_NOT_IDLE'),
 ('❯ synthetic suggestion\nctx: 21% used','TMUX_TARGET_INPUT_NOT_EMPTY'),
 ('❯ Try "fix lint errors"','TMUX_TARGET_INPUT_NOT_EMPTY'),
 ('Do you want to run this command?\n❯ 1. Yes','TMUX_TARGET_NOT_IDLE'),
])
def test_parent_actual_management_proof_compact_waits_for_empty_idle(target,monkeypatch,text,reason):
    parent=target['client'];prepared=parent.prepare_child(dict(child_client_name='child',name='ChildAlpha',program='claude-code',model='fixture-model',workdir=str(target['server']['root'])))
    child=api['RuntimeClient'](parent.clients_parent/'child'/api['CLIENT_LAYOUT']['context'])
    subprocess.run([*target['command'],'new-session','-d','-s','ChildAlpha','sleep 60'],env=target['env'],check=True,capture_output=True)
    raw=subprocess.check_output([*target['command'],'list-panes','-s','-t','=ChildAlpha','-F','#{pane_id}\t#{pane_pid}'],env=target['env'],text=True).strip().split('\t')
    (child.runtime_dir/'live-sessions').mkdir(mode=0o700,exist_ok=True)
    child.save_metadata('claude-code','fixture-model')
    api['atomic_json'](child.runtime_dir/'live-sessions/child-session.json',dict(kind='orrery-global-live-session-v1',**child.binding,agent_id=child.identity['agent_id'],session_id='child-session',pid=int(raw[1]),process_start='fixture-start',pane_id=raw[0]))
    commands=[];original=TMUX['Transport'].command
    def terminal(self,*args,**kw):
        if args[0]=='capture-pane':return subprocess.CompletedProcess(args,0,text,'')
        if args[0]=='send-keys':commands.append(args);return subprocess.CompletedProcess(args,0,'','')
        return original(self,*args,**kw)
    monkeypatch.setattr(TMUX['Transport'],'command',terminal)
    targets=target['targets'];aid=child.identity['agent_id']
    first=targets.resolve(aid,context=parent.path)
    assert first['principal']['kind']=='parent'
    if reason:
        with pytest.raises(TMUX['TargetError'],match='^'+reason+'$'):
            targets.execute('send-compact',aid,context=parent.path,expected=first['state_sha256'])
        assert commands==[]
    else:
        result,name=targets.execute('send-compact',aid,context=parent.path,expected=first['state_sha256'])
        assert name=='compact.submitted' and result['phase']=='submitted'
        assert commands==[('send-keys','-t',raw[0],'-l','/compact'),('send-keys','-t',raw[0],'C-m')]
        targets.emit(result,name)


def test_explicit_dedicated_proof_never_borrows_same_named_default_session(target,monkeypatch):
    import uuid
    client=target['client'];resolver=target['targets']
    second=target['transport'].path.with_name('dedicated.sock')
    command=[target['command'][0],'-S',str(second),'-f','/dev/null']
    try:
        subprocess.run([*command,'new-session','-d','-s','Beta','sleep 60'],env=target['env'],check=True,capture_output=True)
        raw=subprocess.check_output([*command,'display-message','-p','#{pane_id}\t#{pane_pid}'],env=target['env'],text=True).strip().split('\t')
        launch=dict(kind='orrery-global-launch-intent-v1',launch_id=str(uuid.uuid4()),receipt_nonce=str(uuid.uuid4()),phase='running',provider='claude',launch_kind='startup',cwd=str(client.isolation),task_sha256='0'*64,error=None,operator_resolution=None,tmux_transport='global-instance')
        # The fixture supplies synthetic launch evidence only; no product start.
        api['atomic_json'](client.outputs['launch'],launch)
        api['atomic_json'](client.runtime_dir/'live-sessions/fixture-session.json',{**target['live'],'pid':int(raw[1]),'pane_id':raw[0],'launch_id':launch['launch_id'],'receipt_nonce':launch['receipt_nonce']})
        actual=TMUX['Transport'](second)
        monkeypatch.setattr(resolver,'endpoints',lambda *a:[('global-instance',actual)])
        value,_=resolver.execute('target',2,operator=True)
        assert value['target']['transport']=='global-instance' and value['target']['socket_path']==str(second)
        assert value['target']['session_name']=='Beta' and value['target']['pane_id']==raw[0]
        # Deliberate faulty implementation: same-name default namespace fallback.
        monkeypatch.setattr(resolver,'endpoints',lambda *a:[('legacy-default',target['transport'])])
        with pytest.raises(TMUX['TargetError'],match='^TMUX_TARGET_UNAVAILABLE$'):
            resolver.execute('target',2,operator=True)
    finally:subprocess.run([*command,'kill-server'],env=target['env'],capture_output=True,check=False)


def test_real_legacy_watcher_keeps_global_signal_and_delivers_healthy_legacy(target,tmp_path):
    from _installed_client_payload import copy_client_library
    import sys
    state=target['server'];home=state['root']/'agentstack'
    copy_client_library(home)
    runner=home/'hooks/watch-once.sh';runner.parent.mkdir(mode=0o700,exist_ok=True)
    source=(ROOT/'hooks/watch_agent_mail_signals.sh').read_text()
    source=source[:source.index('if command -v fswatch &>/dev/null; then')].replace('\nacquire_lock\n','\n# fixture: no daemon lock\n')
    runner.write_text(source+'\nprocess_existing_signals\nwait\n')
    runtime=home/'runtime';signals=home/'mail/signals/projects/fixture/agents'
    for recipient,mid in [('Beta',1),('Gamma',2)]:
        directory=signals/recipient;directory.mkdir(mode=0o700,parents=True)
        (directory/(str(mid)+'.signal')).write_text(json.dumps({'message':{'id':mid,'from':'FixtureSender','subject':'fixture','body':'synthetic'}}))
    subprocess.run([*target['command'],'new-session','-d','-s','Gamma',"printf 'Claude\\n❯ \\n'; cat"],env=target['env'],check=True,capture_output=True)
    gamma_pane,gamma_pid=subprocess.check_output([*target['command'],'list-panes','-s','-t','=Gamma','-F','#{pane_id}\t#{pane_pid}'],env=target['env'],text=True).strip().split('\t')
    commands=tmp_path/'commands';commands.mkdir()
    ps=commands/'ps';ps.write_text('#!/bin/sh\ncase "$*" in "-p '+str(target['live']['pid'])+' -o lstart="|"-p '+gamma_pid+' -o lstart=") printf "%s\\n" "fixture-start";; *) exit 2;; esac\n');ps.chmod(0o700)
    env={k:v for k,v in os.environ.items() if not k.startswith(('AGENTSTACK_','MCP_AGENT_')) and k not in ('TMUX','TMUX_PANE','PROJECT_KEY','PARENT_AGENT','AGENT_NAME','CHILD_REGISTRATION_TOKEN')}
    env.update(HOME=str(state['root']/'home'),PATH=str(commands)+os.pathsep+str(Path(sys.executable).parent)+os.pathsep+env['PATH'],
               TMUX=str(target['transport'].path)+','+str(target['transport'].command('display-message','-p','#{pid}').stdout.strip())+',0',
               TMUX_TMPDIR=str(target['transport'].path.parent),AGENTSTACK_HOME=str(home),AGENTSTACK_RUNTIME_DIR=str(runtime),
               AGENTSTACK_SIGNALS_DIR=str(home/'mail/signals'),AGENTSTACK_PERSISTENT_DELIVER=str(tmp_path/'no-headless'),AGENTSTACK_LABEL_PREFIX='org.agentstack.test.r2ta')
    result=subprocess.run(['/bin/bash',str(runner)],env=env,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stderr
    notify=json.loads((runtime/'notify-state.json').read_text())
    assert notify['Gamma:2']['last_result']=='success'
    assert notify['Beta:1']['last_result']!='success'
    assert (signals/'Beta/1.signal').exists() and not (signals/'Gamma/2.signal').exists()
    beta=subprocess.check_output([*target['command'],'capture-pane','-p','-t',target['pane']],env=target['env'],text=True)
    assert 'ORRERY Mail notification' not in beta
    gamma=subprocess.check_output([*target['command'],'capture-pane','-p','-t',gamma_pane],env=target['env'],text=True)
    # Read by exact pane ID below if tmux's display target needs disambiguation.
    assert 'ORRERY Mail notification' in gamma


def test_same_target_with_two_authorized_principals_invalidates_old_digest(target):
    resolver=target['targets'];client=target['client']
    owner=resolver.resolve(2,context=client.path);operator=resolver.resolve(2,operator=True)
    assert owner['target']==operator['target']
    assert owner['state_sha256']!=operator['state_sha256']


def test_retired_owner_effect_refused_but_current_operator_proof_is_readable(target):
    client=target['client'];client.call('retire_agent',{})
    value,_=target['targets'].execute('target',2,operator=True)
    assert value['target']['agent_id']==2 and 'send-compact' not in value['next_operations']
    with pytest.raises(TMUX['TargetError'],match='^OWNER_REQUIRED$'):
        target['targets'].execute('target',2,context=client.path)
    with pytest.raises(TMUX['TargetError'],match='^TARGET_UNAVAILABLE$'):
        target['targets'].execute('send-compact',2,operator=True,expected=value['state_sha256'],confirm=lambda:True)


def test_non_tmux_standalone_has_no_invented_launch_or_capture_argv(target):
    path=target['client'].runtime_dir/'live-sessions/fixture-session.json'
    api['atomic_json'](path,{**target['live'],'pane_id':None})
    value,_=target['targets'].execute('target',2,operator=True)
    assert value['target']['kind']=='standalone-process'
    assert value['target']['argv']==[] and value['target']['socket_path'] is None
    with pytest.raises(TMUX['TargetError'],match='^TMUX_TARGET_UNAVAILABLE$'):
        target['targets'].execute('capture',2,operator=True)


def test_app_desktop_metadata_is_not_current_provider_proof(target):
    path=target['client'].runtime_dir/'live-sessions/fixture-session.json'
    api['atomic_json'](path,{**target['live'],'app_state':'working','app_subagent':False})
    with pytest.raises(TMUX['TargetError'],match='^TMUX_TARGET_UNAVAILABLE$'):
        target['targets'].execute('target',2,operator=True)
