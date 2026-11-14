'use strict';
const ui = window.BookingUI;
const find = id => document.getElementById(id);
const support = CommunicationUI.mountSupport(find('client-support'),'client');
const chat=CommunicationUI.mountChat(find('client-appointments'),find('client-chat'),'client');
const notices=CommunicationUI.mountNotifications(find('client-notifications'),'client',async (item,isCurrent)=>{
  if(item.target_kind==='support'){await support.open(item.target_id);return;}
  await refresh(isCurrent);if(!isCurrent())return;
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
}
async function boot() {
  find('booking-draft').hidden = !draft;
  if (draft) find('draft-summary').innerHTML = `<h2>${ui.escape(draft.name)}</h2><p>${ui.date(draft.start_at)} · 50 минут</p><p>Стоимость при выборе: ${ui.escape(draft.price)} ₽</p><p>Видеовстреча. Оплата пока не подключена.</p>`;
  try {user = (await ui.api('/api/client/me')).client;} catch (error) {if (error.status !== 401) {message(error.message); return;} user = null;CommunicationUI.reset('client');}
  find('client-auth').hidden = !!user;
  find('client-workspace').hidden = !user;
  find('book').hidden = !user || !draft;
  if (user) {support.start();notices.start();find('client-greeting').textContent = `${user.name}, ваши встречи`; await refresh();}
}
find('client-register-tab').addEventListener('click', () => switchMode('register'));
find('client-login-tab').addEventListener('click', () => switchMode('login'));
find('client-auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = find('client-auth-submit'); button.disabled = true; message();
  try {
    await ui.api(`/api/client/${mode}`, {name:find('client-name').value,email:find('client-email').value,password:find('client-password').value});
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
    message(data.appointment.status === 'confirmed' ? 'Встреча подтверждена. Она появилась в вашем кабинете.' : data.appointment.status === 'pending' ? 'Заявка отправлена. Психолог ответит в течение 12 часов, но не позднее начала встречи.' : 'Заявка уже была отправлена. Её текущий статус показан ниже.');
  } catch (error) {message(error.message); if (error.status === 401) await boot();} finally {button.disabled = false;}
});
find('discard-draft').addEventListener('click', () => {sessionStorage.removeItem('otklik-booking-draft'); location.href = '/';});
find('client-logout').addEventListener('click', async () => {
  try {await ui.api('/api/client/logout', {}); CommunicationUI.reset('client'); await boot(); message('Вы вышли из кабинета.');} catch (error) {message(error.message);}
});
find('client-refresh').addEventListener('click', () => {message(); refresh().catch(error => message(error.message));});
boot().catch(error => message(error.message));

ui.bindActions(find('client-appointments'),'client',refresh,message);
