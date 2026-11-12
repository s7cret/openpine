"use strict";
const $=id=>document.getElementById(id);
const colors=["#5de4c2","#8ba9ff","#f5be76","#e58fd4","#80c9ee","#b3d67e"];
let compiledRef=null,compiledArtifactRef=null,windowStart=0,windowTotal=0;
let compiledAlertTemplates=false;
let resultJob=null,token="",snapshot=null,compiled=null,compiledText="",compiledName="",job=null,result=null,chartData=null,sourceName="v6.pine",busy=false,lastSpan=null,view=[0,0],visible=[];
const terminal=new Set(["succeeded","failed","cancelled","timed_out","paused"]);
const labels={waiting_resources:"Ожидание ресурсов",queued:"В очереди",running:"Выполняется",paused:"На паузе",succeeded:"Готово",failed:"Ошибка",cancelled:"Отменено",timed_out:"Превышен лимит времени",starting:"Запуск worker",compiling:"Компиляция",executing:"Расчёт",checkpointing:"Сохранение состояния",restoring:"Восстановление"};
function download(name,value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+"\n"],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}
async function downloadRaw(url,name){try{const response=await fetch(url);if(!response.ok)throw new Error(`HTTP ${response.status}`);const href=URL.createObjectURL(await response.blob());const a=document.createElement("a");a.href=href;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(href),5000);}catch(error){diagnostic({diagnostics:[{message:error.message}]});}}
function lock(on){busy=on;if(typeof updateLiveStart==="function")updateLiveStart();if(typeof setMagnifierLock==="function")setMagnifierLock(on);if(typeof setLaunchLock==="function")setLaunchLock(on);if(typeof setInputLock==="function")setInputLock(on);for(const id of ["run","compile","prepare","load-example","version","pine-file","data-source","bars-file","symbol","timeframe","start","end","timeout"])$(id).disabled=on;$("source").readOnly=on;$("cancel").hidden=!(on&&job);$("run").disabled=on||!snapshot;$("download-snapshot").disabled=!snapshot;for(const button of $("history").querySelectorAll("button"))button.disabled=on||button.dataset.available!=="true";}
function diagnostic(value){const ds=value.diagnostics||[value.error||{message:String(value)}];$("diagnostics").hidden=false;$("diagnostic-text").textContent=ds.map(d=>`${d.code||"Ошибка"}: ${d.message}${d.span?`\n${d.span.source}:${d.span.line}:${d.span.column}`:""}`).join("\n\n");lastSpan=ds[0]?.span;$("goto-error").hidden=!lastSpan;}
function clearError(){$("diagnostics").hidden=true;lastSpan=null;}
function status(text,done=0,total=0){$("status").textContent=text;$("progress-text").textContent=`${done.toLocaleString("ru")} / ${total.toLocaleString("ru")} баров`;$("progress").style.width=total?`${Math.min(100,100*done/total)}%`:"0";}
function setSnapshot(value){const wasBusy=busy;const d=value.identity;snapshot={...value,sha256:value.snapshot_id,data:{symbol:d.symbol,timeframe:d.timeframe,bars:[]}};$("data-summary").textContent=`${d.symbol} · ${d.timeframe} · ${d.count.toLocaleString("ru")} баров\n${new Date(d.first_ms).toISOString()} → ${new Date(d.last_ms).toISOString()}\nСнимок ${value.snapshot_id.slice(0,12)}`;$("run-hint").textContent="Данные закреплены. Можно компилировать и запускать.";lock(wasBusy);if(typeof primarySnapshotChanged==="function")primarySnapshotChanged();}
async function loadExample(){const data=await api(`/api/examples/${$("version").value}`);$("source").value=data.source;sourceName=`v${$("version").value}.pine`;$("source-name").textContent=sourceName;sourceChanged();}
function sourceChanged(){if(typeof clearProgramPassport==="function")clearProgramPassport();if(typeof invalidateLaunch==="function")invalidateLaunch();if(typeof clearInputSchema==="function")clearInputSchema();compiled=null;compiledText="";compiledAlertTemplates=false;if(typeof updateLiveAlertOption==="function")updateLiveAlertOption(true);$("download-ir").disabled=true;const match=$("source").value.match(/^\s*\/\/@version=(\d+)/m);$("source-info").textContent=`Pine v${match?match[1]:"1"} · ${new TextEncoder().encode($("source").value).length} байт`;}
async function compile(){clearError();const text=$("source").value;if(compiled&&compiledText===text&&compiledName===sourceName)return compiled;status("Компиляция");const data=await api("/api/compile",{source:text,source_name:sourceName});if(data.status==="rejected"){diagnostic(data);status("Ошибка компиляции");return null;}compiled=data.artifact;compiledRef=data.input_ref;compiledArtifactRef=data.artifact_ref;compiledText=text;compiledName=sourceName;
  compiledAlertTemplates=Boolean(data.alert_conditions?.length)&&[4,5,6].includes(data.identity?.language_version);
  if(typeof updateLiveAlertOption==="function")updateLiveAlertOption();
  await setInputSchema(data.inputs||null);$("download-ir").disabled=false;if(typeof refreshLaunchRequirements==="function")await refreshLaunchRequirements();status("Скомпилировано");return compiled;}
async function prepare(){
  clearError();lock(true);status("Загрузка данных");
  try{
    const mode=$("data-source").value;let body,saved,coverage=null;
    if(mode==="demo")body={provider:"demo"};
    else if(mode==="file"){
      const file=$("bars-file").files[0];if(!file)throw new Error("Выберите JSON-файл с барами или снимком");
      if(file.size>16*1024*1024)throw new Error("Файл превышает 16 MiB");body={provider:"upload",raw:await file.text()};
    }else{
      const start=Date.parse($("start").value+"Z"),end=Date.parse($("end").value+"Z"),symbol=$("symbol").value;
      if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end)throw new Error("Укажите корректный диапазон UTC");
      if(!/^BINANCE:[\p{L}\p{N}]{2,30}$/u.test(symbol)||symbol!==symbol.toUpperCase())throw new Error("Укажите BINANCE:SYMBOL для рынка Spot");
      const task=await api("/api/market/history",{schema_version:"openpine.history-request.1",instrument:{exchange:"binance",market:"spot",symbol:symbol.slice(8),price_kind:"trade"},timeframe:$("timeframe").value,start_ms:start,end_ms:end,gap_policy:$("allow-data-gaps").checked?"allow_with_metadata":"fail",refresh:false,source:$("history-kind").value});
      $("cancel-fetch").hidden=false;
      acquisitionTask=task.task_id;
      const state=await waitAcquisition(task.task_id,s=>{$("data-summary").textContent=`${s.bars.toLocaleString("ru")} баров сохранено · ${s.pages} страниц · ${labels[s.status]||s.status}`;});
      coverage=state.coverage;
      if(state.status!=="succeeded")throw new Error((state.error||"Нет подтверждённых свечей")+(coverage?`\nПокрытие: ${JSON.stringify(coverage.counts)}`:""));
      saved={sha256:state.snapshot_id};
    }
    if(body)saved=await api("/api/snapshots",body);
    setSnapshot(await api(`/api/datasets/${saved.sha256}`));
    if(coverage)$("data-summary").textContent+=`\nПокрытие: ${coverage.confirmed_complete?"подтверждённый диапазон полный":"есть пропуски"}; открытых свечей: ${coverage.counts.not_closed}; до начала истории: ${coverage.counts.before_available}`;
    status("Данные готовы",0,snapshot.identity.count);
  }catch(error){diagnostic({diagnostics:[{message:error.message}]});status("Ошибка данных");}
  finally{acquisitionTask=null;$("cancel-fetch").hidden=true;lock(false);}
}
async function run(){lock(true);job=null;result=null;renderProtection();$("charts").hidden=true;$("empty-chart").hidden=false;$("chart-title").textContent="Результат расчёта";$("chart-subtitle").textContent="Подготовка запуска";$("outputs").replaceChildren();$("identity").hidden=true;$("download-result").disabled=true;try{const prepared=await currentPreparedLaunch(true);const state=await api(`/api/launches/${prepared.launch_id}/start`,{binding_sha256:checkedBindingFor(prepared.launch_id)});showLaunch({...prepared,status:"submitted",job_id:state.job_id});await watchHistoricalJob(state.job_id);}catch(error){diagnostic({diagnostics:[{message:error.message}]});status("Ошибка запуска");}finally{lock(false);}}
function number(value){return value===null||value===undefined?"na":new Intl.NumberFormat("ru",{maximumSignificantDigits:9}).format(value);}
function renderResult(response,data,jobId){clearError();if(typeof showMagnifierResult==="function")showMagnifierResult(response.result?.simulation?.bar_magnifier);result=response;chartData=data;resultJob=jobId;const r=response.result;status("Готово",r.observation_count,r.observation_count);$("empty-chart").hidden=true;$("charts").hidden=false;$("chart-title").textContent=r.declaration.title;$("chart-subtitle").textContent=`${data.data.symbol} · Pine v${r.identity.language_version} · ${r.observation_count.toLocaleString("ru")} баров`;
$("outputs").replaceChildren();
for (const output of r.outputs) {
  const tr=document.createElement("tr");
  const stats=isPineOHLC(output)
    ? [number(pineLatestValue(output)),output.bars.length,output.bars.filter(bar=>bar===null).length]
    : [number("last" in output?output.last:output.values.at(-1)),output.count??output.values.length,
       output.null_count??output.values.filter(v=>v===null).length];
  for (const value of [output.title||output.output_id,...stats]) {
    const td=document.createElement("td");td.textContent=String(value);tr.append(td);
  }
  $("outputs").append(tr);
}
$("identity").hidden=false;$("identity-text").textContent=`Pine v${r.identity.language_version}\nПрограмма: ${response.program_sha256}\nДанные: ${r.data.sha256}\nСнимок: ${response.snapshot_sha256||data.sha256}`;$("download-result").disabled=false;$("reset-view").disabled=false;view=[0,r.observation_count];visible=r.outputs.map((_,i)=>i<32);$("legend").replaceChildren();
for(const [i,o]of r.outputs.slice(0,32).entries()){
  if(isPineLayerHidden(o))continue;
  const b=document.createElement("button");b.textContent=o.title||`Ряд ${i+1}`;b.style.color=colors[i%colors.length];b.setAttribute("aria-pressed","true");
  b.onclick=()=>{visible[i]=!visible[i];b.setAttribute("aria-pressed",String(visible[i]));draw();};$("legend").append(b);
}
renderProtection();
const tables=splitPineDrawings(r.tables||[],r.declaration.overlay);
renderPineTables($("price-tables"),tables.price);
renderPineTables($("output-tables"),tables.pane);
draw();}
function chart(canvas,primary,script,side,plotNumber,explicit,order,overlay){
  const market=canvas.id==="price-chart", bars=chartData.data.bars;
  renderPineCanvas(canvas,{primary,script,side,bars,start:view[0],end:view[1],origin:chartData.start??0,
    market,candles:market&&bars.some(b=>[b.open,b.high,b.low,b.close].every(Number.isFinite)),
    plotNumber,explicit,order,overlay,protection:market?(...args)=>drawProtectionFills(...args):null});
}
function draw(){
  if(!result||result.status!=="executed"||!chartData)return;
  const windowStart=chartData.start??0, total=chartData.outputTotal??result.result.observation_count;
  const layers=result.result.outputs.map((o,i)=>({...o,color:colors[i%colors.length],hiddenLine:!visible[i],
    firstSignalIndex:firstSignalIndex(o,windowStart,total)}));
  const {overlay,chart_scale:scale,visual_order:order}=result.result.declaration;
  const independent=pineIndependentScale(scale,overlay);
  const formatting=pineNumberFormatting(result.result.declaration.number_format,chartData.price_precision,number);
  const plots=pineValuePlots(layers,result.result.identity?.language_version);
  const outputs=splitPineOutputs(layers,overlay,independent);
  const linefills=result.result.linefills||[];
  const bars=chartData.data.bars,hasCandles=bars.some(b=>[b.open,b.high,b.low,b.close].every(Number.isFinite));
  const labels = splitPineDrawings(pineLabelWindow(result.result.labels || [], bars, chartData.start ?? 0), overlay, independent);
  const zones = splitPineDrawings(result.result.boxes || [], overlay, independent);
  const lines = splitPineDrawings(result.result.lines || [], overlay, independent);
  const polylines = splitPineDrawings(result.result.polylines || [], overlay, independent);
  const priceCanvas=$("price-chart"), outputCanvas=$("output-chart");
  const market=[...(!hasCandles?[{values:bars.map(b=>b.close),color:"#8ba9ff",marketSeries:true}]:[]),
    ...protectionSeries().map(layer=>({...layer,protectionSeries:true})),...outputs.price];
  const domain=(key,series=outputs[key],ticks=formatting.chart)=>pineCanvasDomain(series,labels[key],
    lines[key],zones[key],linefills,polylines[key],pineAxisPlots(plots,overlay,key,independent),ticks);
  chart(priceCanvas,domain("price",market,number),independent?domain("script"):null,independent?scale:null,formatting.plot,Boolean(scale),order,overlay);
  chart(outputCanvas,domain("pane"),null,!overlay?scale:null,formatting.plot,Boolean(scale&&!overlay),order,overlay);
  updatePineValueSurfaces($("plot-status-line"),$("plot-data-window"),plots,bars,view,[priceCanvas,outputCanvas],false,formatting.plot);
}

for(const id of ["price-chart","output-chart"]){
  const canvas=$(id);let drag=null;
  canvas.addEventListener("pointerdown",event=>{if(result){drag={x:event.clientX,view:[...view]};canvas.setPointerCapture(event.pointerId);}});
  for(const event of ["pointerup","pointerleave","pointercancel"])canvas.addEventListener(event,()=>drag=null);
  canvas.addEventListener("pointermove",event=>{
    if(!drag||!result||!chartData)return;
    const n=drag.view[1]-drag.view[0],{left,right}=pineCanvasHorizontal(canvas);
    const delta=Math.round((drag.x-event.clientX)*n/Math.max(1,right-left));
    const start=Math.max(0,Math.min(chartData.data.bars.length-n,drag.view[0]+delta));view=[start,start+n];draw();
  });
  bindPineLabelHover(canvas,$("tooltip"),x=>{
    if(!result||!chartData||drag)return "";
    const fraction=pineCanvasFraction(canvas,x);
    const i=Math.min(view[1]-1,view[0]+Math.round(fraction*(view[1]-view[0]-1)));
    const bar=chartData.data.bars[i];if(!bar)return "";
    const start=chartData.start??0,total=chartData.outputTotal??result.result.observation_count;
    const formatting=pineNumberFormatting(result.result.declaration.number_format,chartData.price_precision,number);
    return [new Date(bar.time).toISOString(),"Close: "+number(bar.close),
      ...result.result.outputs.slice(0,32).map((o,j)=>visible[j]&&!isPineLayerHidden(o)&&pineDisplays(o,"data_window")
        ?`${o.title||`Ряд ${j+1}`}: ${pineValueText(o,i,formatting.plot,firstSignalIndex(o,start,total))}`:"").filter(Boolean)].join("\n");
  });
  canvas.addEventListener("wheel",event=>{if(!result)return;event.preventDefault();const n=result.result.observation_count,current=view[1]-view[0],size=Math.max(Math.min(2,n),Math.min(n,Math.round(current*(event.deltaY>0?1.25:.8)))),center=(view[0]+view[1])/2,start=Math.max(0,Math.min(n-size,Math.floor(center-size/2)));view=[start,start+size];draw();},{passive:false});
}
async function history(){const jobs=await api("/api/jobs");$("history").replaceChildren();if(!jobs.length)return;const label=document.createElement("span");label.textContent="Недавние запуски: ";$("history").append(label);for(const state of jobs.slice(0,6)){const b=document.createElement("button");b.textContent=`${labels[state.status]||state.status} · ${state.job_id.slice(0,6)}`;b.dataset.available=String(Boolean(state.result_sha256));b.disabled=busy||!state.result_sha256;b.onclick=()=>loadJob(state.job_id).catch(error=>diagnostic({diagnostics:[{message:error.message}]}));$("history").append(b);}}
$("source").addEventListener("input",sourceChanged);$("load-example").onclick=()=>loadExample().catch(e=>diagnostic(e.message));$("pine-file").onchange=async()=>{const f=$("pine-file").files[0];if(!f)return;if(f.size>65536){diagnostic("Pine-файл превышает 64 KiB");return;}$("source").value=await f.text();sourceName=f.name;$("source-name").textContent=f.name;sourceChanged();};$("data-source").onchange=()=>{$("file-fields").hidden=$("data-source").value!=="file";$("binance-fields").hidden=$("data-source").value!=="binance";snapshot=null;$("data-summary").textContent="Подготовьте данные выбранного источника.";lock(false);};for(const id of ["symbol","timeframe","start","end","bars-file"])$(id).addEventListener("change",()=>{snapshot=null;$("data-summary").textContent="Параметры изменены. Подготовьте данные заново.";lock(false);});$("prepare").onclick=prepare;$("run").onclick=run;$("compile").onclick=async()=>{lock(true);try{await compile();}catch(e){diagnostic(e.message);}finally{lock(false);}};$("cancel").onclick=async()=>{if(job)try{await api(`/api/jobs/${job}/cancel`,{});}catch(e){diagnostic(e.message);}};$("download-ir").onclick=()=>compiledArtifactRef&&downloadRaw(`/api/artifacts/${compiledArtifactRef}`,"openpine-compiled.json");$("download-result").onclick=()=>resultJob&&downloadRaw(`/api/jobs/${resultJob}/result`,"openpine-result.json");$("download-snapshot").onclick=()=>snapshot&&downloadRaw(`/api/snapshots/${snapshot.sha256}`,"openpine-snapshot.json");$("goto-error").onclick=()=>{if(lastSpan){const text=$("source").value,offset=n=>Array.from(text).slice(0,n).join("").length;$("source").focus();$("source").setSelectionRange(offset(lastSpan.start),offset(lastSpan.end));}};$("reset-view").onclick=()=>{view=[0,result.result.observation_count];draw();};new ResizeObserver(draw).observe($("charts"));

async function loadJob(jobId,start=0){await inspectSavedJob(jobId);const response=await api(`/api/jobs/${jobId}/summary`);if(response.status!=="executed"){diagnostic(response);return;}if(!response.result.series){const request=await api(`/api/jobs/${jobId}/request`);const data=await api(`/api/snapshots/${request.snapshot_id}`);renderResult(response,data,jobId);return;}const window=await api(`/api/jobs/${jobId}/chart?start=${start}&limit=2000`);if(!window.bars.length)throw new Error("В этом диапазоне нет баров");windowStart=start;windowTotal=window.total;const rendered={...response,result:{...response.result,observation_count:window.bars.length,outputs:window.outputs,labels:window.labels,lines:window.lines,boxes:window.boxes,linefills:window.linefills,polylines:window.polylines}};renderResult(rendered,{start:window.start,outputTotal:response.result.observation_count,price_precision:window.price_precision,sha256:response.snapshot_sha256,data:{...response.result.data,bars:window.bars},protection:window.protection},jobId);status("Готово",window.total,window.total);$("chart-subtitle").textContent=`${response.result.data.symbol} · Pine v${response.identity.language_version} · бары ${(start+1).toLocaleString("ru")}–${(start+window.bars.length).toLocaleString("ru")} из ${window.total.toLocaleString("ru")}`;$("previous-window").disabled=start===0;$("next-window").disabled=start+window.bars.length>=window.total;}
const paging=document.createElement("div");paging.className="legend";for(const [id,title,delta]of [["previous-window","← Предыдущие 2000",-2000],["next-window","Следующие 2000 →",2000]]){const button=document.createElement("button");button.id=id;button.textContent=title;button.disabled=true;button.onclick=async()=>{button.disabled=true;try{await loadJob(resultJob,Math.max(0,Math.min(windowTotal-1,windowStart+delta)));}catch(error){diagnostic({diagnostics:[{message:error.message}]});button.disabled=false;}};paging.append(button);}$("charts").append(paging);

(async()=>{try{lock(true);const config=await api("/api/config");token=config.token;$("python").textContent=config.python;await loadExample();const saved=await api("/api/snapshots",{provider:"demo"});setSnapshot(await api(`/api/datasets/${saved.sha256}`));await history();if(typeof restoreLaunchSession==="function")await restoreLaunchSession();}catch(error){diagnostic({diagnostics:[{message:error.message}]});}finally{lock(false);}})();
