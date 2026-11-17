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
    const pairs=(values)=>'<dl>'+values.map(([label,value])=>`<div class="detail-row"><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join('')+'</dl>';
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
        $('monitor-users').innerHTML=pairs([['Всего пользователей',u.total],['Клиенты',u.clients],['Психологи',u.psychologists],['Новые клиенты за период',u.registrations.client],['Новые психологи за период',u.registrations.psychologist],['Дата регистрации неизвестна',u.legacy_unknown_date],['Анкеты: одобрены',p.approved||0],['На проверке',p.pending||0],['Скрытые профили',p.hidden],['Архив',p.archived]]);
        $('monitor-meetings').innerHTML=pairs([['Создано записей за период',m.created],['Ожидают подтверждения сейчас',m.pending],['Истекли, ожидают обработки',m.awaiting_expiration],['Закрыты по истечении срока, всего',m.expired],['Будущие подтверждённые',m.upcoming],['Отмены за период',m.cancellations],['Переносы за период',m.reschedules]]);
        $('monitor-tasks').innerHTML=pairs([['Анкеты на проверке',t.profiles_pending],['Дольше двух суток',t.profiles_overdue],['Открытые обращения',t.support_open],['Ожидают ответа администратора',t.support_unanswered],['Без ответа более суток',t.support_overdue]])+'<a class="text-button" href="#admin-support">Открыть поддержку</a>';
        $('monitor-warnings').innerHTML=data.warnings.length?'<ul>'+data.warnings.map(w=>'<li>'+escape(w.text)+'</li>').join('')+'</ul>':'<p>Предупреждений по заданным порогам нет.</p>';
        $('monitor-errors').innerHTML=`<p>Ошибок за период: ${escape(data.errors.count)}.</p><p class="micro">${escape(data.errors.scope)}${!data.errors.available?' Журнал недоступен.':''}${data.errors.partial?' Данные неполные.':''}</p>`+pairs(data.errors.groups.map(g=>[(kinds[g.kind]||g.kind)+' / '+(g.error_type||'без типа'),g.count+' · последний '+new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'}).format(new Date(g.last_at))]))+'<ul class="history">'+data.errors.latest.map(eventHTML).join('')+'</ul>';
        $('monitor-service').innerHTML=pairs([['Версия приложения',s.version],['Версия схемы',s.schema_version],['Время работы',Math.floor(s.uptime_seconds/60)+' мин'],['База доступна',s.database_available?'Да':'Нет'],['Размер базы',bytes(s.database_bytes)],['Свободно на диске',bytes(s.disk_free_bytes)],['Автоматические копии',b.automatic_backups_configured?'Настроены':'Не настроены'],['Последняя копия',b.backup_at?dateText(b.backup_at):'Нет сведений'],['Проверка целостности копии',b.verified_at?dateText(b.verified_at):'Нет сведений'],['Проверка восстановления',b.restore_verified_at?dateText(b.restore_verified_at):'Нет сведений']])+(!b.available?'<p class="notice">Не удалось прочитать сведения о копиях.</p>':'');
        $('monitor-cancellations').innerHTML=data.cancellations.length?'<ul class="history">'+data.cancellations.map(c=>`<li>${target('profile',c.profile_id,'Открыть специалиста')}<p>Отмен: ${escape(c.cancelled)}. Клиентом: ${escape(c.by_client)}, психологом: ${escape(c.by_psychologist)}.</p><p class="micro">Новых записей за тот же период: ${escape(c.total_bookings)}. Отмены могут относиться к ранее созданным записям.</p></li>`).join('')+'</ul>':'<p>Отмен за выбранный период нет.</p>';
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
    function stop(){active=false;epoch++;cursor=null;clearInterval(timer);timer=null;for(const id of ['monitor-users','monitor-meetings','monitor-tasks','monitor-warnings','monitor-errors','monitor-service','monitor-cancellations','monitor-events'])$(id).replaceChildren();$('monitor-message').textContent='';$('monitor-journal-message').textContent='';$('monitor-more').hidden=true;}
    function start(){active=true;epoch++;clearInterval(timer);overview();journal();timer=setInterval(()=>{if(active&&!document.hidden)overview();},30000);}
    return {start,stop,refresh:()=>{if(active){overview();journal();}}};
  }
  return {mount};
})();
