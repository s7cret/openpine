"use strict";
let inputSchema=null,inputControls=new Map(),inputPresets=[];
const inputPanel=document.createElement('section');inputPanel.id='input-panel';inputPanel.className='parameter-panel';inputPanel.hidden=true;
inputPanel.innerHTML='<h2>Параметры скрипта</h2><p class="muted">Настройки закрепляются при запуске. Изменение не затронет сохранённые результаты.</p><div id="input-fields"></div><label for="preset-select">Сохранённый набор</label><select id="preset-select"><option value="">По умолчанию</option></select><div class="live-actions"><input id="preset-name" placeholder="Название набора" aria-label="Название набора"><button id="preset-save" class="secondary">Сохранить</button><button id="input-reset" class="text-button">По умолчанию</button></div><p id="preset-status" role="status"></p>';
document.querySelector('.source-card').append(inputPanel);
function setInputLock(on){for(const control of inputPanel.querySelectorAll('input,select,button'))control.disabled=on||control.dataset.inactive==='true';}
function clearInputSchema(){inputSchema=null;inputControls.clear();inputPanel.hidden=true;}
async function setInputSchema(schema){
  clearInputSchema();if(!schema?.definitions?.length)return;inputSchema=schema;inputPanel.hidden=false;$('input-fields').replaceChildren();inputControls.clear();
  for(const definition of schema.definitions){
    const label=document.createElement('label'),caption=document.createElement('span');caption.textContent=(definition.group?definition.group+' · ':'')+(definition.title||definition.name);label.append(caption);
    const kind=definition.input_type,options=kind==='source'?['open','high','low','close','volume']:definition.options_text||[],control=document.createElement(options.length?'select':'input');
    control.id='parameter-'+definition.name;control.dataset.kind=kind;control.dataset.inactive=String(!definition.active);control.title=definition.tooltip;control.disabled=busy||!definition.active;
    if(options.length)for(const value of options){const option=document.createElement('option');option.value=value;option.textContent=value;control.append(option);}
    else if(kind==='bool')control.type='checkbox';else{control.type='text';if(kind==='int'||kind==='float')control.inputMode=kind==='int'?'numeric':'decimal';}
    if(kind==='bool')control.checked=definition.default;else control.value=definition.default_text;
    inputControls.set(definition.input_id,{control,definition});label.append(control);if(definition.tooltip){const help=document.createElement('small');help.textContent=definition.tooltip;label.append(help);}$('input-fields').append(label);
  }
  $('preset-name').value='';$('preset-status').textContent='';await refreshInputPresets();
}
function getInputOverrides(){
  const values={};for(const [id,{control,definition}]of inputControls){let value=control.value;
    if(definition.input_type==='bool')value=control.checked;
    else if(definition.input_type==='int'){if(!/^(0|-?[1-9][0-9]*)$/.test(value))throw new Error(`Введите целое число: ${definition.title||definition.name}`);value={$int:value};}
    else if(definition.input_type==='float'){if(!value.trim()||!Number.isFinite(Number(value)))throw new Error(`Введите конечное число: ${definition.title||definition.name}`);value=Number(value);}
    values[id]=value;
  }return values;
}
function applyInputValues(values){for(const [id,{control,definition}]of inputControls){const value=values?values[id]:definition.default;if(definition.input_type==='bool')control.checked=value;else control.value=values?(value?.$int??String(value)):definition.default_text;}}
async function refreshInputPresets(){
  if(!inputSchema)return;const hash=inputSchema.sha256,ref=compiledRef,data=await api('/api/presets?input_ref='+encodeURIComponent(ref));if(inputSchema?.sha256!==hash||compiledRef!==ref)return;
  inputPresets=data.presets;$('preset-select').replaceChildren(new Option('По умолчанию',''));for(const preset of inputPresets)$('preset-select').append(new Option(preset.name,preset.preset_id));
}
$('preset-select').onchange=()=>{const preset=inputPresets.find(p=>p.preset_id===$('preset-select').value);applyInputValues(preset?.inputs);$('preset-name').value=preset?.name||'';$('preset-status').textContent=preset?`Ревизия ${preset.revision}`:'';};
$('input-reset').onclick=()=>{applyInputValues(null);$('preset-select').value='';$('preset-name').value='';$('preset-status').textContent='Значения по умолчанию';};
$('preset-save').onclick=async()=>{try{const name=$('preset-name').value.trim(),old=inputPresets.find(p=>p.name===name);if(!name)throw new Error('Укажите название набора');const saved=await api('/api/presets',{input_ref:compiledRef,name,inputs:getInputOverrides(),expected_revision:old?.revision??null});await refreshInputPresets();$('preset-select').value=saved.preset_id;$('preset-status').textContent=`Сохранено · ревизия ${saved.revision}`;}catch(error){$('preset-status').textContent=error.message;}};
