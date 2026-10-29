"use strict";
let programPassport=null,programPassportRaw=null;
const savedPassportRequest=new LatestRequest();
const passportModeNames={history:'История',replay:'Replay',observed:'Брокер по наблюдаемым ценам',intrabar:'Расчёт внутри свечи',cycle:'Фазы стратегии',magnifier:'Bar Magnifier'};
const passportStatuses={configuration_admitted:'Настройки допускаются · данные не проверены',requires_bound_plan:'Нужна проверка на закреплённых данных',needs_configuration:'Нужны данные или настройки',rejected_configuration:'Сочетание не прошло проверку'};
const passportCard=document.createElement('section');passportCard.className='card storage-card passport-card';passportCard.setAttribute('aria-label','Паспорт программы');
passportCard.innerHTML='<div class="card-title"><div><h2>Паспорт программы</h2><p>Возможности и требования выбранного скрипта</p></div></div><div class="storage-body"><p id="passport-summary" role="status">Скомпилируйте программу и выберите основной инструмент и интервал.</p><div class="live-actions"><button id="passport-download" class="text-button" disabled>Скачать паспорт</button></div><div id="passport-modes"></div><p id="passport-evidence" class="muted"></p><details><summary>Профили, источники и диагностика</summary><pre id="passport-detail"></pre></details></div>';
launchCard.after(passportCard);
const passportSaved=document.createElement('button');passportSaved.textContent='Из сохранённой подготовки';passportSaved.className='text-button';passportSaved.id='passport-saved';$('passport-download').after(passportSaved);
function clearProgramPassport(){requirementsRequest.invalidate();savedPassportRequest.invalidate();programPassport=null;programPassportRaw=null;$('passport-download').disabled=true;$('passport-summary').textContent='Программа или параметры изменены. Ожидается новый паспорт.';$('passport-modes').replaceChildren();$('passport-evidence').textContent='';$('passport-detail').textContent='';}
function renderProgramPassport(report,raw){
  if(!report){clearProgramPassport();return;}
  programPassport=report;programPassportRaw=typeof raw==='string'?raw:null;$('passport-download').disabled=!programPassportRaw;
  $('passport-summary').textContent=report.status==='rejected'?report.issues.map(i=>i.diagnostic.message).join('\n'):
    `Pine v${report.program.language_version} · операций: ${report.features.operations.length} · дополнительных контекстов: ${report.requirements.contexts.length}. Данные и свободные ресурсы пока не проверены.`;
  if(report.saved_preparation){const saved=report.saved_preparation;$('passport-summary').textContent=`Сохранённая подготовка ${saved.launch_id.slice(0,8)} · Pine v${report.program?.language_version||'?'} · ${saved.data_validation_recorded?(saved.plan_compatible?'проверка данных сохранена, план совместим':'сохранённый план требует перекомпиляции'):'подготовка данных ещё не закончена'}. Наличие файлов и свободные ресурсы заново не проверялись.`;}
  if(report.instrument_metadata_required?.length)$('passport-summary').textContent+=` Требуются метаданные инструмента: ${report.instrument_metadata_required.join(', ')}.`;
  const body=$('passport-modes');body.replaceChildren();
  for(const row of report.modes||[]){const p=document.createElement('p');p.textContent=`${passportModeNames[row.mode]||row.mode}: ${passportStatuses[row.status]||row.status}`;
    for(const item of row.issues){const diagnostic=item.diagnostic||item,span=diagnostic.span;p.append(document.createTextNode(` — ${diagnostic.message}${span?` (строка ${span.line})`:''}`));}body.append(p);}
  const evidence=report.evidence;
  $('passport-evidence').textContent=evidence?`Есть записанные проверки связанных операций: ${evidence.recorded_corpora.length} наборов. Это не результат исполнения загруженной программы.`+(evidence.request_environment?.legacy_counter_evidence==='reviewed_reconstruction_v1_v3'?' Счётчик запроса v1–v3 использует обозначенную реконструкцию ранних правил.':''):'';
  $('passport-detail').textContent=JSON.stringify(report,null,2);
}
$('passport-download').onclick=()=>{if(!programPassportRaw)return;const blob=new Blob([programPassportRaw],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='openpine-program-passport.json';link.click();URL.revokeObjectURL(url);};
$('source').addEventListener('input',clearProgramPassport);
$('launch-select').addEventListener('change',clearProgramPassport);
passportSaved.onclick=async()=>{const id=$('launch-select').value;if(!id){$('passport-summary').textContent='Выберите сохранённую подготовку в списке.';return;}requirementsRequest.invalidate();
  try{await savedPassportRequest.run(signal=>api(`/api/launches/${id}/passport`,undefined,{signal}),data=>{if($('launch-select').value===id)renderProgramPassport(data.passport,data.passport_json);});}
  catch(error){$('passport-summary').textContent=error.message;}
};
