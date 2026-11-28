/* Public catalog and questionnaire; booking continues in the client account. */
const questions = [
  {key:'topic',multiple:true,title:'Что хочется обсудить?',hint:'Можно выбрать несколько тем. Покажем психологов, которые работают со всеми выбранными темами.',options:[...Object.entries(CatalogOptions.topics),['unknown','Пока не могу сформулировать']]},
  {key:'budget',title:'Какой бюджет вам комфортен?',hint:'Максимальная стоимость одной встречи · 50 минут.',options:CatalogOptions.budgets},
  {key:'gender',title:'С кем комфортнее говорить?',hint:'Выберите предпочтение или оставьте подбор без ограничения.',options:[['any','Пол специалиста не важен'],['female','Предпочитаю женщину'],['male','Предпочитаю мужчину']]},
  {key:'time',multiple:true,title:'Когда вам удобно?',hint:'Можно выбрать несколько частей дня. Время — московское.',options:CatalogOptions.times},
  {key:'age',title:'Возраст психолога',hint:'Учитываем только возраст, указанный специалистом.',options:CatalogOptions.ages}
];
const selected=key=>Array.isArray(state.answers[key])?state.answers[key]:state.answers[key]?[state.answers[key]]:[];
function validAnswer(q){const values=selected(q.key);return values.length>0&&values.every(value=>q.options.some(([key])=>key===value));}

let people=[];
let catalogError=false;
const safe=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const slotClock=value=>{const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value));return parts.find(p=>p.type==='hour').value.padStart(2,'0')+':'+parts.find(p=>p.type==='minute').value.padStart(2,'0');};
async function loadProfiles(){
 catalogError=false;
 try{
  const data=await HttpAPI.request('/api/profiles');
  people=data.profiles.length?data.profiles.map(p=>({...p,local:true,name:safe(p.name),bio:safe(p.bio),approach:safe(p.approach),education:safe(p.education||''),display_name:p.name,initials:safe(p.initials),slots:p.slots.filter(s=>new Date(s)>new Date()),times:[...new Set(p.slots.filter(s=>new Date(s)>new Date()).map(s=>slotClock(s)))]})):[];
 }catch{catalogError=true;people=[];}
}
function avatar(p){return p.photo?`<img class="avatar profile-photo" src="${safe(p.photo)}" alt="Фото специалиста">`:`<div class="avatar" aria-hidden="true">${p.initials}</div>`;}
function localDays(p){const groups=new Map();for(const slot of p.slots){const d=new Date(slot);const key=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);if(!groups.has(key))groups.set(key,{full:new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'long',weekday:'long',year:'numeric'}).format(d),short:new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'short'}).format(d),times:[],slots:[]});groups.get(key).times.push(slotClock(d));groups.get(key).slots.push(slot);}return [...groups.values()].filter(d=>d.times.some(timeMatches));}
function dayTimes(p){return p.local?availableTimes({...p,times:state.days[state.day]?.times||[]}):availableTimes(p);}
const state={answers:{},person:null,day:0,time:null,days:[]};
try{const saved=JSON.parse(sessionStorage.getItem('otklik-flow-answers')||'{}');for(const q of questions){const values=(Array.isArray(saved[q.key])?saved[q.key]:[saved[q.key]]).filter(value=>q.options.some(([key])=>key===value));if(values.length)state.answers[q.key]=q.multiple?((values.includes('unknown')||values.includes('any'))?[values.includes('unknown')?'unknown':'any']:[...new Set(values)]):values[0];}state.answers.preferences=saved.preferences==='custom'||(!saved.preferences&&((saved.gender&&saved.gender!=='any')||(saved.age&&saved.age!=='any')||(saved.time&&!(Array.isArray(saved.time)?saved.time.includes('any'):saved.time==='any'))))?'custom':'none';}catch{}
state.answers.preferences||='none';state.answers.gender||='any';state.answers.age||='any';state.answers.time||=['any'];
const home=document.querySelector('#home');
const flow=document.querySelector('#flow');
const content=document.querySelector('#flow-content');
const money=n=>new Intl.NumberFormat('ru-RU').format(n)+' ₽';
const topicLabel=key=>questions[0].options.find(o=>o[0]===key)?.[1]||'';
const label=key=>{const q=questions.find(q=>q.key===key);return selected(key).map(value=>q.options.find(o=>o[0]===value)?.[1]||'').filter(Boolean).join(', ');};
function focusHeading(){requestAnimationFrame(()=>{const h=content.querySelector('h2');if(h){h.tabIndex=-1;h.focus({preventScroll:true});}window.scrollTo({top:0,behavior:'instant'});});}
function showFlow(){home.hidden=true;flow.hidden=false;}
function reset(){Object.assign(state,{person:null,day:0,time:null,days:[]});home.hidden=false;flow.hidden=true;content.innerHTML='';window.scrollTo({top:0,behavior:'instant'});document.querySelector('[data-start]').focus({preventScroll:true});}
function flowHeader(backLabel='На главную',action='home'){return `<div class="flow-top"><button class="text-button" data-action="${action}">← ${backLabel}</button><span>Онлайн-встречи · время по Москве</span></div>`;}
function questionField(q,index){return `<fieldset class="questionnaire-section questionnaire-${q.key}" data-question="${q.key}" aria-describedby="hint-${q.key}"><legend>${index?`<span class="questionnaire-number" aria-hidden="true">${index}</span>`:''}${q.title}</legend><p id="hint-${q.key}" class="questionnaire-hint">${q.hint}</p><div class="options">${q.options.map(([value,text])=>`<label class="option"><input type="${q.multiple?'checkbox':'radio'}" name="${q.key}" value="${value}" ${selected(q.key).includes(value)?'checked':''}><span>${text}</span></label>`).join('')}</div><p id="error-${q.key}" class="field-error" role="alert" hidden>${q.multiple?'Выберите хотя бы один вариант.':'Выберите один из вариантов.'}</p></fieldset>`;}
function quiz(){
 showFlow();const details=state.answers.preferences==='custom';
 content.innerHTML=flowHeader()+`<div class="quiz questionnaire"><p class="eyebrow">Подбор психолога</p><h2 id="flow-title">Что важно именно вам?</h2><p class="subtext questionnaire-intro">Выберите темы и бюджет. Предпочтения по специалисту можно уточнить по желанию.</p><form id="quiz-form" novalidate><div class="questionnaire-grid">${questionField(questions.find(q=>q.key==='topic'),'01')}${questionField(questions.find(q=>q.key==='budget'),'02')}<fieldset class="questionnaire-section questionnaire-preferences"><legend><span class="questionnaire-number" aria-hidden="true">03</span>Предпочтения по специалисту</legend><p class="questionnaire-hint">Можно оставить выбор открытым или указать, что для вас важно.</p><div class="options">${[['none','Нет предпочтений'],['custom','Уточнить предпочтения']].map(([value,text])=>`<label class="option"><input type="radio" name="preferences" value="${value}" ${state.answers.preferences===value?'checked':''}><span>${text}</span></label>`).join('')}</div></fieldset></div><div id="specialist-preferences" class="preferences-details" ${details?'':'hidden'}>${['gender','age','time'].map(key=>questionField(questions.find(q=>q.key===key))).join('')}</div><div class="questionnaire-submit"><p class="micro">Подбор без регистрации. Ответы можно поменять и после просмотра психологов.</p><button class="primary" id="next">Показать психологов <span aria-hidden="true">→</span></button></div></form><p class="privacy-note">Ответы сохраняются в этой вкладке, чтобы продолжить запись после входа. Подбор отражает ваши предпочтения и не является оценкой состояния.</p></div>`;focusHeading();
}
function timeMatches(t){if(state.answers.preferences!=='custom')return true;const [h,m]=t.split(':').map(Number),minutes=h*60+m;return selected('time').some(value=>value==='any'||(CatalogOptions.timeRanges[value]&&minutes>=CatalogOptions.timeRanges[value][0]&&minutes<CatalogOptions.timeRanges[value][1]));}
function availableTimes(p){return p.times.filter(timeMatches);}
function ageMatches(p){if(state.answers.preferences!=='custom'||state.answers.age==='any')return true;const range=CatalogOptions.ageRanges[state.answers.age];return range&&Number.isInteger(p.age)&&p.age>=range[0]&&p.age<=range[1];}
function matches(){const a=state.answers;return people.filter(p=>(selected('topic').includes('unknown')||selected('topic').every(topic=>p.topics.includes(topic)))&&p.price<=Number(a.budget)&&(a.preferences!=='custom'||a.gender==='any'||p.gender===a.gender)&&ageMatches(p)&&availableTimes(p).length).sort((a,b)=>a.price-b.price);}

function reasons(p){return [selected('topic').includes('unknown')?'Можно начать без сформулированного запроса':`Темы: ${selected('topic').map(topic=>topicLabel(topic).toLowerCase()).join(', ')}`,'Цена в пределах выбранного бюджета',state.answers.preferences==='custom'&&!selected('time').includes('any')?'Есть свободное время в выбранные части дня':'Есть свободные окна для встречи'].filter(Boolean);}
async function results(){showFlow();content.innerHTML=flowHeader()+'<div class="quiz"><h2 id="flow-title">Ищем варианты…</h2><p class="subtext">Загружаем опубликованные профили.</p></div>';await loadProfiles();if(home.hidden===false)return;const found=matches();content.innerHTML=flowHeader('Изменить ответы','edit')+`<div class="results-intro"><p class="eyebrow">Подбор по вашим предпочтениям</p><h2 id="flow-title">${found.length?'С кем мог бы начаться разговор':(catalogError?'Не удалось загрузить профили':'Пока нет точного совпадения')}</h2><p class="subtext">${found.length?'Изучите профиль и выберите свободное время для видеовстречи.':(catalogError?'Проверьте, что приложение запущено через server.py, и повторите подбор.':'Сейчас нет одобренных профилей с подходящими параметрами и будущими окнами. Мы не меняем ваши ограничения без согласия.')}</p><div class="filters-summary"><span class="chip">${label('topic')}</span><span class="chip">${label('budget')}</span>${state.answers.preferences==='custom'?`<span class="chip">${label('gender')}</span><span class="chip">${label('age')}</span><span class="chip">${label('time')}</span>`:'<span class="chip">Без предпочтений по специалисту</span>'}</div></div>${found.length?`<div class="cards">${found.map(p=>`<article class="card specialist-card"><div class="card-portrait">${avatar(p)}</div><div class="card-body"><span class="demo-label">Онлайн · 50 минут</span><h3>${p.name}</h3><p class="bio">${p.approach}</p><p class="subtext">Практика: ${Number(p.experience)||0} лет${Number.isInteger(p.age)?` · Возраст: ${p.age} лет`:''}</p><div class="price">${money(p.price)} <small>/ встреча</small></div><p class="card-availability">Ближайшее: ${safe(new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(p.slots.find(v=>timeMatches(slotClock(v))))))} МСК</p><button class="primary" data-person="${p.id}">Профиль и время</button></div></article>`).join('')}</div>`:`<div class="empty"><h3>Попробуем другие предпочтения?</h3><p class="subtext">Можно изменить темы, бюджет или снять предпочтения по специалисту.</p><button class="primary" data-action="edit">Изменить ответы</button></div>`}<p class="privacy-note">Совпадение по анкете не гарантирует, что специалист подойдёт. В пилоте предусмотрим возможность обсудить замену.</p>`;focusHeading();}
function makeDays(){const now=new Date();const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);const val=t=>parts.find(p=>p.type===t).value;const start=new Date(`${val('year')}-${val('month')}-${val('day')}T12:00:00+03:00`);return [1,2,3].map(n=>{const d=new Date(start.getTime()+n*86400000);return {full:new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'long',weekday:'long'}).format(d),short:new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'short'}).format(d)};});}
function profile(moveFocus=true){const p=state.person;if(!state.days.length)state.days=p.local?localDays(p):makeDays();content.innerHTML=flowHeader('К рекомендациям','results')+`<div class="profile"><section class="specialist-content"><p class="eyebrow">Знакомство со специалистом</p><div class="specialist-heading"><div class="specialist-portrait">${avatar(p)}</div><div class="specialist-identity"><h2 id="flow-title">${p.name}</h2><p class="specialist-approach">${p.approach}</p><p class="specialist-experience">Практика: ${Number(p.experience)||0} ${{one:'год',few:'года',many:'лет',other:'лет'}[new Intl.PluralRules('ru').select(Number(p.experience)||0)]}</p>${Number.isInteger(p.age)?`<p class="specialist-experience">Возраст: ${p.age} лет</p>`:''}<div class="specialist-education"><h3>Образование</h3><p>${p.education||'Специалист пока не добавил информацию для публичного профиля.'}</p></div></div></div><span class="demo-label">Видеовстреча · 50 минут</span><div class="specialist-section"><h3>О специалисте</h3><p class="specialist-bio">${p.bio}</p></div><div class="specialist-section"><h3>С чем работает</h3><div class="filters-summary">${p.topics.map(t=>`<span class="chip">${topicLabel(t)}</span>`).join('')}</div></div><div class="specialist-section"><h3>Что совпало с вашими ответами</h3><ul class="subtext">${reasons(p).map(r=>`<li>${r}</li>`).join('')}</ul></div><p class="notice">Информация заполнена специалистом. Подбор по анкете не гарантирует, что специалист вам подойдёт.</p></section><div class="panel"><p class="eyebrow">Запись на встречу</p><h3>Выберите удобное время</h3><p class="subtext">Свободное время психолога · МСК (UTC+3)</p><fieldset style="border:0;padding:0;margin:0"><legend>День встречи</legend><div class="days">${state.days.map((d,i)=>`<button class="day ${state.day===i?'active':''}" aria-pressed="${state.day===i}" data-day="${i}">${d.short}</button>`).join('')}</div></fieldset><fieldset style="border:0;padding:0;margin:0"><legend>Время начала</legend><div class="times">${dayTimes(p).map(t=>`<button class="slot ${state.time===t?'active':''}" aria-pressed="${state.time===t}" data-time="${t}">${t}</button>`).join('')}</div></fieldset><p class="price">${money(p.price)} <small>/ 50 минут</small></p><p class="subtext">${p.auto_approve?'Запись подтверждается автоматически.':'Психолог подтвердит заявку в течение 12 часов, но не позднее начала встречи.'} Оплата пока не подключена.</p><button class="primary" id="confirm" data-action="confirm" ${state.time?'':'disabled'}>Продолжить запись <span aria-hidden="true">→</span></button><p id="selection-status" class="micro" aria-live="polite">${state.time?`${state.days[state.day].full}, ${state.time} МСК`:'Сначала выберите время встречи'}</p></div></div>`;if(moveFocus)focusHeading();}
function confirm(){
 if(!state.person||!state.time||!dayTimes(state.person).includes(state.time))return;
 const start=state.days[state.day]?.slots?.find(value=>slotClock(value)===state.time);
 if(!start)return;
 try{
  const old=JSON.parse(sessionStorage.getItem('otklik-booking-draft')||'null');
  const key=old&&old.psychologist_id===state.person.id&&old.start_at===start?old.request_key:crypto.randomUUID();
  sessionStorage.setItem('otklik-flow-answers',JSON.stringify(state.answers));
  sessionStorage.setItem('otklik-booking-draft',JSON.stringify({psychologist_id:state.person.id,start_at:start,request_key:key,name:state.person.display_name||state.person.name,price:state.person.price}));
  location.href='/client#book';
 }catch{const status=document.querySelector('#selection-status');status.textContent='Для продолжения записи разрешите хранение данных сайта в этой вкладке.';}
}
document.querySelectorAll('[data-start]').forEach(b=>b.addEventListener('click',()=>{quiz();}));
document.querySelectorAll('.brand').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();reset();}));
content.addEventListener('change',e=>{
 if(e.target.name==='preferences'){
  state.answers.preferences=e.target.value==='custom'?'custom':'none';
  content.querySelector('#specialist-preferences').hidden=state.answers.preferences!=='custom';
  try{sessionStorage.setItem('otklik-flow-answers',JSON.stringify(state.answers));}catch{}
  return;
 }
 const q=questions.find(q=>q.key===e.target.name);
 if(!q||!q.options.some(([value])=>value===e.target.value))return;
 if(q.multiple){
  let values=selected(q.key);const exclusive=q.key==='topic'?'unknown':'any';
  if(e.target.checked)values=e.target.value===exclusive?[exclusive]:[...values.filter(value=>value!==exclusive&&value!==e.target.value),e.target.value];
  else values=values.filter(value=>value!==e.target.value);
  state.answers[q.key]=values;
  content.querySelectorAll('input[name="'+q.key+'"]').forEach(input=>input.checked=values.includes(input.value));
 }else state.answers[q.key]=e.target.value;
 try{sessionStorage.setItem('otklik-flow-answers',JSON.stringify(state.answers));}catch{}
 const section=e.target.closest('[data-question]');
 if(section){section.classList.remove('questionnaire-invalid');section.removeAttribute('aria-invalid');section.setAttribute('aria-describedby','hint-'+q.key);section.querySelector('.field-error').hidden=true;}
});
content.addEventListener('submit',e=>{
 if(e.target.id!=='quiz-form')return;
 e.preventDefault();let first=null;
 for(const q of questions){
  if(state.answers.preferences!=='custom'&&['gender','age','time'].includes(q.key))continue;
  const section=content.querySelector('[data-question="'+q.key+'"]');
  const valid=validAnswer(q);
  section.classList.toggle('questionnaire-invalid',!valid);section.querySelector('.field-error').hidden=valid;
  if(!valid){section.setAttribute('aria-invalid','true');section.setAttribute('aria-describedby','hint-'+q.key+' error-'+q.key);first||=section;}else{section.removeAttribute('aria-invalid');section.setAttribute('aria-describedby','hint-'+q.key);}
 }
 if(first){first.querySelector('input').focus({preventScroll:true});first.scrollIntoView({block:'center',behavior:'instant'});return;}
 results();
});
content.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.person){state.person=people.find(p=>p.id===b.dataset.person);state.day=0;state.time=null;state.days=[];profile();return;}if(b.dataset.day!==undefined){state.day=Number(b.dataset.day);state.time=null;profile(false);content.querySelector(`[data-day="${state.day}"]`).focus({preventScroll:true});return;}if(b.dataset.time){state.time=b.dataset.time;content.querySelectorAll('[data-time]').forEach(s=>{const active=s.dataset.time===state.time;s.classList.toggle('active',active);s.setAttribute('aria-pressed',String(active));});content.querySelector('#confirm').disabled=false;content.querySelector('#selection-status').textContent=`${state.days[state.day].full}, ${state.time} МСК`;return;}switch(b.dataset.action){case'home':reset();break;case'edit':quiz();break;case'results':results();break;case'profile':profile();break;case'confirm':confirm();break;}});

// Quick topic entry uses the same editable questionnaire as the main button.
document.querySelectorAll('[data-topic]').forEach(button=>button.addEventListener('click',()=>{state.answers.topic=[button.dataset.topic];quiz();}));

// A repeat booking opens the same specialist without restarting the questionnaire.
const repeatId=new URLSearchParams(location.search).get('psychologist');
if(repeatId){
 showFlow();content.innerHTML='<p role="status">Загружаем профиль…</p>';
 loadProfiles().then(()=>{
  state.person=people.find(person=>person.id===repeatId);
  if(state.person){state.answers={topic:'unknown',budget:'30000',gender:'any',age:'any',time:['any'],preferences:'none'};state.days=[];state.day=0;state.time=null;profile();}
  else{content.innerHTML=flowHeader()+'<div class="empty"><h2>Нет свободного времени для новой записи</h2><p>Специалист сейчас не принимает новые записи или ещё не добавил окна. Уже назначенные встречи остаются в вашем кабинете.</p><a class="primary" href="/client">Мои встречи</a></div>';}
 });
}

// Keep the heading stable and quiet for assistive technology and reduced motion.
(()=>{
 const phrases=[...document.querySelectorAll('.hero-phrases>span')];
 if(phrases.length<2)return;
 const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
 let active=0,timer;
 function stop(){clearInterval(timer);}
 function start(){
  stop();
  if(reducedMotion.matches||document.hidden)return;
  timer=setInterval(()=>{
   if(home.hidden)return;
   phrases.forEach(phrase=>phrase.classList.remove('is-leaving'));
   phrases[active].classList.replace('is-active','is-leaving');
   active=(active+1)%phrases.length;
   phrases[active].classList.add('is-active');
  },2500);
 }
 reducedMotion.addEventListener('change',()=>{
  if(reducedMotion.matches){phrases.forEach(phrase=>phrase.classList.remove('is-active','is-leaving'));active=0;phrases[0].classList.add('is-active');}
  start();
 });
 document.addEventListener('visibilitychange',start);
 window.addEventListener('pagehide',stop);
 window.addEventListener('pageshow',start);
 start();
})();
