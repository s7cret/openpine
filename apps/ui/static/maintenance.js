"use strict";
let maintenanceStart=0,maintenanceNext=0,maintenanceTotal=0,maintenancePreview=null;
const maintenanceLabels={compatible:'Совместим',recompile_required:'Нужна перекомпиляция',blocked:'Нужна проверка',compile_on_run:'Компиляция при запуске',new_session_required:'Нужна новая сессия',new_research_required:'Нужно новое исследование',new_preparation_required:'Нужна новая подготовка',prepare_required:'Подготовка не завершена'};
const maintenanceActions={resume:'Продолжить с сохранённой точки',wait:'Ожидать выполнения',view_result:'Открыть сохранённый результат',inspect_failure:'Проверить причину остановки',new_run:'Подготовить новый запуск',provide_source:'Нужен исходный файл',compile:'Компилировать при запуске',continue_replay:'Продолжить через воспроизведение',new_session:'Создать новую live-сессию',new_replay:'Создать новое воспроизведение',inspect_replay:'Проверить связанное воспроизведение',new_research:'Создать новое исследование',inspect_research:'Открыть исследование',prepare:'Выполнить подготовку',inspect_preparation:'Открыть подготовку',inspect_record:'Проверить сохранённую запись'};
function clearRestart(){maintenancePreview=null;$('maintenance-apply').disabled=true;$('maintenance-preview').textContent='Сначала выберите остановленный запуск и получите отчёт.';}
async function refreshMaintenance(){
  const button=$('maintenance-refresh');button.disabled=true;clearRestart();
  try{
    const report=await api('/api/maintenance/doctor');
    $('maintenance-status').textContent=`Каталог ${report.catalog?.stored??'недоступен'} / ${report.catalog?.supported??'—'} · ${report.status==='ok'?'предварительная проверка пройдена':report.issues.map(i=>i.code).join(', ')}. Проверка не изменяет хранилище.`;
    const value=await api(`/api/maintenance/compatibility?kind=${$('maintenance-kind').value}&start=${maintenanceStart}&limit=20`);
    maintenanceNext=value.next||0;maintenanceTotal=value.total||0;const body=$('maintenance-rows');body.replaceChildren();
    for(const row of value.items){
      const tr=document.createElement('tr');
      const text=[row.id.slice(0,14),maintenanceLabels[row.status]||row.status,`${row.code}${row.error_code?' · '+row.error_code:''}`,row.checkpoint?`${row.checkpoint.completed} баров`:'—'];
      for(const item of text){const td=document.createElement('td');td.textContent=item;tr.append(td);}
      const td=document.createElement('td');td.textContent=maintenanceActions[row.action]||row.action;
      if(row.kind==='jobs'&&row.source_available&&!['queued','running'].includes(row.job_status)){
        const prepare=document.createElement('button');prepare.className='text-button';prepare.textContent='Отчёт нового запуска';
        prepare.onclick=async()=>{prepare.disabled=true;clearRestart();try{const report=await api(`/api/jobs/${row.id}/restart/preview`,{});maintenancePreview={id:row.id,value:report,request_id:crypto.randomUUID().replaceAll('-','')};$('maintenance-preview').textContent=`Новый расчёт с бара 0. Данные и параметры закреплены; прежний checkpoint не используется. Изменения: ${report.changes.map(c=>c.field).join(', ')||'вычислительная конфигурация совпадает'}.`;$('maintenance-apply').disabled=false;}catch(error){$('maintenance-preview').textContent=error.message;}finally{prepare.disabled=false;}};
        td.append(document.createElement('br'),prepare);
      }
      if(row.result_id){const view=document.createElement('button');view.className='text-button';view.textContent='Открыть результат';view.onclick=()=>loadJob(row.id).catch(e=>{$('maintenance-status').textContent=e.message;});td.append(document.createElement('br'),view);}
      tr.append(td);body.append(tr);
    }
    if(!value.items.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.textContent='Сохранённых записей в этой группе пока нет.';tr.append(td);body.append(tr);}
    $('maintenance-page').textContent=`${maintenanceTotal?maintenanceStart+1:0}–${maintenanceNext} из ${maintenanceTotal}`;
    $('maintenance-previous').disabled=maintenanceStart===0;$('maintenance-next').disabled=maintenanceNext>=maintenanceTotal;
  }catch(error){$('maintenance-status').textContent=error.message;}finally{button.disabled=false;}
}
$('maintenance-refresh').onclick=refreshMaintenance;
$('maintenance-kind').onchange=()=>{maintenanceStart=0;refreshMaintenance();};
$('maintenance-previous').onclick=()=>{maintenanceStart=Math.max(0,maintenanceStart-20);refreshMaintenance();};
$('maintenance-next').onclick=()=>{maintenanceStart=maintenanceNext;refreshMaintenance();};
$('maintenance-apply').onclick=async()=>{
  if(!maintenancePreview)return;const selected=maintenancePreview;$('maintenance-apply').disabled=true;
  try{const receipt=await api(`/api/jobs/${selected.id}/restart/apply`,{preview_id:selected.value.preview_id,request_id:selected.request_id});maintenancePreview=null;$('maintenance-preview').textContent=`Новое задание ${receipt.job_id}. Предыдущий запуск сохранён. Обновите список для проверки состояния.`;await history();}
  catch(error){$('maintenance-preview').textContent=`${error.message} Повторить можно с тем же ключом.`;$('maintenance-apply').disabled=false;}
};
$('maintenance-download').onclick=async()=>{const button=$('maintenance-download');button.disabled=true;try{const value=await api('/api/maintenance/diagnostics');const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='openpine-diagnostics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('maintenance-status').textContent='Пакет содержит сводку и идентификаторы. Исходники, параметры, пути, журналы и свечи исключены.';}catch(error){$('maintenance-status').textContent=error.message;}finally{button.disabled=false;}};
