"""Global launch and notification operations over the fixed client layout.

This module has no Mail storage connection. External effects happen between
prepare and confirm, with every local mutex released.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import secrets
import shutil
import stat
import subprocess
import sys
import uuid

_CONTRACT = None


def contract():
    global _CONTRACT
    if _CONTRACT is None:
        here = Path(__file__).parent
        validator = here / 'schema_contract.py'
        if not validator.exists():
            validator = here.parents[1] / 'packages/agentstack_mail/src/agentstack_mail/schema_contract.py'
        api = runpy.run_path(str(validator))
        value = api['fixture']('global-client-4c.json')
        _CONTRACT = (value, api['validate_schema'])
    return _CONTRACT


def validate(value, name, error):
    fixture, validator = contract()
    validator(value, fixture['local_schemas'][name], 'OUTPUT_SCHEMA_INVALID', error_type=error)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def legacy_policy():
    """Read the existing shell's numeric declarations; do not duplicate defaults.

    Only these data assignments are read, never executed. The old watcher body
    stays byte-identical and remains the source of its delivery bounds.
    """
    import re
    source=(Path(__file__).resolve().parents[2]/'hooks/watch_agent_mail_signals.sh').read_text()
    def number(pattern):
        found=re.search(pattern,source,re.M)
        if found is None:raise ValueError('LEGACY_DELIVERY_POLICY_UNAVAILABLE')
        return int(found.group(1))
    cooldown=number(r'^RETRY_COOLDOWN=(\d+)')
    poll=number(r'^POLL_INTERVAL=(\d+)')
    scan=number(r'Linux\) SCAN_INTERVAL_DEFAULT=(\d+)') if sys.platform.startswith('linux') else number(r'\*\) SCAN_INTERVAL_DEFAULT=(\d+)')
    configured=os.environ.get('AGENTSTACK_MAIL_WATCHER_SCAN_INTERVAL','')
    scan_minimum=number(r'SCAN_INTERVAL < (\d+)')
    if re.fullmatch(r'[0-9]+',configured) and int(configured)>=scan_minimum:scan=int(configured)
    default=number(r'HEADLESS_REPLY_TIMEOUT="\$\{AGENTSTACK_HEADLESS_REPLY_TIMEOUT:-(\d+)\}"')
    timeout=os.environ.get('AGENTSTACK_HEADLESS_REPLY_TIMEOUT','')
    minimum=number(r'HEADLESS_REPLY_TIMEOUT < (\d+)')
    maximum=number(r'HEADLESS_REPLY_TIMEOUT > (\d+)')
    timeout=int(timeout) if re.fullmatch(r'[0-9]+',timeout) and minimum<=int(timeout)<=maximum else default
    return {'max_attempts':None,'lease':timeout+cooldown,'backoff':lambda n:cooldown,
            'poll':poll,'scan':scan,'coalesce':0,'wakes_per_hour':None,'process_timeout':timeout}


def policy(channel, route_kind=None):
    """One channel selector using the existing configuration readers."""
    if route_kind == 'codex-app' or channel == 'codex-app':
        source=Path(__file__).resolve().parents[2]/'integrations/codex_app/src'
        if str(source) not in sys.path:sys.path.insert(0,str(source))
        from agentstack_codex_app.daemon import wake_timing_settings
        values=wake_timing_settings(os.environ)
        return {'max_attempts':values['wake_max_attempts'],'lease':values['wake_lease_seconds'],
                'backoff':lambda n:min(values['wake_max_backoff_seconds'],values['wake_base_backoff_seconds']*2**(n-1)),
                'poll':values['wake_poll_seconds'],'coalesce':values['wake_coalesce_seconds'],
                'wakes_per_hour':values['wake_limit_per_hour'],'process_timeout':values['wake_timeout_seconds']}
    return legacy_policy()


def format_notification(message):
    """A single safe line; the hint never pretends to carry the full body."""
    def line(value):
        return ' '.join(str(value or '').replace('\x1b', '').split())
    sender_row=message.get('sender')
    sender = sender_row.get('name') if isinstance(sender_row,dict) else None
    sender = sender or message.get('from') or message.get('sender_name') or message.get('sender_id')
    text = f"ORRERY Mail notification: message from {line(sender)} [{line(message.get('importance', 'normal'))}]: {line(message.get('subject'))}."
    body = message.get('body_md')
    if isinstance(body, str) and len(body) <= 2000:
        return text + ' Body (complete; no inbox fetch needed): ' + line(body.replace('\r', ' ').replace('\n', ' ⏎ '))
    if isinstance(body, str):
        return text + ' Body preview: ' + line(body[:2000].replace('\r', ' ').replace('\n', ' ⏎ ')) + ' Fetch inbox to read the rest.'
    return text + ' Please call fetch_inbox to read it.'


def policy_blocked(row):
    return row['phase']=='blocked' and row['error'] in ('BELOW_MIN_IMPORTANCE','SELF_MESSAGE')


def notification_eligible(message):
    def rank(value):return {'low':0,'high':2,'urgent':3}.get(str(value).lower(),1)
    return rank(message.get('importance')) >= rank(os.environ.get('AGENTSTACK_MAIL_NOTIFY_MIN_IMPORTANCE','low'))


class RuntimeDelivery:
    def __init__(self, client, api):
        self.client, self.api = client, api
        self.error = api['ClientError']
        self.launch_path = client.outputs['launch']
        self.proxy_receipt = None
        self._watch_errors = {}

    def fail(self, reason):
        raise self.error(reason)

    def read(self, path):
        return self.api['read_json'](path)

    def directory(self, path):
        # Walk only the known fixed chain, never scan the client subtree.
        path = Path(path)
        if not path.is_relative_to(self.client.isolation):
            self.fail('PATH_OUTSIDE_ISOLATION')
        parents = list(path.relative_to(self.client.isolation).parts)
        current = self.client.isolation
        for part in parents:
            current /= part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            self.api['safe_private_directory'](current)
        return path

    def local(self, path, schema):
        value = self.read(path)
        validate(value, schema, self.error)
        return value

    def save(self, path, value, schema):
        validate(value, schema, self.error)
        def check():
            if path.exists() or path.is_symlink():
                self.local(path, schema)
        check()
        self.api['atomic_json'](path, value, before_replace=check)

    def launch(self):
        return self.local(self.launch_path, 'launch_intent')

    def external_route(self, session_id, agent_id=None, *, create=False):
        helper=Path(self.client.config['wrapper_root'])/'integrations/codex_app/src/agentstack_codex_app/identity_store.py'
        external=runpy.run_path(str(helper))['external_id_for'](session_id,agent_id)
        instance=self.consumer_agent(create=create).parent.parent
        path=instance/'external'/hashlib.sha256(external.encode()).hexdigest()/'route.json'
        if create:
            self.directory(path.parent)
        else:
            current=self.client.isolation
            for part in path.parent.relative_to(current).parts:
                current /= part
                if not current.exists() and not current.is_symlink():break
                self.api['safe_private_directory'](current)
        return path

    def external_client(self, session_id, agent_id=None):
        route=self.local(self.external_route(session_id,agent_id),'runtime_route')
        client=self.api['RuntimeClient'](self.client.clients_parent/route['client_name']/'runtime-client.json')
        if client.binding!=self.client.binding:self.fail('ROUTE_OWNER_CONFLICT')
        delivery=RuntimeDelivery(client,self.api)
        receipt=self.read(client.runtime_dir/'session_index/self.json')
        if receipt.get('session_id')!=session_id or delivery.provider_header(receipt.get('transcript_path',''))!=session_id:
            self.fail('LAUNCH_RECEIPT_MISMATCH')
        return client

    def consumer_agent(self, *, create=True):
        c = self.client
        instance = hashlib.sha256(c.binding['expected_server_instance_id'].encode()).hexdigest()
        server = self.read(c.runtime / 'server-config.json')
        expected = {'isolation_root':str(c.isolation), 'runtime_root':str(c.runtime),
            'mail_instance_id':c.binding['expected_server_instance_id'],
            'candidate_generation':c.binding['candidate_generation'], 'authority_epoch':c.binding['authority_epoch'],
            'authority':str(c.authority), 'authority_lock':str(c.lock)}
        if any(server.get(k) != v for k,v in expected.items()):
            self.fail('CONSUMER_ISOLATION_MISMATCH')
        root = c.client_home / 'global-consumer'
        # Fixed roots must stay disjoint, including resolved aliases.
        for other in (c.runtime, c.clients_parent):
            a, b = root.resolve(), other.resolve()
            if a.is_relative_to(b) or b.is_relative_to(a):
                self.fail('ROUTE_OWNER_CONFLICT')
        path = root / instance / 'agents' / str(c.identity['agent_id'])
        if create:
            return self.directory(path)
        current = c.isolation
        for part in path.relative_to(c.isolation).parts:
            current /= part
            if not current.exists() and not current.is_symlink():
                return path
            self.api['safe_private_directory'](current)
        return path

    @contextmanager
    def dir_lock(self, directory, *, exclusive=True):
        info = directory.lstat()
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            current = directory.lstat()
            if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                self.fail('PRIVATE_DIRECTORY_REQUIRED')
            yield
        finally:
            os.close(fd)

    def publish_route(self):
        directory = self.consumer_agent()
        with self.dir_lock(directory), self.client.operation_mutex():
            path = directory / 'route.json'
            route = {'kind': 'orrery-global-runtime-route-v1', 'client_name': self.client.config['client_name']}
            if path.exists() or path.is_symlink():
                prior = self.local(path, 'runtime_route')
                if prior != route:
                    self.fail('ROUTE_OWNER_CONFLICT')
            self.save(path, route, 'runtime_route')

    @contextmanager
    def message(self, mid):
        if type(mid) is not int or not 1 <= mid <= 2**63 - 1:
            self.fail('ARGUMENTS_UNSUPPORTED')
        agent = self.consumer_agent()
        with self.dir_lock(agent, exclusive=False), self.client.operation_mutex():
            route = self.local(agent / 'route.json', 'runtime_route')
            if route['client_name'] != self.client.config['client_name']:
                self.fail('DELIVERY_ROUTE_CHANGED')
            directory = self.directory(agent / 'messages' / str(mid))
            with self.dir_lock(directory):
                try:
                    yield directory / 'intent.json'
                finally:
                    # C is still held: a parallel prepare opens this name only
                    # after cleanup, never the unlinked message-lock inode.
                    # rmdir removes only an empty slot; any intent stays intact.
                    try:directory.rmdir()
                    except OSError as exc:
                        if exc.errno not in (errno.ENOTEMPTY,errno.EEXIST):raise

    def live_rows(self, launch_id):
        directory = self.client.runtime_dir / 'live-sessions'
        if not directory.exists():
            return []
        self.api['safe_private_directory'](directory)
        rows = []
        for path in sorted(directory.glob('*.json')):
            row = self.read(path)
            if row.get('kind') != 'orrery-global-live-session-v1':
                self.fail('OUTPUT_SCHEMA_INVALID')
            if row.get('launch_id') == launch_id:
                rows.append([path.name, row])
        return rows

    def state_digest(self, kind, subject, intent):
        c = self.client
        launch = self.launch() if self.launch_path.exists() or self.launch_path.is_symlink() else None
        return digest({'version':1, 'subject_kind':kind, 'subject_id':subject, 'intent':intent,
            'context_sha256':digest(self.read(c.path)), 'credential_sha256':digest(self.read(c.credential)),
            'launch_sha256':digest(launch) if launch else None,
            'live_sessions_sha256':digest(self.live_rows(launch['launch_id'])) if launch else digest([])})

    def current(self):
        c = self.client
        return {**c.binding, 'agent_id':c.identity['agent_id'],
                'credential_generation':c.identity['credential_generation'],
                'launch_id':self.launch()['launch_id'] if self.launch_path.exists() else None}

    def status_current(self):
        row = self.current()
        row['server_instance_id'] = row.pop('expected_server_instance_id')
        return row

    def remote_verified(self):
        try:
            self.client.observe()
            return True, None
        except self.error as exc:
            return False, str(exc)

    def ending_incomplete(self, row):
        return (row['phase']=='ended' and (row.get('operator_resolution') or {}).get('action')=='confirmed-ended'
                and any(not live.get('ended') for _,live in self.live_rows(row['launch_id'])))

    def status(self, launch_id):
        with self.client.operation_mutex():
            saved = self.launch()
            if saved['launch_id'] != launch_id:
                self.fail('LAUNCH_RECEIPT_MISMATCH')
            verified, reason = self.remote_verified()
            phase = saved['phase']
            live = any(not row.get('ended') for _, row in self.live_rows(launch_id))
            ready = verified and phase == 'running' and live
            unknown = phase in ('exec-unknown', 'abandoned') or self.ending_incomplete(saved)
            observation = 'verified-running' if ready else ('verified-ended' if phase == 'ended' and not unknown else ('verified-not-executed' if phase in ('planned','gated','cancelled') else 'unknown'))
            return {'ok':True, 'state_sha256':self.state_digest('launch',launch_id,saved),
                    'operator_required':unknown, 'next_operations':['launch-resolve'] if unknown else (['launch-retry','launch-cancel'] if phase in ('planned','gated') else []),
                    'current':self.status_current(), 'remote_owner_verified':verified, 'reason':reason or saved['error'],
                    'launch_id':launch_id, 'phase':phase, 'runtime_ready':ready, 'observation':observation,
                    'operator_resolution':saved['operator_resolution']}

    def prepare_launch(self, provider, *, launch_kind='startup', cwd=None):
        c = self.client
        with c.operation_mutex():
            if self.launch_path.exists() or self.launch_path.is_symlink():
                previous = self.launch()
                if self.ending_incomplete(previous):self.fail('LAUNCH_PENDING_CONFLICT')
                if previous['phase'] not in ('ended', 'cancelled'):
                    self.fail('LAUNCH_OUTCOME_UNKNOWN' if previous['phase'] in ('exec-unknown','abandoned') else 'LAUNCH_PENDING_CONFLICT')
            cwd = Path(cwd or os.getcwd()).resolve(strict=True)
            if not cwd.is_dir() or provider not in ('claude','codex','gemini','daemon'):
                self.fail('ARGUMENTS_UNSUPPORTED')
            _, schemas = c.capabilities()
            if 'bind_window_identity' not in schemas:
                self.fail('CAPABILITY_UNAVAILABLE')
            c.output_plan()
            provider_home = self.directory(c.runtime_dir / 'provider-home' / provider)
            # Provider availability is checked before window RPC or tmux effects.
            command = os.environ.get('AGENTSTACK_'+provider.upper()+'_BIN') or {'claude':'claude','codex':'codex','gemini':'agy','daemon':sys.executable}[provider]
            if shutil.which(command) is None:
                self.fail('PROVIDER_UNAVAILABLE')
            task = self.api['read_private'](c.outputs['task']) if c.outputs['task'].exists() else None
            row = {'kind':'orrery-global-launch-intent-v1', 'launch_id':str(uuid.uuid4()),
                'receipt_nonce':secrets.token_urlsafe(32), 'phase':'planned', 'provider':provider,
                'launch_kind':launch_kind, 'cwd':str(cwd), 'task_sha256':hashlib.sha256(task).hexdigest() if task else None,
                'error':None, 'operator_resolution':None}
            self.save(self.launch_path,row,'launch_intent')
        self.publish_route()
        return row

    def bind_window(self):
        c = self.client
        with c.operation_mutex():
            # Replay even when context projection succeeded but acknowledgement
            # did not. The sole pending slot must not strand later mutations.
            pending = c.outputs['mutation']
            row = self.read(pending) if pending.exists() or pending.is_symlink() else None
            if row is not None and row.get('tool') == 'bind_window_identity':
                return c.call('bind_window_identity',row['canonical_payload']['arguments'])
            if c.identity.get('window_row_id') is not None:
                return c.call('verify_window_identity', {k:c.identity[k] for k in ('window_row_id','window_uuid')})
            if row is not None:self.fail('MUTATION_PENDING_CONFLICT')
            return c.call('bind_window_identity', {'window_uuid':str(uuid.uuid4())})

    def resolve_launch(self, request):
        c = self.client
        with c.operation_mutex():
            row = self.launch()
            if row['launch_id'] != request['launch_id'] or self.state_digest('launch',row['launch_id'],row) != request['expected_state_sha256']:
                self.fail('RESOLUTION_DIGEST_MISMATCH')
            resolution = request['resolution']
            action = resolution['action']
            if action not in ('abandon','confirmed-running','confirmed-ended'):
                self.fail('ARGUMENTS_UNSUPPORTED')
            if row['phase'] not in ('exec-unknown','abandoned','ended'):
                self.fail('LAUNCH_OUTCOME_UNKNOWN')
            lives = self.live_rows(row['launch_id'])
            if action == 'confirmed-running':
                if not any(not r.get('ended') and r.get('receipt_nonce') == row['receipt_nonce'] for _,r in lives):
                    self.fail('LAUNCH_RECEIPT_MISMATCH')
                phase = 'running'
            else:
                phase = 'abandoned' if action == 'abandon' else 'ended'
            row.update(phase=phase,operator_resolution=resolution)
            self.save(self.launch_path,row,'launch_intent')
            if phase == 'ended':
                for name, live in lives:
                    if live.get('ended'):continue
                    self.api['atomic_json'](c.runtime_dir/'live-sessions'/name,{**live,'ended':True})
            return self.status(row['launch_id'])

    def pending_messages(self, mid=None):
        args = {'notification_pending_only':True,'include_bodies':True,'limit':1 if mid else 100}
        if mid:
            args['message_ids']=[mid]
        return self.client.call('fetch_inbox', args)

    def captured_owner_matches(self, intent):
        current = self.current()
        return all(intent.get(k) == current[k] for k in ('expected_server_instance_id','candidate_generation','authority_epoch','agent_id','credential_generation'))

    def captured_matches(self, intent):
        return self.captured_owner_matches(intent) and intent.get('launch_id')==self.current()['launch_id']

    def completion(self, path, intent, *, previous_launch=False):
        c = self.client
        if not (self.captured_owner_matches(intent) if previous_launch else self.captured_matches(intent)):
            self.fail('DELIVERY_ROUTE_CHANGED')
        intent['phase']='callback-pending'
        self.save(path,intent,'delivery_intent')
        request = {'version':1,'operation':'delivery_completed', **c.binding,
                   'recipient_agent_id':c.identity['agent_id'],
                   'expected_credential_generation':intent['credential_generation'],
                   'message_ids':[intent['message_id']]}
        # management() is the S1 enrollment envelope, while this is the typed
        # S2b operation envelope on the same Unix endpoint.
        with c.fence(), __import__('socket').socket(__import__('socket').AF_UNIX) as stream:
            stream.settimeout(15)
            stream.connect(c.config['management_socket'])
            stream.sendall(canonical(request).encode()+b'\n')
            data=b''
            while not data.endswith(b'\n') and len(data)<=1048576:
                chunk=stream.recv(65536)
                if not chunk:break
                data+=chunk
            result=json.loads(data)
            schema = self.api['message_contract']()['FIXTURE']['management_contract']['delivery_completed']['response_schema']
            self.api['wire_contract']()['validate_schema'](result,schema,'RESPONSE_INVALID',error_type=self.error)
            if not result.get('ok'):
                self.fail(result.get('reason','RESPONSE_INVALID'))
        # A fact on the server is the only completion record. No delivered cache.
        path.unlink()
        return {'ok':True,'message_id':intent['message_id'],'state':'complete'}

    def prepare(self, mid, channel, *, admissible=True, admission_reason=None, exclude_self=False):
        c=self.client
        with self.message(mid) as path:
            previous = self.local(path,'delivery_intent') if path.exists() or path.is_symlink() else None
            minimum=os.environ.get('AGENTSTACK_MAIL_NOTIFY_MIN_IMPORTANCE','low').lower()
            if previous:
                if previous['phase']=='blocked' and previous['error'] not in ('WAKE_RATE_LIMITED','BELOW_MIN_IMPORTANCE'):
                    return self.prepared_result(mid,'blocked',previous)
                if not self.captured_owner_matches(previous):
                    previous.update(phase='blocked',error='DELIVERY_ROUTE_CHANGED')
                    self.save(path,previous,'delivery_intent')
                    return self.prepared_result(mid,'blocked',previous)
                if previous['phase'] in ('external-confirmed','callback-pending'):
                    self.completion(path,previous,previous_launch=True)
                    return self.prepared_result(mid,'noop')
                if not self.captured_matches(previous):
                    # An old launch is evidence of possible delivery, not a
                    # poison pill for the next session's notification stream.
                    previous.update(**self.current(),phase='planned',error=None,
                        next_attempt_at=None,attempt_expires_at=None,duplicate_possible=True)
                    self.save(path,previous,'delivery_intent')
                if previous['phase']=='blocked' and previous['error']=='WAKE_RATE_LIMITED' and admission_reason=='WAKE_RATE_LIMITED':
                    return self.prepared_result(mid,'blocked',previous)
                if previous['phase']=='blocked' and previous['error']=='BELOW_MIN_IMPORTANCE' and previous.get('notify_min_importance')==minimum:
                    return self.prepared_result(mid,'blocked',previous)
                stamp=datetime.now(timezone.utc)
                times=[datetime.fromisoformat(previous[k]) for k in ('next_attempt_at','attempt_expires_at') if previous.get(k)]
                if times and stamp < max(times):return self.prepared_result(mid,'retry-wait',previous)
            if not admissible or os.environ.get('AGENTSTACK_MAIL_DISABLED') == '1':
                return self.prepared_result(mid,'retry-wait')
            stamp=datetime.now(timezone.utc)
            route_kind='codex-app' if c.registration_metadata().get('program')=='codex-app' else None
            selected=policy(channel,route_kind)
            count=previous['attempt_count'] if previous else 0
            if selected['max_attempts'] is not None and count>=selected['max_attempts']:
                previous.update(phase='blocked',error='DELIVERY_ATTEMPTS_EXHAUSTED')
                self.save(path,previous,'delivery_intent')
                return self.prepared_result(mid,'blocked',previous)
            messages=self.pending_messages(mid)
            if not messages:
                if previous:path.unlink()
                return self.prepared_result(mid,'noop')
            base=previous or {'kind':'orrery-global-delivery-intent-v1',**self.current(),
                'message_id':mid,'attempt_id':str(uuid.uuid4()),'attempt_count':0,
                'channel':channel,'duplicate_possible':False,'operator_resolution':None,
                'next_attempt_at':None,'attempt_expires_at':None}
            reason=('SELF_MESSAGE' if exclude_self and messages[0].get('sender',{}).get('agent_id')==c.identity['agent_id'] else
                    'BELOW_MIN_IMPORTANCE' if not notification_eligible(messages[0]) else admission_reason)
            if reason is not None:
                if reason not in ('SELF_MESSAGE','BELOW_MIN_IMPORTANCE','WAKE_RATE_LIMITED'):
                    self.fail('ARGUMENTS_UNSUPPORTED')
                base.update(phase='blocked',error=reason,next_attempt_at=None,attempt_expires_at=None)
                if reason=='BELOW_MIN_IMPORTANCE':base['notify_min_importance']=minimum
                self.save(path,base,'delivery_intent')
                return self.prepared_result(mid,'blocked',base)
            launch=self.launch()
            if launch['phase']!='running':return self.prepared_result(mid,'retry-wait')
            row={'kind':'orrery-global-delivery-intent-v1',**self.current(), 'message_id':mid,
                 'attempt_id':str(uuid.uuid4()),'phase':'external-unknown','attempt_count':count+1,
                 'next_attempt_at':(stamp+timedelta(seconds=selected['backoff'](count+1))).isoformat(),
                 'attempt_expires_at':(stamp+timedelta(seconds=selected['lease'])).isoformat(),
                 'error':None,'channel':channel,'duplicate_possible':count>0 or bool(previous and previous['duplicate_possible']), 'operator_resolution':None}
            self.save(path,row,'delivery_intent')
            return self.prepared_result(mid,'handoff',row,format_notification(messages[0]))

    @staticmethod
    def prepared_result(mid,state,intent=None,notification=None):
        return {'ok':True,'message_id':mid,'state':state,'attempt_id':intent['attempt_id'] if intent else None,
                'notification':notification,'reason':intent['error'] if intent else None,
                'duplicate_possible':intent['duplicate_possible'] if intent else False}

    def callback(self, mid, attempt, *, confirmed=False, known_failure=False):
        with self.message(mid) as path:
            row=self.local(path,'delivery_intent')
            if row['attempt_id']!=attempt or not self.captured_matches(row):
                self.fail('DELIVERY_ROUTE_CHANGED')
            if confirmed:
                row['phase']='external-confirmed'
                self.save(path,row,'delivery_intent')
                return self.completion(path,row)
            row['error']='KNOWN_NOT_SUBMITTED' if known_failure else 'DELIVERY_OUTCOME_UNKNOWN'
            row['duplicate_possible']=row['duplicate_possible'] or not known_failure
            if known_failure:row['attempt_expires_at']=None
            self.save(path,row,'delivery_intent')
            return {'ok':True,'message_id':mid,'state':'retry-planned'}

    def validate_execution(self, value):
        validate(value,'persistent_execution',self.error)
        c=self.client
        cwd=Path(value['cwd'])
        if not cwd.is_absolute() or not cwd.is_relative_to(c.isolation):self.fail('PATH_OUTSIDE_ISOLATION')
        current=c.isolation
        for part in cwd.relative_to(c.isolation).parts:
            current /= part
            self.api['safe_private_directory'](current)
        if shutil.which(value['argv'][0]) is None:self.fail('PROVIDER_UNAVAILABLE')
        if value['interaction']=='headless' and len(os.fsencode(c.runtime_dir/'provider-home/daemon/wake.sock'))>=104:
            self.fail('PERSISTENT_SOCKET_PATH_TOO_LONG')
        return value

    def execution(self):
        profile=self.read(self.client.profile_path)
        value=profile.get('execution')
        if value is None:self.fail('PERSISTENT_EXECUTION_REQUIRED')
        return self.validate_execution(value)

    def configure_execution(self, value):
        c=self.client
        with c.operation_mutex():
            prior=self.read(c.profile_path)
            c.validate_output(c.profile_path,'profile')
            self.validate_execution(value)
            c.write_output(c.profile_path,{**prior,'execution':value},'profile')
            return {'ok':True,'profile_path':str(c.profile_path),'configured':True}

    def plugin_helpers(self):
        base=Path(__file__).parent
        if not (base/'codex_plugin_trust.py').exists():
            base=Path(self.client.config['wrapper_root'])/'scripts'
        return runpy.run_path(str(base/'codex_plugin_trust.py')), runpy.run_path(str(base/'build-codex-app-marketplace.py'))

    def codex_trust(self):
        home=self.client.runtime_dir/'provider-home/codex'
        path=home/'hook-plan.json'
        try:
            plan=self.read(path)
            if (plan.get('shared_codex_home')!=str(home) or
                    plan.get('cwd')!=str(self.client.isolation)):
                self.fail('CODEX_HOOK_CONFIGURE_REQUIRED')
            self.plugin_helpers()[0]['verify_approved_plan'](plan)
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):
            self.fail('CODEX_HOOK_CONFIGURE_REQUIRED')
        return plan

    def configure_codex(self, action, approval=None):
        c=self.client
        home=self.directory(c.runtime_dir/'provider-home/codex')
        trust,builder=self.plugin_helpers()
        binary=shutil.which(os.environ.get('AGENTSTACK_CODEX_BIN','codex'))
        if binary is None:self.fail('PROVIDER_UNAVAILABLE')
        path=home/'hook-plan.json'
        if action=='plan':
            # All destinations come from the fixed provider HOME. This explicit
            # operator operation installs local payload, without granting trust.
            market=home/'marketplace'
            # The builder can remove its destination. Assert the sole fixed
            # target before permitting an explicit operator rebuild.
            if market != c.runtime_dir/'provider-home/codex/marketplace' or market.parent != home:
                self.fail('CODEX_HOOK_CONFIGURE_REQUIRED')
            if market.exists() or market.is_symlink():
                info=market.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:
                    self.fail('PRIVATE_DIRECTORY_REQUIRED')
            previous=self.read(path) if path.exists() or path.is_symlink() else None
            if previous is not None and (previous.get('schema_version')!=1 or
                    previous.get('shared_codex_home')!=str(home) or previous.get('cwd')!=str(c.isolation)):
                self.fail('CODEX_HOOK_CONFIGURE_REQUIRED')
            builder['build'](Path(c.config['wrapper_root'])/'integrations/codex_app',market,'agentstack-global')
            market.chmod(0o700)
            env=dict(os.environ,CODEX_HOME=str(home))
            for args in ([binary,'plugin','marketplace','add',str(market),'--json'],
                         [binary,'plugin','add','agentstack-codex-app@agentstack-global','--json']):
                result=subprocess.run(args,cwd=c.isolation,env=env,capture_output=True,text=True,timeout=30)
                if result.returncode:self.fail('CODEX_HOOK_CONFIGURE_FAILED')
            installed=json.loads(result.stdout)
            root=Path(installed['installedPath'])
            if root.is_symlink() or not root.resolve().is_relative_to(home.resolve()):self.fail('CODEX_HOOK_CONFIGURE_FAILED')
            plan=trust['capture_plan'](binary,home,c.isolation,root,installed['pluginId'])
            # Replanning always requires a fresh human approval, even when
            # bytes match. This keeps one rule for resumed and changed plans.
            if plan.get('approved') is not False:self.fail('CODEX_HOOK_CONFIGURE_FAILED')
            with c.operation_mutex(), c.fence():
                def unchanged():
                    current=self.read(path) if path.exists() or path.is_symlink() else None
                    if current!=previous:self.fail('CODEX_HOOK_CONFIGURE_REQUIRED')
                self.api['atomic_json'](path,plan,before_replace=unchanged)
            return plan
        if action!='apply':self.fail('ARGUMENTS_UNSUPPORTED')
        current=self.read(path)
        if not isinstance(approval,dict) or approval.get('approved') is not True or {**current,'approved':True}!=approval:
            self.fail('OPERATOR_CONFIRMATION_REQUIRED')
        if approval.get('shared_codex_home')!=str(home) or approval.get('cwd')!=str(c.isolation):
            self.fail('CODEX_HOOK_CONFIGURE_REQUIRED')
        trust['apply_trust'](approval)
        self.api['atomic_json'](path,approval,before_replace=lambda:self.fail('CODEX_HOOK_CONFIGURE_REQUIRED') if self.read(path)!=current else None)
        self.codex_trust()
        return {'ok':True,'configured':True}

    def provider_header(self, transcript):
        helper = self.client.config['wrapper_root']
        helper = Path(helper) / 'hooks/prepare-codex-session-binding.py'
        return runpy.run_path(str(helper))['_transcript_header_session_id'](Path(transcript))

    def generated_config(self, path, value, *, preserve_native=False):
        # Recompute owned output under C. Gemini has one native config: merge
        # only our entry, preserving provider/user fields in that same file.
        with self.client.operation_mutex():
            try:previous=self.api['read_private'](path) if path.exists() or path.is_symlink() else None
            except (OSError,ValueError,self.error):self.fail('GENERATED_CONFIG_UNREADABLE')
            if preserve_native and previous is not None:
                try:
                    native=json.loads(previous)
                    if not isinstance(native,dict) or not isinstance(native.get('mcpServers',{}),dict):raise ValueError()
                except (ValueError,UnicodeError):self.fail('GENERATED_CONFIG_JSON_INVALID')
                value={**native,'mcpServers':{**native.get('mcpServers',{}),**value['mcpServers']}}
            def unchanged():
                current=self.api['read_private'](path) if path.exists() or path.is_symlink() else None
                if current!=previous:self.fail('GENERATED_CONFIG_CHANGED')
            self.api['atomic_json'](path,value,before_replace=unchanged)

    def process_binding(self, session_id=None):
        with self.client.operation_mutex():
            launch = self.launch()
            captured = self.proxy_receipt or (os.environ.get('AGENTSTACK_LAUNCH_ID'),os.environ.get('AGENTSTACK_LAUNCH_NONCE'))
            if (captured[0] != launch['launch_id'] or
                    captured[1] != launch['receipt_nonce'] or
                    launch['phase'] != 'running'):
                self.fail('LAUNCH_RECEIPT_MISMATCH')
            lives = [row for _,row in self.live_rows(launch['launch_id'])
                     if not row.get('ended') and row.get('receipt_nonce') == launch['receipt_nonce']
                     and (session_id is None or row.get('session_id') == session_id)]
            if not lives:
                self.fail('LAUNCH_RECEIPT_MISMATCH')
            observed = self.client.observe()
            return {**observed, 'launch_id':launch['launch_id'], 'phase':launch['phase'],
                    'runtime_ready':bool(observed.get('window_ready'))}

    def launch_task(self, row, *, send_mail=False):
        c = self.client
        task = c.outputs['task']
        if not task.exists():
            return None
        content = self.api['read_private'](task)
        if hashlib.sha256(content).hexdigest() != row['task_sha256']:
            self.fail('TASK_CHANGED')
        if c.outputs['child'].exists():
            state = self.read(c.outputs['child'])
            if state['task_delivery'] == 'mail':
                if send_mail:
                    parent = self.api['RuntimeClient'](c.clients_parent/state['parent']['client_name']/'runtime-client.json')
                    if (parent.identity['agent_id'] != state['parent']['agent_id'] or
                            parent.binding != c.binding):
                        self.fail('CHILD_PARENT_MISMATCH')
                    # The server receipt is the send record. A restart derives
                    # the same UUID and reuses the sole canonical parent intent.
                    request_id = str(uuid.uuid5(uuid.NAMESPACE_OID, canonical({
                        'binding':c.binding, 'agent_id':c.identity['agent_id'],
                        'task_sha256':row['task_sha256'], 'purpose':'child-task'})))
                    parent.call('send_message', {'to_agent_ids':[c.identity['agent_id']],
                        'subject':'Delegated task', 'body_md':content.decode(), 'request_id':request_id})
                return 'Your delegated task is in ORRERY Mail. Fetch your inbox and read the task before beginning work.'
        return content.decode()

    def child_tool_plan(self, provider, home, cwd):
        """Use the existing tool policy against this provider's fixed HOME."""
        child = self.client.outputs['child']
        if not child.exists():
            return {}, [], ''
        policy_api = runpy.run_path(str(Path(self.client.config['wrapper_root'])/'hooks/child_tools.py'))
        selection = self.read(child)['tools_selection']
        try:
            spec = policy_api['normalize'](selection['base'], selection['tools'])
            policy_api['check_provider'](spec, provider)
            prompt = policy_api['prompt_text'](spec, provider)
            if provider == 'claude':
                settings = home/'.claude.json'
                if settings.exists() or settings.is_symlink():
                    self.api['read_private'](settings)
                plan = policy_api['claude_plan'](spec, cwd=cwd, chrome=False, claude_json=str(settings))
                if spec['base'] == 'default':
                    # Strict MCP configuration must preserve the fixed HOME's
                    # default servers rather than silently narrowing selection.
                    saved = policy_api['_load_claude_settings'](str(settings))
                    scopes = [saved.get('mcpServers', {}),
                              policy_api['_project_entry'](saved,cwd).get('mcpServers', {})]
                    for scope in scopes:
                        if isinstance(scope,dict):
                            for name in scope:
                                if not policy_api['looks_like_agent_mail'](name):
                                    plan['servers'][name] = policy_api['_copyable_definition'](name,saved,cwd)
                return plan['servers'], plan['flags'], prompt
            if provider == 'codex':
                import tomllib
                config_path = home/'config.toml'
                config = tomllib.loads(self.api['read_private'](config_path).decode()) if config_path.exists() else {}
                servers = config.get('mcp_servers', {})
                if spec['base'] == 'mail-only':
                    for name, value in servers.items():
                        if not policy_api['looks_like_agent_mail'](name):
                            value['enabled'] = False
                policy_api['codex_apply'](config, spec)
                serializer = runpy.run_path(str(Path(self.client.config['wrapper_root'])/'hooks/child_resume.py'))
                flags = []
                for name, value in config.get('mcp_servers', {}).items():
                    if name == 'orrery-mail':
                        continue
                    # All Mail aliases are replaced by the bound global proxy.
                    value = {**value, 'enabled':False} if policy_api['looks_like_agent_mail'](name) else value
                    flags += ['-c', 'mcp_servers.'+json.dumps(name)+'='+serializer['_toml_value'](value)]
                return {}, flags, prompt
            return {}, [], prompt
        except (ValueError, RuntimeError, KeyError, TypeError, OSError):
            self.fail('TOOL_SELECTION_UNAVAILABLE')

    def provider_command(self, row, provider_session_id=None, *, headless=False):
        c=self.client
        provider=row['provider']
        if row['launch_kind']=='resume':
            prior=self.read(c.runtime_dir/'session_index/self.json')
            saved_id=prior.get('session_id')
            if (not saved_id or prior.get('agent_id')!=c.identity['agent_id'] or
                    (provider_session_id is not None and provider_session_id!=saved_id)):
                self.fail('PROVIDER_SESSION_REQUIRED')
            if provider == 'codex' and self.provider_header(prior.get('transcript_path','')) != saved_id:
                self.fail('PROVIDER_SESSION_REQUIRED')
            provider_session_id=saved_id
        binary=os.environ.get('AGENTSTACK_'+provider.upper()+'_BIN') or {'claude':'claude','codex':'codex','gemini':'agy','daemon':sys.executable}[provider]
        home=self.directory(c.runtime_dir/'provider-home'/provider)
        extra_servers, tool_flags, tool_prompt = self.child_tool_plan(provider,home,row['cwd'])
        proxy=[sys.executable,str(Path(__file__).with_name('runtime_client.py')),'--context',str(c.path),'proxy']
        if provider=='claude':
            config=home/'mcp.json'
            self.generated_config(config,{'mcpServers':{**extra_servers,'orrery-mail':{'command':proxy[0],'args':proxy[1:]}}})
            args=[binary,'--mcp-config',str(config),'--strict-mcp-config']
            if row['launch_kind']=='resume':
                if not provider_session_id:self.fail('PROVIDER_SESSION_REQUIRED')
                args+=['--resume',provider_session_id]
        elif provider=='codex':
            plan=self.codex_trust()
            args=[binary,'-C',row['cwd'],'-c','check_for_update_on_startup=false',
                  '-c','mcp_servers.orrery-mail.command='+json.dumps(proxy[0]),
                  '-c','mcp_servers.orrery-mail.args='+json.dumps(proxy[1:]),
                  '-c','plugins.'+json.dumps(plan['plugin_id'])+'.mcp_servers.agentstack.enabled=false']
            if row['launch_kind']=='resume':
                if not provider_session_id:self.fail('PROVIDER_SESSION_REQUIRED')
                args+=(['exec','resume',provider_session_id] if headless else ['resume',provider_session_id])
        elif provider=='gemini':
            config=self.directory(home/'.gemini/config')/'mcp_config.json'
            self.generated_config(config,{'mcpServers':{'orrery-mail':{'command':proxy[0],'args':proxy[1:]}}},preserve_native=True)
            args=[binary,'--input-format','stream-json','--output-format','stream-json']
            if row['launch_kind']=='resume':
                if not provider_session_id:self.fail('PROVIDER_SESSION_REQUIRED')
                args+=['--resume',provider_session_id]
        elif c.profile_path.exists() and self.read(c.profile_path).get('execution') is not None:
            args=self.execution()['argv']
        else:
            args=[sys.executable,str(Path(__file__).with_name('runtime_client.py')),'--context',str(c.path),'daemon-serve']
        if provider=='claude':
            import shlex
            entry = shlex.join([sys.executable,str(Path(__file__).with_name('runtime_client.py')),'entry','session-start','--context',str(c.path)])
            hooks = {'SessionStart':[{'hooks':[{'type':'command','command':entry}]}]}
            def hook(operation):
                return {'type':'command','command':shlex.join([sys.executable,
                    str(Path(__file__).with_name('runtime_client.py')),'entry',operation,'--context',str(c.path)])}
            hooks.update(PreToolUse=[{'matcher':'Edit|Write|MultiEdit|NotebookEdit','hooks':[hook('pre-edit')]}],
                PostToolUse=[{'matcher':'Edit|Write|MultiEdit|NotebookEdit','hooks':[hook('post-edit')]},
                             {'hooks':[hook('notifications')]}],
                SessionEnd=[{'hooks':[hook('end-session')]}])
            settings = home / 'orrery-hooks.json'
            self.generated_config(settings,{'hooks':hooks})
            args += ['--settings',str(settings)]
        elif provider=='codex':
            self.codex_trust()
        args += tool_flags
        prompt = self.launch_task(row)
        if prompt and row['launch_kind']=='startup' and provider!='gemini':
            args.append(prompt + ('\n\n'+tool_prompt if tool_prompt else ''))
        return args

    def start(self, provider, cwd, *, detached=False, launch_kind='startup', provider_session_id=None):
        c=self.client
        if provider=='gemini' and (not c.outputs['child'].exists() or not c.outputs['task'].exists()):
            self.fail('GEMINI_INTERACTIVE_RUNTIME_UNSUPPORTED')
        if provider=='codex':self.codex_trust()
        row=self.prepare_launch(provider,launch_kind=launch_kind,cwd=cwd)
        self.provider_command(row,provider_session_id)  # all command/config checks precede window RPC
        self.bind_window()
        with c.operation_mutex():
            row=self.launch()
            row['phase']='gated'
            self.save(self.launch_path,row,'launch_intent')
        args=[sys.executable,str(Path(__file__).with_name('runtime_client.py')),'--context',str(c.path),
              'execute-launch',row['launch_id'],row['receipt_nonce']]
        if provider_session_id:args.append(provider_session_id)
        if detached:
            # tmux names are derived from the one launch UUID, never a Mail name.
            name='orrery-'+row['launch_id']
            result=subprocess.run(['tmux','new-session','-d','-s',name,'-c',row['cwd'],
                                   __import__('shlex').join(args)],capture_output=True,text=True,timeout=10)
            if result.returncode:
                # The shell may have started even if a response was lost. Never
                # manufacture evidence of absence from a failed transport.
                self.fail('LAUNCH_OUTCOME_UNKNOWN')
        else:
            self.execute(row['launch_id'],row['receipt_nonce'],provider_session_id)
        return {'ok':True,'launch_id':row['launch_id'],'prepared':True,'runtime_ready':False,'phase':'gated','reason':None}

    def execute(self, launch_id, nonce, provider_session_id=None):
        c=self.client
        row=self.launch()
        if row['launch_id']!=launch_id or row['receipt_nonce']!=nonce or row['phase']!='gated':
            self.fail('LAUNCH_RECEIPT_MISMATCH')
        self.launch_task(row,send_mail=True)
        with c.operation_mutex():
            row=self.launch()
            if row['launch_id']!=launch_id or row['receipt_nonce']!=nonce or row['phase']!='gated':
                self.fail('LAUNCH_RECEIPT_MISMATCH')
            args=self.provider_command(row,provider_session_id)
            c.observe()
            row['phase']='exec-unknown'
            self.save(self.launch_path,row,'launch_intent')
            env=dict(os.environ)
            for key in ('AGENTSTACK_PROJECT_KEY','PROJECT_KEY','PARENT_AGENT','CHILD_REGISTRATION_TOKEN','MCP_AGENT_MAIL_TOKEN','AGENTSTACK_PROXY_TOKEN_FILE','AGENTSTACK_PROXY_AGENT_NAME'):
                env.pop(key,None)
            home=str(c.runtime_dir/'provider-home'/row['provider'])
            env.update(AGENTSTACK_CLIENT_CONFIG=str(c.path),AGENTSTACK_RUNTIME_MODE='global',
                       AGENTSTACK_LAUNCH_ID=launch_id,AGENTSTACK_LAUNCH_NONCE=nonce,
                       AGENTSTACK_PROVIDER_PID=str(os.getpid()),AGENTSTACK_RUNTIME_DIR=str(c.runtime_dir),
                       AGENTSTACK_MCP_URL=c.endpoint,AGENT_NAME=c.identity['name'],
                       AGENTSTACK_HOME=c.config['wrapper_root'],AGENTSTACK_AGENT_ID=str(c.identity['agent_id']))
            env['CODEX_HOME']=home
            env['CLAUDE_CONFIG_DIR']=home
            if row['provider']=='gemini':
                env['HOME']=home
                env.pop('GEMINI_CLI_HOME',None)
            if row['provider']=='daemon' and c.profile_path.exists() and self.read(c.profile_path).get('execution') is not None:
                execution=self.execution()
                if execution['interaction']=='headless':
                    env['AGENTSTACK_PERSISTENT_WAKE_SOCKET']=str(Path(home)/'wake.sock')
                    env['AGENTSTACK_PERSISTENT_BRIDGE_CONTRACT']='orrery-mail-notification-reply-v1'
        # Exec/wait must never retain C: SessionStart needs that same mutex.
        if row['provider']=='gemini':
            raise SystemExit(self.run_gemini(args,env,row))
        os.chdir(row['cwd'])
        os.execvpe(args[0],args,env)

    def run_gemini(self, args, env, row):
        c=self.client
        content=self.api['read_private'](c.outputs['task'])
        if hashlib.sha256(content).hexdigest()!=row['task_sha256']:
            self.fail('TASK_CHANGED')
        renderer=runpy.run_path(str(Path(c.config['wrapper_root'])/'bin/agentstack-gemini-stream'))
        log=c.runtime_dir/'provider-home/gemini/result.jsonl'
        if log.exists() or log.is_symlink():self.api['read_private'](log)
        self.api['atomic_bytes'](log,b'')
        session=None
        process=subprocess.Popen(args,cwd=row['cwd'],env=env,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=sys.stderr,text=True)
        try:
            process.stdin.write(json.dumps({'event':'user','message':{'content':self.launch_task(row)}},ensure_ascii=False)+'\n')
            process.stdin.close()
            def event(value):
                nonlocal session
                if value.get('event')=='init':
                    sid=value.get('conversation_id')
                    if session is not None and session!=sid:self.fail('PROVIDER_SESSION_REQUIRED')
                    self.session_start({'session_id':sid,'provider_pid':process.pid,'cwd':row['cwd']},
                        trusted_receipt=(row['launch_id'],row['receipt_nonce']))
                    session=sid
            rendered=renderer['consume'](log,process.stdout,event)
            status=process.wait(timeout=15)
            if session is None:self.fail('GEMINI_SESSION_RECEIPT_UNAVAILABLE')
            c.end_session({'session_id':session})
            return status or rendered
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            process.stdout.close()
            if not process.stdin.closed:process.stdin.close()

    def session_start(self, payload, *, trusted_receipt=None):
        c=self.client
        with c.operation_mutex():
            row=self.launch()
            launch_id, nonce = trusted_receipt if trusted_receipt is not None else (os.environ.get('AGENTSTACK_LAUNCH_ID'),os.environ.get('AGENTSTACK_LAUNCH_NONCE'))
            if (row['phase'] not in ('exec-unknown','running') or launch_id!=row['launch_id'] or nonce!=row['receipt_nonce']):
                self.fail('LAUNCH_RECEIPT_MISMATCH')
            if row['provider']=='codex':
                if self.provider_header(payload.get('transcript_path','')) != payload.get('session_id'):
                    self.fail('PROVIDER_SESSION_REQUIRED')
            observed = c.call('whois')
            observed.update({k:c.identity[k] for k in ('window_row_id','window_uuid') if k in c.identity})
            c.record_session({**payload,'tool_response':observed})
            evidence=c.record_live(payload,refresh=True)
            evidence.update(launch_id=row['launch_id'],receipt_nonce=row['receipt_nonce'])
            path=c.runtime_dir/'live-sessions'/(payload['session_id']+'.json')
            self.api['atomic_json'](path,evidence)
            row['phase']='running'
            self.save(self.launch_path,row,'launch_intent')
            return self.status(row['launch_id'])

    def delivery_status(self, mid, *, missing_ok=False):
        with self.message(mid) as path:
            if missing_ok and not path.exists() and not path.is_symlink():return None
            row=self.local(path,'delivery_intent')
            verified,reason=self.remote_verified()
            captured={k:row[k] for k in ('expected_server_instance_id','candidate_generation','authority_epoch','agent_id','credential_generation','launch_id')}
            return {'ok':True,'state_sha256':self.state_digest('delivery',mid,row),
                    'operator_required':not policy_blocked(row) and (row['phase']=='blocked' or not self.captured_matches(row)),
                    'next_operations':[] if policy_blocked(row) else ['resolve-delivery'],'current':self.status_current(),
                    'remote_owner_verified':verified,'reason':reason or row['error'],
                    'message_id':mid,'phase':row['phase'],'captured':captured,
                    **{k:row[k] for k in ('attempt_id','attempt_count','attempt_expires_at','next_attempt_at','duplicate_possible','operator_resolution')}}

    def resolve_delivery(self, request):
        mid=request['message_id']
        with self.message(mid) as path:
            row=self.local(path,'delivery_intent')
            if self.state_digest('delivery',mid,row)!=request['expected_state_sha256']:
                self.fail('RESOLUTION_DIGEST_MISMATCH')
            resolution=request['resolution']
            action=resolution['action']
            if action not in ('retry-at-least-once','confirm-external-completed','abandon-without-consuming'):
                self.fail('ARGUMENTS_UNSUPPORTED')
            if action=='confirm-external-completed' and not self.captured_owner_matches(row):self.fail('DELIVERY_ROUTE_CHANGED')
            row.update(operator_resolution=resolution)
            if action=='confirm-external-completed':
                row['phase']='external-confirmed'
            elif action=='abandon-without-consuming':
                row.update(phase='blocked',error='OPERATOR_ABANDONED')
            else:
                row.update(**self.current(),phase='planned',error=None,next_attempt_at=None,attempt_expires_at=None,duplicate_possible=True,attempt_count=0)
            self.save(path,row,'delivery_intent')
        return self.delivery_status(mid)

    def operator(self, operation, request):
        fixture,validator=contract()
        table={**fixture['local_operator_wire']['operations'],**fixture['local_operator_wire']['internal_operations']}
        if operation not in table:self.fail('ARGUMENTS_UNSUPPORTED')
        definition=table[operation]
        validator(request,definition['request_schema'],'ARGUMENTS_UNSUPPORTED',error_type=self.error)
        if operation=='launch':
            result=self.start(request['provider'],os.getcwd(),detached=request['detached'],launch_kind=request['launch_kind'],provider_session_id=request.get('provider_session_id'))
        elif operation=='launch-status':result=self.status(request['launch_id'])
        elif operation=='launch-resolve':result=self.resolve_launch(request)
        elif operation=='delivery-status':result=self.delivery_status(request['message_id'])
        elif operation=='resolve-delivery':result=self.resolve_delivery(request)
        elif operation=='notification-prepare':result=self.prepare(request['message_id'],request['channel'])
        elif operation in ('notification-confirm','notification-failure'):
            result=self.callback(request['message_id'],request['attempt_id'],confirmed=operation=='notification-confirm',known_failure=operation=='notification-failure' and request['outcome']=='known-not-submitted')
        else:
            with self.client.operation_mutex():
                row=self.launch()
                if row['launch_id']!=request['launch_id'] or self.state_digest('launch',row['launch_id'],row)!=request['expected_state_sha256']:
                    self.fail('RESOLUTION_DIGEST_MISMATCH')
                if row['phase'] not in ('planned','gated'):self.fail('LAUNCH_OUTCOME_UNKNOWN')
                if operation=='launch-cancel':
                    row['phase']='cancelled'
                    self.save(self.launch_path,row,'launch_intent')
                    result={'ok':True,'launch_id':row['launch_id'],'prepared':True,'runtime_ready':False,'phase':'cancelled','reason':None}
                else:
                    self.bind_window()
                    row=self.launch()
                    row['phase']='gated'
                    self.save(self.launch_path,row,'launch_intent')
                    result={'ok':True,'launch_id':row['launch_id'],'prepared':True,'runtime_ready':False,'phase':'gated','reason':None}
            if operation=='launch-retry':
                name='orrery-'+row['launch_id']
                probe=subprocess.run(['tmux','has-session','-t','='+name],capture_output=True,text=True,timeout=5)
                if probe.returncode!=0:
                    args=[sys.executable,str(Path(__file__).with_name('runtime_client.py')),'--context',str(self.client.path),'execute-launch',row['launch_id'],row['receipt_nonce']]
                    created=subprocess.run(['tmux','new-session','-d','-s',name,'-c',row['cwd'],__import__('shlex').join(args)],capture_output=True,timeout=10)
                    if created.returncode:
                        # Another retry may have created this exact UUID session.
                        verified=subprocess.run(['tmux','has-session','-t','='+name],capture_output=True,timeout=5)
                        if verified.returncode:self.fail('LAUNCH_OUTCOME_UNKNOWN')
        response=definition.get('response_schema')
        if response is None:
            name,field=definition['response_ref'].split('.')
            response=table[name][field]
        validator(result,response,'RESPONSE_INVALID',error_type=self.error)
        return result

    def proxy(self, source=sys.stdin, output=sys.stdout):
        bound = False
        external_reference = None
        excluded={'ensure_project','register_agent','health_check','retire_agent','unretire_agent','whois','bind_window_identity'}
        auth={'agent_id','registration_token','expected_credential_generation','expected_server_instance_id','candidate_generation','authority_epoch','project_key','agent_name','sender_name','human_key'}
        for line in source:
            reply={'jsonrpc':'2.0'}
            try:
                request=json.loads(line)
                if 'id' not in request:continue
                reply['id']=request['id']
                method=request['method']; params=request.get('params',{})
                if method=='initialize':
                    result={'protocolVersion':params.get('protocolVersion','2025-06-18'),'capabilities':{'tools':{}},'serverInfo':{'name':'orrery-mail','version':'global-4c-v1'}}
                elif method=='tools/list':
                    _,schemas=self.client.capabilities()
                    tools=[]
                    for name,schema in schemas.items():
                        if name in excluded:continue
                        schema=json.loads(json.dumps(schema))
                        schema['properties']={k:v for k,v in schema.get('properties',{}).items() if k not in auth}
                        schema['required']=[k for k in schema.get('required',[]) if k not in auth]
                        schema['additionalProperties']=False
                        tools.append({'name':name,'description':name,'inputSchema':schema})
                    tools.append({'name':'bootstrap','description':'Verify this process against its trusted launch receipt.','inputSchema':{'type':'object','properties':{'session_id':{'type':'string'},'agent_id':{'type':['string','null']}},'additionalProperties':False}})
                    tools.append({'name':'runtime_status','description':'Read this bound runtime identity.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}})
                    result={'tools':tools}
                elif method=='tools/call':
                    name=params['name']; args=params.get('arguments',{})
                    if (name!='bootstrap' and any(k in auth for k in args)) or name in excluded:self.fail('ARGUMENTS_UNSUPPORTED')
                    if name=='bootstrap':
                        reference=(args.get('session_id'),args.get('agent_id'))
                        if external_reference is not None and external_reference != reference:self.fail('LAUNCH_RECEIPT_MISMATCH')
                        if set(args)-{'session_id','agent_id'}:self.fail('ARGUMENTS_UNSUPPORTED')
                        if not os.environ.get('AGENTSTACK_LAUNCH_ID') and self.proxy_receipt is None:
                            target=self.external_client(args.get('session_id'),args.get('agent_id'))
                            self.client=target;self.launch_path=target.outputs['launch']
                            row=self.launch();self.proxy_receipt=(row['launch_id'],row['receipt_nonce'])
                            external_reference=reference
                        value=self.process_binding(args.get('session_id'))
                        bound=True
                    elif name=='runtime_status':
                        if args:self.fail('ARGUMENTS_UNSUPPORTED')
                        value=self.process_binding()
                    else:
                        if not bound:self.fail('BOOTSTRAP_REQUIRED')
                        self.process_binding()
                        value=self.client.call(name,args)
                    result={'content':[{'type':'text','text':canonical(value)}]}
                elif method=='ping':result={}
                else:self.fail('METHOD_UNSUPPORTED')
                reply['result']=result
            except (self.error,ValueError,KeyError,TypeError) as exc:
                reply['error']={'code':-32602,'message':str(exc) if isinstance(exc,self.error) else 'REQUEST_INVALID'}
            output.write(canonical(reply)+'\n'); output.flush()

    def hints(self):
        c=self.client
        directory=c.runtime/'signals/agents'/str(c.identity['agent_id'])
        if not directory.exists():return []
        self.api['safe_private_directory'](directory)
        descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            original=os.fstat(descriptor)
            mids=[]
            for name in os.listdir(descriptor):
                if not __import__('re').fullmatch(r'[1-9][0-9]*\.signal',name):continue
                try:
                    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=descriptor)
                except FileNotFoundError:
                    continue  # server consumed this hint after directory listing
                try:
                    info=os.fstat(fd)
                    if info.st_nlink == 0:
                        continue  # server unlinked an already-open hint
                    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_nlink!=1:
                        self.fail('PRIVATE_FILE_UNSAFE')
                    raw=os.read(fd,16385)
                finally:os.close(fd)
                if len(raw)>16384:self.fail('OUTPUT_SCHEMA_INVALID')
                value=json.loads(raw)
                expected={'kind':'orrery-global-message-signal-v1','version':1,
                    'server_instance_id':c.binding['expected_server_instance_id'],
                    'candidate_generation':c.binding['candidate_generation'],'authority_epoch':c.binding['authority_epoch'],
                    'recipient_agent_id':c.identity['agent_id'],'message_id':int(name[:-7])}
                if value!=expected:self.fail('DELIVERY_ROUTE_CHANGED')
                mids.append(value['message_id'])
            current=directory.lstat()
            if (original.st_dev,original.st_ino)!=(current.st_dev,current.st_ino):self.fail('PRIVATE_DIRECTORY_REQUIRED')
            return sorted(mids)
        finally:os.close(descriptor)

    def tmux_target(self):
        row=self.launch()
        if row['phase']!='running':return None
        for _,live in reversed(self.live_rows(row['launch_id'])):
            pane=live.get('pane_id')
            if not pane or live.get('ended'):continue
            # A pane may have been recycled after its provider exited. The
            # canonical live-session receipt owns PID/start evidence; unknown
            # evidence is ineligible and never consumes a handoff attempt.
            start=self.api['process_start'](live.get('pid'))
            if not start or start=='absent' or start!=live.get('process_start'):continue
            try:
                process=subprocess.run(['tmux','display-message','-p','-t',pane,'#{pane_pid}'],capture_output=True,text=True,timeout=5)
                if process.returncode or process.stdout.strip()!=str(live.get('pid')):continue
                result=subprocess.run(['tmux','display-message','-p','-t',pane,'#{pane_current_command}'],capture_output=True,text=True,timeout=5)
                if result.returncode or result.stdout.strip() in ('bash','zsh','sh','fish','dash','ksh',''):continue
                capture=subprocess.run(['tmux','capture-pane','-p','-t',pane,'-S','-20'],capture_output=True,text=True,timeout=5)
                if capture.returncode:continue
                return pane
            except (OSError,subprocess.TimeoutExpired):continue
        return None

    def deliver_persistent(self, mid):
        import socket
        c=self.client
        path=c.runtime_dir/'provider-home/daemon/wake.sock'
        admissible=False
        if path.exists() or path.is_symlink():
            info=path.lstat()
            admissible=stat.S_ISSOCK(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077
        prepared=self.prepare(mid,'persistent',admissible=admissible)
        if prepared['state']!='handoff':return prepared
        try:
            messages=self.pending_messages(mid)
            if not messages:return self.callback(mid,prepared['attempt_id'],known_failure=True)
            request={'version':1,'contract':'orrery-mail-notification-reply-v1','type':'mail-notification',
                'agent_name':c.identity['name'],'message':messages[0],
                'reply_request_id':str(uuid.uuid5(uuid.NAMESPACE_OID,c.binding['expected_server_instance_id']+':'+str(c.identity['agent_id'])+':'+str(mid)))}
            with socket.socket(socket.AF_UNIX) as stream:
                stream.settimeout(policy('persistent')['process_timeout'])
                stream.connect(str(path));stream.sendall(canonical(request).encode()+b'\n')
                data=b''
                while not data.endswith(b'\n') and len(data)<=65536:
                    block=stream.recv(4096)
                    if not block:break
                    data+=block
            response=json.loads(data)
            confirmed=all(response.get(k)==v for k,v in {'version':1,'contract':request['contract'],'status':'replied','message_id':mid}.items())
            return self.callback(mid,prepared['attempt_id'],confirmed=confirmed)
        except (OSError,ValueError,TypeError):
            return self.callback(mid,prepared['attempt_id'])

    def deliver_tmux(self, mid):
        target=self.tmux_target()
        prepared=self.prepare(mid,'tmux',admissible=target is not None)
        if prepared['state']!='handoff':return prepared
        # No local mutex is held while invoking tmux or waiting for it.
        buffer='orrery-'+prepared['attempt_id']
        try:
            loaded=subprocess.run(['tmux','load-buffer','-b',buffer,'-'],input=prepared['notification'],text=True,capture_output=True,timeout=5)
            if loaded.returncode:
                return self.callback(mid,prepared['attempt_id'],known_failure=True)
            pasted=subprocess.run(['tmux','paste-buffer','-d','-p','-b',buffer,'-t',target],capture_output=True,timeout=5)
            if pasted.returncode:
                return self.callback(mid,prepared['attempt_id'])
            submitted=subprocess.run(['tmux','send-keys','-t',target,'C-m'],capture_output=True,timeout=5)
            return self.callback(mid,prepared['attempt_id'],confirmed=submitted.returncode==0)
        except (OSError,subprocess.TimeoutExpired):
            return self.callback(mid,prepared['attempt_id'])

    def intent_ids(self):
        directory=self.consumer_agent(create=False)/'messages'
        if not directory.exists():return []
        self.api['safe_private_directory'](directory)
        # Names are candidates only. All child admission belongs to message(mid)
        # inside the caller's per-message exception boundary.
        return [int(name) for name in os.listdir(directory)
                if __import__('re').fullmatch(r'[1-9][0-9]*',name)]

    def reconcile_confirmed(self):
        # The existing intent recovers an acknowledgement after the server has
        # consumed its hint. There is no second completion ledger.
        hints=None
        for mid in self.intent_ids():
            try:
                with self.message(mid) as path:
                    if not path.exists() and not path.is_symlink():continue
                    row=self.local(path,'delivery_intent')
                    if policy_blocked(row):
                        if hints is None:hints=set(self.hints())
                        if mid not in hints:path.unlink()
                        continue
                    if row['phase'] not in ('external-confirmed','callback-pending'):continue
                    if not self.captured_owner_matches(row):
                        row.update(phase='blocked',error='DELIVERY_ROUTE_CHANGED')
                        self.save(path,row,'delivery_intent')
                        continue
                    self.completion(path,row,previous_launch=True)
            except (self.error,OSError,ValueError) as exc:self.report_error(mid,exc)

    def report_error(self, scope, exc):
        reason=str(exc) if isinstance(exc,self.error) else 'LOCAL_STATE_UNAVAILABLE'
        print('global-notification '+str(scope)+': '+reason,file=sys.stderr)

    def hook_notifications(self, channel):
        if os.environ.get('AGENTSTACK_MAIL_DISABLED')=='1':return []
        try:self.reconcile_confirmed()
        except (self.error,OSError,ValueError) as exc:self.report_error('reconcile',exc)
        result=[]
        for mid in self.hints():
            try:
                item=self.prepare(mid,channel)
                if item['state']=='handoff':result.append(item)
            except (self.error,OSError,ValueError) as exc:self.report_error(mid,exc)
        return result

    def diagnostics(self):
        warnings=[]
        if self.launch_path.exists() or self.launch_path.is_symlink():
            row=self.launch()
            status=self.status(row['launch_id'])
            if status['operator_required']:
                warnings.append({'reason':row['error'] or 'LAUNCH_OUTCOME_UNKNOWN',
                    'operation':'launch-status','launch_id':row['launch_id'],'next':'launch-resolve'})
        for mid in self.intent_ids():
            try:status=self.delivery_status(mid,missing_ok=True)
            except FileNotFoundError:continue  # concurrent completion removed it
            except (self.error,OSError,ValueError) as exc:
                warnings.append({'reason':str(exc) if isinstance(exc,self.error) else 'LOCAL_STATE_UNAVAILABLE',
                    'operation':'delivery-status','message_id':mid,'next':'repair-local-state'})
                continue
            if status is not None and status['operator_required']:
                warnings.append({'reason':status['reason'] or 'DELIVERY_ROUTE_CHANGED',
                    'operation':'delivery-status','message_id':mid,'next':'resolve-delivery'})
        return warnings

    def watch_once(self):
        # Use routes only as immutable client-name locators, never token/name
        # or completion state. Each actual operation reloads under A -> C -> M.
        c=self.client
        if os.environ.get('AGENTSTACK_MAIL_DISABLED')=='1':return
        consumer=self.consumer_agent().parent
        for folder in consumer.iterdir():
            if not __import__('re').fullmatch(r'[1-9][0-9]*',folder.name):continue
            route_dir=folder
            if not (route_dir/'route.json').exists() and not (route_dir/'route.json').is_symlink():continue
            try:
                self.api['safe_private_directory'](route_dir)
                with self.dir_lock(route_dir,exclusive=False):
                    route=self.local(route_dir/'route.json','runtime_route')
                    target=self.api['RuntimeClient'](c.clients_parent/route['client_name']/'runtime-client.json')
                    if target.identity['agent_id']!=int(folder.name) or target.binding!=c.binding:
                        self.fail('ROUTE_OWNER_CONFLICT')
                delivery=RuntimeDelivery(target,self.api)
                if target.registration_metadata().get('program')=='codex-app':continue
                delivery.reconcile_confirmed()
                persistent=target.profile_path.exists() and delivery.read(target.profile_path).get('execution',{}).get('interaction')=='headless'
                for mid in delivery.hints():
                    try:
                        if persistent:delivery.deliver_persistent(mid)
                        else:delivery.deliver_tmux(mid)
                    except (self.error,OSError,ValueError) as exc:delivery.report_error(mid,exc)
                self._watch_errors.pop(folder.name,None)
            except (self.error,OSError,ValueError) as exc:
                # Diagnostics are derived from the existing intent, no warning
                # ledger and no dependence on Mail to report a local failure.
                reason=str(exc) if isinstance(exc,self.error) else 'LOCAL_STATE_UNAVAILABLE'
                if self._watch_errors.get(folder.name)!=reason:
                    print('global-mail-watcher: '+reason,file=sys.stderr)
                    self._watch_errors[folder.name]=reason

    def watch(self, stop=None):
        """Directory events with the legacy rescue interval, or legacy polling."""
        import select
        import threading
        stop=stop or threading.Event()
        settings=legacy_policy()
        directory=self.client.runtime/'signals'
        # Signals are server-owned. Wait without creating them when the server
        # has not yet reconciled; admission and event setup happen afterwards.
        while not stop.is_set():
            try:self.api['safe_private_directory'](directory)
            except FileNotFoundError:
                stop.wait(settings['poll']);continue
            break
        if stop.is_set():return
        events=None
        if shutil.which('fswatch'):
            try:
                events=subprocess.Popen(['fswatch','-0','-r','--event','Created','--event','Updated',str(directory)],
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            except OSError:events=None
        try:
            while not stop.is_set():
                self.watch_once()
                if stop.is_set():break
                if events is None:
                    stop.wait(settings['poll'])
                elif events.poll() is not None:
                    events.stdout.close();events=None
                else:
                    ready,_,_=select.select([events.stdout],[],[],settings['scan'])
                    if ready:os.read(events.stdout.fileno(),65536)
        finally:
            if events is not None:
                events.terminate()
                try:events.wait(timeout=5)
                except subprocess.TimeoutExpired:events.kill();events.wait()
                events.stdout.close()
