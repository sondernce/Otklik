const $=s=>document.querySelector(s),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const support=CommunicationUI.mountSupport($('#admin-support'),'admin');
const notices=CommunicationUI.mountNotifications($('#admin-notifications'),'admin',async item=>{if(item.target_kind==='appointment'){openAdminPage('admin-appointments');$('#admin-outcome-filter').value='all';await refresh();return;}openAdminPage('admin-support');return support.open(item.target_id);});
const names={draft:'Черновик',pending:'На рассмотрении',approved:'Одобрен',rejected:'Отклонён'};
let setup=false,profiles=[],selected=null,adminEpoch=0,adminLoad=0;
const accountList=AccountManagement.admin($('#admin-accounts'),()=>{const epoch=adminEpoch;return()=>epoch===adminEpoch&&!$('#workspace').hidden;},message);
const monitor=AdminMonitor.mount(async(kind,id)=>{if(kind==='account'){openAdminPage('admin-accounts');await accountList.refresh();return;}if(kind==='support'){openAdminPage('admin-support');await support.open(id);$('#admin-support').scrollIntoView({block:'start'});return;}await refresh();if(kind==='profile'){openAdminPage('profiles');selected=profiles.find(p=>p.id===id);if(!selected)throw Error('Профиль не найден.');$('#status-filter').value=selected.deleted?'deleted':'all';renderList();detail();$('#detail').scrollIntoView({block:'start'});}else if(kind==='appointment'){openAdminPage('admin-appointments');const card=$('#admin-appointments').querySelector(`[data-appointment="${CSS.escape(id)}"]`);if(!card)throw Error('Встреча не найдена.');card.tabIndex=-1;card.focus();card.scrollIntoView({block:'start'});}else message('Событие регистрации клиента не имеет отдельной карточки.');});
function message(text=''){$('#admin-status').textContent=text;$('#admin-status').className=text?'error-status':'';}
async function api(path,data){return HttpAPI.request(path,data);}
async function boot(){const epoch=++adminEpoch;try{const s=await api('/api/status');if(epoch!==adminEpoch)return;setup=s.setup;$('#auth').hidden=s.authenticated;$('#workspace').hidden=!s.authenticated;$('#auth-title').textContent=setup?'Настроим кабинет':'Вход администратора';$('#auth-hint').textContent=setup?'Задайте пароль не короче 8 символов и сохраните его.':'Введите пароль администратора.';$('#auth-submit').textContent=setup?'Создать доступ':'Войти';$('#password').autocomplete=setup?'new-password':'current-password';if(s.authenticated){accountList.refresh();support.start();notices.start();monitor.start();await refresh();}else {accountList.stop();monitor.stop();profiles=[];selected=null;$('#profiles').replaceChildren();$('#detail').replaceChildren();$('#admin-appointments').replaceChildren();CommunicationUI.reset('admin');}}catch(e){message(e.message);}}
async function refresh(){
  const epoch=adminEpoch,load=++adminLoad,root=$('#admin-appointments'),filter=$('#admin-outcome-filter');
  filter.disabled=true;for(const control of root.querySelectorAll('button,select'))control.disabled=true;
  try{
    const bookings=await BookingUI.api('/api/admin/appointments');if(epoch!==adminEpoch||load!==adminLoad)return;
    root.innerHTML=BookingUI.cards(bookings.appointments.filter(item=>filter.value==='all'||(item.outcome?.state||'unreported')===filter.value),'admin');
    const data=await api('/api/admin/profiles');if(epoch!==adminEpoch||load!==adminLoad)return;
    profiles=data.profiles;selected=profiles.find(p=>p.id===selected?.id)||null;renderList();detail();message();
  }catch(e){if(epoch===adminEpoch&&load===adminLoad)message(e.message);}
  finally{if(epoch===adminEpoch&&load===adminLoad){filter.disabled=false;for(const control of root.querySelectorAll('button,select'))control.disabled=false;}}
}
function renderList(){const f=$('#status-filter').value;const list=profiles.filter(p=>f==='deleted'?p.deleted:!p.deleted&&(f==='all'||p.status===f));$('#count').textContent=`(${list.length})`;$('#profiles').innerHTML=list.length?list.map(p=>`<button class="profile-item ${p.id===selected?.id?'active':''}" data-id="${esc(p.id)}"><b>${esc(p.name||'Анкета без имени')}</b><small>${esc(p.deleted?'Архив':names[p.status])} · ${p.published?'В каталоге':'Не в каталоге'}</small></button>`).join(''):'<p class="subtext">В этом разделе пока нет заявок. Психолог может зарегистрироваться через ссылку «Я психолог» на главной.</p>';}
function profileHistory(p){return (p.history||[]).slice().reverse().map(h=>`<li><small>${esc(new Date(h.at).toLocaleString('ru-RU'))} · ${esc(h.actor)}</small>${esc(h.action)}${h.note?`<p>${esc(h.note)}</p>`:''}</li>`).join('');}
function detail(){const p=selected;if(!p){$('#detail').innerHTML='<h2>Карточка специалиста</h2><p class="subtext">Выберите заявку в списке.</p>';return;}const row=(name,value)=>`<div class="detail-row"><dt>${name}</dt><dd>${esc(value||'Не указано')}</dd></div>`;$('#detail').innerHTML=`<div class="editor-top"><h2>${esc(p.name||'Анкета без имени')}</h2><span class="tag">${esc(p.deleted?'Архив':names[p.status])}</span></div>${p.photo?`<img class="avatar profile-photo" src="${esc(p.photo)}" alt="Фото специалиста">`:''}<p class="notice">${p.account_status==='blocked'?'Аккаунт заблокирован. Назначенные встречи сохранены.':p.published?'Показывается в каталоге':p.deleted?'Профиль в архиве':p.hidden?'Показ ограничен администратором':p.status==='approved'?'Одобрен. Для появления в каталоге нужны будущие окна.':'Не показывается в каталоге до одобрения.'}</p><div class="contact-box"><b>Связаться со специалистом</b><p>${esc(p.email||'Почта не указана')}<br>${esc(p.contact||'Дополнительный контакт не указан')}</p></div><dl>${row('Возраст',p.age?`${p.age} лет`:'Не указан')}${row('Опыт',`${p.experience||0} лет`)}${row('Стоимость',`${p.price} ₽ / 50 минут`)}${row('Подход',p.approach)}${row('Описание',p.bio)}${row('Образование',p.education)}${row('Темы',(p.topics||[]).map(t=>(CatalogOptions.topics[t]||'Тема не указана')).join(', '))}${row('Решение',p.review_note)}</dl><h3>Расписание</h3><p class="subtext">${p.slots.filter(s=>new Date(s)>new Date()).map(s=>esc(new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'medium',timeStyle:'short'}).format(new Date(s)))+' МСК').join('<br>')||'Будущих окон нет. Психолог добавляет их после одобрения.'}</p><label for="decision-note">Комментарий к решению · виден психологу</label><textarea id="decision-note" class="review-comment" maxlength="1500" placeholder="При отклонении обязательно укажите причину"></textarea><div class="detail-actions">${p.status==='pending'&&!p.deleted?'<button class="primary" data-action="approve">Одобрить заявку</button><button class="text-button danger" data-action="reject">Отклонить</button>':''}${!p.deleted?`<button class="text-button" data-action="${p.hidden?'restore':'hide'}">${p.hidden?'Разрешить показ':'Скрыть из каталога'}</button><button class="text-button" id="edit-profile">Редактировать данные</button><button class="text-button danger" data-action="delete">Удалить в архив</button>`:'<button class="primary" data-action="restore">Восстановить профиль</button>'}</div><p class="micro">Удаление переносит профиль в архив и запрещает показ. Его можно восстановить. Одобрение само по себе не добавляет профиль в каталог.</p><div id="edit-area"></div><h3>История действий</h3><ul class="history">${profileHistory(p)||'<li>Профиль из предыдущей версии. Требует рассмотрения.</li>'}</ul>`;}
$('#auth-form').addEventListener('submit',async e=>{e.preventDefault();FormErrors.clear($('#auth-form'));if($('#password').value.length<8||$('#password').value.length>200){FormErrors.show({'#password':'Пароль должен содержать от 8 до 200 символов.'});return;}$('#auth-submit').disabled=true;try{await api(setup?'/api/setup':'/api/login',{password:$('#password').value});$('#password').value='';await boot();message();}catch(e){message();FormErrors.show({'#password':e.message});}finally{$('#auth-submit').disabled=false;}});
$('#logout').addEventListener('click',async()=>{try{adminEpoch++;accountList.stop();monitor.stop();await api('/api/logout',{});CommunicationUI.reset('admin');selected=null;await boot();}catch(e){message(e.message);}});
$('#refresh').addEventListener('click',refresh);$('#status-filter').addEventListener('change',()=>{selected=null;renderList();detail();});
$('#profiles').addEventListener('click',e=>{const b=e.target.closest('[data-id]');if(b){selected=profiles.find(p=>p.id===b.dataset.id);renderList();detail();}});
$('#detail').addEventListener('click',async e=>{const button=e.target.closest('button');if(!button||!selected)return;const action=button.dataset.action;if(action){FormErrors.clear($('#detail'));if(action==='reject'&&!$('#decision-note').value.trim()){message();FormErrors.show({'#decision-note':'Укажите причину отказа — её увидит психолог.'});return;}if(action==='delete'&&!confirm('Перенести профиль в архив? Он исчезнет из каталога, но его можно будет восстановить.'))return;button.disabled=true;try{await api('/api/admin/review',{id:selected.id,revision:selected.revision,action,note:$('#decision-note').value});await refresh();monitor.refresh();}catch(e){message(e.message);button.disabled=false;}return;}if(button.id==='edit-profile'){const fields=[['name','Имя'],['approach','Подход'],['bio','Описание'],['education','Образование']];$('#edit-area').innerHTML=`<form id="edit-form"><p class="notice">Изменения сохраняются в анкете и журнале действий. Решение по заявке принимается отдельно.</p>${fields.map(([key,title])=>`<label>${title}<textarea name="${key}">${esc(selected[key])}</textarea></label>`).join('')}<label>Стоимость, ₽<input name="price" type="number" min="500" max="30000" value="${selected.price}"></label><button class="primary">Сохранить изменения</button></form>`;}});
$('#detail').addEventListener('submit',async e=>{if(e.target.id!=='edit-form')return;e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const data=Object.fromEntries(new FormData(e.target));data.price=Number(data.price);await api('/api/admin/profiles',{...data,id:selected.id,revision:selected.revision});await refresh();monitor.refresh();}catch(e){message();if(!e.field||!FormErrors.show({[`#edit-form [name="${e.field}"]`]:e.message}))message(e.message);b.disabled=false;}});
const adminPages = [
  {id: 'monitor-overview', selector: '#monitor-overview', title: 'Обзор сервиса', description: 'Пользователи, встречи и задачи, которым нужно ваше внимание.'},
  {id:'admin-accounts',selector:'#admin-accounts',title:'Аккаунты',description:'Доступ клиентов и психологов к сервису.'},
  {id: 'profiles', selector: '.admin-profiles-section', title: 'Анкеты специалистов', description: 'Проверяйте заявки и управляйте профилями психологов.'},
  {id: 'admin-appointments', selector: '.admin-meetings-panel', title: 'Встречи', description: 'Записи клиентов, статусы и история изменений.'},
  {id: 'admin-support', selector: '.admin-support-panel', title: 'Поддержка', description: 'Обращения клиентов и психологов.'},
  {id: 'admin-tasks', selector: '#admin-tasks', title: 'Задачи администратора', description: 'Анкеты на проверке и обращения, которые ждут вашего ответа.'},
  {id: 'monitor-journal', selector: '#monitor-journal', title: 'Журнал событий', description: 'Действия и события сервиса за выбранный период.'},
];

function renderAdminPage() {
  const page = adminPages.find(item => location.hash === '#' + item.id) || adminPages[0];
  for (const item of adminPages) $(item.selector).hidden = item.selector !== page.selector;
  if (page.selector === '#monitor-journal') {
    const adminOnly = false;
    const category = $('#monitor-category');
    const categoryValue = adminOnly ? 'admin' : 'all';
    const changed = category.value !== categoryValue || category.disabled !== adminOnly;
    category.value = categoryValue;
    category.disabled = adminOnly;
    $('#monitor-journal h2').textContent = page.title;
    $('#monitor-journal').setAttribute('aria-label', page.title);
    if (changed) {
      $('#monitor-kind').value = '';
      $('#monitor-events').replaceChildren();
      $('#monitor-more').hidden = true;
      $('#monitor-filter').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
    }
  }
  for (const link of document.querySelectorAll('.admin-navigation a[href^="#"]')) {
    if (link.hash === '#' + page.id) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  $('.admin-title h1').textContent = page.title;
  $('.admin-title .subtext').textContent = page.description;
  document.title = page.title + ' — Отклик';
}

function openAdminPage(id) {
  if (location.hash !== '#' + id) window.history.pushState(null, '', '#' + id);
  renderAdminPage();
}

document.querySelector('.admin-navigation').addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]');
  if (!link) return;
  event.preventDefault();
  openAdminPage(link.hash.slice(1));
  window.scrollTo({top: 0, behavior: 'instant'});
  const heading = $('.admin-title h1');
  heading.tabIndex = -1;
  heading.focus({preventScroll: true});
});
document.querySelector('#monitor-tasks').addEventListener('click', event => {
  const link = event.target.closest('a[href="#admin-support"]');
  if (!link) return;
  event.preventDefault();
  openAdminPage('admin-support');
  window.scrollTo({top: 0, behavior: 'instant'});
});
window.addEventListener('hashchange', renderAdminPage);
window.addEventListener('popstate', renderAdminPage);
renderAdminPage();
boot();

BookingUI.bindActions($('#admin-appointments'),'admin',refresh,message,()=>{const epoch=adminEpoch;return()=>epoch===adminEpoch&&!$('#workspace').hidden;});

$('#admin-outcome-filter').addEventListener('change',refresh);
