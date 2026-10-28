"use strict";
let corpusCatalogue=null,corpusRun=null,corpusTimer=null,corpusSelectedCase=null;
const corpusLabels={passed:'Пройдено',failed:'Расхождение / ошибка',expected_rejection:'Известное ограничение',running:'Выполняется',interrupted:'Прервано',cancelled:'Остановлено'};
function corpusMessage(text){$('corpus-status').textContent=text;}
function renderCorpus(report){
  corpusRun=report.run_id;
  const counts=report.summary?.counts;
  corpusMessage(`${corpusLabels[report.status]||report.status} · ${report.cases.length} из ${report.case_ids.length}${counts?` · выполнено ${counts.passed} · ограничений ${counts.expected_rejection} · ошибок ${counts.failed}`:''}`);
  const body=$('corpus-rows');body.replaceChildren();
  for(const row of report.cases){
    const tr=document.createElement('tr');
    for(const text of [row.case_id,`v${row.version}`,corpusLabels[row.status]||row.status]){const td=document.createElement('td');td.textContent=text;tr.append(td);}
    const td=document.createElement('td'),view=document.createElement('button');view.className='text-button';view.textContent='Подробности';
    view.onclick=()=>{corpusSelectedCase=row.case_id;$('corpus-detail').textContent=JSON.stringify(row,null,2);$('corpus-detail').parentElement.open=true;};td.append(view);
    const rerun=document.createElement('button');rerun.className='text-button';rerun.textContent='Повторить';rerun.disabled=report.status==='running';rerun.onclick=()=>startCorpus([row.case_id],report.run_id);td.append(rerun);tr.append(td);body.append(tr);
  }
  $('corpus-start').disabled=report.status==='running';$('corpus-download').disabled=false;$('corpus-cancel').disabled=report.status!=='running';
  $('corpus-detail').textContent=JSON.stringify(report.cases.find(row=>row.case_id===corpusSelectedCase)||report.summary||{current:report.current,scope:report.scope},null,2);
}
async function pollCorpus(id){
  clearTimeout(corpusTimer);
  try{const value=await api(`/api/corpus/runs/${id}`);renderCorpus(value.report);if(value.report.status==='running')corpusTimer=setTimeout(()=>pollCorpus(id),1200);else await corpusHistory();}
  catch(error){corpusMessage(error.message);corpusTimer=setTimeout(()=>pollCorpus(id),3000);}
}
async function startCorpus(ids,parent){
  $('corpus-start').disabled=true;
  let pending;
  try{
    const saved=localStorage.getItem('openpine-corpus-pending');
    pending=saved?JSON.parse(saved):{request_id:crypto.randomUUID().replaceAll('-',''),case_ids:ids,...(parent?{parent_id:parent}:{})};
    localStorage.setItem('openpine-corpus-pending',JSON.stringify(pending));
    corpusMessage(`Отправка проверки: ${pending.case_ids.join(', ')}`);
    const value=await api('/api/corpus/runs',pending);localStorage.removeItem('openpine-corpus-pending');
    corpusMessage('Запуск принят. Программы проходят через общую очередь заданий.');await pollCorpus(value.run_id);
    $('corpus-start').textContent='Проверить';
  }catch(error){corpusMessage(`${error.message} Сохранена отправка для ${pending?.case_ids.join(', ')||'выбранных программ'}.`);$('corpus-start').textContent='Повторить отправку';$('corpus-start').disabled=false;}
}
async function corpusHistory(){
  const value=await api('/api/corpus/runs'),select=$('corpus-history'),prior=select.value;select.replaceChildren();
  for(const row of value.runs){const option=document.createElement('option');option.value=row.run_id;option.textContent=`${new Date(row.created_at*1000).toLocaleString()} · ${corpusLabels[row.status]||row.status} · программ: ${row.case_ids.length}`;select.append(option);}
  if([...select.options].some(o=>o.value===(corpusRun||prior)))select.value=corpusRun||prior;
  $('corpus-open').disabled=!value.runs.length;
}
$('corpus-load').onclick=async()=>{
  try{corpusCatalogue=await api('/api/corpus');const select=$('corpus-case');select.replaceChildren();const all=document.createElement('option');all.value='';all.textContent=`Все ${corpusCatalogue.cases.length} программы`;select.append(all);
    for(const row of corpusCatalogue.cases){const option=document.createElement('option');option.value=row.id;option.textContent=`${row.id} · ${row.expected.status==='rejected'?'известное ограничение':'исполнение'}`;select.append(option);}
    $('corpus-start').disabled=false;await corpusHistory();corpusMessage('Корпус готов. Ожидаемые отказы учитываются отдельно. Это проверка примеров OpenPine, без оценки совпадения с TradingView.');
  }catch(error){corpusMessage(error.message);}
};
$('corpus-start').onclick=()=>{if(corpusCatalogue)startCorpus($('corpus-case').value?[$('corpus-case').value]:corpusCatalogue.cases.map(c=>c.id));};
$('corpus-open').onclick=()=>{if($('corpus-history').value)pollCorpus($('corpus-history').value);};
$('corpus-cancel').onclick=async()=>{if(!corpusRun)return;try{await api(`/api/corpus/runs/${corpusRun}/cancel`,{});corpusMessage('Остановка запрошена. Выполненные проверки сохраняются.');}catch(error){corpusMessage(error.message);}};
$('corpus-download').onclick=async()=>{
  if(!corpusRun)return;
  try{const value=await api(`/api/corpus/runs/${corpusRun}`),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`openpine-corpus-${corpusRun}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  catch(error){corpusMessage(error.message);}
};
