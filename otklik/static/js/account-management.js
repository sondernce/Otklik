'use strict';
window.AccountManagement=(()=>{
  function mount(root,role,closed){
    let epoch=0,active=false,key=crypto.randomUUID();
    root.classList.add('panel','settings-panel');
    root.innerHTML=`<h2>Управление аккаунтом</h2><details><summary>Сменить почту</summary><p class="micro">До подтверждения нового адреса действует прежний. После смены завершатся все сессии аккаунта. Локальные письма обрабатываются отдельно.</p><form data-account-email novalidate><label>Новая электронная почта<input id="${role}-new-email" name="email" type="email" maxlength="200" autocomplete="email" required></label><label>Текущий пароль<input id="${role}-email-password" name="password" type="password" autocomplete="current-password" maxlength="200" required></label><button class="primary">Подтвердить новый адрес</button><p role="status" aria-live="polite"></p></form></details><details><summary>Удалить аккаунт</summary><p class="notice">Доступ и личные поля профиля будут удалены без восстановления. Сначала отмените будущие встречи и дождитесь завершения текущей. Переписка, даты и служебная история сохраняются, в том числе у собеседника; сроки их хранения ещё не определены. Резервные копии могут содержать прежние данные.</p><form data-account-delete novalidate><label>Текущий пароль<input id="${role}-delete-password" name="password" type="password" autocomplete="current-password" maxlength="200" required></label><label>Введите УДАЛИТЬ<input id="${role}-delete-confirmation" name="confirmation" autocomplete="off" required></label><button class="text-button danger">Удалить аккаунт</button><p role="status" aria-live="polite"></p></form></details>`;
    const email=root.querySelector('[data-account-email]');
    email.elements.email.addEventListener('input',()=>{key=crypto.randomUUID();});
    for(const form of root.querySelectorAll('form'))form.addEventListener('submit',async event=>{
      event.preventDefault();if(!active)return;const generation=epoch,isDelete=form.hasAttribute('data-account-delete'),status=form.querySelector('[role=status]');
      FormErrors.clear(form);status.textContent='';
      if(!isDelete&&(!form.elements.email.value.trim()||!form.elements.email.validity.valid)){FormErrors.show({['#'+role+'-new-email']:'Введите корректный адрес почты.'});return;}
      if(form.elements.password.value.length<8){FormErrors.show({['#'+role+(isDelete?'-delete-password':'-email-password')]:'Введите текущий пароль.'});return;}
      if(isDelete&&form.elements.confirmation.value!=='УДАЛИТЬ'){FormErrors.show({['#'+role+'-delete-confirmation']:'Введите УДАЛИТЬ.'});return;}
      if(isDelete&&!confirm('Удалить аккаунт без восстановления? Переписка и история встреч сохранятся.'))return;
      const data={password:form.elements.password.value,...(isDelete?{confirmation:form.elements.confirmation.value}:{email:form.elements.email.value,request_key:key})};
      for(const control of form.elements)control.disabled=true;
      try{
        const result=await HttpAPI.request('/api/'+role+'/account/'+(isDelete?'delete':'email-change'),data);
        if(!active||generation!==epoch)return;
        if(isDelete){await closed(result.message);return;}
        status.textContent=result.message;key=crypto.randomUUID();form.elements.password.value='';
      }catch(error){if(active&&generation===epoch)status.textContent=error.message;}
      finally{if(active&&generation===epoch)for(const control of form.elements)control.disabled=false;}
    });
    return {start(){active=true;epoch++;},stop(){active=false;epoch++;key=crypto.randomUUID();for(const form of root.querySelectorAll('form')){form.reset();FormErrors.clear(form);for(const control of form.elements)control.disabled=false;form.querySelector('[role=status]').textContent='';}for(const item of root.querySelectorAll('details'))item.open=false;}};
  }
  function admin(root,isCurrent,message){
    let epoch=0,after=null,exactId=null;
    root.innerHTML='<h2>Аккаунты пользователей</h2><p class="micro">Блокировка завершает сессии и запрещает вход. Назначенные встречи сохраняются; анкета заблокированного психолога не показывается в каталоге. Разблокировка не восстанавливает старые сессии. Удаление профиля выполняет владелец в настройках.</p><form data-accounts-filter class="form-grid"><label>Роль<select name="role"><option value="all">Все</option><option value="client">Клиенты</option><option value="psychologist">Психологи</option></select></label><label>Состояние<select name="status"><option value="all">Все</option><option value="active">Активные</option><option value="blocked">Заблокированные</option><option value="deleted">Удалённые</option></select></label><label>Имя или почта<input name="search" maxlength="200" type="search"></label><button class="text-button">Найти аккаунты</button></form><div data-account-list></div><button type="button" class="text-button" data-accounts-more hidden>Показать ещё аккаунты</button>';
    const form=root.querySelector('form'),list=root.querySelector('[data-account-list]'),more=root.querySelector('[data-accounts-more]');
    async function refresh(append=false){
      const current=isCurrent(),generation=++epoch;if(!current())return;
      const params=new URLSearchParams(new FormData(form));if(exactId)params.set('account_id',exactId);if(append&&after)params.set('after',after);
      try{const data=await HttpAPI.request('/api/admin/accounts?'+params);if(!current()||generation!==epoch)return;
        const esc=BookingUI.escape;const html=data.accounts.map(item=>`<article class="appointment-card"><h3>${esc(item.name||'Без имени')}</h3><p>${esc(item.email)}</p><p>${item.role==='client'?'Клиент':'Психолог'} · ${{active:'Активен',blocked:'Заблокирован',deleted:'Удалён'}[item.status]}</p>${item.status==='deleted'?'<p class="micro">Восстановление недоступно.</p>':`<button type="button" class="text-button ${item.status==='active'?'danger':''}" data-account-id="${esc(item.id)}" data-account-role="${item.role}" data-account-revision="${item.revision}" data-account-status="${item.status==='active'?'blocked':'active'}">${item.status==='active'?'Заблокировать аккаунт':'Разблокировать аккаунт'}</button>`}</article>`).join('');
        if(append)list.insertAdjacentHTML('beforeend',html);else list.innerHTML=html||'<p>Аккаунты не найдены.</p>';
        after=data.next_after;more.hidden=!after;
      }catch(error){if(current()&&generation===epoch)message(error.message);}
    }
    form.addEventListener('submit',event=>{event.preventDefault();exactId=null;refresh();});more.addEventListener('click',()=>refresh(true));
    list.addEventListener('click',async event=>{
      const button=event.target.closest('[data-account-id]');if(!button)return;const current=isCurrent();if(!current())return;
      if(!confirm(button.dataset.accountStatus==='blocked'?'Заблокировать аккаунт? Сессии завершатся, назначенные встречи сохранятся.':'Разблокировать аккаунт?'))return;
      button.disabled=true;
      try{await HttpAPI.request('/api/admin/accounts/status',{account_id:button.dataset.accountId,role:button.dataset.accountRole,status:button.dataset.accountStatus,revision:Number(button.dataset.accountRevision)});if(current())await refresh();}
      catch(error){if(current())message(error.message);}finally{if(current())button.disabled=false;}
    });
    return {refresh,open(target){const [role,id]=target.split(':');form.elements.role.value=role;form.elements.status.value='all';form.elements.search.value='';exactId=id;return refresh();},stop(){epoch++;list.replaceChildren();after=null;exactId=null;more.hidden=true;form.reset();}};
  }
  return {mount,admin};
})();
