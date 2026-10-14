"""One consumer adapter over the existing transport/admission API, including older helpers."""
import os
from pathlib import Path
import re
import subprocess


class LegacyConsumer:
    """One explicit legacy endpoint and one admission for each target effect."""
    def __init__(self,client_home,*,transport=None,api=None,proof_reader=None,transport_api=None):
        self.tmux=transport_api
        if self.tmux is None:raise RuntimeError("GLOBAL_TARGET_HELPER_UNAVAILABLE")
        self.home=Path(client_home)
        self.transport=self.tmux['legacy_transport']() if transport is None else transport
        self.api=self.tmux['module_api']('runtime_client') if api is None else api
        self.proof_reader=proof_reader

    def admission(self,target,operation):
        if target.startswith('='):target=target[1:]
        selector={'pane':target} if re.fullmatch(r'%[0-9]+',target) else ({'window':target} if re.fullmatch(r'@[0-9]+',target) else {'session':target.split(':',1)[0]})
        return self.tmux['legacy_admission'](self.transport,self.home,operation=operation,api=self.api,proof_reader=self.proof_reader,**selector)

    def admitted_argv(self,args):
        args=list(args)
        if not args:raise self.tmux['TargetError']('TMUX_TARGET_INPUT_INVALID')
        command=args[0]
        operation={'send-keys':'input','paste-buffer':'input','split-window':'split','kill-pane':'close','kill-window':'close','kill-session':'kill-session','attach':'attach','attach-session':'attach'}.get(command,'capture')
        if '-t' in args:
            index=args.index('-t')+1
            if index>=len(args):raise self.tmux['TargetError']('TMUX_TARGET_INPUT_INVALID')
            target=args[index]
            # Exact spelling before admission eliminates tmux prefix aliases.
            proof=self.admission(target,operation)
            if target.startswith(('%','@')):pinned=target
            elif operation=='kill-session':pinned=proof['panes'][0]['session_id']
            elif command in ('has-session','show-environment','attach','attach-session','list-panes','rename-session'):
                pinned=proof['panes'][0]['session_id']
            else:pinned=proof['panes'][0]['pane_id']
            args[index]=pinned
        elif command in ('send-keys','paste-buffer','kill-session','kill-pane','kill-window','split-window','attach','attach-session'):
            raise self.tmux['TargetError']('TMUX_TARGET_INPUT_INVALID')
        return [*self.transport.argv,*args]

    def command(self,args,**kwargs):
        argv=self.admitted_argv(args)
        creating=args and args[0]=='new-session' and self.transport.identity is None
        if creating:self.tmux['private_directory'](self.transport.path.parent,create=True)
        if not creating and self.tmux['validate_socket'](self.transport.path)!=self.transport.identity:raise self.tmux['TargetError']('TMUX_SOCKET_CHANGED')
        env=kwargs.pop('env',None) or os.environ
        env={k:v for k,v in env.items() if k not in ('TMUX','TMUX_PANE')}
        kwargs.setdefault('capture_output',True);kwargs.setdefault('text',True);kwargs.setdefault('timeout',5)
        result=self.transport.run(argv,env=env,**kwargs)
        if creating and result.returncode==0:self.transport.identity=self.tmux['validate_socket'](self.transport.path)
        return result

    def stop_group(self,target):
        proof=self.admission(target,'signal')
        for row in proof['panes']:
            result=subprocess.run(['ps','-o','pgid=','-p',str(row['pid'])],capture_output=True,text=True,timeout=3)
            pgid=result.stdout.strip()
            if result.returncode or not pgid.isdigit():raise self.tmux['TargetError']('TMUX_TARGET_UNAVAILABLE')
            current=self.admission(target,'signal')
            if current['state_sha256']!=proof['state_sha256']:raise self.tmux['TargetError']('TMUX_TARGET_CHANGED')
            if self.api['process_start'](row['pid'])!=row['process_start']:raise self.tmux['TargetError']('TMUX_TARGET_CHANGED')
            os.killpg(int(pgid),__import__('signal').SIGSTOP)
