"use strict";
// One prepared context graph is shared by historical execution and every research form.
let launchId=null,launchSignature=null,launchRestoring=false,launchHydrated=false,launchBroker=null,historicalWatch=0;
let launchRequirements=[],launchChoices=new Map(),launchSnapshots=[],launchNextOffset=0,savedLaunchConfiguration=null,launchShownState=null,launchProviderTimeframes=[];
const launchLabels={preparing:'Подготовка данных',ready:'Готов к запуску',submitted:'Задание создано',cancelled:'Подготовка отменена',failed:'Ошибка подготовки'};
const launchCard=document.createElement('section');launchCard.className='card launch-card';
launchCard.innerHTML=`<div class="experiment-body"><h2>Контексты и подготовка запуска</h2><p>Источники определяются по скомпилированному скрипту. Каждый снимок закрепляется на время расчёта и исследований.</p><label>Сохранённая подготовка<select id="launch-select"><option value="">Новая подготовка</option></select></label><div class="live-actions"><button id="launch-open">Открыть подготовку</button><button id="launch-refresh">Обновить список</button><button id="launch-snapshot-more">Ещё сохранённые снимки</button></div><div id="launch-contexts" class="context-grid"></div><label><input id="launch-allow-gaps" type="checkbox"> Разрешить пропуски с отчётом</label><label><input id="launch-window-enabled" type="checkbox"> Задать прогрев и интервал оценки</label><div id="launch-window" hidden><label>Начало прогрева, UTC<input id="launch-warmup" type="datetime-local" step="1"></label><label>Начало оценки, UTC<input id="launch-score" type="datetime-local" step="1"></label><label>Конец, UTC (исключительно)<input id="launch-end" type="datetime-local" step="1"></label><p>Состояние начинается на выбранной истории. Необходимый прогрев задаётся явно; закрытые свечи не создаются и не пересчитываются.</p></div><div class="live-actions"><button id="launch-prepare" class="secondary">Подготовить запуск</button><button id="launch-cancel" class="danger" disabled>Отменить подготовку</button><button id="launch-resume" disabled>Продолжить подготовку</button></div><p id="launch-status" role="status">Скомпилируйте скрипт и выберите основные данные.</p><div id="launch-coverage" class="context-grid"></div></div>`;
$('data-summary').closest('section').after(launchCard);
const savedOption=new Option('Сохранённый снимок','saved');$('data-source').append(savedOption);
const savedPrimary=document.createElement('div');savedPrimary.id='saved-fields';savedPrimary.hidden=true;
savedPrimary.innerHTML='<label>Сохранённые основные данные<select id="primary-snapshot"></select></label>';
$('file-fields').before(savedPrimary);
const oldPrepare=$('prepare').onclick;
$('prepare').onclick=async()=>{if($('data-source').value!=='saved')return oldPrepare();lock(true);try{const id=$('primary-snapshot').value;if(!id)throw new Error('Выберите сохранённый снимок');setSnapshot(await api('/api/datasets/'+id));}catch(error){diagnostic(error.message);}finally{lock(false);}};
$('data-source').addEventListener('change',()=>{savedPrimary.hidden=$('data-source').value!=='saved';invalidateLaunch();refreshLaunchRequirements().catch(launchError);});
const runControls=document.createElement('div');runControls.className='live-actions';
runControls.innerHTML='<button id="historical-pause" disabled>Пауза расчёта</button><button id="historical-resume" disabled>Продолжить расчёт</button><button id="historical-cancel" class="danger" disabled>Отменить расчёт</button>';
$('status').closest('section').append(runControls);
const savedView=document.createElement('details');savedView.id='saved-configuration';savedView.hidden=true;
savedView.innerHTML='<summary>Программа и параметры этого результата</summary><pre id="saved-configuration-info"></pre><textarea id="saved-source" aria-label="Исходник сохранённого запуска" readonly spellcheck="false"></textarea><button id="saved-copy">Открыть копию в редакторе</button><button id="saved-repeat" class="secondary">Повторить на этих данных</button>';
$('identity').after(savedView);

function launchError(error){$('launch-status').textContent=error.message||String(error);diagnostic({diagnostics:[{message:error.message||String(error)}]});}
function launchKey(row){return JSON.stringify([row.symbol,row.timeframe]);}
function invalidateLaunch(){if(launchRestoring||!launchHydrated)return;launchId=null;launchSignature=null;localStorage.removeItem('openpine.launch');if($('launch-status'))$('launch-status').textContent='Настройки изменены. Подготовьте запуск.';}
function launchDate(value){return new Date(value).toISOString().slice(0,19);}
function launchEnd(identity){const date=new Date(identity.last_ms),tf=identity.timeframe;if(tf==='M'){date.setUTCMonth(date.getUTCMonth()+1);return date.getTime();}return identity.last_ms+(tf==='D'?86400000:tf==='W'?604800000:Number(tf)*60000);}
function primarySnapshotChanged(){
  if(!launchRestoring)invalidateLaunch();
  if(snapshot?.identity){$('launch-warmup').value=launchDate(snapshot.identity.first_ms);$('launch-score').value=launchDate(snapshot.identity.first_ms);$('launch-end').value=launchDate(launchEnd(snapshot.identity));}
  refreshLaunchRequirements().catch(launchError);
}
function launchWindow(){if(!$('launch-window-enabled').checked)return null;const get=id=>{const value=Date.parse($(id).value+'Z');if(!Number.isSafeInteger(value)||value<0)throw new Error('Укажите корректное время UTC');return value;};const w={warmup_start_ms:get('launch-warmup'),score_start_ms:get('launch-score'),end_ms:get('launch-end')};if(!(w.warmup_start_ms<=w.score_start_ms&&w.score_start_ms<w.end_ms))throw new Error('Начало прогрева ≤ начало оценки < конец');return w;}
function setLaunchLock(on){for(const control of launchCard.querySelectorAll('input,select,button')){if(control.id==='launch-cancel')continue;if(on){if(!('launchWasDisabled' in control.dataset))control.dataset.launchWasDisabled=String(control.disabled);control.disabled=true;}else{control.disabled=control.dataset.launchWasDisabled==='true';delete control.dataset.launchWasDisabled;}}if(!on){$('launch-resume').disabled=!['cancelled','failed'].includes(launchShownState);$('launch-snapshot-more').disabled=launchNextOffset===null;}for(const control of savedView.querySelectorAll('button'))control.disabled=on||!savedLaunchConfiguration?.source;}
function fillSnapshotSelect(select,rows,selected=''){
  select.replaceChildren(new Option('Выберите снимок',''));
  for(const item of rows){const d=item.identity;select.append(new Option(`${d.symbol} · ${d.timeframe} · ${d.count} баров · ${item.snapshot_id.slice(0,10)}`,item.snapshot_id));}
  if(selected&&!rows.some(r=>r.snapshot_id===selected))select.append(new Option('Закреплённый снимок '+selected.slice(0,12),selected));
  select.value=selected;
}
async function loadLaunchSnapshots(reset=false){
  if(reset){launchSnapshots=[];launchNextOffset=0;}
  if(launchNextOffset===null)return;
  const page=await api(`/api/launches/snapshots?offset=${launchNextOffset}&limit=50`);launchNextOffset=page.next_offset;
  for(const row of page.items)if(!launchSnapshots.some(s=>s.snapshot_id===row.snapshot_id))launchSnapshots.push(row);
  fillSnapshotSelect($('primary-snapshot'),launchSnapshots,$('primary-snapshot').value);
  $('launch-snapshot-more').disabled=launchNextOffset===null;
  renderLaunchRequirements();
}
async function refreshLaunchRequirements(){
  if(!compiledRef||!compiled){launchRequirements=[];renderLaunchRequirements();return;}
  const ref=compiledRef,data=await api('/api/launches/requirements?input_ref='+ref);
  if(ref!==compiledRef)return;launchRequirements=data.contexts;renderLaunchRequirements();
}
function renderLaunchRequirements(){
  $('launch-contexts').replaceChildren();
  const required=launchRequirements.filter(r=>!(snapshot?.identity?.symbol===r.symbol&&snapshot.identity.timeframe===r.timeframe));
  if(!required.length){const p=document.createElement('p');p.className='muted';p.textContent=compiled?'Дополнительные контексты не требуются.':'Контексты появятся после компиляции.';$('launch-contexts').append(p);}
  for(const [index,row]of required.entries()){
    const key=launchKey(row),choice=launchChoices.get(key),box=document.createElement('div');box.className='context-choice';
    const title=document.createElement('h3');title.textContent=`${row.symbol} · ${row.timeframe}`;box.append(title);
    const visibility=document.createElement('p');visibility.textContent=row.requests.some(r=>r.historical_future)?'Historical lookahead on: финальное значение старшего бара доступно с его открытия.':'Lookahead off: значение доступно после закрытия старшего бара.';box.append(visibility);
    const mode=document.createElement('select');mode.id=`context-${index}-source`;mode.setAttribute('aria-label','Источник '+row.symbol+' '+row.timeframe);
    for(const [value,label]of [['file','Файл OHLCV'],['snapshot','Сохранённый снимок'],['history','Загрузить у провайдера']]){const option=new Option(label,value);if(value==='history'&&(!row.symbol.startsWith('BINANCE:')||!launchProviderTimeframes.includes(row.timeframe)))option.disabled=true;mode.append(option);}mode.value=choice?.mode||'file';box.append(mode);
    const file=document.createElement('input');file.type='file';file.accept='.json,application/json';file.id=`context-${index}-file`;file.setAttribute('aria-label','Файл '+row.symbol+' '+row.timeframe);box.append(file);
    const saved=document.createElement('select');saved.id=`context-${index}-snapshot`;saved.setAttribute('aria-label','Снимок '+row.symbol+' '+row.timeframe);
    fillSnapshotSelect(saved,launchSnapshots.filter(r=>r.identity.symbol===row.symbol&&r.identity.timeframe===row.timeframe),choice?.spec?.snapshot_id||'');box.append(saved);
    const provider=document.createElement('select');provider.id=`context-${index}-provider`;provider.append(new Option('Binance Spot REST','rest'),new Option('Месячный архив с SHA256','archive'));provider.value=choice?.source||'rest';box.append(provider);
    const state=document.createElement('p');state.id=`context-${index}-status`;state.textContent=choice?.spec?.snapshot_id?'Снимок закреплён · '+choice.spec.snapshot_id.slice(0,12):'Выберите источник данных.';box.append(state);
    const update=()=>{file.hidden=mode.value!=='file';saved.hidden=mode.value!=='snapshot';provider.hidden=mode.value!=='history';};update();
    mode.onchange=()=>{invalidateLaunch();launchChoices.set(key,{mode:mode.value,source:provider.value});state.textContent='Выберите данные для этого источника.';update();};
    provider.onchange=()=>{invalidateLaunch();launchChoices.set(key,{mode:'history',source:provider.value});};
    saved.onchange=()=>{invalidateLaunch();launchChoices.set(key,{mode:'snapshot',spec:saved.value?{kind:'snapshot',snapshot_id:saved.value}:null});state.textContent=saved.value?'Снимок выбран.':'Выберите снимок.';};
    file.onchange=async()=>{invalidateLaunch();launchChoices.delete(key);try{const f=file.files[0];if(!f)return;if(f.size>8*1024*1024)throw new Error('Файл контекста: не более 8 МиБ. Для больших данных выберите снимок или провайдер.');state.textContent='Загрузка…';const saved=await api('/api/snapshots',{provider:'upload',raw:await f.text()}),manifest=await api('/api/datasets/'+saved.sha256);if(manifest.identity.symbol!==row.symbol||manifest.identity.timeframe!==row.timeframe)throw new Error('Файл относится к другому символу или таймфрейму');launchChoices.set(key,{mode:'file',spec:{kind:'snapshot',snapshot_id:saved.sha256}});state.textContent=`${manifest.identity.count} баров · снимок ${saved.sha256.slice(0,12)}`;}catch(error){state.textContent=error.message;launchError(error);}};
    if(busy)for(const control of box.querySelectorAll('input,select,button')){control.dataset.launchWasDisabled='false';control.disabled=true;}
    $('launch-contexts').append(box);
  }
}
function showLaunch(state){
  launchShownState=state.status;
  $('launch-status').textContent=state.error||`${launchLabels[state.status]} · ${state.sources.filter(s=>s.snapshot_id).length}/${state.sources.length} источников`;
  $('launch-cancel').disabled=!['preparing','ready'].includes(state.status);$('launch-resume').disabled=!['cancelled','failed'].includes(state.status);
  $('launch-coverage').replaceChildren();
  for(const row of state.prepared?.datasets||[]){const box=document.createElement('div'),p=document.createElement('p');box.className='context-choice';const c=row.context;p.textContent=`${c.symbol} · ${c.timeframe} · ${row.identity.count} баров`;box.append(p);const info=document.createElement('p');info.textContent=`Загружено: ${new Date(row.loaded_start_ms).toISOString()} → ${new Date(row.loaded_end_ms).toISOString()}. `+(row.coverage?(row.coverage.requested_complete?'Требуемый диапазон полный.':`Пропуски: ${JSON.stringify(row.coverage.counts)}`):'В этом диапазоне нет закрытия запрошенного бара.');box.append(info);$('launch-coverage').append(box);}
}
async function currentPreparedLaunch(newRun=false){
  if(!await compile())throw new Error('Исправьте ошибки компиляции');if(!snapshot)throw new Error('Подготовьте основные данные');
  await refreshLaunchRequirements();const window=launchWindow(),gap_policy=$('launch-allow-gaps').checked?'allow_with_metadata':'fail',contexts=[];
  for(const row of launchRequirements){if(snapshot.identity.symbol===row.symbol&&snapshot.identity.timeframe===row.timeframe)continue;const choice=launchChoices.get(launchKey(row));if(choice?.mode==='history'){contexts.push(await api('/api/launches/history-spec',{input_ref:compiledRef,snapshot_id:snapshot.sha256,symbol:row.symbol,timeframe:row.timeframe,window,gap_policy,source:choice.source||'rest'}));}else{if(!choice?.spec)throw new Error(`Подготовьте контекст ${row.symbol} / ${row.timeframe}`);contexts.push(choice.spec);}}
  const config={schema_version:'openpine.launch-request.1',input_ref:compiledRef,primary:{kind:'snapshot',snapshot_id:snapshot.sha256},contexts,inputs:getInputOverrides(),timeout_seconds:Number($('timeout').value),gap_policy,...(window?{window}:{}),...(launchBroker?{broker_settings:launchBroker}:{})};
  const signature=JSON.stringify(config);let state;
  if(launchId&&launchSignature===signature)state=await api('/api/launches/'+launchId);
  if(state?.status==='submitted'&&newRun){const prior=await api('/api/jobs/'+state.job_id);if(['succeeded','failed','cancelled','timed_out'].includes(prior.status))state=null;}
  if(!state){config.request_id=crypto.randomUUID().replaceAll('-','');state=await api('/api/launches',config);launchId=state.launch_id;launchSignature=signature;localStorage.setItem('openpine.launch',launchId);}
  showLaunch(state);
  while(state.status==='preparing'){await pause(250);state=await api('/api/launches/'+launchId);showLaunch(state);}
  if(!['ready','submitted'].includes(state.status))throw new Error(state.error||launchLabels[state.status]);
  await refreshLaunchList();return state;
}
async function preparedResearchBase(){const state=await currentPreparedLaunch();return state.base;}
async function refreshLaunchList(){const selected=launchId||$('launch-select').value,rows=await api('/api/launches');$('launch-select').replaceChildren(new Option('Новая подготовка',''));for(const row of rows)$('launch-select').append(new Option(`${launchLabels[row.status]} · ${new Date(row.created_at*1000).toISOString()}`,row.launch_id));$('launch-select').value=selected||'';}
async function copyLaunchSource(source,inputs,primary,contexts,window,broker,timeout){
  launchRestoring=true;try{$('source').value=source.source;sourceName=source.source_name;$('source-name').textContent=sourceName;sourceChanged();await compile();if(!compiled)throw new Error('Сохранённый исходник не допускается текущей установкой');applyInputValues(inputs);setSnapshot(await api('/api/datasets/'+primary));launchChoices=new Map();for(const binding of contexts){const c=binding.context;launchChoices.set(launchKey(c),{mode:'snapshot',spec:{kind:'snapshot',snapshot_id:binding.snapshot_id}});}$('launch-window-enabled').checked=Boolean(window);$('launch-window').hidden=!window;if(window){$('launch-warmup').value=launchDate(window.warmup_start_ms);$('launch-score').value=launchDate(window.score_start_ms);$('launch-end').value=launchDate(window.end_ms);}launchBroker=broker||null;$('timeout').max='3600';$('timeout').value=timeout;await refreshLaunchRequirements();}finally{launchRestoring=false;}
}
async function openLaunch(key){
  let state=await api('/api/launches/'+key);const config=state.config;
  launchId=key;localStorage.setItem('openpine.launch',key);showLaunch(state);
  while(state.status==='preparing'){await pause(250);state=await api('/api/launches/'+key);showLaunch(state);}
  const prepared=state.prepared;
  const configuration=state.job_id?await api(`/api/jobs/${state.job_id}/configuration`):null;
  let source=configuration?.source;
  if(!source){const data=await api('/api/launches/'+key+'/source');source=data.source;}
  if(!source)throw new Error('Исходник отсутствует: сохранён только IR. Запуск через закреплённую подготовку доступен в API.');
  const primary=prepared?.request.snapshot_id||state.sources[0].snapshot_id;
  if(!primary){$('launch-status').textContent=state.error||'Подготовка остановлена до загрузки основных данных. Нажмите «Продолжить подготовку».';return;}
  const contexts=prepared?.request.contexts||[];
  await copyLaunchSource(source,configuration?.inputs||config.inputs,primary,contexts,config.window,config.broker_settings,config.timeout_seconds);
  if(!prepared){for(let i=0;i<config.contexts.length;i++){const spec=config.contexts[i];const pair=await api('/api/launches/'+key+'/sources');const row=pair.sources[i+1];launchChoices.set(launchKey(row),{mode:spec.kind==='snapshot'?'snapshot':'history',spec:spec.kind==='snapshot'?spec:null,source:spec.request?.source});}renderLaunchRequirements();}
  $('launch-allow-gaps').checked=config.gap_policy==='allow_with_metadata';launchId=key;launchSignature=null;localStorage.setItem('openpine.launch',key);showLaunch(state);
  // A restored preparation is opened for inspection; repeating creates a fresh explicit intent.
  if(state.job_id){const jobState=await api('/api/jobs/'+state.job_id);setHistoricalControls(jobState);if(jobState.result_sha256)await loadJob(state.job_id);else if(['queued','running'].includes(jobState.status))await watchHistoricalJob(state.job_id);}
}
async function restoreLaunchSession(){try{const capabilities=await api('/api/market/capabilities');launchProviderTimeframes=capabilities.history_timeframes;await loadLaunchSnapshots(true);await refreshLaunchList();const key=localStorage.getItem('openpine.launch');if(key)try{await openLaunch(key);}catch(error){$('launch-status').textContent=error.message;}}finally{launchHydrated=true;}}
function setHistoricalControls(state){job=state.job_id;$('historical-pause').disabled=!['queued','running'].includes(state.status);$('historical-resume').disabled=state.status!=='paused';$('historical-cancel').disabled=!['queued','running','paused'].includes(state.status);}
async function watchHistoricalJob(key){const generation=++historicalWatch;let state=await api('/api/jobs/'+key);while(generation===historicalWatch){setHistoricalControls(state);status(labels[state.phase]||labels[state.status]||state.status,state.completed,state.total);if(terminal.has(state.status))break;await pause(200);state=await api('/api/jobs/'+key);}if(generation!==historicalWatch)return;setHistoricalControls(state);if(state.result_sha256)await loadJob(key);else if(state.error)diagnostic(state);await history();return state;}
async function inspectSavedJob(key){if(savedLaunchConfiguration?.job_id===key)return;const data=await api(`/api/jobs/${key}/configuration`);savedLaunchConfiguration=data;setHistoricalControls(data.state);$('saved-configuration').hidden=false;$('saved-source').value=data.source?.source||'Исходник не был сохранён; доступен закреплённый IR.';$('saved-configuration-info').textContent=data.datasets.map(d=>`${d.symbol} · ${d.timeframe} · ${d.count} баров`).join('\n')+'\nПараметры: '+JSON.stringify(data.inputs)+'\n'+(data.request.evaluation_window?'Прогрев и оценка: '+JSON.stringify(data.request.evaluation_window):'Оценка на всей выбранной истории');for(const id of ['saved-copy','saved-repeat'])$(id).disabled=!data.source;}
async function copySavedJob(){const saved=savedLaunchConfiguration;if(!saved?.source)throw new Error('Сохранённый исходник недоступен');invalidateLaunch();const r=saved.request;$('launch-allow-gaps').checked=saved.launch_config?.gap_policy==='allow_with_metadata';await copyLaunchSource(saved.source,saved.inputs,r.snapshot_id,r.contexts||[],r.evaluation_window,r.broker_settings,r.timeout_seconds);$('launch-status').textContent='Копия конфигурации открыта. Подготовьте новый запуск.';}
for(const id of ['launch-warmup','launch-score','launch-end','launch-allow-gaps','launch-window-enabled'])$(id).addEventListener('change',()=>{invalidateLaunch();$('launch-window').hidden=!$('launch-window-enabled').checked;});
$('input-fields').addEventListener('change',invalidateLaunch);$('launch-select').onchange=()=>{};
$('launch-prepare').onclick=async()=>{lock(true);try{await currentPreparedLaunch();}catch(error){launchError(error);}finally{lock(false);}};
$('launch-cancel').onclick=async()=>{try{if(launchId)showLaunch(await api(`/api/launches/${launchId}/cancel`,{}));}catch(error){launchError(error);}};
$('launch-resume').onclick=async()=>{const key=launchId||$('launch-select').value;if(!key)return;lock(true);try{showLaunch(await api(`/api/launches/${key}/resume`,{}));await openLaunch(key);}catch(error){launchError(error);}finally{lock(false);}};
$('launch-open').onclick=async()=>{lock(true);try{await openLaunch($('launch-select').value);}catch(error){launchError(error);}finally{lock(false);}};$('launch-refresh').onclick=()=>refreshLaunchList().catch(launchError);$('launch-snapshot-more').onclick=()=>loadLaunchSnapshots().catch(launchError);
$('historical-pause').onclick=async()=>{try{if(job)setHistoricalControls(await api(`/api/jobs/${job}/pause`,{}));}catch(error){launchError(error);}};
$('historical-resume').onclick=async()=>{if(!job)return;lock(true);try{await api(`/api/jobs/${job}/resume`,{});await watchHistoricalJob(job);}catch(error){launchError(error);}finally{lock(false);}};
$('historical-cancel').onclick=async()=>{try{if(job)setHistoricalControls(await api(`/api/jobs/${job}/cancel`,{}));}catch(error){launchError(error);}};
$('saved-copy').onclick=()=>copySavedJob().catch(launchError);$('saved-repeat').onclick=async()=>{try{await copySavedJob();await run();}catch(error){launchError(error);}};
