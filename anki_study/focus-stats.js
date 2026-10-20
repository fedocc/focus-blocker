/* Focus mode leaves Anki's original widgets mounted and restores them unchanged. */
(() => {
 const grid=document.querySelector('.unified-grid');if(!grid)return;
 if(!document.getElementById('slovo-stats-css')){
  const css=document.createElement('link');css.id='slovo-stats-css';css.rel='stylesheet';css.href='/_addons/ADDON_PACKAGE/focus-stats.css';document.head.append(css);
 }
 if(window.slovoFocusStats?.grid===grid&&window.slovoFocusStats.alive()){window.slovoFocusStats.update(PAYLOAD);return;}
 document.getElementById('slovo-stats-switch')?.remove();
 grid.querySelectorAll('.slovo-focus-replacement').forEach(el=>el.remove());
 const model={data:PAYLOAD,mode:PAYLOAD.mode||'focus',period:'today',view:'year',tab:'apps',selected:null,expanded:new Set(),detailScroll:{}};
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fmt=s=>s>=3600?Math.floor(s/3600)+' ч'+(Math.floor(s%3600/60)?' '+Math.floor(s%3600/60)+' мин':''):Math.floor(s/60)+' мин';
 const date=n=>new Date(n+'T12:00:00+03:00');
 const shift=(n,days)=>{const d=date(n);d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10);};
 const replacement=(original,kind)=>{
  if(!original)return null;
  original.classList.add('slovo-original-stats');
  const element=kind==='ss-metric'?original.cloneNode(true):original.cloneNode(false);
  element.removeAttribute('id');element.classList.remove('slovo-original-stats');
  element.classList.add('slovo-focus-replacement',kind);original.after(element);return element;
 };
 const bar=document.createElement('div');bar.id='slovo-stats-switch';
 bar.innerHTML='<div class="ss-segments" role="group" aria-label="Режим статистики"><button data-mode="cards">Карточки</button><button data-mode="focus">◉ Фокус</button></div><span class="ss-caption"></span><div class="ss-segments ss-period" role="group" aria-label="Период статистики"><button data-period="today">Сегодня</button><button data-period="week">7 дней</button><button data-period="month">30 дней</button><button data-period="year">Год</button></div>';
 grid.before(bar);
 const metrics=['studied','time','pace','retention'].map(id=>replacement(grid.querySelector('.onigiri-stat-card[data-stat="'+id+'"]'),'ss-metric'));
 const activity=replacement(grid.querySelector('#onigiri-heatmap-container'),'ss-native-activity');
 if(activity)activity.id='slovo-focus-heatmap';
 // Reuse the installed theme's actual rules, including its user-chosen variables.
 // The original calendar keeps its ID and its own data and handlers.
 const calendarStyle=document.createElement('style');calendarStyle.id='slovo-native-calendar-style';
 document.getElementById(calendarStyle.id)?.remove();
 function calendarRules(rules){return [...rules].map(rule=>{
  if(rule.selectorText)return rule.selectorText.includes('#onigiri-heatmap-container')?rule.cssText.replaceAll('#onigiri-heatmap-container','#slovo-focus-heatmap'):'';
  if(rule.cssRules){const content=calendarRules(rule.cssRules);return content?rule.cssText.slice(0,rule.cssText.indexOf('{')+1)+content+'}':'';}
  return '';
 }).join('\n');}
 calendarStyle.textContent=[...document.styleSheets].map(sheet=>{try{return calendarRules(sheet.cssRules);}catch(_){return '';}}).join('\n');
 document.head.append(calendarStyle);
 const details=replacement(grid.querySelector('.learner-stats-widget[data-widget-id="deck_stats"]'),'ss-panel');
 const periodDays=()=>({today:1,week:7,month:30,year:365})[model.period];
 const inRange=key=>key>=shift(model.data.today,1-periodDays())&&key<=model.data.today;
 const rangeRecords=()=>model.data.records.filter(r=>model.selected?r.day===model.selected:inRange(r.day));
 const duration=r=>Math.max(0,Number(r.seconds)||0);
 function aggregate(){
  const rows=Object.entries(model.data.days).filter(([key])=>inRange(key)).map(([,row])=>row);
  return rows.reduce((s,r)=>({seconds:s.seconds+r.seconds,overtime:s.overtime+(r.overtime_seconds||0),completed:s.completed+r.completed,interrupted:s.interrupted+r.interrupted,attempts:s.attempts+r.attempts}),{seconds:0,overtime:0,completed:0,interrupted:0,attempts:0});
 }
 function spark(values){
  const max=Math.max(1,...values),points=values.map((v,i)=>`${i/(values.length-1)*100},${26-v/max*24}`);
  const line='M '+points.join(' L ');
  return `<svg class="stat-spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true"><path class="stat-spark-fill" d="${line} L 100,28 L 0,28 Z"></path><path class="stat-spark-line" d="${line}"></path></svg>`;
 }
 function renderMetrics(){
  const a=aggregate(),finished=a.completed+a.interrupted,percent=finished?Math.round(a.completed/finished*100):null;
  const seven=Array.from({length:7},(_,i)=>model.data.days[shift(model.data.today,i-6)]||{seconds:0,completed:0,interrupted:0,attempts:0});
  const values=[
   ['✓','Блоки',a.completed,'бл.','Завершены полностью · длительность зависит от режима',seven.map(r=>r.completed),'#38d2b1'],
   ['◷','Время',(a.seconds/3600).toFixed(1).replace('.',','),'ч','По таймеру · включая текущий блок'+(a.overtime?' · дополнительно '+fmt(a.overtime):''),seven.map(r=>r.seconds),'#5ab8ed'],
   ['↗','Блокировки',a.attempts,'раз','Срабатывания защиты сайтов и приложений',seven.map(r=>r.attempts),'#e3bf86'],
   ['◇','Завершение',percent===null?'—':percent+'%','','Доля полных блоков · '+a.interrupted+' прервано',seven.map(r=>r.completed+r.interrupted?r.completed/(r.completed+r.interrupted)*100:0),'#c991df']
  ];
  metrics.forEach((el,i)=>{
   if(!el)return;const[icon,title,value,unit,note,series]=values[i];
   el.title=note;
   const heading=el.querySelector('h3');if(heading)heading.textContent=title;
   const body=el.querySelector('.stat-body');
   if(body)body.innerHTML=`<p class="stat-value">${value}${unit?'<span class="stat-unit">'+unit+'</span>':''}</p>`;
   el.querySelector('.stat-spark')?.remove();
   if(el.classList.contains('has-trend'))el.insertAdjacentHTML('beforeend',spark(series));
  });
 }
 function recordRows(records){
  if(!records.length)return '<div class="ss-empty">Пока нет блоков за этот период.<br>Заверши первый блок — здесь появится история учёбы.</div>';
  return '<div class="ss-timeline">'+[...records].sort((a,b)=>b.ended_at-a.ended_at).map(r=>{
   const time=new Date(r.ended_at*1000).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'});
   const status={completed:'Завершён',interrupted:'Прерван',live:'Сейчас',repair:'Ремонт службы · вне показателей'}[r.outcome];
   const extra=Math.max(0,Number(r.overtime_seconds)||0);
   const note=extra?' · дополнительно '+fmt(extra):'';
   return `<div class="ss-record" title="${esc(r.tasks.join(' · ')+note)}"><span class="ss-record-time">${time}</span><span class="ss-record-title">${esc(r.title)}<br><span class="ss-record-status ${r.outcome}">${status} · ${esc(r.day.slice(5))}${note}</span></span><span>${fmt(duration(r))}</span></div>`;
  }).join('')+'</div>';
 }
 function decorateCalendar(){
  const active=activity?.querySelector('.filter-btn.active');if(active)model.view=active.dataset.view;
  activity?.querySelectorAll('.heatmap-day-cell').forEach(cell=>{
   const key=cell.dataset.dateKey||(cell.classList.contains('today')?model.data.today:null);
   const seconds=key?(model.data.days[key]?.seconds||0):Number(cell.dataset.reviewCount||0)*60;
   const text=(key?key+' · ':'')+fmt(seconds)+(key?' · '+(model.data.days[key]?.completed||0)+' блоков':'');
   cell.dataset.tooltip=text;cell.setAttribute('aria-label',text);
   if(key)cell.dataset.focusDay=key;
  });
 }
 if(activity){
  new MutationObserver(decorateCalendar).observe(activity,{childList:true,subtree:true});
  const choose=event=>{
   const cell=event.target.closest('.heatmap-day-cell');
   if(!cell)return;
   if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;
   event.preventDefault();event.stopImmediatePropagation();
   const key=cell.dataset.focusDay||cell.dataset.dateKey;
   if(key){model.selected=model.selected===key?null:key;renderDetails();}
  };
  activity.addEventListener('click',choose,true);activity.addEventListener('keydown',choose,true);
 }
 function renderActivity(){
  if(!activity)return;
  if(!window.OnigiriHeatmap?.render){activity.textContent='Календарь загружается…';return;}
  const calendar=Object.fromEntries(Object.entries(model.data.days).map(([key,row])=>[key,Math.round(row.seconds/60)]));
  const values=Object.values(calendar).filter(value=>value>0);
  let longest=0,run=0,previous=null;
  Object.keys(model.data.days).sort().forEach(key=>{
   if(!model.data.days[key].completed){run=0;previous=null;return;}
   run=previous&&shift(previous,1)===key?run+1:1;longest=Math.max(longest,run);previous=key;
  });
  const config={...(window.onigiriHeatmapConfig||{}),heatmapDefaultView:model.view,heatmapParticlesAnimation:false,
   i18n:{...(window.onigiriHeatmapConfig?.i18n||{}),activity:'Активность фокуса'}};
  window.OnigiriHeatmap.render(activity,{calendar,due_calendar:{},today_date_key:model.data.today,
   daily_average:values.length?values.reduce((sum,value)=>sum+value,0)/values.length:80,
   streak:model.data.streak,longest_streak:longest,
   firstYear:Number((Object.keys(calendar).sort()[0]||model.data.today).slice(0,4))},config);
  decorateCalendar();
 }
 function aggregateUsage(days,accept){
  const apps={};
  Object.entries(days||{}).sort(([a],[b])=>a.localeCompare(b)).filter(([key])=>accept(key)).forEach(([,day])=>{
   Object.entries(day).forEach(([id,row])=>{
    const app=apps[id]??={id,name:row.name,seconds:0,details:{}};app.seconds+=row.seconds;
    Object.entries(row.details||{}).forEach(([key,item])=>{
     const detail=app.details[key]??={...item,seconds:0};
     detail.name=item.name;detail.group=item.group;detail.seconds+=item.seconds;
    });
   });
  });
  return Object.values(apps).sort((a,b)=>b.seconds-a.seconds||a.name.localeCompare(b.name));
 }
 function usageGroups(row){
  const groups={};
  Object.entries(row.details||{}).filter(([,item])=>item.seconds>0).forEach(([id,item])=>{
   const key=item.kind+':'+item.group;
   const group=groups[key]??={key,name:item.group,kind:item.kind,seconds:0,items:[]};
   group.seconds+=item.seconds;group.items.push({...item,id});
  });
  return Object.values(groups).sort((a,b)=>b.seconds-a.seconds);
 }
 const usageFmt=s=>s<60?Math.floor(s)+' с':fmt(s);
 function usageMarkup(row,total){
  const groups=usageGroups(row),known=groups.reduce((sum,g)=>sum+g.seconds,0);
  const head=`<div class="ss-usage-head"><span class="ss-app-title">${groups.length?'<i class="ss-chevron" aria-hidden="true">›</i>':''}<span title="${esc(row.name)}">${esc(row.name)}</span></span><span class="ss-usage-time">${usageFmt(row.seconds)} · ${total?Math.round(row.seconds/total*100):0}%</span></div><div class="ss-track"><div style="width:${total?row.seconds/total*100:0}%"></div></div>`;
  if(!groups.length)return `<div class="ss-app-usage">${head}</div>`;
  const itemMarkup=item=>`<div class="ss-usage-item"><span title="${esc(item.name)}">${esc(item.name)}</span><span class="ss-usage-time">${usageFmt(item.seconds)}</span></div>`;
  const content=groups.map(group=>{
   if(group.kind!=='site')return `<div class="ss-usage-group-title">${esc(group.name)}</div>`+group.items.sort((a,b)=>b.seconds-a.seconds).map(itemMarkup).join('');
   const key=row.id+'|'+group.key;
   return `<details class="ss-site-usage" data-expand="${esc(key)}"${model.expanded.has(key)?' open':''}><summary class="ss-usage-head"><span class="ss-app-title"><i class="ss-chevron" aria-hidden="true">›</i>${esc(group.name)}</span><span class="ss-usage-time">${usageFmt(group.seconds)}</span></summary><div class="ss-page-list">${group.items.sort((a,b)=>b.seconds-a.seconds).map(itemMarkup).join('')}</div></details>`;
  }).join('');
  const status=model.data.detail_status?.[row.id];
  const supported=(model.data.detail_supported||[]).includes(row.id);
  const note=model.data.detail_messages?.[status]||(!supported?'Для этого приложения учитывается общее время.':'Подробности появятся в новых блоках после разрешения доступа.');
  const unknown=Math.max(0,row.seconds-known);
  return `<details class="ss-app-usage" data-expand="${esc(row.id)}"${model.expanded.has(row.id)?' open':''}><summary>${head}</summary><div class="ss-usage-children">${content}${unknown?`<div class="ss-usage-item ss-unassigned"><span>Без детализации</span><span class="ss-usage-time">${usageFmt(unknown)}</span></div>`:''}${status&&status!=='ready'?`<div class="ss-muted ss-usage-note">${esc(note)}</div>`:''}</div></details>`;
 }
 function restoreDetails(body,focusKey){
  body.scrollTop=model.detailScroll[model.tab]||0;
  if(focusKey){const node=[...body.querySelectorAll('details[data-expand]')].find(el=>el.dataset.expand===focusKey);node?.querySelector('summary')?.focus({preventScroll:true});}
 }
 function renderDetails(){
  if(!details)return;
  const previous=details.querySelector('.ss-detail-body');
  if(previous)model.detailScroll[previous.dataset.tab]=previous.scrollTop;
  const focusKey=details.contains(document.activeElement)?document.activeElement?.closest('details[data-expand]')?.dataset.expand:null;
  details.querySelectorAll('details[data-expand]').forEach(el=>{if(el.open)model.expanded.add(el.dataset.expand);else model.expanded.delete(el.dataset.expand);});
  details.innerHTML=`<div class="ss-panel-head"><span class="ss-panel-title">Разбор учёбы</span><span class="ss-muted">${model.selected?esc(model.selected)+' <button class="ss-day-reset" title="Сбросить выбранный день">×</button>':({today:'Сегодня',week:'7 дней',month:'30 дней',year:'365 дней'})[model.period]}</span></div><div class="ss-tabs" role="tablist" aria-label="Подробности фокуса"><button role="tab" data-tab="apps">Приложения</button><button role="tab" data-tab="blocks">Блоки</button><button role="tab" data-tab="progress">Прогресс</button></div><div class="ss-detail-body"></div>`;
  details.querySelectorAll('[data-tab]').forEach(b=>{b.setAttribute('aria-selected',String(b.dataset.tab===model.tab));b.onclick=()=>{model.tab=b.dataset.tab;renderDetails();};});
  const reset=details.querySelector('.ss-day-reset');if(reset)reset.onclick=()=>{model.selected=null;renderActivity();renderDetails();};
  const body=details.querySelector('.ss-detail-body'),records=rangeRecords();
  body.dataset.tab=model.tab;
  if(model.tab==='blocks'){body.innerHTML=recordRows(records);restoreDetails(body,focusKey);return;}
  if(model.tab==='progress'){
   const completed=records.filter(r=>r.outcome==='completed').length,interrupted=records.filter(r=>r.outcome==='interrupted').length;
  const earned=records.filter(r=>r.outcome!=='repair');
  const seconds=earned.reduce((s,r)=>s+duration(r),0),attempts=earned.reduce((s,r)=>s+r.attempts,0);
  const activeDays=new Set(earned.filter(r=>duration(r)>0).map(r=>r.day)).size;
   body.innerHTML=`<div class="ss-summary-grid"><div class="ss-summary-tile"><strong>${completed}</strong><span class="ss-muted">полных блоков</span></div><div class="ss-summary-tile"><strong>${activeDays?fmt(seconds/activeDays):'—'}</strong><span class="ss-muted">на день с учёбой</span></div><div class="ss-summary-tile"><strong>${seconds?((attempts/seconds)*3600).toFixed(1).replace('.',','):'—'}</strong><span class="ss-muted">срабатываний защиты / ч</span></div><div class="ss-summary-tile"><strong>${interrupted}</strong><span class="ss-muted">прервано</span></div></div><div class="ss-muted">Сравнивай часы: блоки могут длиться 20, 25, 30 или 80 минут. Серия считается по дням с полным блоком. Повторные срабатывания защиты считаются отдельно. Завершение блока не означает выполнение задачи.</div>`;restoreDetails(body,focusKey);return;
  }
  const sorted=aggregateUsage(model.data.usage.days,key=>model.selected?key===model.selected:inRange(key)),total=sorted.reduce((s,r)=>s+r.seconds,0);
  body.innerHTML=(sorted.length?`<div class="ss-muted">На экране в блоках · ${fmt(total)}</div>`+sorted.map(row=>usageMarkup(row,total)).join(''):'<div class="ss-empty">Экранное время начнёт собираться в следующем блоке.<br>Для прошлых блоков доступны время таймера и история.</div>')+`<button type="button" class="ss-detail-settings">Детализация · доступ и настройки →</button><div class="ss-muted">${model.data.tracking?'Учёт включён':'Учёт выключен'} · ${model.data.detail_tracking?'подробности включены':'подробности выключены или недоступны'}. Шаг 5 секунд, только переднее приложение в учебных блоках при открытом Anki. Лекции без нажатий учитываются. Без детализации — старые данные, переходы вкладок или отсутствие доступа. Это время на экране, а не оценка внимания.</div>`;
  body.querySelector('.ss-detail-settings').onclick=()=>pycmd('slovo:details-settings');
  restoreDetails(body,focusKey);
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
 bar.querySelectorAll('[data-period]').forEach(b=>b.onclick=()=>{model.period=b.dataset.period;model.view={today:'week',week:'week',month:'month',year:'year'}[model.period];model.selected=null;render();});
 window.slovoFocusStats={grid,alive:()=>[...metrics,activity,details].filter(Boolean).every(el=>el.isConnected),update(data){model.data=data;const key=JSON.stringify([data.today,data.records.map(r=>[r.id,r.outcome]),Math.floor(data.records.reduce((s,r)=>s+duration(r),0)/15),Math.floor(Object.values(data.usage.days||{}).flatMap(d=>Object.values(d)).reduce((s,r)=>s+r.seconds,0)/15),data.tracking,data.detail_tracking,data.detail_status]);if(key!==this.key){this.key=key;render();}}};
 render();
})();
