"use strict";
// The existing editor and historical result viewer keep their own state.
let liveId=null,liveState=null,liveWindow=null,liveFollow=true,liveStart=0,liveTimer=null,liveGeneration=0;
const liveLabels={warming:"Прогрев",connecting:"Подключение",recovering:"Восстановление",live:"Подключено",backoff:"Повтор подключения",backpressure:"Ожидание расчёта",stopped:"Остановлено",failed:"Ошибка",source_failed:"Нет связи с источником",execution_interrupted:"Продолжение расчёта"};
liveLabels.execution_failed="Расчёт остановлен";liveLabels.storage_interrupted="Ошибка записи";
function intervalLabel(value){return ({D:'1 день',W:'1 неделя',M:'1 месяц'})[value]||(Number(value)%60===0?`${Number(value)/60} ч`:`${value} мин`);}
function liveWarmupStart(timeframe,bars){
  const now=Date.now();if(/^\d+$/.test(timeframe)){const size=Number(timeframe)*60000;return Math.max(0,Math.floor(now/size)*size-bars*size);}
  const date=new Date(now);date.setUTCHours(0,0,0,0);
  if(timeframe==='M'){date.setUTCDate(1);date.setUTCMonth(date.getUTCMonth()-bars);}
  else if(timeframe==='W')date.setUTCDate(date.getUTCDate()-(date.getUTCDay()+6)%7-bars*7);
  else date.setUTCDate(date.getUTCDate()-bars);
  return Math.max(timeframe==='W'?345600000:0,date.getTime());
}
for(const [value,title] of [['D','1 день'],['W','1 неделя'],['M','1 месяц']]){const option=document.createElement('option');option.value=value;option.textContent=title;$('live-timeframe').append(option);}
let acquisitionTask=null;
const historyControls=document.createElement('div');historyControls.className='history-controls';
const historyKind=document.createElement('select');historyKind.id='history-kind';historyKind.setAttribute('aria-label','Способ загрузки истории');
for(const [value,title] of [['rest','REST'],['archive','Месячные архивы с SHA256']]){const option=document.createElement('option');option.value=value;option.textContent=title;historyKind.append(option);}
const gapLabel=document.createElement('label'),gapInput=document.createElement('input');gapInput.type='checkbox';gapInput.id='allow-data-gaps';gapLabel.append(gapInput,document.createTextNode(' Разрешить пропуски с отчётом'));
historyControls.append(historyKind,gapLabel);$('binance-fields').append(historyControls);
const cancelFetch=document.createElement('button');cancelFetch.id='cancel-fetch';cancelFetch.className='danger';cancelFetch.textContent='Отменить загрузку';cancelFetch.hidden=true;
cancelFetch.onclick=()=>acquisitionTask&&api(`/api/market/history/${acquisitionTask}/cancel`,{}).catch(e=>diagnostic(e.message));
$('prepare').parentElement.append(cancelFetch);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function waitAcquisition(id,onProgress=()=>{}){
  while(true){
    const state=await api(`/api/market/history/${id}`);onProgress(state);
    if(["succeeded","empty","failed","cancelled"].includes(state.status))return state;
    await pause(250);
  }
}

async function refreshInstruments(){
  const button=$("live-catalog-refresh");button.disabled=true;
  try{
    const task=await api("/api/market/catalog/refresh",{});
    const state=await waitAcquisition(task.task_id,s=>$("live-summary").textContent=`Каталог: ${labels[s.status]||s.status}`);
    if(state.status!=="succeeded")throw new Error(state.error||"Не удалось загрузить каталог");
    await searchInstruments();$("live-summary").textContent="Каталог обновлён. Можно выбрать инструмент.";
  }catch(error){diagnostic({diagnostics:[{message:error.message}]});}finally{button.disabled=false;}
}

async function searchInstruments(){
  const value=$("live-symbol").value.replace(/^BINANCE:/,"");
  const response=await api(`/api/market/instruments?q=${encodeURIComponent(value)}&limit=50`);
  $("market-instruments").replaceChildren();
  for(const row of response.instruments){if(row.status!=="TRADING")continue;const option=document.createElement("option");option.value=`BINANCE:${row.instrument.symbol}`;option.label=`${row.base_asset} / ${row.quote_asset}`;$("market-instruments").append(option);}
}

async function startLive(){
  if(busy)return;clearError();lock(true);$("live-start").disabled=true;
  try{
    const artifact=await compile();if(!artifact)return;
    const symbol=$("live-symbol").value;
    if(!/^BINANCE:[\p{L}\p{N}]{2,30}$/u.test(symbol)||symbol!==symbol.toUpperCase())throw new Error("Выберите BINANCE:SYMBOL из каталога Spot");
    const timeframe=$("live-timeframe").value,warmup=Number($("live-warmup").value);
    if(!Number.isInteger(warmup)||warmup<1||warmup>100000)throw new Error("Прогрев: от 1 до 100000 баров");
    const start=liveWarmupStart(timeframe,warmup);
    // Discovery is an explicit background task, never a blocking HTTP fetch.
    const cached=await api(`/api/market/instruments?q=${encodeURIComponent(symbol.slice(8))}&limit=100`);
    if(!cached.instruments.some(row=>row.instrument.symbol===symbol.slice(8)&&row.status==="TRADING")){
      const task=await api("/api/market/catalog/refresh",{}),state=await waitAcquisition(task.task_id);
      if(state.status!=="succeeded")throw new Error(state.error||"Каталог недоступен");
    }
    const value=await api("/api/market/live",{input_ref:compiledRef,instrument:{exchange:"binance",market:"spot",symbol:symbol.slice(8),price_kind:"trade"},timeframe,start_ms:start,broker_settings:null,timeout_seconds:60,gap_policy:$("live-allow-gaps").checked?"allow_with_metadata":"fail",fresh_feed:$("live-fresh-feed").checked});
    liveId=value.session_id;liveFollow=true;liveStart=0;
    addLiveSession(value);$("live-session").value=liveId;
    localStorage.setItem("openpine.live",liveId);await pollLive();
  }catch(error){diagnostic({diagnostics:[{message:error.message}]});}finally{lock(false);$("live-start").disabled=Boolean(liveId);}
}

function renderLiveStatus(state){
  liveState=state;const source=state.source,replay=state.replay;
  $("live-status").textContent=liveLabels[state.status]||state.status;
  $("live-status").dataset.connected=String(state.status==="live");
  $("live-stop").disabled=state.desired!=="running";$("live-resume").disabled=state.desired==="running"||state.status==="failed";
  const configuration=state.configuration||source.last_observation;
  if(configuration){$("live-symbol").value=`BINANCE:${configuration.instrument.symbol}`;$("live-timeframe").value=configuration.timeframe;if(state.warmup_done)$("live-warmup").value=Math.max(1,state.sources?.primary?.warmup_offset??state.warmup_offset??1);if(configuration.gap_policy)$("live-allow-gaps").checked=configuration.gap_policy==="allow_with_metadata";}
  if(configuration?.fresh_feed!==undefined)$("live-fresh-feed").checked=configuration.fresh_feed;
  for(const id of ['live-symbol','live-timeframe','live-warmup','live-allow-gaps','live-fresh-feed','live-start'])$(id).disabled=true;
  const selected=Array.from($("live-session").options).find(o=>o.value===state.session_id);if(selected)selected.textContent=`${state.session_id.slice(0,8)} · ${liveLabels[state.status]||state.status}`;
  $("live-summary").textContent=`${configuration?.instrument.symbol||""} · ${intervalLabel(configuration?.timeframe||'1')} · ${replay.confirmed_bars.toLocaleString("ru")} подтверждённых баров · очередь источников: ${state.unprojected_records} · событий ожидают расчёта: ${replay.pending_events} · сессия ${state.session_id.slice(0,8)}`;
  renderLiveContexts(state);
  const gaps=state.mapper.gaps,holes=(state.coverage?.counts.missing_candle||0)+(state.coverage?.counts.not_fetched||0);
  $("live-gap").hidden=!gaps&&!holes;
  $("live-gap").textContent=`Разрывов потока: ${gaps}. После разрывов восстановлено закрытых баров: ${state.mapper.recovered_bars}. Пропусков закрытых свечей: ${holes}. Пропущенные обновления внутри свечей не восстановлены.`;
  const current=state.preview_candle;
  $("live-current").textContent=current?`${current.closed?"Закрытая":"Предварительная"} свеча ${new Date(current.open_time).toISOString()} · O ${number(current.open)}  H ${number(current.high)}  L ${number(current.low)}  C ${number(current.close)}  V ${number(current.volume)}`:"Ожидаем первое обновление биржи.";
  if(state.error)diagnostic({diagnostics:[{code:"LIVE",message:state.error}]});
  else if(source.status==="failed")diagnostic({diagnostics:[{code:"SOURCE",message:source.error}]});
}

function renderLiveContexts(state){
  const holder=$('live-contexts');holder.replaceChildren();
  const bindings=state.configuration?.bindings||{};
  const contexts=Object.keys(state.sources||{}).length?state.sources:{primary:{source:state.source,cursor:state.cursor,warmup_done:state.warmup_done,coverage:state.coverage}};
  for(const [id,row] of Object.entries(contexts)){
    const source=row.source,config=bindings[id]||state.configuration,card=document.createElement('div');card.className='context-card';
    const title=document.createElement('strong');title.textContent=`${config.instrument.symbol} · ${intervalLabel(config.timeframe)}`;
    const status=document.createElement('span');status.className='context-state';status.textContent=liveLabels[source.status]||source.status||'Ожидание';
    const values=document.createElement('p');values.textContent=`${id==='primary'?'Основной':'Запрошенный'} · подтверждено: ${state.replay.contexts?.[id]?.count||0} · очередь: ${source.accepted-row.cursor}`;
    const stamp=document.createElement('small');stamp.textContent=source.last_received_at?`Получено: ${new Date(source.last_received_at).toISOString()}`:(row.warmup_done?'Прогрев завершён':'Загрузка прогрева');
    card.append(title,status,values,stamp);
    if(source.error){const error=document.createElement('p');error.className='context-error';error.textContent=source.error;card.append(error);}
    holder.append(card);
  }
}

async function pollLive(){
  clearTimeout(liveTimer);if(!liveId)return;const generation=++liveGeneration;
  try{
    const state=await api(`/api/market/live/${liveId}`);if(generation!==liveGeneration)return;renderLiveStatus(state);
    if(liveFollow)liveStart=Math.max(0,state.replay.confirmed_bars-500);
    const window=await api(`/api/market/live/${liveId}/chart?start=${liveStart}&limit=500`);
    if(generation!==liveGeneration)return;liveWindow=window;
    $("live-charts").hidden=!liveWindow.bars.length&&!state.preview_candle;
    $("live-previous").disabled=liveStart===0;$("live-next").disabled=liveWindow.next>=liveWindow.total;
    drawLive();
    if($("live-trades").open){const total=state.replay.simulation?.journal?.records||0;const ledger=await api(`/api/market/live/${liveId}/ledger?start=${Math.max(0,total-50)}&limit=50`);$("live-ledger").textContent=ledger.events.map(e=>`${e.sequence} · ${new Date(e.time).toISOString()} · ${e.kind} · ${JSON.stringify(e.value)}`).join("\n")||"Событий пока нет.";}
  }catch(error){if(generation===liveGeneration){$("live-status").textContent="Ожидание сервера";diagnostic({diagnostics:[{message:error.message}]});}}
  if(liveId&&generation===liveGeneration)liveTimer=setTimeout(pollLive,1000);
}

function liveCanvas(canvas,series,candles=false){
  const rect=canvas.getBoundingClientRect();if(!rect.width)return;
  const ratio=window.devicePixelRatio||1;canvas.width=rect.width*ratio;canvas.height=rect.height*ratio;
  const ctx=canvas.getContext("2d");ctx.scale(ratio,ratio);const width=rect.width-85,height=rect.height-32;
  const values=series.flatMap(s=>candles?s.values.flatMap(v=>[v.high,v.low]):s.values).filter(v=>Number.isFinite(v));
  if(!values.length)return;let min=Math.min(...values),max=Math.max(...values);if(min===max){min-=1;max+=1;}
  const scale=Math.max(Math.abs(min),Math.abs(max),1),low=min/scale,span=max/scale-low;
  const y=v=>10+(1-(v/scale-low)/span)*height,x=(i,n)=>8+(i+.5)/Math.max(n,1)*(width-8);
  ctx.font="10px system-ui";
  for(let k=0;k<4;k++){const yy=10+k*height/3;ctx.strokeStyle="#263140";ctx.beginPath();ctx.moveTo(0,yy);ctx.lineTo(width,yy);ctx.stroke();ctx.fillStyle="#91a0b4";ctx.fillText(number((max/scale+(min/scale-max/scale)*k/3)*scale),width+6,yy+3);}
  for(const s of series){ctx.strokeStyle=s.color;ctx.fillStyle=s.color;ctx.lineWidth=1.5;
    if(candles){const n=s.values.length,bw=Math.max(1,Math.min(8,width/n*.65));s.values.forEach((b,i)=>{const xx=x(i,n);ctx.strokeStyle=ctx.fillStyle=b.close>=b.open?"#5de4c2":"#ff8e98";ctx.globalAlpha=b.preview?0.55:1;ctx.beginPath();ctx.moveTo(xx,y(b.high));ctx.lineTo(xx,y(b.low));ctx.stroke();ctx.fillRect(xx-bw/2,Math.min(y(b.open),y(b.close)),bw,Math.max(1,Math.abs(y(b.open)-y(b.close))));});ctx.globalAlpha=1;
    }else{ctx.beginPath();let started=false;s.values.forEach((v,i)=>{if(!Number.isFinite(v)){started=false;return;}if(started)ctx.lineTo(x(i,s.values.length),y(v));else ctx.moveTo(x(i,s.values.length),y(v));started=true;});ctx.stroke();}
  }
}

function drawLive(){
  if(!liveWindow||!liveState)return;
  const bars=[...liveWindow.bars];const raw=liveState.preview_candle;
  if(liveFollow&&raw&&!raw.closed&&(!bars.length||raw.open_time>bars.at(-1).time))bars.push({...raw,time:raw.open_time,preview:true});
  liveCanvas($("live-price-chart"),[{values:bars,color:colors[0]}],true);
  const outputs=liveWindow.outputs.slice(0,32),values=new Map();
  for(const [index,time,id,value] of liveWindow.rows){if(!values.has(id))values.set(id,new Map());values.get(id).set(time,value);}
  const preview=liveWindow.preview;
  if(liveFollow&&preview)for(const output of preview.outputs){if(!values.has(output.output_id))values.set(output.output_id,new Map());values.get(output.output_id).set(preview.time,output.value);}
  liveCanvas($("live-output-chart"),outputs.map((o,i)=>({color:colors[i%colors.length],values:bars.map(b=>values.get(o.output_id)?.get(b.time)??null)})));
  $("live-legend").replaceChildren();for(const [i,o] of outputs.entries()){const span=document.createElement("span");span.style.color=colors[i%colors.length];span.textContent=o.title||o.output_id;$("live-legend").append(span);}
}

$("live-start").onclick=startLive;
$("live-catalog-refresh").onclick=refreshInstruments;
$("live-symbol").addEventListener("change",()=>searchInstruments().catch(e=>diagnostic(e.message)));
$("live-stop").onclick=async()=>{try{renderLiveStatus(await api(`/api/market/live/${liveId}/stop`,{}));}catch(e){diagnostic(e.message);}};
$("live-resume").onclick=async()=>{try{await api(`/api/market/live/${liveId}/resume`,{});await pollLive();}catch(e){diagnostic(e.message);}};
$("live-previous").onclick=()=>{liveFollow=false;liveStart=Math.max(0,liveStart-500);pollLive();};
$("live-next").onclick=()=>{liveFollow=false;liveStart=Math.min(Math.max(0,liveWindow.total-1),liveWindow.next);pollLive();};
$("live-tail").onclick=()=>{liveFollow=true;pollLive();};
function addLiveSession(state){if(Array.from($("live-session").options).some(o=>o.value===state.session_id))return;const option=document.createElement('option');option.value=state.session_id;option.textContent=`${state.session_id.slice(0,8)} · ${liveLabels[state.status]||state.status}`;$("live-session").append(option);}
$("live-session").onchange=()=>{clearTimeout(liveTimer);liveGeneration++;liveId=$("live-session").value||null;liveFollow=true;if(liveId){localStorage.setItem('openpine.live',liveId);pollLive();}else{localStorage.removeItem('openpine.live');for(const id of ['live-symbol','live-timeframe','live-warmup','live-allow-gaps','live-fresh-feed','live-start'])$(id).disabled=false;$("live-stop").disabled=true;$("live-resume").disabled=true;$("live-status").textContent='Новое наблюдение';$("live-summary").textContent='Выберите параметры нового наблюдения. Сохранённые сессии остаются в списке.';$("live-gap").hidden=true;$("live-charts").hidden=true;}};
new ResizeObserver(drawLive).observe($("live-charts"));
$('live-session').addEventListener('change',()=>{if(!liveId)$('live-contexts').replaceChildren();});
(async()=>{try{const config=await api("/api/config");token=config.token;await searchInstruments();const sessions=(await api("/api/market/live")).sort((a,b)=>(b.created_at||0)-(a.created_at||0));for(const s of sessions)addLiveSession(s);const saved=localStorage.getItem("openpine.live");const selected=sessions.find(s=>s.session_id===saved)||sessions.find(s=>s.desired==="running")||sessions[0];if(selected){liveId=selected.session_id;$("live-session").value=liveId;await pollLive();}}catch(error){diagnostic(error.message);}})();
