"use strict";
const magnifierCard=document.createElement('section');magnifierCard.className='card';
magnifierCard.innerHTML=`<div class="experiment-body"><h2>Исполнение по младшим свечам</h2><label><input id="magnifier-enabled" type="checkbox"> Использовать Bar Magnifier</label><p>Для Pine v1–v4 — явная реконструкция OpenPine. Внутри младшей свечи используется модель из четырёх ценовых точек. Обычный расчёт Pine выполняется при закрытии основной свечи.</p><div id="magnifier-fields" hidden><label>Снимок младших свечей<input id="magnifier-snapshot" placeholder="ID сохранённого снимка" spellcheck="false"></label><label>Загрузить файл младших OHLCV<input id="magnifier-file" type="file" accept=".json,application/json"></label><div class="live-actions"><button id="magnifier-upload">Сохранить файл</button><button id="magnifier-inspect">Проверить снимок</button></div><label>Младший интервал в минутах<input id="magnifier-timeframe" type="number" min="1" max="1439" value="1"></label><button id="magnifier-fetch">Догрузить через провайдер</button><label>При неполном покрытии<select id="magnifier-missing"><option value="reject">Отклонить запуск</option><option value="parent_ohlc">Модель всей основной свечи с отметкой в отчёте</option></select></label><label>Перерасчёт после исполнения<select id="magnifier-fills"><option value="inherit">Из скрипта</option><option value="true">Включить явно</option><option value="false">Выключить явно</option></select></label><label>Исполнение при закрытии<select id="magnifier-close"><option value="inherit">Из скрипта</option><option value="true">Включить явно</option><option value="false">Выключить явно</option></select></label><label>Контрольная точка через младших свечей<input id="magnifier-checkpoint" type="number" min="0" max="1000000" value="1000"></label><p id="magnifier-status" role="status"></p></div><p id="magnifier-result" role="status"></p></div>`;
launchCard.after(magnifierCard);
function magnifierChanged(){invalidateLaunch();$('magnifier-fields').hidden=!$('magnifier-enabled').checked;if(typeof passportSettingsChanged==='function')passportSettingsChanged();}
for(const control of magnifierCard.querySelectorAll('input,select'))control.addEventListener('change',magnifierChanged);
function setMagnifierLock(on){for(const control of magnifierCard.querySelectorAll('input,select,button'))control.disabled=on;}
function getMagnifierConfiguration(){
  if(!$('magnifier-enabled').checked)return null;
  const id=$('magnifier-snapshot').value.trim(),every=Number($('magnifier-checkpoint').value);
  if(!/^[0-9a-f]{64}$/.test(id))throw new Error('Подготовьте снимок младших свечей');
  if(!Number.isInteger(every)||every<0||every>1000000)throw new Error('Некорректный интервал контрольных точек');
  const cycle={};for(const [id,name] of [['magnifier-fills','calc_on_order_fills'],['magnifier-close','process_orders_on_close']])if($(id).value!=='inherit')cycle[name]=$(id).value==='true';
  return {schema_version:'openpine.bar-magnifier.1',snapshot_id:id,missing:$('magnifier-missing').value,cycle_settings:Object.keys(cycle).length?cycle:null,checkpoint_every_children:every};
}
function setMagnifierConfiguration(value){
  $('magnifier-enabled').checked=Boolean(value);$('magnifier-fields').hidden=!value;
  $('magnifier-snapshot').value=value?.snapshot_id||'';$('magnifier-missing').value=value?.missing||'reject';$('magnifier-checkpoint').value=value?.checkpoint_every_children??1000;
  for(const [id,name] of [['magnifier-fills','calc_on_order_fills'],['magnifier-close','process_orders_on_close']])$(id).value=value?.cycle_settings?.[name]===undefined?'inherit':String(value.cycle_settings[name]);
}
function showMagnifierResult(value){$('magnifier-result').textContent=value?`Младшие свечи: ${value.covered_bars.toLocaleString('ru')} основных баров; fallback: ${value.fallback_bars.toLocaleString('ru')}. Модельных точек: ${value.model_points.toLocaleString('ru')}. ${value.external_equivalence?'':'Результат локальной модели OpenPine.'}`:'';}
async function magnifierInspect(){const data=await api('/api/datasets/'+$('magnifier-snapshot').value.trim());$('magnifier-status').textContent=`${data.identity.symbol} · ${data.identity.timeframe} мин · ${data.identity.count.toLocaleString('ru')} свечей. Полное соответствие основной истории проверяется при подготовке запуска.`;}
async function magnifierAction(action){lock(true);try{await action();invalidateLaunch();}catch(error){$('magnifier-status').textContent=error.message;}finally{lock(false);}}
$('magnifier-inspect').onclick=()=>magnifierAction(magnifierInspect);
$('magnifier-upload').onclick=()=>magnifierAction(async()=>{const file=$('magnifier-file').files[0];if(!file||file.size>16*1024*1024)throw new Error('Выберите JSON-файл до 16 MiB');const data=await api('/api/snapshots',{provider:'upload',raw:await file.text()});$('magnifier-snapshot').value=data.sha256;await magnifierInspect();});
$('magnifier-fetch').onclick=()=>magnifierAction(async()=>{
  if(!snapshot)throw new Error('Сначала подготовьте основные свечи');const data=snapshot.identity,tf=$('magnifier-timeframe').value;
  if(!data.symbol.startsWith('BINANCE:')||!/^\d+$/.test(data.timeframe)||!/^\d+$/.test(tf)||Number(tf)>=Number(data.timeframe)||Number(data.timeframe)%Number(tf))throw new Error('Провайдер поддерживает Binance Spot и кратный младший минутный интервал');
  const task=await api('/api/market/history',{schema_version:'openpine.history-request.1',instrument:{exchange:'binance',market:'spot',symbol:data.symbol.slice(8),price_kind:'trade'},timeframe:tf,start_ms:data.first_ms,end_ms:data.last_ms+Number(data.timeframe)*60000,gap_policy:$('magnifier-missing').value==='reject'?'fail':'allow_with_metadata',refresh:false,source:$('history-kind').value});
  const state=await waitAcquisition(task.task_id,s=>{$('magnifier-status').textContent=`Сохранено младших свечей: ${s.bars.toLocaleString('ru')} · ${s.status}`;});
  if(state.status!=='succeeded')throw new Error(state.error||'Не удалось подготовить младшие свечи');$('magnifier-snapshot').value=state.snapshot_id;await magnifierInspect();
});
