"""Real HTTP admission → real private socket, with no dashboard SQLite access."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from dashboard import server as dashboard
from dashboard.http_operator import configure, read_capability
from test_global_child_client import server


@pytest.fixture
def ui(server,monkeypatch):
    home=server['root']/'agentstack'
    configure(home)
    monkeypatch.setenv('AGENTSTACK_HOME',str(home))
    monkeypatch.setenv('AGENTSTACK_GLOBAL_SERVER_CONFIG',str(server['config_path']))
    monkeypatch.setenv('AGENTSTACK_MCP_URL',server['url'])
    monkeypatch.setattr(dashboard,'GLOBAL_MODE',True)
    monkeypatch.setattr(dashboard,'BIND_HOST','127.0.0.1')
    http=ThreadingHTTPServer(('127.0.0.1',0),dashboard.Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    # Public management transport must never use even a readonly SQLite handle.
    def forbidden(*a,**kw):raise AssertionError('dashboard SQLite opened')
    monkeypatch.setattr(dashboard.sqlite3,'connect',forbidden)
    try:yield server,http,read_capability(home)
    finally:http.shutdown();http.server_close();thread.join(timeout=5)


def request(ui,path,*,auth=True,method='GET',extra=None,body=None):
    state,http,token=ui
    headers={'Authorization':'Bearer '+token} if auth else {}
    headers.update(extra or {})
    if body is not None:headers['Content-Type']='application/json'
    url=f'http://127.0.0.1:{http.server_port}'+path
    try:
        with urlopen(Request(url,headers=headers,method=method,data=json.dumps(body).encode() if body is not None else None),timeout=5) as response:
            return response.status,response.read()
    except HTTPError as exc:return exc.code,exc.read()


def test_api_and_visible_message_bridge_without_sqlite(ui):
    code,raw=request(ui,'/api/version')
    assert code==200 and json.loads(raw)['api']==dashboard.API_GENERATION
    assert json.loads(raw)['capabilities']['global_dashboard_ui']==0
    code,raw=request(ui,'/api/agents?limit=1')
    page=json.loads(raw)
    assert code==200 and page['runtime_mode']=='global' and len(page['data']['items'])==1,raw
    assert 'registration_token' not in raw.decode()
    code,raw=request(ui,'/api/mail/recent?view_as_agent_id=2&limit=1')
    page=json.loads(raw)
    assert code==200,raw
    mid=page['data']['items'][0]['id']
    code,raw=request(ui,f'/api/mail/message?view_as_agent_id=2&id={mid}')
    assert code==200 and json.loads(raw)['data']['body_md'] is not None
    code,raw=request(ui,'/api/mail-watcher-health')
    assert code==200 and json.loads(raw)['data']['server_ready'] is True


@pytest.mark.parametrize('method',['GET','POST','PUT','PATCH','DELETE','OPTIONS'])
def test_every_nonstatic_route_passes_admission_before_dispatch(ui,method):
    for path in ['/api/agents','/api/quotas','/api/global/spawn','/api/unknown','/future-operation']:
        code,raw=request(ui,path,auth=False,method=method)
        assert code==403 and json.loads(raw)['reason']=='HTTP_OPERATOR_REQUIRED'
    assert request(ui,'/',auth=False)[0]==200


@pytest.mark.parametrize('path,reason',[
    ('/api/mail/recent','UI_VIEWPOINT_REQUIRED'),
    ('/api/mail/recent?view_as_agent_id=bad','QUERY_INVALID'),
    ('/api/mail/recent?view_as_agent_id=2&limit=101','QUERY_INVALID'),
    ('/api/mail/legacy-label?view_as_agent_id=2&label=old&source=1','QUERY_INVALID'),
    ('/api/mail/recent?view_as_agent_id=2&view_as_agent_id=1','QUERY_INVALID'),
])
def test_http_input_refusal_is_not_a_legacy_fallback(ui,path,reason):
    code,raw=request(ui,path)
    assert code==400 and json.loads(raw)['reason']==reason,raw


def test_wrong_origin_host_and_capability_rejected(ui):
    assert request(ui,'/api/version',extra={'Origin':'http://evil.example'})[0]==403
    assert request(ui,'/api/version',extra={'Host':'evil.example'})[0]==403
    assert request(ui,'/api/version',extra={'Authorization':'Bearer wrong'})[0]==403


def test_spawn_http_and_same_name_discovery_use_fixed_root(ui):
    import os
    body={'client_name':'http-new','name':'HttpOwner','program':'daemon',
        'model':'deterministic','cwd':os.environ['HOME']}
    code,raw=request(ui,'/api/global/spawn',method='POST',body=body)
    state=json.loads(raw)
    assert code==200 and state['stage']=='registered_unconfigured',raw
    literal=ui[0]['root']/'agentstack/clients/http-new/runtime-client.json'
    assert literal.exists()
    code,raw=request(ui,'/api/global/spawn-status?client_name=http-new')
    assert code==200 and json.loads(raw)['agent_id']==state['agent_id']
    code,raw=request(ui,'/api/global/clients?name=HttpOwner')
    assert code==200 and json.loads(raw)['matches'][0]['client_name']=='http-new'
    code,raw=request(ui,'/api/global/spawn',method='POST',body={**body,'client_name':'other'})
    assert code==409 and json.loads(raw)['reason']=='EXISTING_CLIENT_REQUIRED'
    assert not (literal.parent.parent/'other').exists()


def test_bad_spawn_input_is_rejected_before_any_root(ui):
    import os
    body={'client_name':'../escape','name':'HttpOwner','program':'daemon',
        'model':'deterministic','cwd':os.environ['HOME']}
    code,raw=request(ui,'/api/global/spawn',method='POST',body=body)
    assert code==400 and json.loads(raw)['reason']=='QUERY_INVALID'
    assert not (ui[0]['root']/'agentstack/clients').exists()
