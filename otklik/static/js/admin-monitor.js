'use strict';
window.AdminMonitor=(()=>{
  const escape=BookingUI.escape,date=BookingUI.date;
  const kinds={registered:'Регистрация',profile_changed:'Изменена анкета',profile_draft:'Анкета сохранена',profile_pending:'Анкета отправлена на проверку',profile_approved:'Анкета одобрена',profile_rejected:'Анкета отклонена',profile_archived:'Профиль в архиве',profile_hidden:'Профиль скрыт',profile_restored:'Профиль восстановлен',booking_created:'Создана запись',booking_confirmed:'Запись подтверждена',booking_declined:'В записи отказано',booking_expired:'Срок заявки истёк',cancelled:'Встреча отменена',change_proposed:'Предложен перенос',rescheduled:'Встреча перенесена',change_declined:'Перенос отклонён',change_cancelled:'Перенос отозван',change_expired:'Срок переноса истёк',support_message:'Сообщение поддержки',support_status:'Изменён статус обращения',http_unexpected_error:'Ошибка сервера',http_database_error:'Ошибка базы или файлов',server_thread_error:'Сбой обработки соединения',startup_failed:'Ошибка запуска',startup_ready:'Сервис запущен',startup_stopped:'Сервис остановлен',connection_lost:'Соединение потеряно',security_login_failed:'Неудачный вход',security_limited:'Ограничены запросы',security_denied:'Отказ в доступе'};
  const roles={admin:'Администратор',client:'Клиент',psychologist:'Психолог',system:'Система'};
  const results={success:'Успешно',failure:'Ошибка',denied:'Отказ',limited:'Ограничено'};
  const fields={name:'имя',gender:'пол',price:'цена',experience:'опыт',style:'стиль',approach:'подход',bio:'описание',education:'образование',topics:'темы',photo:'фото',contact:'контакт',status:'статус',hidden:'скрыт',deleted:'в архиве',slots:'расписание'};
  function mount(openTarget){
    const $=id=>document.getElementById(id);
    let active=false,epoch=0,overviewLoad=0,journalLoad=0,cursor=null,timer=null;
    const period=()=>{const p=new URLSearchParams({days:$('monitor-days').value});if($('monitor-from').value||$('monitor-to').value){p.set('from',$('monitor-from').value);p.set('to',$('monitor-to').value);}return p;};
    let comparisonValues = {};
    const problemMetrics = new Set([
      'Истекли, ожидают обработки', 'Отмены за период', 'Ошибки за период', 'Дольше двух суток',
      'Ожидают ответа администратора', 'Без ответа более суток',
      'Скрытые профили', 'Дата регистрации неизвестна',
      'Ожидают подтверждения сейчас', 'Анкеты на проверке', 'На проверке', 'Открытые обращения',
    ]);
    const neutralMetrics = new Set([
      'Закрыты по истечении срока, всего', 'Архив',
      'Переносы за период', 'Версия схемы',
    ]);
    const pairs = (values, category = 'metrics') => '<dl class="metric-grid">' + values.map(([label, value]) => {
      let tone = 'neutral';
      if (typeof value === 'number' && value > 0 && !neutralMetrics.has(label)) {
        tone = problemMetrics.has(label) || category === 'errors' ? 'bad' : 'good';
      }
      let comparison = '';
      const previous = comparisonValues[label];
      if (category === 'metrics' && typeof value === 'number' && typeof previous === 'number') {
        const delta = value - previous;
        tone = delta === 0 || value === 0 ? 'neutral'
          : (problemMetrics.has(label) ? delta < 0 : delta > 0) ? 'good' : 'bad';
        const percent = previous === 0 ? (value === 0 ? '0%' : '—')
          : new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(delta / previous * 100) + '%';
        comparison = `<span class="metric-previous">Прошлый период · ${escape(previous)}</span><span class="metric-change">${delta > 0 ? '+' : ''}${escape(delta)} · ${escape(percent)}</span>`;
      }
      if (category === 'service') {
        tone = 'neutral';
        if (label === 'База доступна') tone = value === 'Да' ? 'good' : 'bad';
        if (['Последняя копия', 'Проверка целостности копии', 'Проверка восстановления', 'Автоматические копии'].includes(label)
            && !['Нет сведений', 'Не настроены'].includes(value)) tone = 'good';
        if (['Последняя копия', 'Проверка целостности копии', 'Проверка восстановления', 'Автоматические копии'].includes(label)
            && ['Нет сведений', 'Не настроены'].includes(value)) tone = 'bad';
        if (label === 'Свободно на диске') tone = value === 'Недоступно' || parseFloat(value) < 512 ? 'bad' : 'good';
      }
      if (category === 'errors') tone = 'bad';
      return `<div class="metric-card metric-card--${tone}"><dt>${escape(label)}</dt><dd>${escape(value)}</dd>${comparison}</div>`;
    }).join('') + '</dl>';
    const metricGroups = ['monitor-users', 'monitor-meetings', 'monitor-tasks', 'monitor-service', 'monitor-errors'];
    const preferenceKey = 'otklik-admin-hidden-metrics-v1';
    let hiddenMetrics = {};
    try {
      const saved = JSON.parse(localStorage.getItem(preferenceKey) || '{}');
      if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
        for (const group of metricGroups) {
          hiddenMetrics[group] = Array.isArray(saved[group]) ? saved[group].filter(value => typeof value === 'string') : [];
        }
      }
    } catch {}
    function saveMetricPreferences() {
      try {
        localStorage.setItem(preferenceKey, JSON.stringify(hiddenMetrics));
      } catch {
        $('monitor-message').textContent = 'Выбор применён. Браузер не разрешил сохранить его для следующего посещения.';
      }
    }
    function configureMetrics() {
      for (const group of metricGroups) {
        const root = $(group);
        const cards = [...root.querySelectorAll('.metric-card')];
        let controls = $(group + '-visibility');
        if (!controls) {
          controls = document.createElement('button');
          controls.type = 'button';
          controls.dataset.metricOpen = group;
          controls.textContent = 'Показатели';
          controls.setAttribute('aria-haspopup', 'dialog');
          controls.id = group + '-visibility';
          controls.className = 'metric-settings';
          const heading = root.previousElementSibling;
          const header = document.createElement('div');
          header.className = 'metric-section-header';
          heading.before(header);
          header.append(heading, controls);
        }
        let visible = 0;
        for (const card of cards) {
          card.hidden = (hiddenMetrics[group] || []).includes(card.querySelector('dt').textContent);
          if (!card.hidden) visible++;
        }
        let empty = $(group + '-empty');
        if (!empty) {
          empty = document.createElement('p');
          empty.id = group + '-empty';
          empty.className = 'micro';
          empty.textContent = 'Все показатели скрыты. Выберите нужные в настройке показателей.';
          root.append(empty);
        }
        empty.hidden = visible > 0;
      }
    }
    const metricDialog = document.createElement('dialog');
    metricDialog.className = 'metric-dialog';
    metricDialog.setAttribute('aria-labelledby', 'metric-dialog-title');
    $('workspace').append(metricDialog);
    $('workspace').addEventListener('click', event => {
      const button = event.target.closest('[data-metric-open]');
      if (button) {
        const group = button.dataset.metricOpen;
        const heading = button.closest('.metric-section-header').querySelector('h3').textContent;
        const labels = [...$(group).querySelectorAll('.metric-card dt')].map(item => item.textContent);
        metricDialog.innerHTML = `<div class="metric-dialog-heading"><h2 id="metric-dialog-title">${escape(heading)}</h2><button type="button" class="text-button" data-metric-close aria-label="Закрыть">×</button></div><fieldset><legend>Показывать показатели</legend>`
          + labels.map(label => `<label><input type="checkbox" data-metric-group="${group}" value="${escape(label)}" ${(hiddenMetrics[group] || []).includes(label) ? '' : 'checked'}>${escape(label)}</label>`).join('')
          + `</fieldset><div class="metric-dialog-actions"><button type="button" class="primary" data-metric-close>Готово</button></div>`;
        metricDialog.showModal();
      }
      if (event.target.closest('[data-metric-close]')) metricDialog.close();
    });
    metricDialog.addEventListener('click', event => {
      if (event.target !== metricDialog) return;
      const bounds = metricDialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) metricDialog.close();
    });
    $('workspace').addEventListener('change', event => {
      const input = event.target.closest('[data-metric-group]');
      if (!input) return;
      const group = input.dataset.metricGroup;
      const hidden = new Set(hiddenMetrics[group] || []);
      if (input.checked) hidden.delete(input.value);
      else hidden.add(input.value);
      hiddenMetrics[group] = [...hidden];
      configureMetrics();
      saveMetricPreferences();
    });
    const bytes=value=>value===null?'Недоступно':(value/1024/1024).toFixed(1)+' МиБ';
    const target=(kind,id,label='Открыть')=>`<button type="button" class="text-button" data-monitor-target="${escape(kind)}" data-monitor-id="${escape(id)}">${escape(label)}</button>`;
    const states={draft:'Черновик',pending:'На рассмотрении',approved:'Одобрена',rejected:'Отклонена',open:'Открыто',closed:'Закрыто'};
    const state=value=>value&&typeof value==='object'?Object.entries(value).map(([key,v])=>(fields[key]||key)+': '+(key==='hidden'||key==='deleted'?(v?'да':'нет'):(states[v]||(v??'нет данных')))).join(', '):(states[value]||value);
    function eventHTML(item){
      const details=item.details||{},changed=(details.fields||[]).filter(Boolean);
      let info=changed.length?'<p>Изменены поля: '+changed.map(f=>escape(fields[f]||f)).join(', ')+'.</p>':'';
      if(details.before!==undefined)info+=`<p>Было: ${escape(state(details.before))}<br>Стало: ${escape(state(details.after))}</p>`;
      if(details.error_type)info+=`<p>Тип ошибки: ${escape(details.error_type)}</p>`;
      if(details.operation)info+=`<p>Операция: ${escape(details.method||'')} ${escape(details.operation)}</p>`;
      if(details.from_start||details.to_start)info+=`<p>${details.from_start?date(details.from_start):''}${details.to_start?' → '+date(details.to_start):''}</p>`;
      if(details.frames?.length)info+='<details><summary>Место ошибки</summary><p>'+details.frames.map(f=>escape(f.file)+':'+escape(f.line)).join('<br>')+'</p></details>';
      return `<li><small>${date(item.created_at)} · ${escape(roles[item.actor]||'Система')} · ${escape(results[item.result]||item.result)}</small><b>${escape(kinds[item.kind]||item.kind)}</b>${item.request_id?'<p>Код ошибки: <code>'+escape(item.request_id)+'</code></p>':''}${info}${item.target_id?target(item.target_kind,item.target_id):''}</li>`;
    }
    async function overview(){
      const generation=epoch,load=++overviewLoad;$('monitor-message').textContent='Обновляем обзор…';
      try{
        const data=await HttpAPI.request('/api/admin/overview?'+period());
        if(!active||generation!==epoch||load!==overviewLoad)return;
        const u=data.users,m=data.meetings,t=data.tasks,p=data.profiles,s=data.service,b=data.maintenance;
        const previous = data.comparison;
        comparisonValues = previous ? {
          'Новые клиенты за период': previous.registrations.client,
          'Новые психологи за период': previous.registrations.psychologist,
          'Создано записей за период': previous.meetings.created,
          'Отмены за период': previous.meetings.cancellations,
          'Переносы за период': previous.meetings.reschedules,
          'Ошибки за период': previous.errors,
        } : {};
        const formatDate = value => new Intl.DateTimeFormat('ru-RU', {timeZone:'Europe/Moscow', dateStyle:'short', timeStyle:'short'}).format(new Date(value));
        $('monitor-comparison-period').textContent = previous
          ? 'Сравнение с ' + formatDate(previous.period.from) + ' — ' + formatDate(previous.period.to) + ' МСК'
          : '';

        $('monitor-users').innerHTML=pairs([['Всего пользователей',u.total],['Клиенты',u.clients],['Психологи',u.psychologists],['Новые клиенты за период',u.registrations.client],['Новые психологи за период',u.registrations.psychologist],['Дата регистрации неизвестна',u.legacy_unknown_date],['Анкеты: одобрены',p.approved||0],['На проверке',p.pending||0],['Скрытые профили',p.hidden],['Архив',p.archived]]);
        $('monitor-meetings').innerHTML=pairs([['Создано записей за период',m.created],['Ожидают подтверждения сейчас',m.pending],['Истекли, ожидают обработки',m.awaiting_expiration],['Закрыты по истечении срока, всего',m.expired],['Будущие подтверждённые',m.upcoming],['Отмены за период',m.cancellations],['Переносы за период',m.reschedules]]);
        $('monitor-tasks').innerHTML=pairs([['Анкеты на проверке',t.profiles_pending],['Дольше двух суток',t.profiles_overdue],['Открытые обращения',t.support_open],['Ожидают ответа администратора',t.support_unanswered],['Без ответа более суток',t.support_overdue]])+'<a class="text-button" href="#admin-support">Открыть поддержку</a>';
        $('monitor-warnings').innerHTML=data.warnings.length?'<ul>'+data.warnings.map(w=>'<li>'+escape(w.text)+'</li>').join('')+'</ul>':'<p>Предупреждений по заданным порогам нет.</p>';
        $('monitor-errors').innerHTML=pairs([['Ошибки за период',data.errors.count]])+`<p class="micro">${escape(data.errors.scope)}${!data.errors.available?' Журнал недоступен.':''}${data.errors.partial?' Данные неполные.':''}</p>`+pairs(data.errors.groups.map(g=>[(kinds[g.kind]||g.kind)+' / '+(g.error_type||'без типа'),g.count+' · последний '+new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'}).format(new Date(g.last_at))]), 'errors')+'<ul class="history">'+data.errors.latest.map(eventHTML).join('')+'</ul>';
        $('monitor-service').innerHTML=pairs([['Версия приложения',s.version],['Версия схемы',s.schema_version],['Время работы',Math.floor(s.uptime_seconds/60)+' мин'],['База доступна',s.database_available?'Да':'Нет'],['Размер базы',bytes(s.database_bytes)],['Свободно на диске',bytes(s.disk_free_bytes)],['Автоматические копии',b.automatic_backups_configured?'Настроены':'Не настроены'],['Последняя копия',b.backup_at?dateText(b.backup_at):'Нет сведений'],['Проверка целостности копии',b.verified_at?dateText(b.verified_at):'Нет сведений'],['Проверка восстановления',b.restore_verified_at?dateText(b.restore_verified_at):'Нет сведений']], 'service')+(!b.available?'<p class="notice">Не удалось прочитать сведения о копиях.</p>':'');
        $('monitor-cancellations').innerHTML=data.cancellations.length?'<ul class="history">'+data.cancellations.map(c=>`<li>${target('profile',c.profile_id,'Открыть специалиста')}<p>Отмен: ${escape(c.cancelled)}. Клиентом: ${escape(c.by_client)}, психологом: ${escape(c.by_psychologist)}.</p><p class="micro">Новых записей за тот же период: ${escape(c.total_bookings)}. Отмены могут относиться к ранее созданным записям.</p></li>`).join('')+'</ul>':'<p>Отмен за выбранный период нет.</p>';
        configureMetrics();
        $('monitor-message').textContent='Обзор обновлён. Время указано по Москве.';
      }catch(error){if(active&&generation===epoch&&load===overviewLoad)$('monitor-message').textContent='Не удалось обновить обзор. '+error.message;}
    }
    const dateText=value=>new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'medium',timeStyle:'short'}).format(new Date(value))+' МСК';
    async function journal(more=false){
      const generation=epoch,load=++journalLoad,params=period();
      if(more&&cursor)params.set('cursor',cursor);
      params.set('category',$('monitor-category').value);params.set('result',$('monitor-result').value);params.set('kind',$('monitor-kind').value);
      $('monitor-more').disabled=true;$('monitor-journal-message').textContent='Загружаем события…';
      try{
        const data=await HttpAPI.request('/api/admin/events?'+params);
        if(!active||generation!==epoch||load!==journalLoad)return;
        if(!more)$('monitor-events').replaceChildren();
        $('monitor-events').insertAdjacentHTML('beforeend',data.events.map(eventHTML).join(''));
        cursor=data.next_cursor;$('monitor-more').hidden=!cursor;
        $('monitor-journal-message').textContent=(data.events.length?'События загружены.':'Событий за выбранный период нет.')+' '+data.log_scope+(!data.logs_available?' Журнал ошибок недоступен.':'')+(data.logs_partial?' Данные журнала неполные.':'');
      }catch(error){if(active&&generation===epoch&&load===journalLoad)$('monitor-journal-message').textContent=error.message;}
      finally{if(active&&generation===epoch&&load===journalLoad)$('monitor-more').disabled=false;}
    }
    $('monitor-kind').innerHTML='<option value="">Все типы</option>'+Object.entries(kinds).map(([key,label])=>`<option value="${key}">${escape(label)}</option>`).join('');
    $('monitor-period').addEventListener('submit',event=>{event.preventDefault();if(!active)return;overview();journal();});
    $('monitor-filter').addEventListener('submit',event=>{event.preventDefault();if(active)journal();});
    $('monitor-more').addEventListener('click',()=>{if(active&&cursor)journal(true);});
    for(const id of ['monitor-overview','monitor-journal'])$(id).addEventListener('click',async event=>{
      const button=event.target.closest('[data-monitor-target]');if(!button||!active)return;
      const generation=epoch;button.disabled=true;
      try{await openTarget(button.dataset.monitorTarget,button.dataset.monitorId);}catch(error){if(active&&generation===epoch)$('monitor-message').textContent=error.message;}finally{button.disabled=false;}
    });
    function stop(){metricDialog.close();active=false;epoch++;cursor=null;clearInterval(timer);timer=null;for(const id of ['monitor-users','monitor-meetings','monitor-tasks','monitor-warnings','monitor-errors','monitor-service','monitor-cancellations','monitor-events'])$(id).replaceChildren();$('monitor-message').textContent='';$('monitor-journal-message').textContent='';$('monitor-more').hidden=true;}
    function start(){active=true;epoch++;clearInterval(timer);overview();journal();timer=setInterval(()=>{if(active&&!document.hidden)overview();},30000);}
    return {start,stop,refresh:()=>{if(active){overview();journal();}}};
  }
  return {mount};
})();
