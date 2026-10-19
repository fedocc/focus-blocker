/* Focus mode leaves Anki's original widgets mounted and restores them unchanged. */
(() => {
 const grid=document.querySelector('.unified-grid');if(!grid)return;
 if(!document.getElementById('slovo-stats-css')){
  const css=document.createElement('link');css.id='slovo-stats-css';css.rel='stylesheet';css.href='/_addons/ADDON_PACKAGE/focus-stats.css';document.head.append(css);
 }
 if(window.slovoFocusStats?.grid===grid&&window.slovoFocusStats.alive()){window.slovoFocusStats.update(PAYLOAD);return;}
 document.getElementById('slovo-stats-switch')?.remove();
 grid.querySelectorAll('.slovo-focus-replacement').forEach(el=>el.remove());
 const model={data:PAYLOAD,mode:PAYLOAD.mode||'focus',period:'today',tab:'apps',selected:null};
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fmt=s=>s>=3600?Math.floor(s/3600)+' ч'+(Math.floor(s%3600/60)?' '+Math.floor(s%3600/60)+' мин':''):Math.floor(s/60)+' мин';
 const date=n=>new Date(n+'T12:00:00+03:00');
 const shift=(n,days)=>{const d=date(n);d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10);};
 const replacement=(original,kind)=>{
  if(!original)return null;
  original.classList.add('slovo-original-stats');
  const element=document.createElement('section');element.className='slovo-focus-replacement '+kind;original.after(element);return element;
 };
 const bar=document.createElement('div');bar.id='slovo-stats-switch';
 bar.innerHTML='<div class="ss-segments" role="group" aria-label="Режим статистики"><button data-mode="cards">Карточки</button><button data-mode="focus">◉ Фокус</button></div><span class="ss-caption"></span><div class="ss-segments ss-period" role="group" aria-label="Период статистики"><button data-period="today">Сегодня</button><button data-period="week">7 дней</button><button data-period="month">30 дней</button><button data-period="year">Год</button></div>';
 grid.before(bar);
 const metrics=['studied','time','pace','retention'].map(id=>replacement(grid.querySelector('.onigiri-stat-card[data-stat="'+id+'"]'),'ss-metric'));
 const activity=replacement(grid.querySelector('#onigiri-heatmap-container'),'ss-panel');
 const details=replacement(grid.querySelector('.learner-stats-widget[data-widget-id="deck_stats"]'),'ss-panel');
 const periodDays=()=>({today:1,week:7,month:30,year:365})[model.period];
 const inRange=key=>key>=shift(model.data.today,1-periodDays())&&key<=model.data.today;
 const rangeRecords=()=>model.data.records.filter(r=>model.selected?r.day===model.selected:inRange(r.day));
 const duration=r=>Math.max(0,Number(r.seconds)||0);
 function aggregate(){
  const rows=Object.entries(model.data.days).filter(([key])=>inRange(key)).map(([,row])=>row);
  return rows.reduce((s,r)=>({seconds:s.seconds+r.seconds,completed:s.completed+r.completed,interrupted:s.interrupted+r.interrupted,attempts:s.attempts+r.attempts}),{seconds:0,completed:0,interrupted:0,attempts:0});
 }
 function spark(values,color){
  const max=Math.max(1,...values),points=values.map((v,i)=>`${i/(values.length-1)*200},${30-v/max*26}`).join(' ');
  return `<svg class="ss-spark" viewBox="0 0 200 34" preserveAspectRatio="none" aria-hidden="true"><polygon fill="${color}" opacity=".1" points="0,34 ${points} 200,34"/><polyline fill="none" stroke="${color}" stroke-width="1.5" points="${points}"/></svg>`;
 }
 function renderMetrics(){
  const a=aggregate(),finished=a.completed+a.interrupted,percent=finished?Math.round(a.completed/finished*100):null;
  const seven=Array.from({length:7},(_,i)=>model.data.days[shift(model.data.today,i-6)]||{seconds:0,completed:0,interrupted:0,attempts:0});
  const values=[
   ['✓','Блоки',a.completed,'бл.',model.period==='today'?a.completed+' из 5 основных':'Завершены полностью',seven.map(r=>r.completed),'#38d2b1'],
   ['◷','Время',(a.seconds/3600).toFixed(1).replace('.',','),'ч','По таймеру · включая текущий блок',seven.map(r=>r.seconds),'#5ab8ed'],
   ['↗','Блокировки',a.attempts,'раз','Срабатывания защиты сайтов и приложений',seven.map(r=>r.attempts),'#e3bf86'],
   ['◇','Завершение',percent===null?'—':percent+'%','','Доля полных блоков · '+a.interrupted+' прервано',seven.map(r=>r.completed+r.interrupted?r.completed/(r.completed+r.interrupted)*100:0),'#c991df']
  ];
  metrics.forEach((el,i)=>{if(!el)return;const[icon,title,value,unit,note,series,color]=values[i];el.style.setProperty('--tone',color);el.innerHTML=`<div class="ss-metric-head"><span class="ss-icon">${icon}</span>${title}</div><div class="ss-metric-body"><div class="ss-value">${value}<span class="ss-unit">${unit}</span></div><div class="ss-note">${note}</div></div>${spark(series,color)}`;});
 }
 function recordRows(records){
  if(!records.length)return '<div class="ss-empty">Пока нет блоков за этот период.<br>Заверши первый блок — здесь появится история учёбы.</div>';
  return '<div class="ss-timeline">'+[...records].sort((a,b)=>b.ended_at-a.ended_at).map(r=>{
   const time=new Date(r.ended_at*1000).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'});
   const status={completed:'Завершён',interrupted:'Прерван',live:'Сейчас',repair:'Ремонт службы · вне показателей'}[r.outcome];
   return `<div class="ss-record" title="${esc(r.tasks.join(' · '))}"><span class="ss-record-time">${time}</span><span class="ss-record-title">${esc(r.title)}<br><span class="ss-record-status ${r.outcome}">${status} · ${esc(r.day.slice(5))}</span></span><span>${fmt(duration(r))}</span></div>`;
  }).join('')+'</div>';
 }
 function renderActivity(){
  if(!activity)return;
  activity.innerHTML=`<div class="ss-panel-head"><span class="ss-panel-title">Активность фокуса</span><span class="ss-pill">♨ ${model.data.streak} дн. подряд</span></div><div class="ss-activity-body"></div><div class="ss-muted ss-activity-foot"></div>`;
  const body=activity.querySelector('.ss-activity-body');
  if(model.period==='today')body.innerHTML=recordRows(model.data.records.filter(r=>r.day===model.data.today));
  else if(model.period==='year'){
   const first=shift(model.data.today,-364),pad=(date(first).getUTCDay()+6)%7,start=shift(first,-pad);let html='<div class="ss-year" role="group" aria-label="Календарь времени фокуса">';
   for(let week=0;week<Math.ceil((365+pad)/7);week++){
    const weekDate=shift(start,week*7),previousWeek=shift(weekDate,-7),monthLabel=week===0||weekDate.slice(0,7)!==previousWeek.slice(0,7)?date(weekDate).toLocaleDateString('ru-RU',{month:'short',timeZone:'Europe/Moscow'}):'';
    html+='<div class="ss-year-week"><span class="ss-month-label">'+esc(monthLabel)+'</span>';for(let weekday=0;weekday<7;weekday++){
     const key=shift(start,week*7+weekday),r=model.data.days[key]||{},hours=(r.seconds||0)/3600;
     const color=hours>=6?'#71e4bc':hours>=4?'#41ba9e':hours>=2?'#318c89':hours>0?'#426d78':'#36535f80';
     html+=`<button class="ss-cell ${key===model.data.today?'today':''}" style="background:${color}" data-day="${key}" ${key<first||key>model.data.today?'disabled':''} aria-pressed="${model.selected===key}" aria-label="${key}: ${fmt(r.seconds||0)}, блоков ${r.completed||0}" title="${key} · ${fmt(r.seconds||0)} · ${r.completed||0} блоков"></button>`;
    }html+='</div>';
   }body.innerHTML=html+'</div><div class="ss-legend">Меньше '+['#36535f80','#426d78','#318c89','#41ba9e','#71e4bc'].map(c=>`<i style="background:${c}"></i>`).join('')+' Больше</div>';
  }else{
   const days=Array.from({length:periodDays()},(_,i)=>{const key=shift(model.data.today,i-periodDays()+1);return{key,...(model.data.days[key]||{seconds:0})};}),max=Math.max(3600,...days.map(d=>d.seconds));
   body.innerHTML='<div class="ss-bars">'+days.map((r,i)=>`<div class="ss-bar-col"><button class="ss-bar-track" data-day="${r.key}" aria-label="${r.key}: ${fmt(r.seconds)}" title="${r.key} · ${fmt(r.seconds)}"><span class="ss-bar-fill" style="height:${r.seconds/max*100}%"></span></button><span class="ss-bar-label">${model.period==='week'?date(r.key).toLocaleDateString('ru-RU',{weekday:'short',timeZone:'Europe/Moscow'}):(i%5===0?Number(r.key.slice(-2)):'')}</span></div>`).join('')+'</div>';
  }
  activity.querySelector('.ss-activity-foot').textContent=model.period==='today'?'История блоков · время окончания по Москве':'Высота / цвет — время по таймеру. Нажми на день для подробностей.';
  body.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{model.selected=model.selected===b.dataset.day?null:b.dataset.day;renderActivity();renderDetails();});
 }
 function renderDetails(){
  if(!details)return;
  details.innerHTML=`<div class="ss-panel-head"><span class="ss-panel-title">Разбор учёбы</span><span class="ss-muted">${model.selected?esc(model.selected)+' <button class="ss-day-reset" title="Сбросить выбранный день">×</button>':({today:'Сегодня',week:'7 дней',month:'30 дней',year:'365 дней'})[model.period]}</span></div><div class="ss-tabs" role="tablist" aria-label="Подробности фокуса"><button role="tab" data-tab="apps">Приложения</button><button role="tab" data-tab="blocks">Блоки</button><button role="tab" data-tab="progress">Прогресс</button></div><div class="ss-detail-body"></div>`;
  details.querySelectorAll('[data-tab]').forEach(b=>{b.setAttribute('aria-selected',String(b.dataset.tab===model.tab));b.onclick=()=>{model.tab=b.dataset.tab;renderDetails();};});
  const reset=details.querySelector('.ss-day-reset');if(reset)reset.onclick=()=>{model.selected=null;renderActivity();renderDetails();};
  const body=details.querySelector('.ss-detail-body'),records=rangeRecords();
  if(model.tab==='blocks'){body.innerHTML=recordRows(records);return;}
  if(model.tab==='progress'){
   const completed=records.filter(r=>r.outcome==='completed').length,interrupted=records.filter(r=>r.outcome==='interrupted').length;
  const earned=records.filter(r=>r.outcome!=='repair');
  const seconds=earned.reduce((s,r)=>s+duration(r),0),attempts=earned.reduce((s,r)=>s+r.attempts,0);
  const activeDays=new Set(earned.filter(r=>duration(r)>0).map(r=>r.day)).size;
   body.innerHTML=`<div class="ss-summary-grid"><div class="ss-summary-tile"><strong>${completed}</strong><span class="ss-muted">полных блоков</span></div><div class="ss-summary-tile"><strong>${activeDays?fmt(seconds/activeDays):'—'}</strong><span class="ss-muted">на день с учёбой</span></div><div class="ss-summary-tile"><strong>${seconds?((attempts/seconds)*3600).toFixed(1).replace('.',','):'—'}</strong><span class="ss-muted">срабатываний защиты / ч</span></div><div class="ss-summary-tile"><strong>${interrupted}</strong><span class="ss-muted">прервано</span></div></div><div class="ss-muted">Цель — 5 полных блоков в учебный день. Серия считается по дням с полным блоком. Повторные срабатывания защиты считаются отдельно. Завершение блока не означает выполнение задачи.</div>`;return;
  }
  const apps={};Object.entries(model.data.usage.days||{}).filter(([key])=>model.selected?key===model.selected:inRange(key)).forEach(([,day])=>Object.entries(day).forEach(([id,row])=>{apps[id]??={name:row.name,seconds:0};apps[id].seconds+=row.seconds;}));
  const sorted=Object.values(apps).sort((a,b)=>b.seconds-a.seconds),total=sorted.reduce((s,r)=>s+r.seconds,0);
  body.innerHTML=(sorted.length?`<div class="ss-muted">На экране в блоках · ${fmt(total)}</div>`+sorted.map((row,i)=>`<div class="ss-usage-row"><div class="ss-usage-head"><span title="${esc(row.name)}">${esc(row.name)}</span><span>${fmt(row.seconds)} · ${Math.round(row.seconds/total*100)}%</span></div><div class="ss-track"><div style="width:${row.seconds/total*100}%"></div></div></div>`).join(''):'<div class="ss-empty">Экранное время начнёт собираться в следующем блоке.<br>Для прошлых блоков доступны время таймера и история.</div>')+`<div class="ss-muted">${model.data.tracking?'Учёт включён':'Учёт выключен'} · оценка по переднему приложению, шаг 5 секунд, только во время фокуса и при открытом Anki. Лекции без нажатий тоже учитываются. Это время на экране, а не оценка внимания. Отключение — в «Инструментах».</div>`;
 }
 function render(){
  document.documentElement.dataset.slovoMode=model.mode;
  bar.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===model.mode)));
  bar.querySelector('.ss-period').style.display=model.mode==='focus'?'':'none';
  bar.querySelector('.ss-caption').textContent=model.mode==='focus'?'Вся учёба · блоки, часы и приложения':'Повторения карточек Anki';
  bar.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period===model.period)));
  if(model.mode==='focus'){renderMetrics();renderActivity();renderDetails();}
 }
 bar.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{model.mode=b.dataset.mode;render();pycmd('slovo:stats-mode:'+model.mode);});
 bar.querySelectorAll('[data-period]').forEach(b=>b.onclick=()=>{model.period=b.dataset.period;model.selected=null;render();});
 window.slovoFocusStats={grid,alive:()=>[...metrics,activity,details].filter(Boolean).every(el=>el.isConnected),update(data){model.data=data;const key=JSON.stringify([data.today,data.records.map(r=>[r.id,r.outcome]),Math.floor(data.records.reduce((s,r)=>s+duration(r),0)/15),Math.floor(Object.values(data.usage.days||{}).flatMap(d=>Object.values(d)).reduce((s,r)=>s+r.seconds,0)/15),data.tracking]);if(key!==this.key){this.key=key;render();}}};
 render();
})();
