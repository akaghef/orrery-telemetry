"""Explicit operator preparation/status over existing client truth, no UI journal."""
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
API = runpy.run_path(str(ROOT/'bin/lib/runtime_client.py'))
# Initialize the shared pure contracts before accepting concurrent operations.
API['operator_schemas']()


class RuntimeOperator:
    def __init__(self, config_path):
        self.api=API
        self.path=self.api['absolute'](str(config_path))
        if self.path.name!='server-config.json':raise self.api['ClientError']('CONFIG_PATH_INVALID')
        self.raw=self.api['read_private'](self.path)
        self.config=self.api['read_json'](self.path)
        c=self.config
        if c.get('kind')!='orrery-global-server-s2c-v1' or c.get('activation_enabled') is not False:
            raise self.api['ClientError']('ISOLATED_CONFIG_REQUIRED')
        self.isolation=self.api['absolute'](c.get('isolation_root'))
        self.runtime=self.api['absolute'](c.get('runtime_root'))
        if self.path!=self.runtime/'server-config.json' or not self.runtime.is_relative_to(self.isolation):
            raise self.api['ClientError']('CONFIG_PATH_INVALID')
        self.api['safe_private_directory'](self.isolation)
        # Preparation owns the runtime's directory mode. Its private isolation
        # root and typed 0600 config are the admission boundary, as in the client.
        self.binding={'expected_server_instance_id':c.get('mail_instance_id'),
            'candidate_generation':c.get('candidate_generation'),'authority_epoch':c.get('authority_epoch')}
        if any(not isinstance(v,str) or not v for v in self.binding.values()):
            raise self.api['ClientError']('BINDING_INVALID')
        self.schemas=self.api['operator_schemas']()
        self.transport=SimpleNamespace(config=c)

    def read(self, query):
        # No local DB verification: the admitted server owns preparation proof,
        # tracker, authority fence and the single SQLite read transaction.
        if self.api['read_private'](self.path)!=self.raw:
            raise self.api['ClientError']('CONTEXT_CHANGED')
        request={'version':1,'action':'ui_read',**self.binding,'query':query}
        validate=self.api['wire_contract']()['validate_schema']
        try:validate(request,self.schemas['REQUEST'])
        except ValueError as exc:raise self.api['ClientError']('QUERY_INVALID') from exc
        reply=self.api['RuntimeClient'].management_exchange(self.transport,request)
        if not isinstance(reply,dict) or reply.get('ok') is not True:
            raise self.api['ClientError'](reply.get('reason','MANAGEMENT_REJECTED') if isinstance(reply,dict) else 'MANAGEMENT_REJECTED')
        try:validate(reply,self.schemas['RESPONSE'])
        except ValueError as exc:raise self.api['ClientError']('RESPONSE_INVALID') from exc
        if reply['binding']!=self.binding:
            raise self.api['ClientError']('MANAGEMENT_BINDING_MISMATCH')
        if reply['query_kind']!=query['kind'] or (query['kind']=='messages' and reply.get('tool')!=query['tool']):
            raise self.api['ClientError']('RESPONSE_INVALID')
        if self.api['read_private'](self.path)!=self.raw:
            raise self.api['ClientError']('CONTEXT_CHANGED')
        return reply


    @property
    def clients_parent(self):
        return self.isolation/'agentstack/clients'

    def path_for(self, name):
        self.api['validate_client_name'](name)
        return self.clients_parent/name/self.api['CLIENT_LAYOUT']['context']

    def client(self, name):
        path=self.path_for(name)
        pending=path.parent/self.api['CLIENT_LAYOUT']['registration']
        recovery=None
        if pending.exists() or pending.is_symlink():
            saved=self.api['read_json'](pending)
            if saved.get('row'):
                recovery=('finalize-create',saved['row']['agent_id'])
        client=self.api['RuntimeClient'](path,recovery=recovery)
        if client.binding!=self.binding or client.runtime!=self.runtime:
            raise self.api['ClientError']('CHILD_BINDING_MISMATCH')
        return client

    def template(self):
        c=self.config
        # Verify the exact read-only inputs before creating any output. The
        # remote server validates preparation proof, never this caller's DB.
        self.read({'kind':'health'})
        lock=self.api['absolute'](c['authority_lock'])
        authority=self.api['absolute'](c['authority'])
        for path in (lock,authority):
            if not path.is_relative_to(self.isolation) or path.resolve()!=path:
                raise self.api['ClientError']('PATH_OUTSIDE_ISOLATION')
            self.api['read_private'](path)
        expected={'kind':'orrery-global-authority-v1','phase':'active','root_status':'active',
            'runtime_root':str(self.runtime),'mail_instance_id':self.binding['expected_server_instance_id'],
            'candidate_generation':self.binding['candidate_generation'],'authority_epoch':self.binding['authority_epoch']}
        value=self.api['read_json'](authority)
        if any(value.get(k)!=v for k,v in expected.items()):
            raise self.api['ClientError']('WRITER_FENCED')
        info=lock.lstat()
        return {'kind':self.api['KIND'],'mode':'global','activation_enabled':False,
            'wrapper_root':str(ROOT),'isolation_root':str(self.isolation),'runtime_root':str(self.runtime),
            'agentstack_home':str(self.isolation/'agentstack'),'authority':str(authority),
            'authority_lock':str(lock),'lock_identity':[info.st_dev,info.st_ino],
            'management_socket':c['management_socket'],
            'mcp_url':self.api['local_endpoint'](os.environ.get('AGENTSTACK_MCP_URL','')),**self.binding}

    def prepare(self, name):
        template=self.template()
        return self.api['prepare_client_root'](template,name)

    def provider(self, client):
        program=client.registration_metadata().get('program')
        mapping={'claude':'claude','claude-code':'claude','codex':'codex','gemini':'gemini','antigravity':'gemini','daemon':'daemon'}
        if program not in mapping:raise self.api['ClientError']('PROVIDER_UNAVAILABLE')
        return mapping[program]

    def configured(self, client):
        provider=self.provider(client)
        delivery=self.api['runtime_delivery'](client)
        if provider=='daemon':
            delivery.execution()
        elif provider in ('claude','codex'):
            home=client.runtime_dir/'provider-home'/provider
            helpers=self.api['delivery_contract']()['instruction_api']()
            helpers['provider_instructions'](home,provider,str(ROOT),'')
            if provider=='codex':delivery.codex_trust()
        else:
            raise self.api['ClientError']('GEMINI_INTERACTIVE_RUNTIME_UNSUPPORTED')
        return provider

    def status(self, name):
        path=self.path_for(name)
        result={'ok':True,'client_name':name,'stage':'absent','agent_id':None,
            'next_operations':['prepare-root'],'reason':None,'launch_status':None}
        if not path.parent.exists() and not path.parent.is_symlink():return result
        try:
            client=self.client(name)
            with client.operation_mutex():
                pending=client.outputs['registration']
                if pending.exists() or pending.is_symlink():
                    row=self.api['read_json'](pending)
                    if row.get('terminal'):
                        result.update(stage='unavailable',next_operations=['repair-local-state'],reason='REGISTRATION_OWNER_CONFLICT')
                    elif row.get('row'):
                        result.update(stage='registration_saved',agent_id=row['row']['agent_id'],next_operations=['finalize-registration'])
                    else:result.update(stage='registration_pending',next_operations=['replay-registration','registration-abandon'])
                elif client.identity is None:
                    result.update(stage='root_only',next_operations=['replay-registration'])
                else:
                    has_launch=client.outputs['launch'].exists() or client.outputs['launch'].is_symlink()
                    row=client.inspect_owner() if has_launch else client.observe()
                    result['agent_id']=row['agent_id']
                    if has_launch:
                        delivery=self.api['runtime_delivery'](client)
                        saved=delivery.launch()
                        observed=delivery.status(saved['launch_id'])
                        result.update(stage='launch_present',next_operations=['launch-status'],
                            launch_status={k:observed[k] for k in ('launch_id','state_sha256','phase','operator_required','next_operations')})
                    else:
                        try:self.configured(client)
                        except (OSError,ValueError,RuntimeError) as exc:
                            result.update(stage='registered_unconfigured',next_operations=['configure-provider'],reason=str(exc))
                        else:result.update(stage='configured',next_operations=['launch'])
        except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
            result.update(stage='unavailable',next_operations=['repair-local-state'],reason=str(exc))
        self.api['wire_contract']()['validate_schema'](result,self.api['operator_schemas']()['FIXTURE']['spawn_status_schema'])
        return result

    def lookup(self, name=None):
        target=self.read({'kind':'resolve_name','name':name})['data']['agent_id'] if name is not None else None
        matches=[]
        if self.clients_parent.exists() or self.clients_parent.is_symlink():
            self.api['safe_private_directory'](self.clients_parent)
            for entry in sorted(self.clients_parent.iterdir()):
                # Enumerate names only. Validation and observation are per root.
                self.api['validate_client_name'](entry.name)
                status=self.status(entry.name)
                if status['stage']=='unavailable':raise self.api['ClientError']('CLIENT_LOOKUP_UNAVAILABLE')
                pending=entry/self.api['CLIENT_LAYOUT']['registration']
                saved=self.api['read_json'](pending) if pending.exists() or pending.is_symlink() else {}
                if name is None or (target is not None and status['agent_id']==target) or saved.get('name')==name:
                    matches.append({k:status[k] for k in ('client_name','agent_id','stage')})
        result={'ok':True,'matches':matches}
        self.api['wire_contract']()['validate_schema'](result,self.api['operator_schemas']()['FIXTURE']['client_lookup_schema'])
        return result

    def create(self, name, *, agent_name, program, model):
        self.api['validate_client_name'](name)
        known=self.lookup(agent_name)['matches']
        if any(row['client_name']!=name for row in known):
            reason='CLIENT_SELECTION_REQUIRED' if len(known)>1 else 'EXISTING_CLIENT_REQUIRED'
            return {'ok':False,'reason':reason,'matches':known}
        path=self.path_for(name)
        if not path.exists() and not path.is_symlink():self.prepare(name)
        client=self.client(name)
        with client.operation_mutex():
            if client.identity and not client.outputs['registration'].exists():
                if client.outputs['child'].exists():raise self.api['ClientError']('CLIENT_ROOT_IS_CHILD')
                metadata=client.registration_metadata()
                if client.identity.get('name')!=agent_name or metadata.get('program')!=program or metadata.get('model')!=model:
                    raise self.api['ClientError']('EXISTING_IDENTITY_REQUIRED')
                return self.status(name)
            client.create(agent_name,program,model)
        return self.status(name)

    def spawn(self, name, *, agent_name, program, model, cwd):
        result=self.create(name,agent_name=agent_name,program=program,model=model)
        if not result['ok'] or result['stage']=='launch_present':return result
        client=self.client(name)
        delivery=self.api['runtime_delivery'](client)
        provider=self.provider(client)
        # This POST is explicit configuration; regular status/resume never
        # rewrites provider instructions or grants Codex trust.
        if provider in ('claude','codex'):delivery.instruction_configure(provider)
        try:self.configured(client)
        except (OSError,ValueError,RuntimeError):return self.status(name)
        delivery.start(provider,cwd,detached=True)
        return self.status(name)

    def resume(self, name, *, prepare_only=False):
        client=self.client(name)
        current=self.status(name)
        if current['stage']!='launch_present':
            return current
        saved=current['launch_status']
        if saved['operator_required']:raise self.api['ClientError']('LAUNCH_OUTCOME_UNKNOWN')
        if saved['phase'] not in ('ended','cancelled'):return current
        delivery=self.api['runtime_delivery'](client)
        provider=self.configured(client)
        receipt=self.api['read_json'](client.runtime_dir/'session_index/self.json')
        if not receipt.get('session_id') or receipt.get('agent_id')!=client.identity['agent_id']:
            raise self.api['ClientError']('PROVIDER_SESSION_REQUIRED')
        previous=delivery.launch()
        if client.outputs['child'].exists():client.resume_child({})
        else:
            if client.session_liveness()!='absent':raise self.api['ClientError']('SESSION_LIVENESS_UNKNOWN')
            observed=client.inspect_owner()
            if observed.get('retired_at') is not None:client.call('unretire_agent')
            client.reconnect()
        if not prepare_only:
            delivery.start(provider,previous['cwd'],detached=True,launch_kind='resume',provider_session_id=receipt['session_id'])
        return self.status(name)
