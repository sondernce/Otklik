'use strict';
(()=>{
  const $=id=>document.getElementById(id),forms=['access-request','access-confirm','access-reset'];
  let role,purpose,token,epoch=0;
  const validRole=()=>['client','psychologist'].includes(role);
  const back=()=>{$('access-back').href=role==='psychologist'?'/psychologist#login':'/client#login';};
  const lock=(form,busy)=>{for(const control of form.querySelectorAll('button,input,select'))control.disabled=busy;};
  function activate(){
    epoch++;const params=new URLSearchParams(location.hash.slice(1));
    role=params.get('role')||'client';purpose=params.get('purpose')||'request';token=params.get('token');
    // The bearer link stays in memory only, never in a request URL or browser storage.
    history.replaceState(null,'',location.pathname);
    for(const id of forms){$(id).reset();$(id).hidden=true;lock($(id),false);FormErrors.clear($(id));}
    $('access-message').textContent='';back();
    if(!validRole()||!['request','verify','reset','change'].includes(purpose)||(purpose!=='request'&&!/^[A-Za-z0-9_-]{43}$/.test(token||''))){
      $('access-title').textContent='Доступ к аккаунту';$('access-hint').textContent='Ссылка неполная или недействительна. Откройте её из письма либо запросите новую.';return;
    }
    $('access-role').value=role;
    $(purpose==='verify'||purpose==='change'?'access-confirm':purpose==='reset'?'access-reset':'access-request').hidden=false;
    $('access-confirm').querySelector('button').textContent=purpose==='change'?'Подтвердить новую почту':'Подтвердить почту';
    $('access-title').textContent=purpose==='change'?'Смена почты':purpose==='verify'?'Подтверждение почты':purpose==='reset'?'Новый пароль':'Восстановление пароля';
    $('access-hint').textContent=purpose==='change'?'Подтвердите новый адрес. Ссылка действует 30 минут. Все сессии аккаунта завершатся.':purpose==='verify'?'Нажмите кнопку, чтобы подтвердить адрес. Ссылка действует 24 часа.':purpose==='reset'?'Ссылка действует 30 минут. После смены пароля потребуется войти заново на всех устройствах.':'Укажите почту своего аккаунта. В локальной версии внешняя отправка писем ещё не настроена.';
  }
  $('access-role').addEventListener('change',()=>{role=$('access-role').value;back();});
  for(const id of forms)$(id).addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;if(form.hidden)return;
    FormErrors.clear(form);$('access-message').textContent='';
    let endpoint,data;const generation=epoch;
    if(purpose==='request'){
      if(!$('access-email').value.trim()||!$('access-email').validity.valid){FormErrors.show({'#access-email':'Введите корректную электронную почту.'});return;}
      endpoint='password/request-reset';data={email:$('access-email').value};
    }else if(purpose==='reset'){
      const password=$('access-password').value;
      if(password.length<8||password.length>200){FormErrors.show({'#access-password':'Пароль должен содержать от 8 до 200 символов.'});return;}
      if(password!==$('access-password-repeat').value){FormErrors.show({'#access-password-repeat':'Пароли должны совпадать.'});return;}
      endpoint='password/reset';data={token,password};
    }else{endpoint=purpose==='change'?'account/confirm-email':'email/confirm';data={token};}
    lock(form,true);
    try{
      const result=await HttpAPI.request('/api/'+role+'/'+endpoint,data);if(generation!==epoch)return;
      $('access-message').textContent=result.message;
      if(purpose!=='request'){token=null;form.reset();form.hidden=true;}
    }catch(error){if(generation===epoch)$('access-message').textContent=error.message+(purpose==='reset'&&error.status===400?' Если пароль уже меняли по этой ссылке, попробуйте войти с новым паролем.':'');}
    finally{if(generation===epoch)lock(form,false);}
  });
  window.addEventListener('hashchange',()=>{if(location.hash)activate();});
  activate();
})();
