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
    if(options.length)for(const [index,value] of options.entries()){const option=document.createElement('option');option.value=kind.startsWith('enum:')?definition.options[index]:value;option.textContent=value===''&&definition.inherits_primary?(kind==='symbol'?'Основной инструмент':'Основной интервал'):value;control.append(option);}
    else if(kind==='bool')control.type='checkbox';else{control.type='text';if(kind==='int'||kind==='float')control.inputMode=kind==='int'?'numeric':'decimal';}
    if(kind==='bool')control.checked=definition.default;else control.value=kind.startsWith('enum:')?definition.default:definition.default_text;
    if(kind==='symbol')control.placeholder='BINANCE:BTCUSDT';
    if(kind==='timeframe')control.placeholder='1, 60, D, W, M';
    if(definition.inherits_primary){control.placeholder=kind==='symbol'?'Основной инструмент':'Основной интервал';const help=document.createElement('small');help.textContent='Пустое значение использует основной '+(kind==='symbol'?'инструмент.':'интервал.');label.append(help);}
    if(kind==='color')attachColorPicker(label,control);
    inputControls.set(definition.input_id,{control,definition});label.append(control);if(definition.tooltip){const help=document.createElement('small');help.textContent=definition.tooltip;label.append(help);}$('input-fields').append(label);
  }
  $('preset-name').value='';$('preset-status').textContent='';await refreshInputPresets();
}
function getInputOverrides(){
  const values={};for(const [id,{control,definition}]of inputControls){let value=control.value;
    if(definition.input_type==='bool')value=control.checked;
    else if(definition.input_type==='int'){if(!/^(0|-?[1-9][0-9]*)$/.test(value))throw new Error(`Введите целое число: ${definition.title||definition.name}`);value={$int:value};}
    else if(definition.input_type==='float'){if(!value.trim()||!Number.isFinite(Number(value)))throw new Error(`Введите конечное число: ${definition.title||definition.name}`);value=Number(value);}
    else if(definition.input_type==='color'){if(!/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(value))throw new Error(`Введите HEX-цвет: ${definition.title||definition.name}`);value=value.toUpperCase()+(value.length===7?'FF':'');}
    values[id]=value;
  }return values;
}
function applyInputValues(values){for(const [id,{control,definition}]of inputControls){const value=values?values[id]:definition.default;if(definition.input_type==='bool')control.checked=value;else control.value=values?(value?.$int??String(value)):definition.input_type.startsWith('enum:')?definition.default:definition.default_text;if(definition.input_type==='color')control.dispatchEvent(new Event('input'));}}
async function refreshInputPresets(){
  if(!inputSchema)return;const hash=inputSchema.sha256,ref=compiledRef,data=await api('/api/presets?input_ref='+encodeURIComponent(ref));if(inputSchema?.sha256!==hash||compiledRef!==ref)return;
  inputPresets=data.presets;$('preset-select').replaceChildren(new Option('По умолчанию',''));for(const preset of inputPresets)$('preset-select').append(new Option(preset.name,preset.preset_id));
}
$('preset-select').onchange=()=>{const preset=inputPresets.find(p=>p.preset_id===$('preset-select').value);applyInputValues(preset?.inputs);$('preset-name').value=preset?.name||'';$('preset-status').textContent=preset?`Ревизия ${preset.revision}`:'';};
$('input-reset').onclick=()=>{applyInputValues(null);$('preset-select').value='';$('preset-name').value='';$('preset-status').textContent='Значения по умолчанию';};
$('preset-save').onclick=async()=>{try{const name=$('preset-name').value.trim(),old=inputPresets.find(p=>p.name===name);if(!name)throw new Error('Укажите название набора');const saved=await api('/api/presets',{input_ref:compiledRef,name,inputs:getInputOverrides(),expected_revision:old?.revision??null});await refreshInputPresets();$('preset-select').value=saved.preset_id;$('preset-status').textContent=`Сохранено · ревизия ${saved.revision}`;}catch(error){$('preset-status').textContent=error.message;}};

function attachColorPicker(label,control){
  const row=document.createElement('div'),picker=document.createElement('input'),alpha=document.createElement('input'),caption=document.createElement('small');
  picker.type='color';picker.setAttribute('aria-label','Цвет');alpha.type='range';alpha.min='0';alpha.max='255';alpha.step='1';alpha.setAttribute('aria-label','Непрозрачность');
  for(const element of [picker,alpha]){element.dataset.inactive=control.dataset.inactive;element.disabled=control.disabled;}
  function sync(){if(!/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(control.value))return;picker.value=control.value.slice(0,7);alpha.value=String(control.value.length===9?parseInt(control.value.slice(7),16):255);caption.textContent='Непрозрачность: '+Math.round(Number(alpha.value)*100/255)+'%';}
  function change(){control.value=picker.value.toUpperCase()+Number(alpha.value).toString(16).padStart(2,'0').toUpperCase();sync();control.dispatchEvent(new Event('change',{bubbles:true}));}
  picker.oninput=change;alpha.oninput=change;control.addEventListener('input',sync);sync();row.append(picker,alpha,caption);label.append(row);
}
