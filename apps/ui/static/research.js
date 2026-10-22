"use strict";
const researchCard=document.createElement('section');researchCard.className='card';
researchCard.innerHTML=`<div class="experiment-body"><h2>Отчёт исследования</h2><p>Условия, параметры, результаты и проверка на отложенных данных.</p><div class="live-controls"><label>Тип<select id="research-kind"><option value="search">Подбор параметров</option><option value="experiment">Серия экспериментов</option><option value="walkforward">Walk-forward</option></select></label><label>Исследование<select id="research-id"></select></label><button id="research-refresh">Обновить список</button><button id="research-show">Показать отчёт</button><button id="research-download" disabled>Скачать JSON</button></div><p id="research-status" role="status"></p><div id="research-summary"></div><details><summary>Полные условия и результаты</summary><pre id="research-json"></pre></details><details><summary>Удаление истории</summary><p>Удаляются выбранное исследование и его задания. Общие данные сохраняются. Освобождение файлов выполняет последующая сборка мусора.</p><button id="research-preview">Показать состав удаления</button><button id="research-delete" class="danger" disabled>Удалить по отчёту</button><pre id="research-deletion"></pre></details></div>`;
const researchFooter=document.querySelector('main footer');if(researchFooter)researchFooter.before(researchCard);else document.querySelector('main').append(researchCard);
let researchReport=null,researchReportRaw=null,researchPreview=null,researchEpoch=0;
const researchCollections={search:'searches',experiment:'experiments',walkforward:'walkforwards'};
function researchChoice(kind,id){return $('research-kind').value===kind&&$('research-id').value===id;}
function researchCurrent(epoch,kind,id){return epoch===researchEpoch&&researchChoice(kind,id);}
function researchClear(){
  researchEpoch++;researchReport=null;researchReportRaw=null;researchPreview=null;
  $('research-download').disabled=true;$('research-delete').disabled=true;
  $('research-deletion').textContent='';$('research-json').textContent='';
  $('research-summary').replaceChildren();$('research-status').textContent='';
  const empty=!$('research-id').value;$('research-show').disabled=empty;$('research-preview').disabled=empty;
}
function researchFailure(error){$('research-status').textContent=error.message;}
async function refreshResearch(){
  researchClear();const epoch=researchEpoch,kind=$('research-kind').value;
  $('research-show').disabled=true;$('research-preview').disabled=true;
  try{
    const rows=await api('/api/'+researchCollections[kind]);
    if(epoch!==researchEpoch||$('research-kind').value!==kind)return;
    $('research-id').replaceChildren();
    for(const row of rows){const option=document.createElement('option');option.value=row.search_id||row.experiment_id||row.walkforward_id;option.textContent=`${row.name} · ${row.status} · ${option.value.slice(0,8)}`;$('research-id').append(option);}
  }finally{
    if(epoch===researchEpoch){const empty=!$('research-id').value;$('research-show').disabled=empty;$('research-preview').disabled=empty;}
  }
}
function renderResearchReport(value,raw){
  researchReport=value;researchReportRaw=raw;$('research-json').textContent=raw;
  $('research-status').textContent=`${value.report.manifest.name} · ${value.report.desired==='cancelled'?'Отменено':value.report.complete?'Завершено':'Промежуточный отчёт'} · контрольная сумма ${value.sha256}`;
  if(value.report.stability){const info=document.createElement('p'),s=value.report.stability;info.textContent=`Подходящих проверок: ${s.eligible_holdouts}; оценка от ${searchScore(s.score_min)} до ${searchScore(s.score_max)}, медиана ${searchScore(s.score_median)}; смен параметров: ${s.winner_changes}. Капитал между окнами не переносится.`;$('research-summary').append(info);}
  if(value.report.folds){
    for(const fold of value.report.folds){const info=document.createElement('p'),status=fold.receipt?.status;info.textContent=`Окно ${fold.ordinal+1}: ${({succeeded:'Проверка пройдена',holdout_failed:'Проверка не пройдена',no_eligible_candidate:'Нет подходящего варианта',cancelled:'Отменено'})[status]||'Ожидает результата'}`;$('research-summary').append(info);}
  }
  for(const variant of value.report.variants){
    const details=document.createElement('details'),title=document.createElement('summary'),pre=document.createElement('pre');
    title.textContent=`${variant.fold_ordinal===undefined?'':'Окно '+(variant.fold_ordinal+1)+' · '}${({selection:'Подбор',holdout:'Проверка',experiment:'Эксперимент'})[variant.phase]||'Вариант'} ${variant.candidate_ordinal+1} · ${({succeeded:'Готово',failed:'Ошибка',cancelled:'Отменено',planned:'Ожидает',skipped:'Пропущено',timed_out:'Истёк лимит'})[variant.status]||variant.status}`;
    pre.textContent=JSON.stringify({parameters:variant.inputs,metrics:variant.descriptive_metrics,selection:variant.plan.evaluation_window,broker:variant.plan.simulation_policy},null,2);
    details.append(title);const metrics=variant.descriptive_metrics;
    if(metrics?.status==='available'){
      const table=document.createElement('table'),labels={initial_capital:'Начальный капитал',ending_equity:'Итоговый капитал',net_profit:'Изменение капитала',return_percent:'Доходность, %',max_drawdown:'Максимальная просадка',max_drawdown_percent:'Максимальная просадка, %',closed_trades:'Закрытых сделок',winning_trades:'Прибыльных сделок',win_rate_percent:'Доля прибыльных сделок, %',profit_factor:'Profit factor',commission:'Комиссии'};
      for(const [key,label]of Object.entries(labels)){const tr=document.createElement('tr'),name=document.createElement('td'),cell=document.createElement('td');name.textContent=label;cell.textContent=metrics.values[key]===null?'Не определено':searchScore(metrics.values[key]);cell.title=metrics.values[key]??metrics.undefined?.[key]??'';tr.append(name,cell);table.append(tr);}
      details.append(table);
    }
    details.append(pre);$('research-summary').append(details);
  }
  $('research-download').disabled=false;
}
$('research-refresh').onclick=()=>refreshResearch().catch(researchFailure);
$('research-kind').onchange=()=>refreshResearch().catch(researchFailure);
$('research-id').onchange=researchClear;
$('research-show').onclick=async()=>{
  researchClear();const epoch=researchEpoch,kind=$('research-kind').value,id=$('research-id').value;
  try{
    if(!id)throw new Error('Выберите сохранённое исследование');
    $('research-status').textContent='Чтение результатов и расчёт отчёта…';
    const response=await fetch(`/api/${researchCollections[kind]}/${id}/report`),raw=await response.text();
    if(!researchCurrent(epoch,kind,id))return;
    const value=JSON.parse(raw);
    if(!response.ok)throw new Error(value.diagnostics?.map(row=>row.message).join('\n')||`HTTP ${response.status}`);
    renderResearchReport(value,raw);
  }catch(error){if(researchCurrent(epoch,kind,id))researchFailure(error);}
};
$('research-download').onclick=()=>{
  if(!researchReport)return;
  if(!researchChoice(researchReport.report.kind,researchReport.report.id)){researchClear();return;}
  const url=URL.createObjectURL(new Blob([researchReportRaw],{type:'application/json'})),link=document.createElement('a');
  link.href=url;link.download=`openpine-${researchReport.report.id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('research-preview').onclick=async()=>{
  const epoch=++researchEpoch,kind=$('research-kind').value,id=$('research-id').value;
  researchPreview=null;$('research-delete').disabled=true;$('research-deletion').textContent='';
  try{
    if(!id)throw new Error('Выберите исследование');
    const value=await api(`/api/research/${kind}/${id}/preview`,{});
    if(!researchCurrent(epoch,kind,id))return;
    researchPreview={kind,id,value};
    $('research-deletion').textContent=`Заданий: ${value.jobs.length}; серий: ${value.experiments.length}; после GC возможно освободить ${value.reclaimable_object_bytes} байт.\n`+JSON.stringify(value,null,2);
    $('research-delete').disabled=false;
  }catch(error){if(researchCurrent(epoch,kind,id))researchFailure(error);}
};
$('research-delete').onclick=async()=>{
  if(!researchPreview)return;
  const selected=researchPreview,{kind,id,value}=selected;
  if(!researchChoice(kind,id)){researchClear();researchFailure(new Error('Выбор изменился. Получите новый состав удаления.'));return;}
  const name=$('research-id').selectedOptions[0]?.textContent||id;
  if(!confirm(`Удалить исследование «${name}» (${id.slice(0,8)}) и ${value.jobs.length} заданий?`))return;
  if(!researchChoice(kind,id)||researchPreview!==selected){researchClear();return;}
  const epoch=++researchEpoch;researchPreview=null;$('research-delete').disabled=true;
  try{
    await api(`/api/research/${kind}/${id}/delete`,{preview_id:value.preview_id});
    if(!researchCurrent(epoch,kind,id))return;
    const refreshing=refreshResearch(),refreshEpoch=researchEpoch;await refreshing;
    if(refreshEpoch===researchEpoch)$('research-status').textContent='История удалена. Ненужные файлы доступны для последующей сборки мусора.';
  }catch(error){if(researchCurrent(epoch,kind,id))researchFailure(error);}
};
refreshResearch().catch(researchFailure);
