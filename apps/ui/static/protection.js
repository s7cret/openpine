/* Bounded, identity-coupled overlays from the currently loaded chart window. */
function protectionView(){return result&&chartData?.protection;}
function protectionSeries(){
  const info=protectionView();if(!info)return [];
  const n=chartData.data.bars.length,start=info.start_bar,levels=new Map(),low=Array(n).fill(null),high=Array(n).fill(null);
  function put(p,i){if(!p||i<0||i>=n)return;const key=p.exit_id||'protection';if(!levels.has(key))levels.set(key,{stop:Array(n).fill(null),limit:Array(n).fill(null)});const row=levels.get(key);row.stop[i]=p.stop===null?null:Number(p.stop);row.limit[i]=p.limit===null?null:Number(p.limit);}
  for(const e of info.events){
    if(e.kind==='mark'){
      const i=e.bar_index+1-start;for(const p of e.value.protections||[e.value.protection])put(p,i);
    }else if(e.kind==='protection_trigger'){
      put(e.value,e.bar_index-start);
    }
    if(e.kind==='fill'){
      const i=e.bar_index-start,v=Number(e.value.price);
      if(i>=0&&i<n&&Number.isFinite(v)){low[i]=low[i]===null?v:Math.min(low[i],v);high[i]=high[i]===null?v:Math.max(high[i],v);}
    }
  }
  return [...Array.from(levels.values()).flatMap(row=>[{values:row.stop,color:'#ff8a8a'},{values:row.limit,color:'#56d4ae'}]),{values:low,hiddenLine:true},{values:high,hiddenLine:true}];
}
function drawProtectionFills(ctx,x,y){
  const info=protectionView();if(!info)return;
  for(const e of info.events){
    const i=e.bar_index-info.start_bar;if(e.kind!=='fill'||i<view[0]||i>=view[1])continue;
    const px=x(i),py=y(Number(e.value.price)),d=e.value.direction;
    ctx.fillStyle=d===1?'#56d4ae':'#ff8a8a';ctx.beginPath();ctx.moveTo(px,py-d*5);ctx.lineTo(px-4,py+d*3);ctx.lineTo(px+4,py+d*3);ctx.closePath();ctx.fill();
  }
}
let protectionPage=0;
function renderProtection(){
  let card=document.getElementById('protection-card');
  if(!card){
    card=document.createElement('section');card.id='protection-card';card.className='card';card.setAttribute('aria-label','Защитные выходы');
    const heading=document.createElement('h2');heading.textContent='Защитные выходы и исполнения';
    const note=document.createElement('p');note.id='protection-model';
    const intro=document.createElement('div');intro.className='storage-body';intro.append(heading,note);
    const table=document.createElement('table'),thead=document.createElement('thead'),tr=document.createElement('tr');
    for(const name of ['Бар / UTC','Событие','ID / уровень / причина']){const th=document.createElement('th');th.textContent=name;tr.append(th);}
    thead.append(tr);const body=document.createElement('tbody');body.id='protection-events';table.append(thead,body);
    const scroll=document.createElement('div');scroll.className='table-scroll';scroll.append(table);
    const actions=document.createElement('div');actions.className='legend';
    for(const [id,label,delta]of [['protection-prev','← Ранее',-1],['protection-next','Позже →',1]]){
      const button=document.createElement('button');button.id=id;button.textContent=label;button.onclick=()=>{protectionPage+=delta;renderProtectionRows();};actions.append(button);
    }
    const status=document.createElement('span');status.id='protection-page';actions.append(status);
    card.append(intro,scroll,actions);document.querySelector('.chart-card').after(card);
  }
  const info=protectionView();card.hidden=!info;protectionPage=0;if(!info)return;
  const ambiguous=info.events.filter(e=>e.kind==='protection_trigger'&&e.value.ambiguous).length;
  document.getElementById('protection-model').textContent=`Красный — stop, зелёный — take-profit, треугольники — исполнения. Заявки действуют со следующего бара. При неизвестном порядке двух касаний выбран stop; таких событий в загруженном диапазоне: ${ambiguous}.`+(info.policy.profile_id==='openpine.partial-broker.1'?' Объём разделён между выходами. Пересечения на открытии исполняются первыми; при неизвестном внутрисвечном порядке stop имеет приоритет перед take-profit.':'')+(info.truncated?' Достигнут лимит событий: показан частичный журнал. Уменьшите окно графика; полный журнал доступен через API ledger.':'');
  renderProtectionRows();
}
function renderProtectionRows(){
  const info=protectionView();if(!info)return;
  const rows=info.events.filter(e=>e.kind!=='mark'&&e.bar_index>=info.start_bar),size=200,pages=Math.max(1,Math.ceil(rows.length/size));
  protectionPage=Math.max(0,Math.min(pages-1,protectionPage));const body=document.getElementById('protection-events');body.replaceChildren();
  const labels={order:'Заявка',fill:'Исполнение',trade:'Закрытая часть',reservation_reduced:'Объём уменьшен',protection_trigger:'Защитный выход',cancelled:'Отмена',protection_activated:'Защита активна',cancel_command:'Команда отмены',ignored:'Пропуск'};
  const reasons={modified:'Изменение уровня или объёма',target_removed:'Вход отменён или заменён',target_not_filled:'Вход не исполнен',position_closed_or_reversed:'Позиция закрыта или развёрнута',bracket_peer_filled:'Исполнен другой выход группы',cancel:'Отмена по ID',cancel_all:'Отмена всех',no_matching_entry:'Нет соответствующего входа',no_matching_position:'Исходная позиция уже закрыта',no_unreserved_quantity:'Нет свободного объёма после округления',zero_after_rounding:'Объём округлён до нуля',reservation_exhausted:'Остаток позиции зарезервирован другими выходами'};
  for(const e of rows.slice(protectionPage*size,(protectionPage+1)*size)){
    const v=e.value,tr=document.createElement('tr');
    const detail=[v.exit_id||v.entry_id||v.intent?.exit_id||v.intent?.entry_id||'',v.quantity?`Объём ${v.quantity}`:'',v.remaining_quantity!==undefined?`Остаток ${v.remaining_quantity}`:'',v.net_profit!==undefined?`Результат ${v.net_profit}`:'',v.commission!==undefined?`Комиссия ${v.commission}`:'',v.price?`Цена ${v.price}`:'',v.role==='stop'?'Stop':v.role==='limit'?'Take-profit':'',v.level?`Уровень ${v.level}`:'',v.reason?(reasons[v.reason]||v.reason):'',v.ambiguous?'Два касания: порядок неизвестен':''].filter(Boolean).join(' · ');
    for(const value of [`${e.bar_index} · ${new Date(e.time).toISOString()}`,labels[e.kind]||e.kind,detail]){const td=document.createElement('td');td.textContent=value;tr.append(td);}
    body.append(tr);
  }
  if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.textContent='В этом диапазоне событий заявок нет.';tr.append(td);body.append(tr);}
  document.getElementById('protection-prev').disabled=protectionPage===0;document.getElementById('protection-next').disabled=protectionPage+1>=pages;
  document.getElementById('protection-page').textContent=`${protectionPage+1} / ${pages}`;
}
