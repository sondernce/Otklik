'use strict';
const ui = window.BookingUI;
const find = id => document.getElementById(id);
const accountManagement=AccountManagement.mount(find('client-account-management'),'client',async text=>{sessionEpoch++;clearWorkspace();await boot();message(text);});
const emailAccess=EmailAccessUI.mount(find('client-email-access'),'client');
const support = CommunicationUI.mountSupport(find('client-support'),'client');
const chat=CommunicationUI.mountChat(find('client-appointments'),find('client-chat'),'client');
const notices=CommunicationUI.mountNotifications(find('client-notifications'),'client',async (item,isCurrent)=>{
  if(item.target_kind==='support'){showView('support');await support.open(item.target_id);return;}
  await refresh(isCurrent);if(!isCurrent())return;showView('meetings');
  if(item.kind==='appointment_message'){chat.open(item.target_id,'Переписка по встрече');find('client-chat').scrollIntoView({block:'nearest'});}
  else {const card=find('client-appointments').querySelector(`[data-appointment="${CSS.escape(item.target_id)}"]`);if(!card)throw Error('Встреча не найдена. Обновите кабинет.');card.tabIndex=-1;card.focus();card.scrollIntoView({block:'nearest'});}
});
let registrationKey=crypto.randomUUID();
let mode = 'register', user = null, draft = null, sessionEpoch=0, appointmentLoad=0;
function currentSession(){const epoch=sessionEpoch;return()=>epoch===sessionEpoch&&user!==null;}
function clearWorkspace(){
  user=null;accountManagement.stop();emailAccess.stop();CommunicationUI.reset('client');
  find('client-appointments').replaceChildren();find('client-settings').reset();find('client-settings').querySelector('button').disabled=false;
  find('client-settings-message').textContent='';find('client-greeting').textContent='Мои встречи';
  find('client-next').replaceChildren();find('client-specialist').replaceChildren();find('client-specialist').hidden=true;
  calendarBookings=[];calendarMonth=null;selectedDay=null;
  for(const suffix of ['','-large']){find('client-days'+suffix).replaceChildren();find('client-day-details'+suffix).replaceChildren();}
}
try {draft = JSON.parse(sessionStorage.getItem('otklik-booking-draft') || 'null');} catch {}
if (!draft || typeof draft.psychologist_id !== 'string' || !Number.isFinite(Date.parse(draft.start_at)) || typeof draft.request_key !== 'string') draft = null;
function message(text='') {find('client-message').textContent = text;}
function switchMode(next) {
  mode = next;
  FormErrors.clear(find('client-auth-form'));
  find('client-name-label').hidden = mode === 'login';
  find('client-age-label').hidden = mode === 'login';
  find('client-age').required = mode === 'register';
  find('client-name').required = mode === 'register';
  find('client-auth-title').textContent = mode === 'login' ? 'Войти в кабинет' : 'Создать кабинет';
  find('client-auth-submit').textContent = mode === 'login' ? 'Войти' : 'Создать кабинет';
  find('client-password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  find('client-register-tab').setAttribute('aria-pressed', String(mode === 'register'));
  find('client-login-tab').setAttribute('aria-pressed', String(mode === 'login'));
}
async function refresh(isCurrent=()=>true) {
  const session=currentSession(),load=++appointmentLoad;
  if(!session())return;
  let data;
  try{data=await ui.api('/api/client/appointments');}catch(error){if(session()&&isCurrent()&&load===appointmentLoad)throw error;return;}
  if(!session()||!isCurrent()||load!==appointmentLoad)return;
  find('client-appointments').innerHTML = ui.cards(data.appointments, 'client');
  calendarBookings = data.appointments.filter(item => ['pending', 'confirmed'].includes(item.status));
  const upcoming = calendarBookings.filter(item => Date.parse(item.start_at) > Date.now()).sort((a,b) => Date.parse(a.start_at) - Date.parse(b.start_at))[0];
  ClientHome.render(upcoming,user);
  if (!calendarMonth) {
    selectedDay = moscowDate(upcoming ? new Date(upcoming.start_at) : new Date());
    calendarMonth = new Date(selectedDay.slice(0, 7) + '-01T12:00:00Z');
  }
  refreshCalendars();
}
async function boot() {
  const epoch=++sessionEpoch;
  find('booking-draft').hidden = !draft;
  document.body.classList.toggle('has-booking-draft',!!draft);
  if (draft) find('draft-summary').innerHTML = `<h2>${ui.escape(draft.name)}</h2><p>${ui.date(draft.start_at)} · 50 минут</p><p>Стоимость при выборе: ${ui.escape(draft.price)} ₽</p><p>Видеовстреча. Оплата пока не подключена.</p>`;
  try {const data=await ui.api('/api/client/me');if(epoch!==sessionEpoch)return false;if(user&&user.id!==data.client.id)clearWorkspace();user=data.client;}
  catch (error) {if(epoch!==sessionEpoch)return false;if (error.status !== 401) {message(error.message); return false;} clearWorkspace();}
  find('client-auth').hidden = !!user;
  find('client-workspace').hidden = !user;
  find('book').hidden = !user || !draft;
  document.body.classList.toggle('dashboard-active', !!user);
  if (user) {
    find('workspace-messages').append(find('client-message'), find('booking-draft'));
    const form = find('client-settings');
    form.elements.name.value = user.name;
    form.elements.age.value = user.age ?? '';
  } else {
    find('client-auth').before(find('client-message'), find('booking-draft'));
    calendarMonth = null;
    if(location.hash==='#login')switchMode('login');
  }
  if (user) {accountManagement.start();emailAccess.start();showView(['overview','meetings','settings','support'].includes(location.hash.slice(1))?location.hash.slice(1):'overview',false);support.start();notices.start();find('client-greeting').textContent = `${user.name}, добрый день`; await refresh();}
  return epoch===sessionEpoch;
}
find('client-register-tab').addEventListener('click', () => switchMode('register'));
find('client-login-tab').addEventListener('click', () => switchMode('login'));
find('client-auth-form').addEventListener('input',()=>{registrationKey=crypto.randomUUID();});
find('client-auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form=event.currentTarget;FormErrors.clear(form);const errors={};
  if(mode==='register'&&find('client-name').value.trim().length<2)errors['#client-name']='Укажите имя от 2 символов.';
  if(mode==='register'&&(!find('client-age').value||!Number.isInteger(Number(find('client-age').value))||Number(find('client-age').value)<1||Number(find('client-age').value)>120))errors['#client-age']='Укажите возраст от 1 до 120 лет.';
  if(!find('client-email').value||!find('client-email').validity.valid)errors['#client-email']='Укажите корректную электронную почту.';
  if(find('client-password').value.length<8)errors['#client-password']='Пароль должен содержать от 8 символов.';
  if(FormErrors.show(errors))return;
  const epoch=++sessionEpoch;
  const button = find('client-auth-submit'); button.disabled = true;
  form.querySelectorAll('input').forEach(input=>input.readOnly=true);find('client-register-tab').disabled=find('client-login-tab').disabled=true;message();
  try {
    const result=await ui.api(`/api/client/${mode}`, {name:find('client-name').value,email:find('client-email').value,password:find('client-password').value,...(mode === 'register' ? {age:Number(find('client-age').value),request_key:registrationKey} : {})});
    if(epoch!==sessionEpoch)return;
    find('client-password').value = '';
    if(result.verification_required){location.assign('/registration#role=client');return;}
    CommunicationUI.reset('client');
    if(!await boot())return;
    if (draft) {message('Вы вошли. Проверьте время и отправьте заявку.'); find('book').focus();}
  } catch (error) {if(epoch===sessionEpoch){const fields={name:'#client-name',age:'#client-age',email:'#client-email',password:'#client-password'};if(error.field&&fields[error.field])FormErrors.show({[fields[error.field]]:error.message});else if(error.status===409)FormErrors.show({'#client-email':error.message});else if(error.status===401)FormErrors.show({'#client-email':'Проверьте почту.','#client-password':error.message});else message(error.message);}} finally {button.disabled = false;form.querySelectorAll('input').forEach(input=>input.readOnly=false);find('client-register-tab').disabled=find('client-login-tab').disabled=false;}
});
find('book').addEventListener('click', async () => {
  if (!draft) return;
  const isCurrent=currentSession();
  const button = find('book'); button.disabled = true; message();
  try {
    const data = await ui.api('/api/client/appointments', draft);
    if(!isCurrent())return;
    sessionStorage.removeItem('otklik-booking-draft');
    sessionStorage.removeItem('otklik-flow-answers');
    draft = null;
    if(!await boot())return;
    showView('meetings');
    message(data.appointment.status === 'confirmed' ? 'Встреча подтверждена. Она появилась в вашем кабинете.' : data.appointment.status === 'pending' ? 'Заявка отправлена. Психолог ответит в течение 12 часов, но не позднее начала встречи.' : 'Заявка уже была отправлена. Её текущий статус показан ниже.');
  } catch (error) {if(!isCurrent())return;message(error.message); if (error.status === 401) await boot();} finally {button.disabled = false;}
});
find('discard-draft').addEventListener('click', () => {sessionStorage.removeItem('otklik-booking-draft'); location.href = '/';});
find('client-logout').addEventListener('click', async () => {
  const epoch=++sessionEpoch;
  try {await ui.api('/api/client/logout', {});if(epoch!==sessionEpoch)return;clearWorkspace();if(await boot())message('Вы вышли из кабинета.');} catch (error) {if(epoch===sessionEpoch)message(error.message);}
});
find('client-refresh').addEventListener('click', () => {message(); refresh().catch(error => message(error.message));});
function showView(view,updateURL=true) {
  if(!['overview','meetings','settings','support'].includes(view))view='overview';
  if(updateURL&&location.hash!=='#'+view)history.pushState(null,'','#'+view);
  document.querySelectorAll('[data-client-view]').forEach(section => section.hidden = section.dataset.clientView !== view);
  document.querySelectorAll('[data-client-tab]').forEach(button => {
    const selected = button.dataset.clientTab === view;
    button.classList.toggle('active', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
}
document.querySelectorAll('[data-client-tab], [data-client-open]').forEach(button => {
  button.addEventListener('click', () => showView(button.dataset.clientTab || button.dataset.clientOpen));
});
find('client-settings').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const isCurrent=currentSession();
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    await ui.api('/api/client/settings', {name:form.elements.name.value, age:Number(form.elements.age.value)});
    if(!isCurrent())return;
    find('client-greeting').textContent = form.elements.name.value.trim() + ', ваши встречи';
    find('client-settings-message').textContent = 'Настройки сохранены.';
  } catch (error) {
    if(isCurrent())find('client-settings-message').textContent = error.message;
  } finally {
    if(isCurrent())button.disabled = false;
  }
});

const dateKey = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'
});
const monthLabel = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'UTC', month: 'long', year: 'numeric'
});
const dayLabel = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
});
let calendarBookings = [];
let calendarMonth;
let selectedDay;

function moscowDate(date) {
  return dateKey.format(date);
}

function renderDayDetails(suffix) {
  const detail = find('client-day-details' + suffix);
  detail.replaceChildren();
  if (!selectedDay) return;
  const heading = document.createElement('h3');
  heading.textContent = dayLabel.format(new Date(selectedDay + 'T12:00:00Z'));
  detail.append(heading);
  const meetings = calendarBookings.filter(item => moscowDate(new Date(item.start_at)) === selectedDay);
  if (!meetings.length) {
    const empty = document.createElement('p');
    empty.className = 'subtext';
    empty.textContent = 'На этот день встреч нет.';
    detail.append(empty);
  }
  for (const meeting of meetings) {
    const entry = document.createElement('div');
    entry.className = 'client-calendar-meeting';
    entry.innerHTML=`<div class="cal-appointment"><div class="cal-appointment-time"><strong>${ClientHome.clock(meeting.start_at)}</strong><span>50 минут</span></div><div><strong>${ui.escape(meeting.psychologist_name)}</strong><span class="cal-status ${meeting.status==='confirmed'?'confirmed':''}">${meeting.status==='confirmed'?'Подтверждена':'Ждём подтверждения'}</span></div></div><button type="button" class="primary cal-open-meeting" data-home-appointment="${ui.escape(meeting.id)}">Открыть встречу</button>`;
    detail.append(entry);
  }
}

function renderCalendar(suffix) {
  find('client-month' + suffix).textContent = monthLabel.format(calendarMonth).replace(' г.','');
  const grid = find('client-days' + suffix);
  grid.replaceChildren();
  const today = moscowDate(new Date());
  const meetingDays = new Set(calendarBookings.map(item => moscowDate(new Date(item.start_at))));
  const offset = (calendarMonth.getUTCDay() + 6) % 7;
  for (let i = 0; i < offset; i++) grid.append(document.createElement('span'));
  const month = calendarMonth.getUTCMonth();
  for (const date = new Date(calendarMonth); date.getUTCMonth() === month; date.setUTCDate(date.getUTCDate() + 1)) {
    const key = date.toISOString().slice(0, 10);
    const button = document.createElement('button');
    const hasMeeting = meetingDays.has(key);
    button.type = 'button';
    button.dataset.date = key;
    button.textContent = date.getUTCDate();
    button.classList.toggle('has-meeting', hasMeeting);
    button.classList.toggle('weekend',[0,6].includes(date.getUTCDay()));
    button.tabIndex=(key===selectedDay||(!selectedDay.startsWith(calendarMonth.toISOString().slice(0,7))&&date.getUTCDate()===1))?0:-1;
    button.addEventListener('keydown',event=>{const offsets={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7};if(!(event.key in offsets))return;event.preventDefault();const next=new Date(key+'T12:00:00Z');next.setUTCDate(next.getUTCDate()+offsets[event.key]);selectedDay=next.toISOString().slice(0,10);calendarMonth=new Date(selectedDay.slice(0,7)+'-01T12:00:00Z');refreshCalendars();find('client-days'+suffix).querySelector('[data-date="'+selectedDay+'"]').focus();});
    if (key === today) button.setAttribute('aria-current', 'date');
    button.setAttribute('aria-pressed', String(key === selectedDay));
    button.setAttribute('aria-label', dayLabel.format(date) + (hasMeeting ? ', есть встреча' : ''));
    button.addEventListener('click', () => {
      selectedDay = key;
      refreshCalendars();
      find('client-days' + suffix).querySelector('[data-date="' + key + '"]').focus();
    });
    grid.append(button);
  }
  renderDayDetails(suffix);
}

function moveMonth(offset) {
  calendarMonth.setUTCMonth(calendarMonth.getUTCMonth() + offset);
  selectedDay = calendarMonth.toISOString().slice(0, 10);
  refreshCalendars();
}

function refreshCalendars() {
  for (const suffix of ['', '-large']) renderCalendar(suffix);
}

for (const suffix of ['', '-large']) {
  find('month-prev' + suffix).addEventListener('click', () => moveMonth(-1));
  find('month-next' + suffix).addEventListener('click', () => moveMonth(1));
}

boot().catch(error => message(error.message));

ui.bindActions(find('client-appointments'),'client',refresh,message,currentSession);

window.addEventListener('popstate',()=>showView(location.hash.slice(1),false));
window.addEventListener('hashchange',()=>showView(location.hash.slice(1),false));
find('month-today').addEventListener('click',()=>{selectedDay=moscowDate(new Date());calendarMonth=new Date(selectedDay.slice(0,7)+'-01T12:00:00Z');refreshCalendars();});
find('client-workspace').addEventListener('click',event=>{const button=event.target.closest('[data-home-appointment]');if(!button)return;const id=button.dataset.homeAppointment;showView('meetings');if(button.hasAttribute('data-home-chat')){chat.open(id,'Переписка по встрече');find('client-chat').scrollIntoView({block:'nearest'});}else{const card=find('client-appointments').querySelector('[data-appointment="'+CSS.escape(id)+'"]');if(card){card.tabIndex=-1;card.focus();card.scrollIntoView({block:'nearest'});}}});
