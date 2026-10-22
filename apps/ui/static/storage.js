"use strict";
let storageStart=0,storageNext=0,storageTotal=0,storagePreview=null;
function storageElement(tag,text,className){const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(className)element.className=className;return element;}
function storageBytes(value){if(value<1024)return `${value} Б`;const scale=Math.min(3,Math.floor(Math.log(value)/Math.log(1024)));return `${(value/1024**scale).toLocaleString('ru',{maximumFractionDigits:1})} ${['Б','КиБ','МиБ','ГиБ'][scale]}`;}
function storageDate(value){return new Date(value).toISOString().replace('T',' ').slice(0,16);}
function coverageLabel(report){
  if(!report)return 'Нет отдельного отчёта';
  const missing=(report.counts?.missing_candle||0)+(report.counts?.not_fetched||0);
  if(missing)return `Пропуски: ${missing.toLocaleString('ru')}`;
  return report.requested_complete?'Проверенный диапазон полный':report.confirmed_complete?'Закрытая часть полная':'Требует проверки';
}
async function refreshStorage(){
  $('storage-refresh').disabled=true;
  try{
    const value=await api(`/api/market/storage?start=${storageStart}&limit=50`);storageNext=value.next;storageTotal=value.total;
    const store=value.storage,execution=value.execution;
    $('storage-summary').textContent=`${storageTotal.toLocaleString('ru')} снимков и блоков · файлы ${storageBytes(store.object_bytes)} · каталог ${storageBytes(store.catalog_bytes)} · активных сессий ${value.active_sessions}`;
    $('storage-execution').textContent=`Блоков журналов: ${store.raw_segments} исходных / ${store.event_segments} событий · незапечатанный хвост: ${store.raw_tail_records} / ${store.event_tail_records}. Пакетных запусков: ${execution.worker_launches}; учтённый CPU ${execution.child_cpu_seconds.toFixed(1)} с${execution.last_batch?`; выборка RSS ${storageBytes(execution.last_batch.peak_rss_sampled_bytes)}`:''}.`;
    const body=$('storage-datasets');body.replaceChildren();
    for(const row of value.datasets){
      const tr=document.createElement('tr'),name=storageElement('td');name.append(storageElement('strong',`${row.symbol} · ${intervalLabel(row.timeframe)}`),storageElement('small',row.kind==='snapshot'?'Снимок результата загрузки':'Блок данных'));
      name.title=`${row.snapshot_id}\n${row.provider||''}`;tr.append(name,storageElement('td',`${storageDate(row.first_ms)} → ${storageDate(row.last_ms)}`),storageElement('td',row.bars.toLocaleString('ru')),
        storageElement('td',coverageLabel(row.coverage)),storageElement('td',storageBytes(row.object_bytes)));body.append(tr);
    }
    if(!value.datasets.length){const row=document.createElement('tr'),cell=storageElement('td','Сохранённые диапазоны появятся после загрузки данных.');cell.colSpan=5;row.append(cell);body.append(row);}
    $('storage-page').textContent=`${Math.min(storageStart+1,storageTotal)}–${storageNext} из ${storageTotal}`;
    $('storage-previous').disabled=storageStart===0;$('storage-next').disabled=storageNext>=storageTotal;
    const feeds=$('storage-feeds');feeds.replaceChildren();
    for(const row of value.feeds){
      const card=storageElement('div',undefined,'context-card');card.append(storageElement('strong',`${row.instrument.symbol} · ${intervalLabel(row.timeframe)}`),
        storageElement('span',liveLabels[row.status]||row.status||'Ожидание','context-state'),
        storageElement('p',`Подписчиков: ${row.subscribers} · очередь до ${row.max_pending_records} · переподключений: ${row.reconnects}`));
      if(row.error)card.append(storageElement('p',row.error,'context-error'));feeds.append(card);
    }
    if(!value.feeds.length)feeds.append(storageElement('p','Живые источники пока не запускались.'));
  }catch(error){$('storage-summary').textContent=`Обзор недоступен: ${error.message}`;}
  finally{$('storage-refresh').disabled=false;}
}
function invalidateStoragePreview(){storagePreview=null;$('storage-apply').disabled=true;$('storage-candidates').hidden=true;}
async function previewStorage(){
  invalidateStoragePreview();$('storage-preview').disabled=true;
  try{
    const days=Number($('storage-retention-days').value);if(!Number.isInteger(days)||days<1||days>36500)throw new Error('Срок хранения: 1–36500 дней.');
    const policy={before_ms:Math.max(0,Date.now()-days*86400000),grace_seconds:3600};
    const value=await api('/api/market/maintenance/preview',policy);storagePreview={policy,value};
    const blockers=Object.entries(value.blockers).filter(([,count])=>count).map(([name,count])=>`${({jobs:'заданий',history:'загрузок',live_sessions:'live-сессий',workers:'процессов',physical_feeds:'соединений',replay_attempts:'процессов воспроизведения'})[name]||name}: ${count}`).join(', ');
    $('storage-maintenance').textContent=`К удалению: ${value.sessions.length} сессий, ${value.feeds.length} источников, ${value.objects.length} файлов (${storageBytes(value.reclaimable_object_bytes)}).${blockers?` Для применения остановите активную работу (${blockers}).`:''}${value.truncated?' Показана первая порция; после применения потребуется новый отчёт.':''}`;
    $('storage-candidates').hidden=!value.sessions.length;$('storage-candidates').textContent=value.sessions.slice(0,20).join('\n')+(value.sessions.length>20?'\n…':'');
    $('storage-apply').disabled=!value.apply_allowed||(!value.sessions.length&&!value.objects.length);
  }catch(error){$('storage-maintenance').textContent=error.message;}finally{$('storage-preview').disabled=false;}
}
$('storage-refresh').onclick=refreshStorage;
$('storage-previous').onclick=()=>{storageStart=Math.max(0,storageStart-50);refreshStorage();};
$('storage-next').onclick=()=>{storageStart=storageNext;refreshStorage();};
$('storage-retention-days').oninput=invalidateStoragePreview;$('storage-preview').onclick=previewStorage;
$('storage-rotate').onclick=async()=>{const button=$('storage-rotate');button.disabled=true;try{await api('/api/market/maintenance/rotate',{});$('storage-maintenance').textContent='Текущие блоки закрыты. Журналы продолжатся с прежних курсоров.';invalidateStoragePreview();await refreshStorage();}catch(error){$('storage-maintenance').textContent=error.message;}finally{button.disabled=false;}};
$('storage-apply').onclick=async()=>{
  if(!storagePreview)return;const {policy,value}=storagePreview;
  if(!confirm(`Удалить ${value.sessions.length} остановленных сессий и ${value.objects.length} файлов (${storageBytes(value.reclaimable_object_bytes)}) по показанному отчёту?`))return;
  $('storage-apply').disabled=true;
  try{const result=await api('/api/market/maintenance/apply',{...policy,preview_id:value.preview_id});$('storage-maintenance').textContent=`Удалено сессий: ${result.sessions_removed}; освобождено файлов: ${result.objects_removed} (${storageBytes(result.object_bytes_removed)}). Снимки свечей сохранены.`;invalidateStoragePreview();storageStart=0;await refreshStorage();}
  catch(error){$('storage-maintenance').textContent=error.message;invalidateStoragePreview();}
};
(async()=>{try{token=(await api('/api/config')).token;await refreshStorage();}catch(error){$('storage-summary').textContent=error.message;}})();
