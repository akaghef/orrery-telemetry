"""Optional real-browser regression with synthetic HTTP data and own CDP tabs.

Run through an isolated HOME/TMUX runner. --cdp-helper points to the existing
browser bridge; this script never launches a browser or a provider binary.
"""
import sys,pathlib,json,threading,base64,time,hashlib,argparse
from http.server import ThreadingHTTPServer
parser=argparse.ArgumentParser();parser.add_argument('--output',type=pathlib.Path,required=True);parser.add_argument('--cdp-helper',type=pathlib.Path,required=True);options=parser.parse_args()
ROOT=pathlib.Path(__file__).resolve().parents[1];QA=options.output;QA.mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'packages/agentstack_mail/tests'),str(ROOT)]
import pytest
from service_teardown import TEST_LABEL_PREFIX
from test_global_child_client import server
from dashboard import server as dashboard
from dashboard.http_operator import configure,read_capability
from dashboard.global_backend import OPERATOR_API
from agentstack_mail.global_s2c import S2cRuntime
from test_global_server_s2b import owner
import uuid
cdp={};exec(options.cdp_helper.read_text().split('\ndef main():')[0],cdp)
result={};tabs=[]
with pytest.MonkeyPatch.context() as mp:
 gen=server.__wrapped__(mp);s=next(gen);http=None
 try:
  mp.setenv('AGENTSTACK_MCP_URL',s['url']);mp.setenv('AGENTSTACK_GLOBAL_SERVER_CONFIG',str(s['config_path']))
  home=s['root']/'agentstack';configure(home);mp.setenv('AGENTSTACK_HOME',str(home));mp.setenv('AGENTSTACK_LABEL_PREFIX',TEST_LABEL_PREFIX);token=read_capability(home)
  mp.setattr(dashboard,'GLOBAL_MODE',True);mp.setattr(dashboard,'BIND_HOST','127.0.0.1')
  runtime=S2cRuntime(s['config_path'])
  def send(subject,to,reply=None):
   args=owner(s,1,request_id=str(uuid.uuid4()),to_agent_ids=to,subject=subject,body_md=subject+' body <script>window.injected=true</script>\n'+'Long fixture text. '*30)
   if reply is not None:args['reply_to']=reply
   return runtime.apply('send_message',args)['message_id']
  hidden=send('HIDDEN_SENTINEL',[1]);vis=send('Visible root',[2],hidden)
  for i in range(6):vis=send('Visible reply '+str(i),[2],vis)
  op=OPERATOR_API['RuntimeOperator'](s['config_path'])
  op.prepare('pending');c=op.client('pending');C=type(c);original=C.rpc
  def unsent(self,method,params):
   if self is c and params.get('name')=='register_agent':raise op.api['ClientError']('TRANSPORT_FAILED')
   return original(self,method,params)
  C.rpc=unsent
  try:
   try:c.create('RecoverUnsent','daemon','deterministic')
   except RuntimeError as e:assert str(e)=='TRANSPORT_FAILED'
  finally:C.rpc=original
  pending=c.outputs['registration'];pending_before=pending.read_bytes()
  http=ThreadingHTTPServer(('127.0.0.1',0),dashboard.Handler)
  thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  origin=f'http://127.0.0.1:{http.server_port}'
  def tab(pre='',query='?view_as_agent_id=2'):
   t=cdp['http']('/json/new?about:blank',method='PUT');tabs.append(t['id']);w=cdp['WS'](t['webSocketDebuggerUrl']);w.sock.settimeout(20)
   w.call('Page.enable');w.call('Runtime.enable');w.call('Emulation.setDeviceMetricsOverride',width=1400,height=1000,deviceScaleFactor=1,mobile=False)
   injection="window.__errors=[];window.addEventListener('unhandledrejection',e=>window.__errors.push(String(e.reason)));window.addEventListener('error',e=>window.__errors.push(e.message));"+pre
   w.call('Page.addScriptToEvaluateOnNewDocument',source=injection);w.call('Page.navigate',url=origin+'/'+query);return w
  def ev(w,js):
   r=w.call('Runtime.evaluate',expression=js,awaitPromise=True,returnByValue=True)
   if 'exceptionDetails' in r:raise RuntimeError(str(r['exceptionDetails']))
   return r.get('result',{}).get('value')
  def wait(w,expression):
   deadline=time.monotonic()+15
   while time.monotonic()<deadline:
    if ev(w,expression):return
    time.sleep(.1)
   raise RuntimeError('browser wait timed out: '+expression)
  def shot(w,name):
   ev(w,'document.fonts.ready');time.sleep(.3)
   data=w.call('Page.captureScreenshot',format='png')['data'];(QA/name).write_bytes(base64.b64decode(data))
  init="sessionStorage.setItem('orrery.operator-capability',"+json.dumps(token)+");window.__requests=[];window.__aborts=0;const realFetch=window.fetch.bind(window);window.fetch=async function(path,options){window.__requests.push({path:String(path),method:options?.method||'GET'});options?.signal?.addEventListener('abort',()=>window.__aborts++);const r=await realFetch(path,options);if(window.__holdRoster&&(window.__holdPath?String(path).startsWith(window.__holdPath):String(path).startsWith('/api/agents'))){window.__holdRoster=false;return {ok:r.ok,status:r.status,json:async()=>{const j=await r.json();await new Promise(resolve=>window.__releaseRoster=resolve);return j;}};}return r;};"
  w=tab(init,query='?view_as_agent_id=2&message_id='+str(vis));wait(w,"!!window.OrreryGlobalDashboard && document.querySelectorAll('.global-agent').length>0")
  wait(w,"document.querySelectorAll('.reply-item').length===7")
  result['visible_mail']=ev(w,"({cards:document.querySelectorAll('.reply-item').length,hiddenInDOM:document.documentElement.outerHTML.includes('HIDDEN_SENTINEL'),hiddenInURL:location.href.includes('HIDDEN_SENTINEL'),injected:!!window.injected,header:[...document.querySelectorAll('#edrawer .ed-a,#edrawer .ed-b')].map(x=>x.textContent),errors:window.__errors})")
  assert not result['visible_mail']['hiddenInDOM'] and not result['visible_mail']['injected'] and not result['visible_mail']['errors']
  assert ev(w,"getComputedStyle(document.body).backgroundColor!=='rgba(0, 0, 0, 0)' && getComputedStyle(document.querySelector('header')).display==='flex'")
  shot(w,'global-mail-dark.png');ev(w,"window.AgentStackColorTheme.setPreference('light')");shot(w,'global-mail-light.png')
  w.call('Emulation.setDeviceMetricsOverride',width=316,height=900,deviceScaleFactor=1,mobile=False);shot(w,'global-mail-narrow.png')
  result['narrow']=ev(w,"({width:innerWidth,body:document.body.scrollWidth,drawer:document.querySelector('#edrawer').getBoundingClientRect().width})")
  w.call('Emulation.setDeviceMetricsOverride',width=1400,height=1000,deviceScaleFactor=1,mobile=False)
  ev(w,"document.querySelector('#newbtn').click();for(const [n,v] of Object.entries({client_name:'pending',name:'RecoverUnsent',program:'daemon',model:'deterministic',cwd:"+json.dumps(str(s['root']/'home'))+"})){document.querySelector('.global-form').elements[n].value=v;}document.querySelector('.global-form').requestSubmit();")
  wait(w,"[...document.querySelectorAll('.global-form button')].some(x=>x.textContent==='replay-registration')")
  wait(w,"document.querySelector('.global-form').textContent.includes('registration_pending')")
  result['pending_recovery']=ev(w,"({text:document.querySelector('.global-form').textContent,requests:window.__requests.filter(x=>x.path.includes('/api/global/'))})")
  result['pending_recovery']['pending_bytes_unchanged']=pending.read_bytes()==pending_before
  result['pending_recovery']['server_agent_id']=op.read({'kind':'resolve_name','name':'RecoverUnsent'})['data']['agent_id']
  assert result['pending_recovery']['server_agent_id'] is None
  assert result['pending_recovery']['pending_bytes_unchanged']
  ev(w,"[...document.querySelectorAll('.global-form button')].find(x=>x.textContent==='replay-registration').click()")
  wait(w,"document.querySelector('.global-form').textContent.includes('registered_unconfigured')")
  recovered=op.client('pending')
  result['pending_recovery']['requests']=ev(w,"window.__requests.filter(x=>x.path.includes('/api/global/'))")
  result['pending_recovery']['replayed']=recovered.identity['agent_id']==op.read({'kind':'resolve_name','name':'RecoverUnsent'})['data']['agent_id']
  assert recovered.local_owner()==json.loads(pending_before)['registration_token']
  assert result['pending_recovery']['replayed'] and not pending.exists()
  shot(w,'pending-recovery.png')
  ev(w,"[...document.querySelectorAll('.global-form button')].find(x=>x.textContent==='Cancel').click();window.__holdRoster=true;window.__refresh=window.OrreryGlobalDashboard.refresh().catch(e=>String(e));void 0;")
  wait(w,"typeof window.__releaseRoster==='function'")
  ev(w,"window.OrreryGlobalDashboard.lock();window.__releaseRoster();")
  ev(w,"window.__refresh")
  result['lock_during_body']=ev(w,"({stored:!!sessionStorage.getItem('orrery.operator-capability'),cards:document.querySelectorAll('.global-agent').length,selectors:document.querySelector('select[aria-label=\"Mail viewpoint\"]').options.length,lockedDialog:!!document.querySelector('[name=capability]'),netinfo:document.querySelector('#netinfo').textContent})")
  assert result['lock_during_body']['cards']==0 and result['lock_during_body']['selectors']==0
  assert ev(w,'window.__aborts>0')
  shot(w,'locked-after-delayed-body.png')
  result['delayed_reads']={}
  for name,path,query in [('health','/api/mail-watcher-health',''),('detail','/api/mail/message','?view_as_agent_id=2&message_id='+str(vis)),('validation','/api/version','')]:
   ev(w,"window.__holdPath="+json.dumps(path)+";window.__holdRoster=true;document.querySelector('[name=capability]').value="+json.dumps(token)+";document.querySelector('.global-form').requestSubmit()")
   wait(w,"window.__holdRoster===false && typeof window.__releaseRoster==='function'")
   # detail is requested by opening a saved conversation, not by roster refresh.
   ev(w,"window.OrreryGlobalDashboard.lock();window.__releaseRoster();")
   time.sleep(.2)
   result['delayed_reads'][name]=ev(w,"document.querySelectorAll('.global-agent,.reply-item,#gsvg .node').length===0 && document.querySelector('[name=capability]')?.value===''")
   assert result['delayed_reads'][name]
   ev(w,"window.__holdPath=null;window.__releaseRoster=null")
  # An unsolicited old share after Lock must not unlock a listener.
  ev(w,"window.__lateChannel=new BroadcastChannel('orrery.operator-session');window.__lateChannel.postMessage({type:'share',capability:"+json.dumps(token)+"});window.OrreryGlobalDashboard.lock();")
  time.sleep(.7)
  result['queued_share_before_lock']=ev(w,"({stored:!!sessionStorage.getItem('orrery.operator-capability'),lockedDialog:!!document.querySelector('[name=capability]'),cards:document.querySelectorAll('.global-agent').length})")
  assert not result['queued_share_before_lock']['stored'] and result['queued_share_before_lock']['lockedDialog']
  ev(w,"window.__lateChannel.close();window.OrreryGlobalDashboard.lock()")
  # Storage exception occurs before initialization; record rather than hide it.
  w2=tab("for(const name of ['getItem','setItem','removeItem'])Storage.prototype[name]=function(){throw new DOMException('denied','SecurityError')};",query='')
  time.sleep(1)
  result['storage_unavailable']=ev(w2,"({initialized:!!window.OrreryGlobalDashboard,unlockInput:!!document.querySelector('[name=capability]'),errors:window.__errors})")
  assert result['storage_unavailable']['initialized'] and result['storage_unavailable']['unlockInput']
  ev(w2,"document.querySelector('[name=capability]').value="+json.dumps(token)+";document.querySelector('.global-form').requestSubmit()")
  wait(w2,"document.querySelectorAll('.global-agent').length>0 && !document.querySelector('[name=capability]')")
  result['storage_unavailable']['memory_unlock']=ev(w2,"document.querySelector('.global-status').textContent.includes('BROWSER_SESSION_STORAGE_UNAVAILABLE')")
  assert result['storage_unavailable']['memory_unlock'] and not ev(w2,"window.__errors.length")
  w2.call('Page.reload');wait(w2,"!!document.querySelector('[name=capability]')");result['storage_unavailable']['reload_requires_unlock']=True
  ev(w2,"window.OrreryGlobalDashboard.lock()");time.sleep(.2)
  # Unrequested share is ignored, while a fresh tab can request a matching share.
  ev(w,"document.querySelector('[name=capability]').value="+json.dumps(token)+";document.querySelector('.global-form').requestSubmit()")
  wait(w,"!document.querySelector('[name=capability]')")
  peer=tab(query='');wait(peer,"document.querySelectorAll('.global-agent').length>0")
  result['correlated_share']=True
  ev(w,"window.OrreryGlobalDashboard.lock()")
  wait(peer,"!!document.querySelector('[name=capability]') && document.querySelectorAll('.global-agent').length===0")
  result['peer_lock']=True
  result['head']=__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()

 finally:
  for tid in tabs:
   try:cdp['http']('/json/close/'+tid)
   except Exception:pass
  if http:http.shutdown();http.server_close();thread.join(timeout=5)
  gen.close()
(QA/'browser-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
