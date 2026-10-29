'use strict';
const tracePanel=document.createElement('section');tracePanel.className='card storage-card trace-card';tracePanel.setAttribute('aria-label','Разбор исполнения');
tracePanel.innerHTML=`<div class="card-title"><div><h2>Разбор исполнения</h2><p>Строка Pine → значение → условие → заявка</p></div></div><div class="storage-body">
<p class="muted">Отдельный диагностический запуск использует закреплённые входы. Индексы баров начинаются с 0; прогрев выполняется с начала входа.</p>
<div class="live-actions"><label>Источник<select id="trace-mode"><option value="history">Сохранённая подготовка</option><option value="replay">Выбранная запись наблюдения</option></select></label>
<label>С бара<input id="trace-start" type="number" min="0" value="0"></label><label>До бара, не включая<input id="trace-end" type="number" min="1" value="1"></label>
<label>Строки, через запятую<input id="trace-lines" placeholder="Все строки"></label><button id="trace-run" class="primary">Разобрать исполнение</button></div>
<div class="live-actions"><label>Сохранённая трасса<input id="trace-id" maxlength="32"></label><button id="trace-open" class="secondary">Открыть трассу</button><button id="trace-pause" class="text-button">Пауза трассы</button><button id="trace-resume" class="text-button">Продолжить трассу</button><button id="trace-cancel" class="text-button">Отменить трассу</button></div>
<p id="trace-status" role="status">Выберите подготовку или запись и диапазон.</p><p id="trace-binding" class="muted"></p>
<div class="live-actions"><button id="trace-previous" class="text-button">← События</button><button id="trace-next" class="text-button">События →</button><button id="trace-download" class="text-button">Скачать страницу трассы</button></div></div>
<div class="table-scroll trace-records"><table><thead><tr><th>Событие / бар</th><th>Контекст / фаза</th><th>Строка / операция</th><th>Значение / решение</th></tr></thead><tbody id="trace-events"></tbody></table></div>
<details open><summary>Закреплённый исходник и выбранное событие</summary><pre id="trace-source">Исходник появится после выбора трассы.</pre><pre id="trace-event"></pre></details>`;
document.querySelector('.values-card').after(tracePanel);
let traceGeneration=0,traceSelected=null,tracePage=0,traceNext=0,traceSource=null,tracePoll=null;
const traceLabels={queued:'В очереди',running:'Выполняется',succeeded:'Завершён',completed:'Завершён',paused:'На паузе',cancelled:'Отменён',failed:'Ошибка',timed_out:'Истёк лимит времени',waiting_resources:'Ожидает ресурсов'};
function traceValue(value){if(!value)return '';if(value.type==='na')return 'na';if(value.type==='void')return 'Вызов';if(value.type==='array')return `array<${value.element}> · ${value.length} элементов · ${JSON.stringify(value.preview)}`;return `${value.type}: ${value.value===undefined?JSON.stringify(value):String(value.value)}`;}
function traceSignature(){return JSON.stringify([$ ('trace-mode').value,$('launch-select').value,$('live-session').value,$('trace-start').value,$('trace-end').value,$('trace-lines').value]);}
function traceClearEvent(message='Исходник появится после выбора трассы.'){$('trace-source').textContent=message;$('trace-event').textContent='';}
function traceInvalidate(){traceGeneration++;clearTimeout(tracePoll);traceSelected=null;traceSource=null;$('trace-events').replaceChildren();$('trace-binding').textContent='';traceClearEvent();$('trace-status').textContent='Выбор изменён. Выполните новый разбор или откройте сохранённую трассу.';}
for(const id of ['trace-mode','trace-start','trace-end','trace-lines','launch-select','live-session'])$(id).addEventListener('change',traceInvalidate);
function traceShowEvent(event){
  $('trace-event').textContent=JSON.stringify(event,null,2);
  if(!traceSource?.source){$('trace-source').textContent='Исходник не сохранён; координаты и site ID доступны в событии.';return;}
  const line=event.span?.line;$('trace-source').replaceChildren();
  traceSource.source.split('\n').forEach((text,index)=>{const row=document.createElement(line===index+1?'mark':'span');row.textContent=`${index+1}: ${text}\n`;$('trace-source').append(row);});
}
async function traceLoad(id,page=0,generation=++traceGeneration){
  const data=await api(`/api/traces/${id}/events?start=${page}&limit=100`);
  if(generation!==traceGeneration)return;
  const status=data.status;traceSelected=id;tracePage=page;traceNext=data.next;$('trace-id').value=id;localStorage.setItem('openpine.trace-id',id);
  $('trace-status').textContent=`${traceLabels[status.job.status]||status.job.status} · записано ${data.total} событий`+(status.trace?.truncated?` · запись ограничена: ${status.trace.reason}`:'')+(status.warning?` · ${status.warning.status}`:'');
  $('trace-binding').textContent=`Трасса ${id} · источник ${status.configuration.parent_launch_id||status.configuration.parent_replay_id||status.configuration.parent_job_id} · бары ${status.configuration.options.start_bar}–${status.configuration.options.end_bar-1}`;
  const body=$('trace-events');body.replaceChildren();traceSource=null;traceClearEvent('Загрузка закреплённого исходника…');
  for(const event of data.events){const row=document.createElement('tr');
    const fields=[`${event.sequence} · бар ${event.primary_bar}`,`${event.context_id||'—'} · ${event.pass_phase||event.phase} · ветка ${event.branch_id}`,
      `${event.span?.line||'—'} · ${event.operation||event.operator||event.kind}`,event.broker_event?JSON.stringify(event.broker_event):event.value?traceValue(event.value):JSON.stringify(event.error??event.kind)];
    for(const field of fields){const cell=document.createElement('td');cell.textContent=field;row.append(cell);}row.tabIndex=0;row.onclick=()=>traceShowEvent(event);row.onkeydown=e=>{if(e.key==='Enter')traceShowEvent(event);};body.append(row);}
  $('trace-previous').disabled=page===0;$('trace-next').disabled=data.next>=data.total;
  const source=await api(`/api/traces/${id}/source`);if(generation!==traceGeneration)return;traceSource=source;
  if(data.events.length)traceShowEvent(data.events[0]);
  else {traceClearEvent('В выбранном диапазоне событий нет.');if(source?.source)$('trace-source').textContent=source.source;}
  if(['queued','running'].includes(status.job.status))tracePoll=setTimeout(()=>traceLoad(id,page,generation).catch(e=>{if(generation===traceGeneration)$('trace-status').textContent=e.message;}),700);
}
async function traceView(id,page=0){const generation=++traceGeneration;clearTimeout(tracePoll);try{await traceLoad(id,page,generation);}catch(e){if(generation===traceGeneration)$('trace-status').textContent=e.message;}}
$('trace-run').onclick=async()=>{
  const generation=++traceGeneration,signature=traceSignature();clearTimeout(tracePoll);$('trace-run').disabled=true;
  try{
    const replay=$('trace-mode').value==='replay',id=replay?liveState?.replay?.replay_id:$('launch-select').value;
    if(!id)throw new Error('Сначала выберите сохранённую подготовку или наблюдение.');
    const report=(await api(`/api/${replay?'replays':'launches'}/${id}/passport`)).passport;
    if(generation!==traceGeneration||signature!==traceSignature())return;
    const key=crypto.randomUUID().replaceAll('-','');$('trace-id').value=key;localStorage.setItem('openpine.trace-id',key);
    const lines=$('trace-lines').value.trim()?$('trace-lines').value.split(',').map(x=>Number(x.trim())):[];
    await api('/api/traces',{request_id:key,[replay?'replay_id':'launch_id']:id,binding_sha256:report[replay?'replay_binding':'binding'].sha256,
      options:{start_bar:Number($('trace-start').value),end_bar:Number($('trace-end').value),lines}});
    if(generation===traceGeneration&&signature===traceSignature())await traceLoad(key,0,generation);
  }catch(error){if(generation===traceGeneration)$('trace-status').textContent=error.message;}finally{$('trace-run').disabled=false;}
};
$('trace-id').value=localStorage.getItem('openpine.trace-id')||'';
$('trace-open').onclick=()=>traceView($('trace-id').value.trim());
for(const action of ['pause','resume','cancel'])$('trace-'+action).onclick=async()=>{const generation=++traceGeneration;clearTimeout(tracePoll);try{const id=traceSelected||$('trace-id').value.trim();await api(`/api/traces/${id}/${action}`,{});if(generation===traceGeneration)await traceLoad(id,tracePage,generation);}catch(e){if(generation===traceGeneration)$('trace-status').textContent=e.message;}};
$('trace-previous').onclick=()=>traceSelected&&traceView(traceSelected,Math.max(0,tracePage-100));
$('trace-next').onclick=()=>traceSelected&&traceView(traceSelected,traceNext);
$('trace-download').onclick=()=>traceSelected&&downloadRaw(`/api/traces/${traceSelected}/events?start=${tracePage}&limit=100`,'openpine-trace-page.json');
for(const id of ['price-chart','output-chart'])$(id).addEventListener('dblclick',event=>{
  if(!chartData?.data?.bars?.length)return;const rect=event.currentTarget.getBoundingClientRect();
  const index=Math.max(view[0],Math.min(view[1]-1,Math.round(view[0]+(event.clientX-rect.left-12)/Math.max(1,rect.width-97)*Math.max(1,view[1]-view[0]-1))));
  $('trace-start').value=windowStart+index;$('trace-end').value=windowStart+index+1;traceInvalidate();tracePanel.scrollIntoView({block:'start'});
});
