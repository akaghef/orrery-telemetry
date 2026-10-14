"""Effects after successful initial admission must recheck the actual target."""
import os
from pathlib import Path
import runpy
import socket
import subprocess
import tempfile
import pytest
ROOT=Path(__file__).resolve().parents[1]
API=runpy.run_path(str(ROOT/'bin/lib/runtime_tmux.py'))

@pytest.fixture
def consumer(tmp_path):
    base=tempfile.TemporaryDirectory(prefix='r2tc-',dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
    path=Path(base.name).resolve()/'fixture.sock'
    with base, socket.socket(socket.AF_UNIX) as stream:
        stream.bind(str(path));path.chmod(0o600)
        calls=[];state={'global':False,'actual':'Alpha','pid':100}
        def run(argv,**kw):
            calls.append(argv)
            command=argv[3]
            if command in ('list-panes','display-message'):
                return subprocess.CompletedProcess(argv,0,'$1\t'+state['actual']+'\t%1\t'+str(state['pid'])+'\n','')
            return subprocess.CompletedProcess(argv,0,'','')
        transport=API['Transport'](path,binary='fixture-tmux',run=run);transport.server_pid=123
        c=API['LegacyConsumer'](tmp_path,transport=transport,api={'process_start':lambda *a,**kw:'start'},
             proof_reader=lambda *a,**kw:[{'live':{'pane_id':'%1','pid':100,'process_start':'start'}}] if state['global'] else [])
        yield c,calls,state


@pytest.mark.parametrize('effect',[['paste-buffer','-t','Alpha','-b','fixture'],['send-keys','-t','Alpha','C-m'],['send-keys','-t','Alpha','C-m'],['kill-session','-t','Alpha'],['attach-session','-t','Alpha']])
def test_global_replacement_after_initial_capture_blocks_each_effect(consumer,effect):
    c,calls,state=consumer
    c.command(['capture-pane','-p','-t','Alpha'])
    state['global']=True
    with pytest.raises(API['TargetError'],match='^LEGACY_TARGET_IS_GLOBAL$'):c.command(effect)
    assert not any(argv[3]==effect[0] for argv in calls)


@pytest.mark.parametrize('actual',['AlphaX','alpha'])
def test_prefix_or_case_alias_never_reaches_effect(consumer,actual):
    c,calls,state=consumer;state['actual']=actual
    with pytest.raises(API['TargetError'],match='^TMUX_TARGET_UNAVAILABLE$'):
        c.command(['send-keys','-t','Alpha','C-m'])
    assert not any(argv[3]=='send-keys' for argv in calls)
    assert calls[0][3:7]==['list-panes','-s','-t','=Alpha']


def test_normal_legacy_effect_uses_pane_id_and_explicit_socket(consumer):
    c,calls,_=consumer;c.command(['send-keys','-t','Alpha','C-m'])
    assert calls[-1][3:]==['send-keys','-t','%1','C-m']
    assert all(argv[1:3]==['-S',str(c.transport.path)] for argv in calls)


def test_initial_only_admission_mutant_is_detected_by_effect_counter(consumer,monkeypatch):
    c,calls,state=consumer;c.command(['capture-pane','-p','-t','Alpha'])
    state['global']=True
    # Deliberate bypass of the production command's admission, matching a
    # faulty implementation which validates once and reuses that permission.
    monkeypatch.setattr(c,'admitted_argv',lambda args:[*c.transport.argv,*args])
    c.command(['send-keys','-t','%1','C-m'])
    def no_foreign_effect():assert not any(argv[3]=='send-keys' for argv in calls)
    with pytest.raises(AssertionError):no_foreign_effect()


def test_fresh_legacy_creation_uses_its_explicit_endpoint_only():
    import shutil
    binary=shutil.which('tmux')
    if binary is None:pytest.skip('tmux unavailable')
    with tempfile.TemporaryDirectory(prefix='r2fresh-',dir='/private/tmp' if Path('/private/tmp').is_dir() else None) as value:
        home=Path(value).resolve();path=home/('tmux-'+str(os.getuid()))/'default'
        transport=API['Transport'](path,binary=binary,allow_absent=True)
        c=API['LegacyConsumer'](home,transport=transport,proof_reader=lambda *a,**k:[],api={})
        try:
            result=c.command(['new-session','-d','-s','Fresh','sleep 60'])
            assert result.returncode==0,result.stderr
            assert transport.identity==API['validate_socket'](path)
            assert transport.command('has-session','-t','=Fresh').returncode==0
        finally:subprocess.run([binary,'-S',str(path),'kill-server'],capture_output=True,check=False)


def test_legacy_dashboard_session_id_is_resolved_as_id_not_literal_name(consumer):
    c,calls,state=consumer
    c.command(['capture-pane','-t','$1','-p'])
    assert calls[0][3:7]==['list-panes','-s','-t','$1']
    assert calls[-1][3:]==['capture-pane','-t','%1','-p']
    state['global']=True
    with pytest.raises(API['TargetError'],match='^LEGACY_TARGET_IS_GLOBAL$'):
        c.command(['capture-pane','-t','$1','-p'])


def test_new_adapter_reuses_older_helper_primitives_without_new_class(consumer):
    c,calls,state=consumer
    api={k:v for k,v in API.items() if k not in ('LegacyConsumer','TARGET_SPEC','TARGET_REASONS','runtime_api_view')}
    factory=runpy.run_path(str(ROOT/'bin/lib/legacy_consumer.py'))['LegacyConsumer']
    adapter=factory(c.home,transport=c.transport,api=c.api,proof_reader=c.proof_reader,transport_api=api)
    adapter.command(['send-keys','-t','Alpha','C-m'])
    assert calls[-1][3:]==['send-keys','-t','%1','C-m']
    state['global']=True
    with pytest.raises(API['TargetError'],match='^LEGACY_TARGET_IS_GLOBAL$'):
        adapter.command(['send-keys','-t','Alpha','C-m'])


def test_pure_legacy_inventory_does_not_initialize_global_operator_contracts(tmp_path,monkeypatch):
    function=API['global_inventory'];namespace=function.__globals__;original=namespace['module_api'];loaded=[]
    def modules(name):
        loaded.append(name)
        assert name not in ('runtime_operator','runtime_observation')
        return original(name)
    monkeypatch.setitem(namespace,'module_api',modules)
    monkeypatch.setitem(namespace,'installed_hints',lambda *a:{})
    for key in ('AGENTSTACK_RUNTIME_MODE','AGENTSTACK_GLOBAL_SERVER_CONFIG','AGENTSTACK_CLIENT_CONFIG'):
        monkeypatch.delenv(key,raising=False)
    value=function(tmp_path,api=original('runtime_client'))
    assert value=={'proofs':[],'diagnostics':[],'global_complete':True}
    assert loaded==[]


def test_legacy_session_rename_pins_the_session_id_not_its_first_pane(consumer):
    c,calls,state=consumer
    c.command(['rename-session','-t','$1','NewName'])
    assert calls[-1][3:]==['rename-session','-t','$1','NewName']
    state['global']=True
    with pytest.raises(API['TargetError'],match='^LEGACY_TARGET_IS_GLOBAL$'):
        c.command(['rename-session','-t','$1','NewName'])
    assert sum(argv[3]=='rename-session' for argv in calls)==1


def test_legacy_attach_or_create_cannot_adopt_an_existing_global_session(consumer):
    c,calls,state=consumer;original=c.transport.run
    def run(argv,**kw):
        if argv[3]=='list-sessions':
            calls.append(argv);return subprocess.CompletedProcess(argv,0,'Alpha\n','')
        return original(argv,**kw)
    c.transport.run=run
    c.command(['new-session','-A','-s','Alpha'])
    assert sum(argv[3]=='new-session' for argv in calls)==1
    state['global']=True
    with pytest.raises(API['TargetError'],match='^LEGACY_TARGET_IS_GLOBAL$'):
        c.command(['new-session','-A','-s','Alpha'])
    assert sum(argv[3]=='new-session' for argv in calls)==1
