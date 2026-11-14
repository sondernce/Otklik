'use strict';
window.BookingUI = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statuses = {pending:'Ожидает подтверждения',confirmed:'Подтверждена',declined:'Отклонена психологом',expired:'Срок подтверждения истёк',cancelled:'Отменена'};
  const date = value => new Intl.DateTimeFormat('ru-RU', {timeZone:'Europe/Moscow',day:'numeric',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value)) + ' МСК';
  async function api(path, data) {
    const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const body = await response.json();
    if (!response.ok) {const error = Error(body.error || 'Не удалось выполнить действие.'); error.status = response.status; throw error;}
    return body;
  }
  function cards(items, role) {
    if (!items.length) return '<p class="subtext">Записей пока нет.</p>';
    return items.map(item => {
      const future = new Date(item.start_at) > new Date();
      const active = future && ['pending','confirmed'].includes(item.status);
      const pending = item.change;
      const attributes = `data-appointment="${escape(item.id)}" data-revision="${item.revision}"`;
      const action = (name,label,extra='') => `<button type="button" class="text-button" data-booking-action="${name}" ${extra}>${label}</button>`;
      let controls = '';
      if (role !== 'admin') {
        controls += `<button type="button" class="text-button" data-chat-appointment="${escape(item.id)}">Переписка</button>`;
        if (role === 'psychologist' && item.status === 'pending' && future) controls += action('confirm','Подтвердить') + action('decline','Отклонить');
        if (active) controls += action('cancel','Отменить встречу');
        if (item.status === 'confirmed' && future && !pending) controls += action('reschedule','Перенести');
        if (pending) controls += pending.offered_by === role ? action('withdraw','Отозвать перенос',`data-change="${escape(pending.id)}"`) : action('accept','Принять перенос',`data-change="${escape(pending.id)}"`) + action('reject-change','Отклонить перенос',`data-change="${escape(pending.id)}"`);
        if (role === 'client') controls += `<a class="text-button" href="/?psychologist=${encodeURIComponent(item.profile_id)}">Записаться ещё раз</a>`;
      }
      const events = {rescheduled:'Время изменено',cancelled:'Встреча отменена',change_proposed:'Предложен перенос',change_declined:'Перенос отклонён',change_cancelled:'Перенос отозван'};
      const history = (item.history || []).map(event => `<li>${events[event.action] || escape(event.action)} · ${event.actor === 'client'?'клиент':'психолог'} · ${date(event.at)}${event.to_start?' → '+date(event.to_start):''}</li>`).join('');
      return `<article class="appointment-card" ${attributes}><h3>${escape(role === 'client' ? item.psychologist_name : item.client_name)}</h3>${role==='admin'?`<p>Психолог: ${escape(item.psychologist_name)}</p>`:''}<p>${date(item.start_at)} · 50 минут</p><p>${escape(item.price)} ₽ · ${statuses[item.status] || escape(item.status)}</p>${item.status==='pending'?`<p class="micro">Ответ психолога до ${date(item.expires_at)}. Без подтверждения время освободится.</p>`:''}${item.status==='cancelled'?`<p class="micro">Отменил ${item.cancelled_by==='client'?'клиент':'психолог'}.</p>`:''}${pending?`<div class="notice"><b>Предложен перенос на ${date(pending.start_at)}</b><p>${pending.offered_by==='client'?'Клиент':'Психолог'} ждёт ответа до ${date(pending.expires_at)}. Пока действует исходная встреча.</p></div>`:''}${controls?`<div class="save-row">${controls}</div>`:''}<div data-change-form></div>${history?`<details><summary>История изменений</summary><ul class="history">${history}</ul></details>`:''}</article>`;
    }).join('');
  }
  function bindActions(container, role, refresh, message) {
    container.addEventListener('click', async event => {
      const button = event.target.closest('[data-booking-action]');
      if (!button) return;
      const card = button.closest('[data-appointment]');
      const action = button.dataset.bookingAction;
      if (action === 'cancel' && !window.confirm('Отменить встречу? Время снова станет доступно для записи. Оплата пока не подключена.')) return;
      const base = `/api/${role}/appointments`;
      const payload = {id:card.dataset.appointment, revision:Number(card.dataset.revision)};
      button.disabled = true;
      try {
        message('');
        if (action === 'reschedule') {
          const options = await api(base+'/reschedule/options', payload);
          const target = card.querySelector('[data-change-form]');
          target.innerHTML = options.slots.length ? `<div class="reschedule-form"><label>Новое время · МСК<select data-new-time>${options.slots.map(value=>`<option value="${escape(value)}">${date(value)}</option>`).join('')}</select></label><p class="micro">До согласования действует исходная встреча. При автоматическом подтверждении психолога ваш перенос применяется сразу. Стоимость сохраняется.</p><button type="button" class="primary" data-booking-action="send-change">Предложить время</button></div>` : '<p class="notice">Других свободных окон пока нет. Исходная встреча сохранена.</p>';
          target.dataset.requestKey = crypto.randomUUID();
          target.dataset.revision = options.revision;
          target.querySelector('select')?.focus();
          return;
        }
        if (action === 'send-change') {
          const form = card.querySelector('[data-change-form]');
          const response = await api(base+'/reschedule/propose', {...payload,revision:Number(form.dataset.revision),start_at:form.querySelector('select').value,request_key:form.dataset.requestKey});
          message(response.appointment.start_at === form.querySelector('select').value ? 'Встреча перенесена.' : 'Предложение отправлено. До ответа действует исходная встреча.');
        } else if (['accept','reject-change','withdraw'].includes(action)) {
          await api(base+'/reschedule/respond', {...payload,change_id:button.dataset.change,action:action==='reject-change'?'decline':action});
          message(action==='accept'?'Встреча перенесена.':'Предложение закрыто. Исходная встреча сохранена.');
        } else if (action === 'cancel') {
          await api(base+'/cancel', payload);
          message('Встреча отменена.');
        } else {
          await api(base+'/decision', {...payload,action});
        }
        await refresh();
      } catch (error) {
        message(error.message);
        try {await refresh();} catch {}
      } finally {button.disabled = false;}
    });
  }
  return {escape, date, api, cards, bindActions};
})();
