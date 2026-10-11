"""Dashboard's private operator bridge. Never opens a Mail database."""
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
# Python's module import lock initializes this one operator/client API before
# concurrent HTTP requests. No per-request temporary module aliases.
OPERATOR_API=runpy.run_path(str(ROOT/'bin/lib/runtime_operator.py'))


class BackendError(RuntimeError):
    def __init__(self, reason, status=503):
        super().__init__(reason)
        self.status=status


class Backend:
    def __init__(self, config_path):
        self.operator=OPERATOR_API['RuntimeOperator'](config_path)

    def read(self, query):
        return {'runtime_mode':'global',**self.operator.read(query)}

    def get(self, path, params):
        def one(key,default=None):
            values=params.get(key,[])
            if len(values)>1:raise BackendError('QUERY_INVALID',400)
            return values[0] if values else default
        def number(key,default=None):
            value=one(key,default)
            if value is None:return None
            if not str(value).isascii() or not str(value).isdigit() or not 1<=int(value)<=2**63-1:
                raise BackendError('QUERY_INVALID',400)
            return int(value)
        if path=='/api/global/clients':return self.operator.lookup(one('name'))
        if path=='/api/global/spawn-status':return self.operator.status(one('client_name'))
        if path=='/api/agents':
            return self.read(dict(kind='roster',before_agent_id=number('before_agent_id'),limit=number('limit',200)))
        if path=='/api/mail-watcher-health':return self.read(dict(kind='health'))
        supported={'/api/mail/recent','/api/mail/message','/api/mail/conversation',
            '/api/mail/legacy-label','/api/messages-since','/api/agent-history','/api/edge-messages','/api/graph'}
        if path not in supported:raise BackendError('GLOBAL_ROUTE_UNAVAILABLE',404)
        aid=number('view_as_agent_id')
        if aid is None:raise BackendError('UI_VIEWPOINT_REQUIRED',400)
        limit=number('limit',100);cursor=one('cursor')
        if path=='/api/mail/message':q=dict(kind='message',message_id=number('id'),view_as_agent_id=aid)
        elif path in {'/api/mail/conversation','/api/mail/legacy-label'}:
            if 'source' in params:raise BackendError('QUERY_INVALID',400)
            args={'cursor':cursor,'per_thread_limit':limit}
            if path.endswith('conversation'):args['message_id']=number('message_id')
            else:args['thread_id']=one('label')
            q=dict(kind='messages',tool='summarize_thread',view_as_agent_id=aid,arguments=args)
        elif path=='/api/edge-messages':
            q=dict(kind='edge_messages',view_as_agent_id=aid,a_agent_id=number('a_agent_id'),
                b_agent_id=number('b_agent_id'),limit=limit,cursor=cursor)
        else:
            args={'query':one('query',''),'limit':limit,'cursor':cursor,'include_bodies':False}
            if path=='/api/mail/recent':
                q=dict(kind='messages',tool='search_messages',view_as_agent_id=aid,arguments=args)
            else:
                q=dict(kind='activity',view_as_agent_id=aid,limit=limit,cursor=cursor)
                if path=='/api/agent-history':q['target_agent_id']=number('agent_id')
                if path=='/api/messages-since':
                    value=one('after_id','0')
                    if not value.isascii() or not value.isdigit():raise BackendError('QUERY_INVALID',400)
                    q['after_id']=int(value)
        return self.read(q)

    def post(self,path,payload,validate_cwd,open_terminal):
        table={'/api/global/spawn':'http_spawn_schema','/api/global/resume':'http_resume_schema'}
        if path not in table:raise BackendError('GLOBAL_ROUTE_UNAVAILABLE',404)
        api=self.operator.api
        schema=api['operator_schemas']()['FIXTURE'][table[path]]
        try:api['wire_contract']()['validate_schema'](payload,schema)
        except ValueError as exc:raise BackendError('QUERY_INVALID',400) from exc
        if path.endswith('/spawn'):
            cwd=validate_cwd(payload['cwd'])
            return self.operator.spawn(payload['client_name'],agent_name=payload['name'],
                program=payload['program'],model=payload['model'],cwd=cwd)
        result=self.operator.resume(payload['client_name'],prepare_only=payload.get('prepare_only',False))
        launch=result.get('launch_status')
        if (payload.get('open',True) and not payload.get('prepare_only',False)
                and launch and launch['phase'] in ('gated','running') and not launch['operator_required']):
            opened=open_terminal(['tmux','attach-session','-t','=orrery-'+launch['launch_id']],title=payload['client_name'])
            if not opened.get('ok'):raise BackendError('TERMINAL_OPEN_FAILED')
        return result
