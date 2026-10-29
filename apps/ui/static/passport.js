"use strict";
let programPassport=null,programPassportRaw=null;
const savedPassportRequest=new LatestRequest();
let checkedLaunchBinding=null;
const passportModeNames={history:'История',replay:'Replay',observed:'Брокер по наблюдаемым ценам',intrabar:'Расчёт внутри свечи',cycle:'Фазы стратегии',magnifier:'Bar Magnifier'};
const passportStatuses={configuration_admitted:'Настройки допускаются · данные не проверены',inputs_validated:'Входы закреплённого запуска проверены',requires_bound_plan:'Нужна проверка на закреплённых данных',needs_configuration:'Нужны данные или настройки',rejected_configuration:'Сочетание не прошло проверку'};
const passportCard=document.createElement('section');passportCard.className='card storage-card passport-card';passportCard.setAttribute('aria-label','Паспорт программы');
passportCard.innerHTML='<div class="card-title"><div><h2>Паспорт программы</h2><p>Возможности и требования выбранного скрипта</p></div></div><div class="storage-body"><p id="passport-summary" role="status">Скомпилируйте программу и выберите основной инструмент и интервал.</p><div class="live-actions"><button id="passport-download" class="text-button" disabled>Скачать паспорт</button></div><div id="passport-modes"></div><p id="passport-evidence" class="muted"></p><details><summary>Профили, источники и диагностика</summary><pre id="passport-detail"></pre></details></div>';
launchCard.after(passportCard);
const passportMode=document.createElement('label');passportMode.textContent='Проверить настройки режима';
const passportSelect=document.createElement('select');passportSelect.id='passport-mode';passportSelect.innerHTML='<option value="history">История / Bar Magnifier</option><option value="replay">Replay / live по выбранным настройкам</option>';passportMode.append(passportSelect);$('passport-summary').before(passportMode);
function selectedPassportOptions(){
  const replay=$('passport-mode')?.value==='replay';
  const options={execution_mode:replay?'replay':'history'};
  if(typeof launchBroker!=='undefined'&&launchBroker)options.broker_settings=launchBroker;
  if(replay){
    if($('live-paper-orders')?.checked)options.broker_profile='openpine.observed-broker.1';
    const updates=$('live-calculation-updates')?.checked,fills=$('live-calculation-fills')?.checked,close=$('live-process-close')?.checked;
    if(fills||close){options.calculation_profile='openpine.strategy-cycle.1';options.cycle_settings={...(updates?{calc_on_every_tick:true}:{}),...(fills?{calc_on_order_fills:true}:{}),...(close?{process_orders_on_close:true}:{})};}
    else if(updates)options.calculation_profile='openpine.observed-calculation.1';
  }else if(typeof getMagnifierConfiguration==='function'){
    const lower=getMagnifierConfiguration();if(lower){options.execution_mode='magnifier';options.magnifier=lower;}
  }
  return options;
}
function passportSelectionSignature(){
  return JSON.stringify([typeof compiledRef==='undefined'?null:compiledRef,typeof getInputOverrides==='function'?getInputOverrides():{},
    typeof primaryPlanningContext==='function'?primaryPlanningContext():null,selectedPassportOptions(),
    typeof snapshot==='undefined'?null:snapshot?.sha256,typeof launchChoices==='undefined'?[]:[...launchChoices],
    ...['live-session','launch-select','launch-allow-gaps','launch-window-enabled','launch-warmup','launch-score','launch-end','timeout'].map(id=>[$(id)?.value,$(id)?.checked])]);
}
function passportSettingsChanged(){clearProgramPassport();if(typeof refreshLaunchRequirements==='function')refreshLaunchRequirements().catch(error=>{$('passport-summary').textContent=error.message;});}
passportSelect.onchange=passportSettingsChanged;
for(const id of ['live-session','live-paper-orders','live-calculation-updates','live-calculation-fills','live-process-close','live-symbol','live-timeframe'])$(id)?.addEventListener('change',passportSettingsChanged);
const passportSaved=document.createElement('button');passportSaved.textContent='Из сохранённой подготовки';passportSaved.className='text-button';passportSaved.id='passport-saved';$('passport-download').after(passportSaved);
function clearProgramPassport(){requirementsRequest.invalidate();savedPassportRequest.invalidate();checkedLaunchBinding=null;programPassport=null;programPassportRaw=null;$('passport-download').disabled=true;$('passport-summary').textContent='Программа или параметры изменены. Ожидается новый паспорт.';$('passport-modes').replaceChildren();$('passport-evidence').textContent='';$('passport-detail').textContent='';}
function renderProgramPassport(report,raw){
  if(!report){clearProgramPassport();return;}
  programPassport=report;programPassportRaw=typeof raw==='string'?raw:null;$('passport-download').disabled=!programPassportRaw;
  $('passport-summary').textContent=report.status==='rejected'?report.issues.map(i=>i.diagnostic.message).join('\n'):
    `Pine v${report.program.language_version} · операций: ${report.features.operations.length} · дополнительных контекстов: ${report.requirements.contexts.length}. Данные и свободные ресурсы пока не проверены.`;
  if(report.saved_preparation){const saved=report.saved_preparation;$('passport-summary').textContent=`Сохранённая подготовка ${saved.launch_id.slice(0,8)} · Pine v${report.program?.language_version||'?'} · ${saved.data_validation_recorded?(saved.plan_compatible?'проверка данных сохранена, план совместим':'сохранённый план требует перекомпиляции'):'подготовка данных ещё не закончена'}. Наличие файлов и свободные ресурсы заново не проверялись.`;}
  if(report.instrument_metadata_required?.length)$('passport-summary').textContent+=` Требуются метаданные инструмента: ${report.instrument_metadata_required.join(', ')}.`;
  if(report.selection)$('passport-summary').textContent+=` Выбранный режим: ${passportModeNames[report.selection.mode]||report.selection.mode} — ${passportStatuses[report.selection.status]||report.selection.status}.`;
  if(report.validation){const v=report.validation;$('passport-summary').textContent=v.status==='validated'?'Входы закреплённого запуска проверены сейчас: целостность, покрытие, метаданные и конфигурация драйвера. Ресурсы выделяются при допуске задания.':v.issues.map(i=>i.diagnostic.message).join('\n');}
  if(report.execution){const e=report.execution;$('passport-summary').textContent=e.whole_program_execution_verified?`Задание ${e.job_id.slice(0,8)} завершено: программа, план и сохранённый результат совпадают. Проверено ${e.completed} баров.`:`Исполнение: ${e.status} · ${e.completed||0}/${e.total||0} баров. Полное успешное исполнение пока не подтверждено.`;}
  if(report.replay_validation){const v=report.replay_validation,e=report.replay_execution;$('passport-summary').textContent=v.status==='blocked'?v.issues.map(i=>i.diagnostic.message).join('\n'):`Запись Replay: проверено событий ${v.events_verified??'—'}, сохранённый префикс исполнения: ${e?.applied_events||0}. Проверка ленты не исполняет события; полное завершение программы здесь не заявляется.`;}
  const body=$('passport-modes');body.replaceChildren();
  for(const row of report.modes||[]){const p=document.createElement('p');const executed=report.execution?.whole_program_execution_verified&&row.mode===report.selection?.mode,validated=report.validation?.status==='validated'&&row.mode===report.validation.mode;const state=executed?'Исполнено в этом запуске':validated?'Входы закреплённого запуска проверены':passportStatuses[row.status]||row.status;p.textContent=`${passportModeNames[row.mode]||row.mode}: ${state}`;
    for(const item of executed||validated?[]:row.issues){const diagnostic=item.diagnostic||item,span=diagnostic.span;p.append(document.createTextNode(` — ${diagnostic.message}${span?` (строка ${span.line})`:''}`));}body.append(p);}
  const evidence=report.evidence;
  $('passport-evidence').textContent=evidence?`Записанные проверки связанных операций: ${evidence.recorded_corpora.length} наборов. `+(report.execution?.whole_program_execution_verified?'Исполнение выбранного запуска подтверждено его отдельной квитанцией.':'Эти проверки не подтверждают исполнение загруженной программы.')+(evidence.request_environment?.legacy_counter_evidence==='reviewed_reconstruction_v1_v3'?' Счётчик запроса v1–v3 использует обозначенную реконструкцию ранних правил.':''):'';
  $('passport-detail').textContent=JSON.stringify(report,null,2);
}
$('passport-download').onclick=()=>{if(!programPassportRaw)return;const blob=new Blob([programPassportRaw],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='openpine-program-passport.json';link.click();URL.revokeObjectURL(url);};
$('source').addEventListener('input',clearProgramPassport);
$('launch-select').addEventListener('change',clearProgramPassport);
passportSaved.onclick=async()=>{const id=$('launch-select').value;if(!id){$('passport-summary').textContent='Выберите сохранённую подготовку в списке.';return;}requirementsRequest.invalidate();
  try{await savedPassportRequest.run(signal=>api(`/api/launches/${id}/passport`,undefined,{signal}),data=>{if($('launch-select').value===id)renderProgramPassport(data.passport,data.passport_json);});}
  catch(error){$('passport-summary').textContent=error.message;}
};
for(const [id,label,action] of [['passport-check','Проверить входы','validate'],['passport-execution','Результат запуска','execution']]){
  const button=document.createElement('button');button.id=id;button.textContent=label;button.className='text-button';passportSaved.after(button);
  button.onclick=()=>{const key=$('launch-select').value||(typeof launchId==='undefined'?null:launchId);if(!key){$('passport-summary').textContent='Сначала подготовьте запуск.';return;}inspectBoundLaunch(key,action).catch(error=>{$('passport-summary').textContent=error.message;});};
}
async function inspectBoundLaunch(key,action='validate'){
  requirementsRequest.invalidate();checkedLaunchBinding=null;const signature=passportSelectionSignature();
  return savedPassportRequest.run(async signal=>{
    const saved=await api(`/api/launches/${key}/passport`,undefined,{signal});
    if(action==='execution')return api(`/api/launches/${key}/execution`,undefined,{signal});
    return api(`/api/launches/${key}/validate`,{binding_sha256:saved.passport.binding.sha256},{signal});
  },data=>{
    if(signature!==passportSelectionSignature())return;
    renderProgramPassport(data.passport,data.passport_json);
    if(data.validation){if(data.validation.status!=='validated')throw new Error(data.validation.issues.map(i=>i.diagnostic.message).join('\n'));checkedLaunchBinding={key,sha256:data.validation.binding.sha256,signature};}
    return data;
  });
}
function checkedBindingFor(key){if(!checkedLaunchBinding||checkedLaunchBinding.key!==key||checkedLaunchBinding.signature!==passportSelectionSignature())throw new Error('Настройки изменены. Проверьте входы заново.');return checkedLaunchBinding.sha256;}
const passportReplay=document.createElement('button');passportReplay.textContent='Проверить запись наблюдения';passportReplay.id='passport-replay';passportReplay.className='text-button';passportSaved.after(passportReplay);
passportReplay.onclick=async()=>{
  const key=typeof liveState==='undefined'?null:liveState?.replay?.replay_id;
  if(!key){$('passport-summary').textContent='Выберите сохранённое наблюдение с записью событий.';return;}
  requirementsRequest.invalidate();checkedLaunchBinding=null;const signature=passportSelectionSignature();
  try{await savedPassportRequest.run(async signal=>{
    const selected=await api(`/api/replays/${key}/passport`,undefined,{signal});
    return api(`/api/replays/${key}/validate`,{binding_sha256:selected.passport.replay_binding.sha256},{signal});
  },data=>{if(signature===passportSelectionSignature())renderProgramPassport(data.passport,data.passport_json);});}
  catch(error){$('passport-summary').textContent=error.message;}
};
