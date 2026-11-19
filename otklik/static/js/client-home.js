/* Presentation only: all actions use the authenticated client's existing appointment. */
'use strict';
window.ClientHome = (() => {
 const e=BookingUI.escape;
 const clock=v=>new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(v));
 const fmt=(v,opts)=>new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',...opts}).format(new Date(v));
 function render(item,client){
  const specialist=document.getElementById('client-specialist'),meeting=document.getElementById('client-next');
  document.getElementById('client-greeting').textContent=(client.name||'')+', добрый день';
  document.getElementById('home-today').textContent=fmt(new Date(),{weekday:'long',day:'numeric',month:'long'});
  document.getElementById('client-discover').hidden=!item;
  specialist.hidden=!item;
  if(!item){specialist.replaceChildren();meeting.classList.add('empty-meeting-frame');meeting.innerHTML='<p class="eyebrow">Начнём с вашего запроса</p><h2>Найдите того, с кем можно быть собой</h2><p>Ответьте на несколько вопросов — мы поможем сориентироваться в выборе психолога.</p><a class="primary" href="/">Подобрать психолога</a><p class="micro">Можно пока не знать, о чём хочется поговорить.</p>';return;}
  meeting.classList.remove('empty-meeting-frame');
  const photo=typeof item.psychologist_photo==='string'&&/^data:image\/(png|jpeg|webp);base64,/.test(item.psychologist_photo)?item.psychologist_photo:'';
  const portrait=photo?`<img src="${e(photo)}" alt="Фото ${e(item.psychologist_name)}">`:`<div class="meeting-photo-fallback" aria-label="Фотография пока не добавлена">${e(item.psychologist_name.split(/\s+/).slice(0,2).map(x=>x[0]).join(''))}</div>`;
  specialist.innerHTML=`<div class="meeting-person">${portrait}<div><p class="subtle-label">Ваш психолог</p><h2>${e(item.psychologist_name)}</h2><p class="subtext">${e(item.psychologist_approach||'')}</p><a class="text-button compact" href="/?psychologist=${encodeURIComponent(item.profile_id)}">О специалисте</a></div></div>`;
  const confirmed=item.status==='confirmed';
  const attrs=`data-home-appointment="${e(item.id)}"`;
  const deadline=item.expires_at?BookingUI.date(item.expires_at):'';
  meeting.innerHTML=`<div class="meeting-topline"><p class="eyebrow">Ваша ближайшая встреча</p><span class="meeting-status ${confirmed?'confirmed':''}">${confirmed?'Подтверждена':'Ожидает подтверждения'}</span></div><div class="meeting-date-row"><div class="date-tile"><span>${e(fmt(item.start_at,{month:'long'}))}</span><strong>${e(fmt(item.start_at,{day:'numeric'}))}</strong><span>${e(fmt(item.start_at,{weekday:'long'}))}</span></div><div><p class="meeting-when">${e(fmt(item.start_at,{weekday:'long',day:'numeric',month:'long'}))}</p><p class="meeting-clock">${clock(item.start_at)} <span>МСК</span></p><p class="subtext meeting-duration">Онлайн · 50 минут · ${Number(item.price).toLocaleString('ru-RU')} ₽</p></div></div><div class="meeting-explanation"><strong>${confirmed?'Встреча согласована':'Заявка у психолога'}</strong><p>${confirmed?(item.video_url?'Ссылка для подключения готова.':'Психолог пока не добавил ссылку. Она появится в карточке встречи.'):'Ответ психолога до '+e(deadline)+'. Без подтверждения время освободится.'}</p></div><div class="meeting-actions">${confirmed&&item.video_url?`<a class="primary" href="${e(item.video_url)}" target="_blank" rel="noopener noreferrer">Присоединиться</a>`:`<button class="primary" type="button" ${attrs}>${confirmed?'Открыть встречу':'Посмотреть заявку'}</button>`}<button class="text-button" type="button" ${attrs} data-home-chat>Написать психологу</button></div><p class="meeting-footer">${confirmed?'Все детали и действия доступны в «Моих встречах».':'Пока ждёте ответа, можно спокойно подготовиться к разговору.'}</p>`;
 }
 return {render,clock};
})();
