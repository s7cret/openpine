"use strict";
const researchOperationCollections={experiment:'experiments',search:'searches',walkforward:'walkforwards'};
const apiWaiting=new Map();let apiSequence=0;
function readOnlyPost(path){return path==='/api/launches/requirements'||path==='/api/launches/history-spec'||/^\/api\/launches\/[0-9a-f]{32}\/research-base$/.test(path);}
class LatestRequest{
  constructor(){this.sequence=0;this.controller=null;}
  invalidate(){this.sequence++;this.controller?.abort();this.controller=null;}
  async run(load,accept){
    this.invalidate();const sequence=this.sequence,controller=new AbortController();this.controller=controller;
    try{const data=await load(controller.signal);if(sequence===this.sequence&&!controller.signal.aborted)return accept(data);}
    catch(error){if(sequence===this.sequence&&!controller.signal.aborted)throw error;}
    finally{if(sequence===this.sequence)this.controller=null;}
  }
}
function apiNotice(){
  let panel=document.getElementById('api-wait');
  if(!panel){panel=document.createElement('section');panel.id='api-wait';panel.className='card experiment-body';panel.setAttribute('role','status');panel.setAttribute('aria-live','polite');const text=document.createElement('p');text.id='api-wait-text';const stop=document.createElement('button');stop.id='api-wait-cancel';stop.textContent='Прекратить ожидание';stop.onclick=()=>{for(const item of apiWaiting.values())item.controller.abort();};const hint=document.createElement('p');hint.className='muted';hint.textContent='Принятые сервером запуски продолжатся. Пауза и отмена доступны в их карточках.';panel.append(text,stop,hint);document.querySelector('.run-card').after(panel);}
  panel.hidden=apiWaiting.size===0;
  if(apiWaiting.size){const item=Array.from(apiWaiting.values()).at(-1);document.getElementById('api-wait-text').textContent=`${item.reason==='resources'?'Ожидание ресурсов':'Повтор соединения'} · повтор ${item.attempt} из 8 · следующий запрос через ${(item.delay/1000).toFixed(1)} с`;}
}
function apiDelay(ms,signal){return new Promise((resolve,reject)=>{const stop=()=>{clearTimeout(timer);reject(new Error('Ожидание прекращено. Принятый запуск можно открыть из сохранённых.'));};const timer=setTimeout(()=>{signal.removeEventListener('abort',stop);resolve();},ms);signal.addEventListener('abort',stop,{once:true});if(signal.aborted)stop();});}
async function api(path,body,policy={}){
  const keyed=policy.requestId!==undefined;
  if(keyed&&(!/^\/api\/(experiments|searches|walkforwards)$/.test(path)||!/^[0-9a-f]{32}$/.test(policy.requestId)||body===undefined))throw new Error('Некорректный ключ отправки исследования');
  const controller=new AbortController(),sequence=++apiSequence,deadline=Date.now()+120000,timer=setTimeout(()=>controller.abort(),120000);
  const externalAbort=()=>controller.abort();if(policy.signal){policy.signal.addEventListener('abort',externalAbort,{once:true});if(policy.signal.aborted)controller.abort();}
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-OpenPine-Token':token,...(keyed?{'X-OpenPine-Request':policy.requestId}:{})},body:JSON.stringify(body)};
  if(body&&Object.keys(body).length===0&&/^\/api\/(experiments|searches|walkforwards)\/[0-9a-f]{32}\/(pause|resume|cancel)$/.test(path))options.headers['X-OpenPine-Control-View']='progress';
  const safe=body===undefined||keyed||readOnlyPost(path);let last;
  try{
    for(let attempt=0;attempt<8;attempt++){
      let delay=Math.min(800,50*2**attempt),reason='network';
      try{
        const response=await fetch(path,{...options,signal:controller.signal});let data;
        try{data=await response.json();}catch(error){throw Object.assign(new Error('Сервер вернул некорректный ответ'),{protocol:true});}
        if(response.ok)return data;
        const error=Object.assign(new Error(data.diagnostics?.map(d=>d.message).join('\n')||`HTTP ${response.status}`),{status:response.status,response:data});
        const busy=response.status===503&&data.diagnostics?.length&&data.diagnostics.every(d=>d.code==='COORDINATOR_BUSY')&&data.admission?.retryable!==false;
        if(!safe||!busy)throw error;last=error;reason='resources';
        const retry=response.headers.get('Retry-After');if(retry&&/^\d+(?:\.\d+)?$/.test(retry))delay=Math.min(2000,Number(retry)*1000);
      }catch(error){
        if(controller.signal.aborted)throw Object.assign(new Error('Ожидание прекращено или истёк срок ответа. Проверьте сохранённые запуски.'),{cancelled:true});
        if(error.status||error.protocol||!safe)throw error;last=error;
      }
      if(attempt===7||Date.now()>=deadline)break;
      delay=Math.min(delay,deadline-Date.now());apiWaiting.set(sequence,{controller,attempt:attempt+1,delay,reason});apiNotice();await apiDelay(delay,controller.signal);
    }
    throw last||new Error('Истёк срок ожидания ответа');
  }finally{clearTimeout(timer);policy.signal?.removeEventListener('abort',externalAbort);apiWaiting.delete(sequence);if(document.getElementById('api-wait'))apiNotice();}
}
function researchCanonical(value){if(Array.isArray(value))return '['+value.map(researchCanonical).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+researchCanonical(value[k])).join(',')+'}';return JSON.stringify(value);}
function pendingKey(kind){return 'openpine.pending-research.'+kind;}
function pendingRead(kind){const raw=localStorage.getItem(pendingKey(kind));if(!raw)return null;const value=JSON.parse(raw);if(value.kind!==kind||!/^[0-9a-f]{32}$/.test(value.request_id)||value.origin!==location.origin||!value.payload)throw new Error('Некорректная сохранённая отправка исследования');return value;}
function settleResearch(kind,receipt){localStorage.setItem('openpine.research-selection.'+kind,receipt.resource_id);localStorage.removeItem(pendingKey(kind));renderPendingResearch();return receipt;}
async function createResearch(kind,payload){
  if(!researchOperationCollections[kind])throw new Error('Неизвестный тип исследования');let saved=pendingRead(kind);
  if(saved&&researchCanonical(saved.payload)!==researchCanonical(payload))throw new Error('Сначала восстановите предыдущую отправку в панели незавершённых запросов.');
  if(!saved){saved={schema_version:'openpine.pending-research.1',kind,origin:location.origin,request_id:crypto.randomUUID().replaceAll('-',''),payload};const raw=JSON.stringify(saved);if(new TextEncoder().encode(raw).length>1024*1024)throw new Error('Запрос превышает 1 МиБ для восстановления в браузере; используйте CLI с файлом операции.');localStorage.setItem(pendingKey(kind),raw);}
  renderPendingResearch();
  try{const receipt=await api('/api/'+researchOperationCollections[kind],saved.payload,{requestId:saved.request_id});return settleResearch(kind,receipt);}
  catch(error){
    if([400,403,409,410].includes(error.status)){
      try{await api(`/api/operations/${kind}/${saved.request_id}`);}catch(lookupError){if([404,410].includes(lookupError.status)){localStorage.removeItem(pendingKey(kind));renderPendingResearch();}}
    }
    throw error;
  }
}
async function selectResearchReceipt(kind,identifier){
  if(kind==='experiment'){selectedExperiment=identifier;experimentPage=0;await refreshExperiments();await showExperiment();}
  else if(kind==='search'){selectedSearch=identifier;searchPage=0;await refreshSearches();await showSearch();}
  else{await refreshWalks();document.getElementById('walk-select').value=identifier;await showWalk();}
}
function renderPendingResearch(){
  let panel=document.getElementById('research-pending');if(!panel){panel=document.createElement('section');panel.id='research-pending';panel.className='card experiment-body';document.querySelector('.run-card').after(panel);}panel.replaceChildren();
  const title=document.createElement('h2');title.textContent='Незавершённые отправки';panel.append(title);let count=0;
  for(const kind of Object.keys(researchOperationCollections)){
    let saved;try{saved=pendingRead(kind);}catch(error){const p=document.createElement('p');p.textContent=error.message;panel.append(p);count++;continue;}
    if(!saved)continue;count++;const row=document.createElement('div'),text=document.createElement('p'),button=document.createElement('button');text.textContent=`${saved.payload.name||'Исследование'} · ответ о создании ещё не подтверждён`;button.textContent='Проверить и восстановить';button.className='secondary';
    button.onclick=async()=>{button.disabled=true;try{let receipt;try{receipt=await api(`/api/operations/${kind}/${saved.request_id}`);}catch(error){if(error.status!==404)throw error;receipt=await api('/api/'+researchOperationCollections[kind],saved.payload,{requestId:saved.request_id});}settleResearch(kind,receipt);await selectResearchReceipt(kind,receipt.resource_id);}catch(error){text.textContent=error.message;button.disabled=false;}};row.append(text,button);panel.append(row);
  }panel.hidden=count===0;
}
async function restoreResearchOperations(){
  renderPendingResearch();
  for(const kind of Object.keys(researchOperationCollections)){
    try{const saved=pendingRead(kind);if(saved){const receipt=await api(`/api/operations/${kind}/${saved.request_id}`);settleResearch(kind,receipt);await selectResearchReceipt(kind,receipt.resource_id);}
      else{const identifier=localStorage.getItem('openpine.research-selection.'+kind);if(identifier)await selectResearchReceipt(kind,identifier);}
    }catch(error){if([400,410].includes(error.status)){localStorage.removeItem('openpine.research-selection.'+kind);if(error.status===410)localStorage.removeItem(pendingKey(kind));renderPendingResearch();}else if(error.status!==404){const panel=document.getElementById('research-pending');const p=document.createElement('p');p.textContent=error.message;panel.append(p);panel.hidden=false;}}
  }
}
window.addEventListener('DOMContentLoaded',()=>restoreResearchOperations());
