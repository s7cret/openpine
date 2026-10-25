/* Bounded, identity-coupled overlays from the currently loaded chart window. */
function protectionView(){return result&&chartData?.protection;}
function protectionSeries(){
  const info=protectionView();if(!info)return [];
  const n=chartData.data.bars.length,start=info.start_bar,levels=new Map(),low=Array(n).fill(null),high=Array(n).fill(null);
  const overlayIds=trailingOverlayTargets(info),overlaySet=new Set(overlayIds.slice(-64));
  function put(p,i){if(!p||i<0||i>=n||p.targets?.some(t=>t.kind==='pending'))return;const key=p.exit_id||'protection';if(!levels.has(key))levels.set(key,{stop:Array(n).fill(null),limit:Array(n).fill(null)});const row=levels.get(key);const stop=p.stop??(p.role&&p.role!=='limit'?p.level:null),limit=p.limit??(p.role==='limit'?p.level:null);row.stop[i]=stop===null?null:Number(stop);row.limit[i]=limit===null?null:Number(limit);}
  function putExit(order,state,mark,i){
    if(state.kind==='pending'||i<0||i>=n)return;const key=`exit:${order.exit_id}:${state.target_id}`;if(!overlaySet.has(key))return;const direction=mark.protections.find(p=>p.order_id===order.order_id)?.direction||1;
    if(!levels.has(key))levels.set(key,{stop:Array(n).fill(null),limit:Array(n).fill(null),activation:Array(n).fill(null)});const row=levels.get(key);
    const fixed=state.stop===null?null:Number(state.stop),trail=state.trail_stop!==null&&state.stop_eligible_bar<=i+start?Number(state.trail_stop):state.previous_trail_stop===null?null:Number(state.previous_trail_stop);
    row.stop[i]=fixed===null?trail:trail===null?fixed:direction===1?Math.max(fixed,trail):Math.min(fixed,trail);row.limit[i]=state.limit===null?null:Number(state.limit);
    row.activation[i]=state.activated_bar===null&&state.activation!==null?Number(state.activation):null;
  }
  function putEntry(p,i){if(!p?.intent||i<0||i>=n)return;const key='entry:'+p.intent.entry_id;if(!levels.has(key))levels.set(key,{stop:Array(n).fill(null),limit:Array(n).fill(null),entry:true});const row=levels.get(key),active=p.activated_bar!==null;row.stop[i]=p.intent.stop===null||active?null:Number(p.intent.stop);row.limit[i]=p.intent.limit===null||p.intent.stop!==null&&!active?null:Number(p.intent.limit);}
  for(const e of info.events){
    if(e.kind==='mark'){
      const i=e.bar_index+1-start;for(const p of e.value.protections||[e.value.protection])put(p,i);
      for(const p of e.value.entries||[])putEntry(p,i);
      for(const order of e.value.exit_states||[])for(const state of order.targets)putExit(order,state,e.value,i);
    }else if(e.kind==='protection_trigger'){
      put(e.value,e.bar_index-start);
    }else if(e.kind==='entry_trigger'||e.kind==='entry_activated'){
      putEntry(e.value,(e.kind==='entry_activated'?e.value.limit_eligible_bar:e.bar_index)-start);
    }
    if(e.kind==='fill'){
      const i=e.bar_index-start,v=Number(e.value.price);
      if(i>=0&&i<n&&Number.isFinite(v)){low[i]=low[i]===null?v:Math.min(low[i],v);high[i]=high[i]===null?v:Math.max(high[i],v);}
    }
  }
  return [...Array.from(levels.values()).flatMap(row=>[{values:row.stop,color:row.entry?'#e8b36d':'#ff8a8a'},{values:row.limit,color:row.entry?'#8bb7ff':'#56d4ae'},...(row.activation?[{values:row.activation,color:'#c6a0ff'}]:[])]),{values:low,hiddenLine:true},{values:high,hiddenLine:true}];
}
function trailingOverlayTargets(info){const ids=new Set();for(const e of info.events)if(e.kind==='mark')for(const o of e.value.exit_states||[])for(const t of o.targets){const key=`exit:${o.exit_id}:${t.target_id}`;ids.delete(key);ids.add(key);}return [...ids];}
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
  const trailing=['openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1'].includes(info.policy.profile_id),grouped=trailing||info.policy.profile_id==='openpine.order-group-broker.1',lots=grouped||info.policy.profile_id==='openpine.lot-broker.1';
  card.querySelector('h2').textContent=grouped?'Лоты и связанные заявки':lots?'Лоты, отложенные входы и выходы':info.policy.profile_id==='openpine.pending-entry-broker.1'?'Отложенные входы и защитные выходы':'Защитные выходы и исполнения';
  const ambiguous=info.events.filter(e=>e.kind==='protection_trigger'&&e.value.ambiguous).length;
  document.getElementById('protection-model').textContent=`Красный — stop, зелёный — take-profit, треугольники — исполнения. Заявки действуют со следующего бара. При неизвестном порядке двух касаний выбран stop; таких событий в загруженном диапазоне: ${ambiguous}.`+(info.policy.profile_id==='openpine.partial-broker.1'?' Объём разделён между выходами. Пересечения на открытии исполняются первыми; при неизвестном внутрисвечном порядке stop имеет приоритет перед take-profit.':'')+(info.truncated?' Достигнут лимит событий: показан частичный журнал. Уменьшите окно графика; полный журнал доступен через API ledger.':'');
  renderProtectionRows();
  if(lots||info.policy.profile_id==='openpine.pending-entry-broker.1')document.getElementById('protection-model').textContent+=' Вход: синий — limit, оранжевый — stop. Stop-limit после внутрисвечной активации ждёт следующий бар; защита внутрисвечного входа также действует со следующего бара. При неизвестном порядке исполняется не более одного внутрисвечного входа за бар.';
  renderLotPosition(info,lots,card);
  if(!lots){const risk=document.getElementById('risk-rules'),economics=document.getElementById('economic-orders');if(risk)risk.hidden=true;if(economics)economics.hidden=true;}
  if(trailing)document.getElementById('protection-model').textContent+=' Фиолетовый — уровень активации trailing. Перенос по high/low действует со следующего бара; на известном open активация и перенос учитываются до проверки стопа.';
  if(trailing&&trailingOverlayTargets(info).length>64)document.getElementById('protection-model').textContent+=' На графике показаны уровни 64 последних целевых остатков; полный состав выбранного бара доступен в таблице.';
  if(!lots&&document.getElementById('order-groups'))document.getElementById('order-groups').hidden=true;
  if(!lots&&document.getElementById('trailing-exits'))document.getElementById('trailing-exits').hidden=true;
  if(info.policy.profile_id!=='openpine.economics-broker.1'&&document.getElementById('economic-orders'))document.getElementById('economic-orders').hidden=true;
}
function renderProtectionRows(){
  const info=protectionView();if(!info)return;
  const rows=info.events.filter(e=>e.kind!=='mark'&&e.bar_index>=info.start_bar),size=200,pages=Math.max(1,Math.ceil(rows.length/size));
  protectionPage=Math.max(0,Math.min(pages-1,protectionPage));const body=document.getElementById('protection-events');body.replaceChildren();
  const labels={order:'Заявка',fill:'Исполнение',trade:'Закрытая часть',reservation_reduced:'Объём уменьшен',protection_trigger:'Защитный выход',cancelled:'Отмена',protection_activated:'Защита активна',cancel_command:'Команда отмены',ignored:'Пропуск',entry_activated:'Stop-limit активирован',entry_trigger:'Условие входа',entry_deferred:'Вход отложен',oca_filled:'OCA: исполнение',oca_reduced:'OCA: уменьшение',replaced:'Замена заявки',trailing_activated:'Trailing активирован',trailing_updated:'Trailing подтянут'};
  const reasons={modified:'Изменение уровня или объёма',target_removed:'Вход отменён или заменён',target_not_filled:'Вход не исполнен',position_closed_or_reversed:'Позиция закрыта или развёрнута',bracket_peer_filled:'Исполнен другой выход группы',cancel:'Отмена по ID',cancel_all:'Отмена всех',no_matching_entry:'Нет соответствующего входа',no_matching_position:'Исходная позиция уже закрыта',no_unreserved_quantity:'Нет свободного объёма после округления',zero_after_rounding:'Объём округлён до нуля',reservation_exhausted:'Остаток позиции зарезервирован другими выходами'};
  Object.assign(labels,{risk_triggered:'Лимит риска сработал',risk_period:'Период риска',risk_adjusted:'Ограничение входа',risk_blocked:'Отклонено по риску',risk_liquidation:'Закрытие по риску'});
  Object.assign(labels,{quantity_resolved:'Расчёт объёма',commission_calculated:'Расчёт комиссии'});
  Object.assign(reasons,{below_minimum_quantity:'Ниже минимального количества',below_minimum_notional:'Ниже минимальной суммы'});
  Object.assign(reasons,{modified_entry:'Изменение отложенного входа',unknown_intrabar_entry_order:'Порядок внутрисвечных входов неизвестен',pyramiding_limit:'Достигнут предел открытых лотов',entry_not_filled:'Вход не исполнен',no_matching_lots:'Исходные лоты уже закрыты',reservation_exhausted_or_target_closed:'Зарезервированный остаток исчерпан или лот закрыт'});
  Object.assign(reasons,{oca_cancel:'Отмена по исполнению участника OCA',oca_reduce:'Объём исчерпан по OCA reduce',active_lot_limit:'Достигнут предел 16 активных лотов',no_unreserved_lots:'Нет свободных лотов для общего закрытия'});
  for(const e of rows.slice(protectionPage*size,(protectionPage+1)*size)){
    const v={...e.value},tr=document.createElement('tr');
    if(v.order_kind){v.quantity=v.intent?.quantity;v.entry_id=v.intent?.entry_id;}
    const detail=[v.exit_id||v.entry_id||v.intent?.exit_id||v.intent?.entry_id||'',v.quantity?`Объём ${v.quantity}`:'',v.remaining_quantity!==undefined?`Остаток ${v.remaining_quantity}`:'',v.net_profit!==undefined?`Результат ${v.net_profit}`:'',v.commission!==undefined?`Комиссия ${v.commission}`:'',v.price?`Цена ${v.price}`:'',v.role==='stop'?'Stop':v.role==='limit'?'Take-profit':'',v.level?`Уровень ${v.level}`:'',v.reason?(reasons[v.reason]||v.reason):'',v.ambiguous?'Два касания: порядок неизвестен':''].filter(Boolean).join(' · ');
    const entryDetail=(v.order_kind?` · Вход ${v.order_kind}${v.intent.limit!==null?' · Limit '+v.intent.limit:''}${v.intent.stop!==null?' · Stop '+v.intent.stop:''}`:'')+(v.lot_id?` · Лот ${v.lot_id.slice(0,8)}`:'')+(v.position_remaining_quantity!==undefined?` · Остаток позиции ${v.position_remaining_quantity}`:'')+(v.targets?` · Целевых записей ${v.targets.length}`:'')+(v.command==='order'?' · strategy.order':v.command==='close_all'||v.intent?.kind==='close_all'?' · strategy.close_all':'')+(v.oca_name?` · OCA ${v.oca_name} (${v.oca_type})`:'')+(v.previous_quantity!==undefined?` · Было ${v.previous_quantity}`:'');
    for(const value of [`${e.bar_index} · ${new Date(e.time).toISOString()}`,labels[e.kind]||e.kind,detail+entryDetail]){const td=document.createElement('td');td.textContent=value;tr.append(td);}
    body.append(tr);
  }
  if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.textContent='В этом диапазоне событий заявок нет.';tr.append(td);body.append(tr);}
  document.getElementById('protection-prev').disabled=protectionPage===0;document.getElementById('protection-next').disabled=protectionPage+1>=pages;
  document.getElementById('protection-page').textContent=`${protectionPage+1} / ${pages}`;
}

function renderLotPosition(info,enabled,card){
  let section=document.getElementById('lot-position');
  if(!section){
    section=document.createElement('div');section.id='lot-position';section.className='storage-body';
    const heading=document.createElement('h3');heading.textContent='Состав позиции';
    const label=document.createElement('label');label.htmlFor='lot-mark-select';label.textContent='Позиция на закрытии бара';
    const select=document.createElement('select');select.id='lot-mark-select';
    const summary=document.createElement('p');summary.id='lot-position-summary';
    const note=document.createElement('p');note.id='lot-position-note';
    const table=document.createElement('table'),head=document.createElement('thead'),row=document.createElement('tr');
    for(const text of ['Лот / вход','Бар входа','Направление','Остаток','Цена входа','Остаток комиссии']){const th=document.createElement('th');th.textContent=text;row.append(th);}
    head.append(row);const body=document.createElement('tbody');body.id='lot-position-rows';table.append(head,body);
    const scroll=document.createElement('div');scroll.className='table-scroll';scroll.append(table);section.append(heading,label,select,summary,note,scroll);
    card.insertBefore(section,card.children[1]);
  }
  section.hidden=!enabled;if(!enabled)return;
  const marks=info.events.filter(e=>e.kind==='mark'&&Array.isArray(e.value.lots));
  const select=document.getElementById('lot-mark-select');select.replaceChildren();
  for(const mark of marks){const option=document.createElement('option');option.value=String(mark.sequence);option.textContent=`${mark.bar_index} · ${new Date(mark.time).toISOString()} · лотов ${mark.value.lots.length}`;select.append(option);}
  select.disabled=!marks.length;
  const grouped=['openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1'].includes(info.policy.profile_id);
  section.querySelector('label').textContent=grouped?'Позиция и заявки на закрытии бара':'Позиция на закрытии бара';
  document.getElementById('lot-position-note').textContent=`Правило закрытия: ${info.policy.close_entries_rule}. `+(grouped?`strategy.entry: предел ${Math.max(1,info.policy.pyramiding)}; strategy.order: до 16 активных лотов. `:`Предел открытых лотов: ${Math.max(1,info.policy.pyramiding)}. `)+'Состояние на подтверждённом закрытии; доступны записи загруженного диапазона.'+(info.truncated?' Журнал ограничен: для остальных баров уменьшите диапазон.':'');
  function show(){
    const mark=marks.find(e=>String(e.sequence)===select.value),body=document.getElementById('lot-position-rows');body.replaceChildren();
    const average=mark?.value.position_avg_price;
    document.getElementById('lot-position-summary').textContent=mark?`Объём позиции ${mark.value.position_size} · Средняя цена ${average===null?'na':Number(average).toLocaleString('ru-RU',{maximumFractionDigits:8})} · Открытых лотов ${mark.value.lots.length}`:'Состояние позиции в этом диапазоне недоступно.';
    for(const lot of mark?.value.lots||[]){
      const row=document.createElement('tr');for(const value of [`${lot.fill_id.slice(0,8)} · ${lot.entry_id}`,lot.bar_index,lot.direction===1?'Long':'Short',lot.quantity,lot.price,lot.commission]){const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);}body.append(row);
    }
    if(!body.children.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=6;cell.textContent=mark?'Открытых лотов нет.':'Нет сохранённой отметки.';row.append(cell);body.append(row);}
    renderOrderGroups(mark,info,card);
    renderTrailingExits(mark,info,card);
    renderEconomicOrders(mark,info,card);
    renderRiskRules(mark,info,card);
  }
  select.onchange=show;if(marks.length)select.value=String(marks[marks.length-1].sequence);show();
}

function renderEconomicOrders(mark,info,card){
  let section=document.getElementById('economic-orders');
  if(!['openpine.economics-broker.1','openpine.risk-broker.1'].includes(info.policy.profile_id)){if(section)section.hidden=true;return;}
  if(!section){
    section=document.createElement('div');section.id='economic-orders';section.className='storage-body';
    const heading=document.createElement('h3');heading.textContent='Объём и комиссии';
    const summary=document.createElement('p');summary.id='economic-summary';const note=document.createElement('p');note.id='economic-rules';
    section.append(heading,summary,note);
    for(const [id,title,columns] of [['economic-sizing-rows','Последние расчёты объёма до выбранного бара',['Бар / вход','Режим','Исходный qty','Бюджет','Цена расчёта','Объём','Решение']],['economic-fee-rows','Последние начисления комиссии до выбранного бара',['Бар / fill','Тип','Ставка','Объём','Цена fill','Комиссия','Основание']]]){
      const h=document.createElement('h4');h.textContent=title;const table=document.createElement('table'),head=document.createElement('thead'),row=document.createElement('tr');
      for(const label of columns){const th=document.createElement('th');th.textContent=label;row.append(th);}head.append(row);const body=document.createElement('tbody');body.id=id;table.append(head,body);const scroll=document.createElement('div');scroll.className='table-scroll';scroll.append(table);section.append(h,scroll);
    }
    const anchor=document.getElementById('lot-position');card.insertBefore(section,anchor?anchor.nextSibling:card.children[1]);
  }
  section.hidden=false;const p=info.policy,modes={fixed:'Фиксированное количество',cash:'Сумма в валюте',percent_of_equity:'Процент капитала',explicit:'Явный qty'};
  document.getElementById('economic-summary').textContent=`${modes[p.quantity_mode]}: ${p.default_quantity} · Комиссия ${p.commission.kind}: ${p.commission.value} · Бар ${mark?.bar_index??'—'}`;
  document.getElementById('economic-rules').textContent=`Объём закрепляется на close создания заявки. Гэп меняет стоимость, сохраняя объём. Шаг ${p.settings.quantity_step}; минимум количества ${p.settings.minimum_quantity??'не задан'}; минимум суммы ${p.settings.minimum_notional??'не задан'}. Показаны последние 16 событий каждого вида в загруженном окне. Фиксированная комиссия начисляется на первый фактический fill заявки.`;
  const reasons={zero_after_rounding:'Нулевой остаток',below_minimum_quantity:'Ниже минимума количества',below_minimum_notional:'Ниже минимума суммы'};
  const basis={first_order_fill:'Первый fill заявки',already_charged_order:'Уже начислена',actual_fill:'Фактическое исполнение'};
  for(const [kind,id] of [['quantity_resolved','economic-sizing-rows'],['commission_calculated','economic-fee-rows']]){
    const body=document.getElementById(id);body.replaceChildren();
    const events=mark?info.events.filter(e=>e.kind===kind&&e.bar_index<=mark.bar_index).slice(-16):[];
    for(const e of events){const v=e.value,row=document.createElement('tr'),values=kind==='quantity_resolved'?[`${e.bar_index} · ${v.entry_id}`,modes[v.mode],v.requested??'По умолчанию',v.budget??'—',v.reference_price,v.quantity,v.status==='resolved'?'Принято':reasons[v.reason]||v.reason]:[`${e.bar_index} · ${v.fill_id.slice(0,8)}`,v.kind,v.policy_value,v.quantity,v.price,v.amount,basis[v.basis]||v.basis];
      for(const value of values){const td=document.createElement('td');td.textContent=String(value);row.append(td);}body.append(row);
    }
    if(!events.length){const row=document.createElement('tr'),td=document.createElement('td');td.colSpan=7;td.textContent='В загруженном диапазоне до выбранного бара событий нет.';row.append(td);body.append(row);}
  }
}

function renderRiskRules(mark,info,card){
  let section=document.getElementById('risk-rules');
  if(info.policy.profile_id!=='openpine.risk-broker.1'){if(section)section.hidden=true;return;}
  if(!section){
    section=document.createElement('div');section.id='risk-rules';section.className='storage-body';
    const heading=document.createElement('h3');heading.textContent='Ограничения риска';
    const status=document.createElement('p');status.id='risk-status';const summary=document.createElement('p');summary.id='risk-summary';
    const note=document.createElement('p');note.textContent='Контроль на известном open, после fill и на close. Закрытие по риску на close ждёт следующий open. Учтены обычные комиссии и проскальзывание. Показано состояние выбранного бара и последние 16 событий в загруженном окне.';
    section.append(heading,status,summary,note);
    for(const [id,columns] of [['risk-limit-rows',['Правило','Порог / направление','Единица','Область действия']],['risk-event-rows',['Бар','Событие / правило','Показатель','Порог','Действие / основание']]]){
      const table=document.createElement('table'),head=document.createElement('thead'),row=document.createElement('tr');
      for(const title of columns){const c=document.createElement('th');c.textContent=title;row.append(c);}head.append(row);
      const body=document.createElement('tbody');body.id=id;table.append(head,body);const scroll=document.createElement('div');scroll.className='table-scroll';scroll.append(table);section.append(scroll);
    }
    document.getElementById('economic-orders').after(section);
  }
  section.hidden=false;const state=mark?.value.risk,halt=state?.halt;
  const names={allow_entry_in:'Разрешённое направление',max_position_size:'Размер позиции',max_drawdown:'Просадка капитала',max_cons_loss_days:'Убыточные дни подряд',max_intraday_loss:'Убыток за период',max_intraday_filled_orders:'Исполненные заявки за период'};
  const units={cash:'Валюта счёта',percent_of_equity:'% капитала',long:'Long',short:'Short',all:'Оба направления'};
  const basis={open:'Open',fill:'После исполнения',close:'Close — закрытие на следующем open',creation_close:'Создание на close'};
  document.getElementById('risk-status').textContent=!state?'Состояние риска недоступно в этом диапазоне.':state.blocked?`Торговля остановлена: ${names[halt.rule]} · ${halt.permanent?'До конца запуска':'До следующего периода'}${state.needs_close?' · Позиция ждёт закрытия на следующем open':''}`:'Торговля разрешена';
  document.getElementById('risk-summary').textContent=state?`Бар ${mark.bar_index} · Период ${state.period===null?'Прогрев':new Date(state.period).toISOString()} · ${state.period_kind==='chart_bar'?'Один бар W/M':'День UTC'} · Максимум капитала ${state.peak} · Максимум периода ${state.period_peak} · Исполнено заявок ${state.filled_orders} · Убыточных дней подряд ${state.loss_days}`:'';
  const limits=document.getElementById('risk-limit-rows');limits.replaceChildren();
  function row(body,values){const r=document.createElement('tr');for(const value of values){const c=document.createElement('td');c.textContent=String(value);r.append(c);}body.append(r);}
  for(const rule of Object.values(info.policy.limits).filter(Boolean))row(limits,[names[rule.kind],units[rule.value]||rule.value,units[rule.unit]||'—',['allow_entry_in','max_position_size'].includes(rule.kind)?'strategy.entry':'Все торговые действия']);
  const events=document.getElementById('risk-event-rows');events.replaceChildren();
  const labels={risk_triggered:'Лимит сработал',risk_period:'Граница периода',risk_adjusted:'Изменение входа',risk_blocked:'Команда отклонена',risk_liquidation:'Закрытие по риску'};
  const actions={close_only:'Только закрытие позиции',reduced:'Объём уменьшен',blocked:'Вход отклонён',opened:'Новый период',closed:'Период завершён'};
  const rows=mark?info.events.filter(e=>e.kind.startsWith('risk_')&&e.bar_index<=mark.bar_index).slice(-16):[];
  for(const e of rows){const v=e.value;row(events,[e.bar_index,`${labels[e.kind]}${v.rule?' · '+names[v.rule]:''}`,v.observed??v.quantity??v.net_profit??'—',v.threshold??'—',actions[v.action]||basis[v.basis]||'—']);}
  if(!rows.length)row(events,['—','В этом диапазоне событий риска нет.','—','—','—']);
}

function renderOrderGroups(mark,info,card){
  let section=document.getElementById('order-groups');
  const enabled=['openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1'].includes(info.policy.profile_id);
  if(!enabled){if(section)section.hidden=true;return;}
  if(!section){
    section=document.createElement('div');section.id='order-groups';section.className='storage-body';
    const heading=document.createElement('h3');heading.textContent='Связанные заявки';
    const summary=document.createElement('p');summary.id='order-group-summary';
    const note=document.createElement('p');note.textContent='Cancel отменяет участников после исполнения заявки; reduce уменьшает их остатки. Изменение группы применяется перед следующим исполнением. Выбран тот же бар, что и для состава позиции.';
    const table=document.createElement('table'),head=document.createElement('thead'),row=document.createElement('tr');
    for(const label of ['Группа / правило','Заявка / ID','Операция','Состояние','Исходный объём','Остаток','Уровни']){const cell=document.createElement('th');cell.textContent=label;row.append(cell);}
    head.append(row);const body=document.createElement('tbody');body.id='order-group-rows';table.append(head,body);
    const scroll=document.createElement('div');scroll.className='table-scroll';scroll.append(table);section.append(heading,summary,note,scroll);
    document.getElementById('lot-position').after(section);
  }
  section.hidden=false;const groups=mark?.value.order_groups||[],body=document.getElementById('order-group-rows');body.replaceChildren();
  document.getElementById('order-group-summary').textContent=mark?`Групп ${groups.length} · Активных участников ${groups.reduce((n,g)=>n+g.members.length,0)} · Бар ${mark.bar_index}`:'Состояние групп в этом диапазоне недоступно.';
  const orders=new Map([...(mark?.value.entries||[]),...(mark?.value.protections||[])].map(o=>[o.order_id,o]));
  for(const group of groups)for(const member of group.members){
    const order=orders.get(member.order_id),intent=order?.intent||{};
    const status=order?.targets?.some(t=>t.kind==='pending')?'Ожидает вход':member.command==='exit'?(order?.eligible_bar>mark.bar_index?'Со следующего бара':'Защита активна'):order?.status==='limit_active'?'Limit активен':'Ожидает';
    const levels=[intent.stop===null||intent.stop===undefined?'':'Stop '+intent.stop,intent.limit===null||intent.limit===undefined?'':'Limit '+intent.limit].filter(Boolean).join(' · ')||'Market';
    const row=document.createElement('tr');for(const value of [`${group.name} · ${group.type}`,`${intent.exit_id||intent.entry_id||''} · ${member.order_id.slice(0,8)}`,`strategy.${member.command}`,status,member.original_quantity,member.remaining_quantity,levels]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}body.append(row);
  }
  if(!body.children.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=7;cell.textContent=mark?'Активных OCA-групп нет.':'Нет сохранённой отметки.';row.append(cell);body.append(row);}
}

function renderTrailingExits(mark,info,card){
  let section=document.getElementById('trailing-exits');
  if(!['openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1'].includes(info.policy.profile_id)){if(section)section.hidden=true;return;}
  if(!section){
    section=document.createElement('div');section.id='trailing-exits';section.className='storage-body';
    const heading=document.createElement('h3');heading.textContent='Защитные уровни и trailing';
    const summary=document.createElement('p');summary.id='trailing-summary';
    const note=document.createElement('p');note.textContent='Уровни рассчитаны от цены конкретного лота. Действующий стоп и новый trailing-stop показаны отдельно: перенос по high/low начинает действовать со следующего бара. При совместной защите выбирается более защитный стоп.';
    const table=document.createElement('table'),head=document.createElement('thead'),row=document.createElement('tr');
    for(const label of ['Выход / вход','Лот','Остаток','Состояние','Цена входа','Stop / Take','Активация','Экстремум','Действующий стоп','Новый trailing-stop','Действует с бара']){const cell=document.createElement('th');cell.textContent=label;row.append(cell);}
    head.append(row);const body=document.createElement('tbody');body.id='trailing-rows';table.append(head,body);const scroll=document.createElement('div');scroll.className='table-scroll';scroll.append(table);section.append(heading,summary,note,scroll);
    document.getElementById('order-groups').after(section);
  }
  section.hidden=false;const orders=mark?.value.exit_states||[],body=document.getElementById('trailing-rows');body.replaceChildren();
  document.getElementById('trailing-summary').textContent=mark?`Выходов ${orders.length} · Целевых остатков ${orders.reduce((n,o)=>n+o.targets.length,0)} · Бар ${mark.bar_index}`:'Состояние защиты в этом диапазоне недоступно.';
  const labels={pending_entry:'Ожидает вход',waiting_activation:'Ждёт активацию',trailing:'Trailing активен',fixed:'Фиксированные уровни'};
  for(const order of orders)for(const state of order.targets){
    const levels=[state.stop===null?'':'Stop '+state.stop,state.limit===null?'':'Take '+state.limit].filter(Boolean).join(' · ')||'—';
    const values=[`${order.exit_id} / ${order.from_entry}`,state.target_id.slice(0,8),state.quantity,labels[state.status]||state.status,state.entry_price??'—',levels,state.activation??'—',state.extreme??'—',state.effective_stop??'—',state.trail_stop??'—',state.stop_eligible_bar??'—'];
    const row=document.createElement('tr');for(const value of values){const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);}body.append(row);
  }
  if(!body.children.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=11;cell.textContent=mark?'Активных защитных остатков нет.':'Нет сохранённой отметки.';row.append(cell);body.append(row);}
}
