'use strict';
const ui = window.BookingUI;
const find = id => document.getElementById(id);
const support = CommunicationUI.mountSupport(find('client-support'),'client');
const chat=CommunicationUI.mountChat(find('client-appointments'),find('client-chat'),'client');
const notices=CommunicationUI.mountNotifications(find('client-notifications'),'client',async (item,isCurrent)=>{
  if(item.target_kind==='support'){showView('support');await support.open(item.target_id);return;}
  await refresh(isCurrent);if(!isCurrent())return;showView('meetings');
  if(item.kind==='appointment_message'){chat.open(item.target_id,'Переписка по встрече');find('client-chat').scrollIntoView({block:'nearest'});}
  else {const card=find('client-appointments').querySelector(`[data-appointment="${CSS.escape(item.target_id)}"]`);if(!card)throw Error('Встреча не найдена. Обновите кабинет.');card.tabIndex=-1;card.focus();card.scrollIntoView({block:'nearest'});}
});
let mode = 'register', user = null, draft = null;
try {draft = JSON.parse(sessionStorage.getItem('otklik-booking-draft') || 'null');} catch {}
if (!draft || typeof draft.psychologist_id !== 'string' || !Number.isFinite(Date.parse(draft.start_at)) || typeof draft.request_key !== 'string') draft = null;
function message(text='') {find('client-message').textContent = text;}
function switchMode(next) {
  mode = next;
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
  const data = await ui.api('/api/client/appointments');
  if(!isCurrent())return;
  find('client-appointments').innerHTML = ui.cards(data.appointments, 'client');
  calendarBookings = data.appointments.filter(item => ['pending', 'confirmed'].includes(item.status));
  const upcoming = calendarBookings.filter(item => Date.parse(item.start_at) > Date.now()).sort((a,b) => Date.parse(a.start_at) - Date.parse(b.start_at))[0];
  find('client-next').textContent = upcoming ? upcoming.psychologist_name + ' · ' + ui.date(upcoming.start_at) : 'Пока нет предстоящих встреч';
  if (!calendarMonth) {
    selectedDay = moscowDate(upcoming ? new Date(upcoming.start_at) : new Date());
    calendarMonth = new Date(selectedDay.slice(0, 7) + '-01T12:00:00Z');
  }
  refreshCalendars();
}
async function boot() {
  find('booking-draft').hidden = !draft;
  if (draft) find('draft-summary').innerHTML = `<h2>${ui.escape(draft.name)}</h2><p>${ui.date(draft.start_at)} · 50 минут</p><p>Стоимость при выборе: ${ui.escape(draft.price)} ₽</p><p>Видеовстреча. Оплата пока не подключена.</p>`;
  try {user = (await ui.api('/api/client/me')).client;} catch (error) {if (error.status !== 401) {message(error.message); return;} user = null;CommunicationUI.reset('client');}
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
  }
  if (user) {support.start();notices.start();find('client-greeting').textContent = `${user.name}, ваши встречи`; await refresh();}
}
find('client-register-tab').addEventListener('click', () => switchMode('register'));
find('client-login-tab').addEventListener('click', () => switchMode('login'));
find('client-auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = find('client-auth-submit'); button.disabled = true; message();
  try {
    await ui.api(`/api/client/${mode}`, {name:find('client-name').value,email:find('client-email').value,password:find('client-password').value,...(mode === 'register' ? {age:Number(find('client-age').value)} : {})});
    find('client-password').value = '';
    CommunicationUI.reset('client');
    await boot();
    if (draft) {message('Вы вошли. Проверьте время и отправьте заявку.'); find('book').focus();}
  } catch (error) {message(error.message);} finally {button.disabled = false;}
});
find('book').addEventListener('click', async () => {
  if (!draft) return;
  const button = find('book'); button.disabled = true; message();
  try {
    const data = await ui.api('/api/client/appointments', draft);
    sessionStorage.removeItem('otklik-booking-draft');
    sessionStorage.removeItem('otklik-flow-answers');
    draft = null;
    await boot();
    showView('meetings');
    message(data.appointment.status === 'confirmed' ? 'Встреча подтверждена. Она появилась в вашем кабинете.' : data.appointment.status === 'pending' ? 'Заявка отправлена. Психолог ответит в течение 12 часов, но не позднее начала встречи.' : 'Заявка уже была отправлена. Её текущий статус показан ниже.');
  } catch (error) {message(error.message); if (error.status === 401) await boot();} finally {button.disabled = false;}
});
find('discard-draft').addEventListener('click', () => {sessionStorage.removeItem('otklik-booking-draft'); location.href = '/';});
find('client-logout').addEventListener('click', async () => {
  try {await ui.api('/api/client/logout', {}); CommunicationUI.reset('client'); await boot(); message('Вы вышли из кабинета.');} catch (error) {message(error.message);}
});
find('client-refresh').addEventListener('click', () => {message(); refresh().catch(error => message(error.message));});
function showView(view) {
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
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    await ui.api('/api/client/settings', {name:form.elements.name.value, age:Number(form.elements.age.value)});
    find('client-greeting').textContent = form.elements.name.value.trim() + ', ваши встречи';
    find('client-settings-message').textContent = 'Настройки сохранены.';
  } catch (error) {
    find('client-settings-message').textContent = error.message;
  } finally {
    button.disabled = false;
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
    entry.textContent = meeting.psychologist_name + ' · ' + ui.date(meeting.start_at);
    detail.append(entry);
  }
}

function renderCalendar(suffix) {
  find('client-month' + suffix).textContent = monthLabel.format(calendarMonth);
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

ui.bindActions(find('client-appointments'),'client',refresh,message);
