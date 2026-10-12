/* Capability-admitted global dashboard. Legacy HTML driver is never run here. */
(async function(){
'use strict';
const F=window.OrreryMailFlow,E=F.element,B=F.button,$=s=>document.querySelector(s);
const nativeFetch=window.fetch.bind(window), storageKey='orrery.operator-capability';
const memoryStorage=new Map(),requests=new Set();let memoryOnly=false,shareRequest=null,shareAllowed=true;
function stored(scope,method,key,value){
 const k=scope+':'+key;
 if(!(scope==='sessionStorage'&&memoryOnly)){
  try{const store=window[scope];if(method==='getItem')return store.getItem(key);store[method](key,value);}
  catch(_){if(scope==='sessionStorage')memoryOnly=true;}
 }
 if(method==='getItem')return memoryStorage.get(k)||null;
 if(method==='setItem')memoryStorage.set(k,value);else memoryStorage.delete(k);
}
let capability=stored('sessionStorage','getItem',storageKey)||'',epoch=0,viewer=null,roster=[],edge=null,page=null,selected=null,notice=null,seq=0,view='deck',dialog=null,focusReturn=null;
const channel=typeof BroadcastChannel==='function'?new BroadcastChannel('orrery.operator-session'):null;
function clearMail(){seq++;edge=null;page=null;selected=null;notice=null;conversationQuery=null;$('#ed-list').replaceChildren();$('#edrawer').classList.remove('on');$('#edrawer').setAttribute('aria-hidden','true');$('#edrawer .ed-a').textContent='—';$('#edrawer .ed-b').textContent='—';$('#edrawer .ed-cnt').textContent='0';}
function lock(broadcast=true){epoch++;shareRequest=null;shareAllowed=false;const input=dialog?.querySelector('[name=capability]');if(input)input.value='';for(const request of requests)request.abort();requests.clear();capability='';stored('sessionStorage','removeItem',storageKey);clearMail();roster=[];$('#wrap').replaceChildren();$('#gsvg').replaceChildren();selector.replaceChildren();viewer=null;$('#netinfo').textContent='Locked';$('#mail-health').title='Locked';$('#mail-health').removeAttribute('data-status');$('#mail-health .mh-label').textContent='MAIL · LOCKED';$('#global-instruction-settings')?.replaceChildren();say('');if(broadcast)channel?.postMessage({type:'lock'});unlock();}
async function api(path,body){
 const generation=epoch,token=capability;
 if(!token)throw Error('HTTP_OPERATOR_REQUIRED');
 const controller=new AbortController();requests.add(controller);
 try{
  const r=await nativeFetch(path,{signal:controller.signal,method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store'});
  if(generation!==epoch)throw Error('PAGE_LOCKED');
  const j=await r.json();
  if(generation!==epoch)throw Error('PAGE_LOCKED');
  if(!r.ok||j.ok===false){if(r.status===403)lock();throw Error(j.reason||'REQUEST_FAILED');}return j;
 }finally{requests.delete(controller);}
}
function showDialog(title){
 if(dialog)dialog.remove();focusReturn=document.activeElement;
 dialog=E('div','global-dialog');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label',title);
 const form=E('form','global-form');form.append(E('h2','',title));dialog.append(form);document.body.append(dialog);return form;
}
function closeDialog(){dialog?.remove();dialog=null;focusReturn?.focus();}
function errorAt(form,error){let row=form.querySelector('.global-error');if(!row){row=E('p','global-error');row.setAttribute('role','alert');form.append(row);}row.textContent=String(error.message||error).slice(0,256);}
function unlock(){
 if(dialog?.querySelector('[name=capability]'))return;
 const form=showDialog('Unlock ORRERY');form.append(E('p','','Run agentstack-http-operator show with this dashboard’s AGENTSTACK_HOME. Paste its capability here. It stays in this page session, never in a URL.'));
 const label=E('label','','Operator capability'),input=E('input');input.type='password';input.name='capability';input.autocomplete='off';input.required=true;label.append(input);form.append(label,B('Unlock',()=>form.requestSubmit()));
 form.onsubmit=async e=>{e.preventDefault();shareRequest=null;const generation=++epoch;try{await acceptCapability(input.value.trim(),generation);}catch(error){if(generation===epoch){capability='';errorAt(form,error);}}};if(memoryOnly)form.append(E('p','','BROWSER_SESSION_STORAGE_UNAVAILABLE · Memory only; enter the capability again after reload.'));const help=E('p');help.append('Details: ');for(const [label,file] of [['English','global-delivery-client.en.md'],['日本語','global-delivery-client.md']]){const a=E('a','',label);a.href='https://github.com/gyroid-eth/orrery-telemetry/blob/master/docs/'+file+'#operator-ui-access';a.target='_blank';a.rel='noopener';help.append(a,' ');}form.append(help);input.focus();
}
async function acceptCapability(token,generation){
 if(generation!==epoch)throw Error('PAGE_LOCKED');capability=token;
 await start();if(generation!==epoch)throw Error('PAGE_LOCKED');
 stored('sessionStorage','setItem',storageKey,capability);shareAllowed=true;closeDialog();
 if(memoryOnly)say('BROWSER_SESSION_STORAGE_UNAVAILABLE · Memory only; reload requires Unlock.');
}
channel?.addEventListener('message',async e=>{
 const m=e.data;if(m?.type==='lock'){lock(false);return;}
 if(m?.type==='request'&&capability&&shareAllowed&&typeof m.request_id==='string')channel.postMessage({type:'share',request_id:m.request_id,capability});
 if(m?.type==='share'&&!capability&&shareRequest&&m.request_id===shareRequest.id&&shareRequest.generation===epoch&&performance.now()<=shareRequest.deadline&&typeof m.capability==='string'){
  const generation=epoch;shareRequest=null;
  try{await acceptCapability(m.capability,generation);}catch(_){if(generation===epoch)capability='';}
 }
});
const tools=E('div','global-tools');
const viewerLabel=E('label','','View as '),selector=E('select');selector.setAttribute('aria-label','Mail viewpoint');viewerLabel.append(selector);
tools.append(viewerLabel,B('Lock',()=>lock()));$('header .spacer').before(tools);
const status=E('div','global-status');status.setAttribute('role','status');$('#wrap').before(status);
function say(message){status.textContent=message;}
for(const id of ['history','usage-pill','need-chip','st-network','st-display','st-usage']){const e=document.getElementById(id);if(e)e.hidden=true;}
$('header .gauges').hidden=true;
$('#q').oninput=renderRoster;
$('#settings-btn').onclick=()=>{settingsNotice().catch(error=>say(error.message));$('#settings').classList.add('on');$('#settings').setAttribute('aria-hidden','false');$('#settings-btn').setAttribute('aria-expanded','true');};
$('#settings-close').onclick=()=>{$('#settings').classList.remove('on');$('#settings').setAttribute('aria-hidden','true');$('#settings-btn').setAttribute('aria-expanded','false');$('#settings-btn').focus();};
for(const b of document.querySelectorAll('[data-theme-pref]'))b.onclick=()=>window.AgentStackColorTheme.setPreference(b.dataset.themePref);
for(const [id,mode] of [['v-deck','deck'],['v-net','net']])document.getElementById(id).onclick=()=>setView(mode);
function setView(mode){view=mode;document.body.dataset.view=mode;$('#v-deck').classList.toggle('on',mode==='deck');$('#v-net').classList.toggle('on',mode==='net');if(mode==='net')refreshGraph().catch(error=>say(error.message));}
function closeDrawer(){$('#edrawer').classList.remove('on');$('#edrawer').setAttribute('aria-hidden','true');focusReturn?.focus();}
$('#edrawer .ed-close').onclick=closeDrawer;
document.addEventListener('keydown',e=>{if(e.key==='Tab'&&dialog){const nodes=[...dialog.querySelectorAll('input,select,button,a[href]')].filter(x=>!x.disabled);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}if(e.key==='Escape'){if(dialog&&!dialog.querySelector('[name=capability]'))closeDialog();else if($('#settings').classList.contains('on'))$('#settings-close').click();else closeDrawer();}});
selector.onchange=()=>{viewer=selector.value?Number(selector.value):null;clearMail();const u=new URL(location.href);if(viewer)u.searchParams.set('view_as_agent_id',viewer);else u.searchParams.delete('view_as_agent_id');u.searchParams.delete('message_id');u.searchParams.delete('thread_id');history.replaceState(null,'',u);refreshGraph().catch(error=>say(error.message));};
async function loadRoster(){
 let before=null,all=[];
 do{const j=await api('/api/agents?limit=200'+(before?'&before_agent_id='+before:''));all.push(...j.data.items);before=j.data.next_before_agent_id;}while(before&&all.length<10000);
 roster=all;selector.replaceChildren(E('option','','Choose a viewpoint'));selector.firstChild.value='';
 for(const row of roster){const o=E('option','',row.name+' · #'+row.agent_id);o.value=row.agent_id;selector.append(o);}
 if(viewer&&roster.some(a=>a.agent_id===viewer))selector.value=viewer;else viewer=null;
 renderRoster();if(before)say('Roster is limited to 10,000 agents. Narrow the server inventory before continuing.');
}
function renderRoster(){const root=$('#wrap');root.replaceChildren();const list=E('div','global-crew');root.append(list);const q=$('#q').value.toLowerCase();
 for(const a of roster.filter(a=>(a.name+' '+a.task_description).toLowerCase().includes(q))){
  const b=B('',()=>{if(!viewer){say('Choose a Mail viewpoint first.');selector.focus();return;}focusReturn=b;openEdge(viewer,a.agent_id).catch(error=>say(error.message));},'global-agent');b.append(E('h2','',a.name),E('p','',a.program+' · '+a.model),E('p','',a.task_description||'No task recorded'),E('p','',a.retired_at?'Retired':'Registered · session activity unavailable'));list.append(b);
 }
 if(!roster.length)list.append(E('p','','No registered agents.'));
}
const svgNS='http://www.w3.org/2000/svg';function svg(tag,attrs,text){const e=document.createElementNS(svgNS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!=null)e.textContent=text;return e;}
async function refreshGraph(){
 const root=$('#gsvg');if(!viewer){root.replaceChildren();say('Choose a Mail viewpoint to show visible edges.');return;}
 const mine=viewer,generation=epoch;
 const j=await api('/api/graph?view_as_agent_id='+mine+'&limit=100');if(mine!==viewer||generation!==epoch)return;
 const counts=new Map();for(const m of j.data.items)for(const r of [...m.to,...m.cc,...m.bcc]){if(m.sender.agent_id===r.agent_id)continue;const pair=[m.sender.agent_id,r.agent_id].sort((a,b)=>a-b),key=pair.join(':');const row=counts.get(key)||{pair,count:0};row.count++;counts.set(key,row);}
 root.replaceChildren();const width=root.clientWidth||1200,height=root.clientHeight||700,cx=width/2,cy=height/2,radius=Math.max(100,Math.min(width,height)*.29),positions=new Map();
 roster.forEach((a,i)=>positions.set(a.agent_id,{x:cx+Math.cos(i*2*Math.PI/Math.max(1,roster.length))*radius,y:cy+Math.sin(i*2*Math.PI/Math.max(1,roster.length))*radius}));
 for(const {pair,count} of counts.values()){const [a,b]=pair.map(id=>positions.get(id));if(!a||!b)continue;const group=svg('g',{tabindex:0,role:'button','aria-label':'Open visible Mail edge'});const attrs={x1:a.x,y1:a.y,x2:b.x,y2:b.y};group.append(svg('line',{...attrs,class:'global-edge'}),svg('line',{...attrs,class:'global-edge-hit'}),svg('text',{x:(a.x+b.x)/2,y:(a.y+b.y)/2,class:'global-node-label'},count));const open=()=>{focusReturn=group;openEdge(...pair).catch(error=>say(error.message));};group.onclick=open;group.onkeydown=e=>{if(e.key==='Enter')open();};root.append(group);}
 for(const a of roster){const p=positions.get(a.agent_id),g=window.OrreryNetworkNode.render(a.name,{...a,retired:!!a.retired_at,state:'unknown',role:'none',annot:{role:'ACTIVITY UNKNOWN'}},{avatar:()=>null});g.setAttribute('transform',`translate(${p.x} ${p.y})`);g.setAttribute('tabindex','0');g.setAttribute('role','button');g.setAttribute('aria-label',a.name+' · session activity unavailable');const title=svg('title',{},'Session activity unavailable');g.append(title);const open=()=>{focusReturn=g;openEdge(viewer,a.agent_id).catch(error=>say(error.message));};g.onclick=open;g.onkeydown=e=>{if(e.key==='Enter')open();};root.append(g);}
 $('#netinfo').textContent=roster.length+' nodes · '+counts.size+' visible links'+(j.data.next_cursor?' · recent page only':'');
}
async function bodies(messages,mine=viewer){
 let at=0;const out=new Array(messages.length);
 await Promise.all(Array.from({length:Math.min(4,messages.length)},async()=>{for(;;){const i=at++;if(i>=messages.length)return;const m=messages[i];out[i]=m.body_md==null?(await api('/api/mail/message?view_as_agent_id='+mine+'&id='+m.id)).data:m;}}));return out;
}
async function instruction(agent){try{return (await api('/api/instruction-migration?agent_id='+agent)).data;}catch(error){return {disk_state:'unknown',session_state:'unknown',findings:[],reason:error.message};}}
async function openEdge(a,b,cursor=null){
 const request=++seq,mine=viewer;
 const j=await api('/api/edge-messages?view_as_agent_id='+mine+'&a_agent_id='+a+'&b_agent_id='+b+'&limit=20'+(cursor?'&cursor='+encodeURIComponent(cursor):''));
 const rows=await bodies(j.data.items,mine),finding=await instruction(mine);
 if(request!==seq||mine!==viewer)return;conversationQuery=null;edge={a,b};notice=finding;
 const previous=cursor&&page?page.items:[];page={...j.data,items:[...previous,...rows].sort((x,y)=>x.id-y.id)};selected=cursor?selected:rows[0]?.id??null;draw();
}
function draw(){
 const drawer=$('#edrawer');drawer.classList.add('on');drawer.setAttribute('aria-hidden','false');
 const participants=F.participants(page.items,edge);
 drawer.querySelector('.ed-a').textContent=participants[0];drawer.querySelector('.ed-b').textContent=participants[1];drawer.querySelector('.ed-cnt').textContent=page.items.length+' visible';
 const root=$('#ed-list');
 const options={selected,instruction:window.parent!==window&&new URLSearchParams(location.search).get('embed')==='1'?null:notice,onSelect:id=>selectMessage(id).catch(error=>say(error.message))};
 if(conversationQuery?.label!=null){
  root.replaceChildren();if(options.instruction&&F.needsNotice(options.instruction)&&!F.dismissed(options.instruction))root.append(F.notice(options.instruction));
  for(const group of page.groups){const section=E('section');section.append(E('p','gap-note','Saved label · source '+group.origin.source_provenance));const body=E('div');F.render(body,{...options,instruction:null,messages:group.rows,incomplete:group.history_incomplete});section.append(body);root.append(section);}
  if(page.next_cursor)root.append(B('Load more visible mail',()=>loadMore().catch(error=>say(error.message)),'reply-load'));
 }else F.render(root,{...options,messages:page.items,incomplete:page.items.some(m=>m.history_incomplete),next:page.next_cursor,onMore:()=>loadMore().catch(error=>say(error.message))});

}
async function selectMessage(id){
 if(id!=null&&!page.items.some(m=>m.id===id)){await conversation(id);return;}
 const box=$('#ed-list'),pos=box.scrollTop;selected=id;draw();box.scrollTop=pos;if(id!=null)document.getElementById('reply-'+id)?.scrollIntoView({block:'nearest'});
}
let conversationQuery=null;
async function conversation(id,cursor=null,label=null){
 const request=++seq,mine=viewer;
 const qs=new URLSearchParams({view_as_agent_id:mine,limit:20,...(label!=null?{label}:{message_id:id}),...(cursor?{cursor}:{})});
 const j=await api('/api/mail/'+(label!=null?'legacy-label':'conversation')+'?'+qs);
 const groups=[];for(const group of j.data.items){const rows=await bodies(group.source_message_ids.map(id=>({id,body_md:null})),mine);groups.push({...group,rows});}
 const finding=await instruction(mine);if(request!==seq||mine!==viewer)return;
 // Legacy label groups retain source boundaries; never infer reply edges from labels.
 edge=null;conversationQuery={id,label};notice=finding;const previous=cursor&&page?page.groups||[]:[];const merged=[...previous];for(const group of groups){const prior=merged.find(x=>JSON.stringify(x.origin)===JSON.stringify(group.origin));if(prior)prior.rows.push(...group.rows);else merged.push(group);}for(const group of merged)group.rows.sort((a,b)=>a.id-b.id);page={...j.data,items:merged.flatMap(g=>g.rows),groups:merged};selected=id;
 draw();
}
async function loadMore(){if(edge)return openEdge(edge.a,edge.b,page.next_cursor);if(conversationQuery)return conversation(conversationQuery.id,page.next_cursor,conversationQuery.label);}
function recentDirs(){try{const a=JSON.parse(stored('localStorage','getItem','agentdash.spawn.recent-dirs')||'[]');return Array.isArray(a)?a.filter(x=>typeof x==='string').slice(0,10):[];}catch(_){return [];}}
const oldDir=stored('localStorage','getItem','agentdash.spawn.dir');if(oldDir){const a=recentDirs();if(!a.includes(oldDir))a.unshift(oldDir);stored('localStorage','setItem','agentdash.spawn.recent-dirs',JSON.stringify(a.slice(0,10)));stored('localStorage','removeItem','agentdash.spawn.dir');}
$('#newbtn').onclick=()=>{
 const form=showDialog('Launch an agent');
 const fields={client_name:'Client slot (new identity needs a new slot)',name:'Agent name',model:'Model',cwd:'Work folder'};
 for(const [name,title] of Object.entries(fields)){const label=E('label','',title),input=E('input');input.name=name;input.required=true;if(name==='cwd'){input.setAttribute('list','global-recent-dirs');input.value=recentDirs()[0]||'';}label.append(input);form.append(label);}
 const choices=E('datalist');choices.id='global-recent-dirs';for(const path of recentDirs()){const o=E('option');o.value=path;choices.append(o);}form.append(choices);
 const label=E('label','','Engine'),provider=E('select');provider.name='program';for(const [v,t] of [['claude-code','Claude'],['codex','Codex'],['antigravity','Gemini'],['daemon','Daemon']]){const o=E('option','',t);o.value=v;provider.append(o);}label.append(provider);form.append(label,E('p','','Existing names keep their identity. Configuration or trust may need an explicit operator step. No credentials are issued by a resume.'));
 const folderResult=E('div');form.append(B('Browse folders',async()=>{try{const row=await api('/api/fs/dirs?path='+encodeURIComponent(form.elements.cwd.value));folderResult.replaceChildren();if(row.path){form.elements.cwd.value=row.path;folderResult.append(E('p','',row.path));for(const d of row.dirs)folderResult.append(B(d.name,()=>{form.elements.cwd.value=d.path;},'reply-load'));}else folderResult.append(E('p','','Folder is outside the configured roots or unavailable.'));}catch(error){errorAt(form,error);}}),folderResult);
 const result=E('div');form.append(B('Cancel',closeDialog),B('Spawn',()=>form.requestSubmit()),result);
 form.onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(form));try{
  const lookup=await api('/api/global/clients?name='+encodeURIComponent(data.name));result.replaceChildren();
  if(lookup.matches.length){for(const m of lookup.matches){const current=await api('/api/global/spawn-status?client_name='+encodeURIComponent(m.client_name));showOperation(result,current,{...data,client_name:m.client_name},lookup.matches.length>1);}return;}
  const r=await api('/api/global/spawn',data);const paths=recentDirs().filter(x=>x!==data.cwd);paths.unshift(data.cwd);stored('localStorage','setItem','agentdash.spawn.recent-dirs',JSON.stringify(paths.slice(0,10)));showOperation(result,r,data);await loadRoster();
 }catch(error){errorAt(form,error);if(capability){try{const current=await api('/api/global/spawn-status?client_name='+encodeURIComponent(data.client_name));showOperation(result,current,data);}catch(_){/* Original refusal remains visible. */}}}};form.querySelector('input').focus();
 (async()=>{const input=form.elements.cwd,initial=input.value;let chosen=null;for(const path of recentDirs()){const row=await api('/api/fs/dirs?path='+encodeURIComponent(path));if(row.path){chosen=row.path;break;}}if(!chosen)chosen=(await api('/api/fs/dirs')).path;if(dialog?.contains(input)&&input.value===initial)input.value=chosen||'';})().catch(error=>errorAt(form,error));
};
function showOperation(root,row,intent=null,append=false){
 if(!append)root.replaceChildren();root.append(E('p','',row.client_name+' · '+row.stage+' · '+(row.reason||'')));
 const execute=async(op)=>{try{
  const r=op==='resume'?await api('/api/global/resume',{client_name:row.client_name,open:true}):await api('/api/global/spawn',{...intent,...(row.registration_sha256?{expected_digest:row.registration_sha256}:{})});
  showOperation(root,r,intent);await loadRoster();
 }catch(error){root.append(E('p','global-error',error.message));}};
 for(const op of row.next_operations||[]){
  if((['replay-registration','finalize-registration','launch'].includes(op)||(op==='configure-provider'&&row.reason==='INSTRUCTION_PROVIDER_CONFIG_REQUIRED'))&&intent)root.append(B(op,()=>execute(op)));
  else if(op==='configure-provider'&&['CODEX_HOOK_CONFIGURE_REQUIRED','PERSISTENT_EXECUTION_REQUIRED'].includes(row.reason)){
   const codex=row.reason==='CODEX_HOOK_CONFIGURE_REQUIRED';
   root.append(E('p','',codex?"Set AGENTSTACK_CLIENT_CONFIG to this slot’s runtime-client.json. Run codex-plan, review and explicitly approve the plan, then run codex-apply. Launch never approves trust.":"Set AGENTSTACK_CLIENT_CONFIG to this slot’s runtime-client.json. If its fixed profile.json is absent, run agentstack-runtime-client save-profile first. Then run agentstack-persistent configure --operator before launch."));
   const details=E('p','','Details: ');
   for(const [label,file,anchor] of [['English','global-delivery-client.en.md',codex?'codex-hook-trust':'persistent-execution'],['日本語','global-delivery-client.md',codex?'codex-hook-trust':'persistent-execution']]){const a=E('a','',label);a.href='https://github.com/gyroid-eth/orrery-telemetry/blob/master/docs/'+file+'#'+anchor;a.target='_blank';a.rel='noopener';details.append(a,' ');}root.append(details);
  }
  else if(op==='launch-status')root.append(B('Resume '+row.client_name,()=>execute('resume')));
  else if(op==='registration-abandon'&&row.registration_sha256)root.append(B('Abandon registration attempt',async()=>{if(!confirm('Abandon this local attempt? The server identity will remain.'))return;try{await api('/api/global/registration-abandon',{client_name:row.client_name,expected_digest:row.registration_sha256});root.replaceChildren(E('p','','Local pending attempt abandoned.'));}catch(error){root.append(E('p','global-error',error.message));}}));
  else root.append(E('p','',op));
 }
}

async function settingsNotice(){
 let root=$('#global-instruction-settings');if(!root){root=E('section');root.id='global-instruction-settings';$('#settings').append(root);}
 if(!viewer){root.replaceChildren(E('p','','Choose a Mail viewpoint to check instructions.'));return;}
 const mine=viewer,row=await instruction(mine);if(mine!==viewer)return;root.replaceChildren(E('h3','','Instructions'),F.notice(row,{force:true}));
 const d=await api('/api/deliverables');if(mine!==viewer)return;
 root.append(E('h3','','Deliverables'),E('p','',d.configured?'Only explicitly configured deliverable roots are searched.':'No deliverable roots configured. Set AGENTSTACK_DELIVERABLE_ROOTS; a work folder never expands this scope.'));
 for(const item of d.items[roster.find(a=>a.agent_id===mine)?.name]||[])root.append(E('p','',item.title+' · '+item.rel));
}
async function health(){
 const h=(await api('/api/mail-watcher-health')).data;
 $('#mail-health').title='Server readiness: '+h.server_ready+'; signal delivery: '+h.signal.status+'; pending: '+h.signal.pending_count+'; oldest age (seconds): '+(h.signal.oldest_pending_age_seconds??'none');
 $('#mail-health').dataset.status=h.signal.status;
 $('#mail-health .mh-label').textContent='MAIL · '+h.signal.status;
}
async function start(){
 const version=await api('/api/version');if(version.runtime_mode!=='global'||version.capabilities.global_dashboard_ui!==1)throw Error('GLOBAL_UI_CAPABILITY_REQUIRED');
 const route=new URLSearchParams(location.search),value=route.get('view_as_agent_id');viewer=value&&/^[1-9]\d*$/.test(value)?Number(value):null;
 await loadRoster();
 await health();
 setView(route.get('view')==='net'?'net':'deck');
 const mid=route.get('message_id'),old=route.get('thread_id');
 if(mid&&(!/^[1-9]\d*$/.test(mid)||old&&old!==mid))throw Error('REPLY_INPUT_CONFLICT');
 if((mid||old)&&!viewer){say('Choose a Mail viewpoint, then reopen this saved link.');return;}
 if(mid||old){if(old)say('This is an old Mail link. Copy the current URL after opening it.');if(mid||/^[1-9]\d*$/.test(old)){const id=Number(mid||old);await conversation(id);const u=new URL(location.href);u.searchParams.delete('thread_id');u.searchParams.set('message_id',id);history.replaceState(null,'',u);}else await conversation(null,null,old);}
}
if(capability){try{await acceptCapability(capability,epoch);}catch(error){lock();say(error.message);}}else{unlock();if(channel){shareRequest={id:crypto.randomUUID(),generation:epoch,deadline:performance.now()+500};channel.postMessage({type:'request',request_id:shareRequest.id});setTimeout(()=>{shareRequest=null;},500);}}
setInterval(()=>{if(capability&&!dialog){loadRoster().then(()=>health()).then(()=>view==='net'?refreshGraph():null).catch(error=>say(error.message));}},15000);
setInterval(()=>{if(capability&&viewer&&page){const mine=viewer;instruction(mine).then(row=>{if(mine===viewer){notice=row;draw();}});}},60000);
window.OrreryGlobalDashboard=Object.freeze({lock,refresh:()=>start()});
})();
