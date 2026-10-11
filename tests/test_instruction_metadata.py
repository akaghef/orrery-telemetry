"""Real metadata and cooperative alias races, confined to the fixture HOME."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import subprocess
import sys

import pytest
from test_instruction_blocks import env, engine, apply, m


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS ACL preservation')
def test_acl_survives_managed_block_replacement(env):
    e=engine(env);e.home.mkdir();e.target.write_text('outside\n')
    subprocess.run(['/bin/chmod','+a','everyone allow read',str(e.target)],check=True)
    before=m.acl(e.target)
    assert before
    apply(e)
    assert m.acl(e.target)==before
    e.uninstall()
    assert m.acl(e.target)==before and e.target.read_text()=='outside\n'


def test_symlink_aliases_share_resolved_target_mutex_and_compare_inputs(env):
    e=engine(env);e.home.mkdir();e.target.write_text('outside\n')
    alias=e.home/'alias';alias.symlink_to(e.target.name)
    other=engine(env);other.target=alias
    plans=[e.plan(),other.plan()]
    def update(pair):
        instance,plan=pair
        try:return instance.apply(plan,m.sha(m.canonical(plan)),assume_yes=True)['stage']
        except m.Refused as exc:return exc.reason
    with ThreadPoolExecutor(2) as workers:
        results=list(workers.map(update,zip([e,other],plans)))
    assert results.count('complete')==1 and results.count('INSTRUCTION_PLAN_CHANGED')==1
    assert alias.is_symlink() and m.block_bytes(e.target.read_bytes())==e.block


@pytest.mark.parametrize('resolved,source,flag,expected',[
    ('','none',False,'unrelated'),('','none',True,'unresolved'),
    ('Existing','env',False,'participant'),('Existing','tmux',False,'participant'),
    ('','identity-conflict',False,'unresolved'),
])
def test_instruction_admission_uses_existing_resolution_without_registering(env,resolved,source,flag,expected):
    import uuid
    from test_instruction_blocks import ROOT
    sid='instruction-admission-'+uuid.uuid4().hex
    path=Path('/tmp/.claude-agent-registered-'+sid)
    try:
        if flag:path.touch()
        result=subprocess.run(['/bin/bash','-c',
            '. "$1"; agentstack_instruction_admission','fixture',str(ROOT/'hooks/session-identity-policy.sh')],
            env={**env,'RESOLVED_AGENT':resolved,'RESOLVED_AGENT_SRC':source,'AGENTSTACK_SESSION_ID':sid},
            capture_output=True,text=True,check=True)
        assert result.stdout.strip()==expected and not result.stderr
    finally:path.unlink(missing_ok=True)



def test_provider_instruction_symlink_never_writes_user_target(env):
    from test_instruction_blocks import ROOT
    e=engine(env);e.home.mkdir();e.target.write_text('user note')
    home=e.install/'runtime/provider-home/claude';home.mkdir(parents=True)
    (home/'CLAUDE.md').symlink_to(e.target)
    with pytest.raises(m.Refused,match='PROVIDER_CONFIG_REQUIRED'):
        m.provider_instructions(home,'claude',ROOT,'',configure=True)
    assert e.target.read_text()=='user note' and (home/'CLAUDE.md').is_symlink()



def test_same_referent_source_is_updated_once_and_has_no_cleanup_notice(env):
    e=engine(env);e.home.mkdir();e.target.write_text('outside\n')
    source=Path(env['AGENTSTACK_PROJECT_KEY'])/'CLAUDE.md';source.symlink_to(e.target)
    assert e.plan()['sources']==[]
    apply(e)
    findings=e.detect()
    assert {f['kind'] for f in findings}=={'current'}
    assert all(not f['cleanup_argv'] for f in findings)
    assert source.is_symlink()


def test_existing_directory_symlink_allows_new_user_file_but_broken_link_refuses(env):
    e=engine(env)
    real=Path(env['HOME'])/'real-config';real.mkdir()
    e.home.symlink_to(real, target_is_directory=True)
    assert apply(e)['stage']=='complete'
    assert e.home.is_symlink() and (real/'CLAUDE.md').read_bytes()==e.block
    broken=Path(env['HOME'])/'broken-config';broken.symlink_to('missing-directory', target_is_directory=True)
    with pytest.raises(m.Refused,match='INSTRUCTION_PATH_UNSAFE'):
        m.snapshot(broken/'CLAUDE.md')
