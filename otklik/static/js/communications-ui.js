'use strict';
window.CommunicationUI = (() => {
  const widgets = [];
  const date = value => BookingUI.date(value);
  async function api(path, data) {
    return HttpAPI.request(path,data);
  }
  function errorText(error, fallback) {return error.status ? error.message : fallback;}

  function thread(root, role, kind, onTicket=()=>{}) {
    const labelId = 'message-'+crypto.randomUUID();
    root.innerHTML = `<h3 data-thread-title></h3><p data-thread-help class="micro"></p><div data-message-list role="log" aria-label="Сообщения"></div><p data-thread-status role="status" aria-live="polite"></p><form data-message-form><label for="${labelId}">Сообщение</label><textarea id="${labelId}" data-message-body maxlength="4000" rows="3" required></textarea><p class="micro">До 4000 символов. Вложения пока не поддерживаются.</p><div class="save-row"><button class="primary" data-send-message>Отправить</button><button type="button" class="text-button" data-refresh-messages>Обновить сообщения</button><button type="button" class="text-button" data-close-thread>Закрыть переписку</button></div></form>`;
    const list=root.querySelector('[data-message-list]'), input=root.querySelector('textarea'), send=root.querySelector('[data-send-message]'), status=root.querySelector('[data-thread-status]');
    const drafts=new Map();
    let ident=null, last=0, marked=0, seen=new Set(), epoch=0, fetching=null, timer=null, busy=false, writable=true;
    const endpoint=kind==='appointment'?'messages':'support/messages';
    const idField=kind==='appointment'?'appointment_id':'ticket_id';
    function controls() {input.disabled=busy||!writable; send.disabled=busy||!writable;}
    async function refresh() {
      if (!ident || fetching===epoch) return;
      const requestEpoch=epoch;
      fetching=requestEpoch;
      try {
        const data=await api(`/api/${role}/${endpoint}?${idField}=${encodeURIComponent(ident)}&after=${last}`);
        if (requestEpoch!==epoch) return;
        for (const item of data.messages) {
          if (seen.has(item.id)) continue;
          seen.add(item.id);
          const entry=document.createElement('article');entry.className='appointment-card';entry.dataset.messageId=item.id;
          const by=document.createElement('p');by.className='micro';
          by.textContent=(item.sender_role===role?'Вы':{client:'Клиент',psychologist:'Психолог',admin:'Поддержка'}[item.sender_role])+' · '+date(item.created_at);
          const body=document.createElement('p');body.dataset.messageText='';body.textContent=item.body;body.style.whiteSpace='pre-wrap';body.style.overflowWrap='anywhere';
          entry.append(by,body);list.append(entry);
        }
        last=data.last_id;
        if(last>marked&&root.getClientRects().length&&!document.hidden){
          try{
            await api(`/api/${role}/notifications/read`,{target_kind:kind==='appointment'?'appointment':'support',target_id:ident,through_source_id:last});
            if(requestEpoch!==epoch)return;
            marked=last;window.dispatchEvent(new CustomEvent('otklik-notifications-changed',{detail:role}));
          }catch{/* Retry read receipts on the next refresh without hiding delivered messages. */}
        }
        if(requestEpoch!==epoch)return;
        if (data.ticket) {
          const wasWritable=writable;
          writable=data.ticket.status==='open';
          if(writable&&!wasWritable)status.textContent='';
          onTicket(data.ticket);controls();
        }
        status.textContent=!writable?'Обращение закрыто. Его можно открыть снова.':data.has_more?'Загружаем следующие сообщения.':data.messages.length?'':status.textContent;
        if (data.has_more) setTimeout(() => {if(requestEpoch===epoch)refresh();},100);
      } catch (error) {
        if (requestEpoch!==epoch) return;
        status.textContent=errorText(error,'Не удалось загрузить сообщения. Попробуйте обновить их.');
        if ([401,409].includes(error.status)) {list.replaceChildren();last=0;seen.clear();}
      } finally {if(fetching===requestEpoch)fetching=null;}
    }
    function open(id,title) {
      if (ident!==id) {
        epoch++;ident=id;last=0;marked=0;seen=new Set();list.replaceChildren();busy=false;writable=true;
        input.value=drafts.get(id)?.body||'';status.textContent='';controls();
      }
      root.hidden=false;
      root.querySelector('[data-thread-title]').textContent=title;
      root.querySelector('[data-thread-help]').textContent=kind==='appointment'?'Переписка доступна только участникам встречи. Для связи с администратором создайте отдельное обращение в поддержку.':'Обращение видите вы и администратор поддержки.';
      if (!timer) timer=setInterval(() => {if(!root.hidden)refresh();},5000);
      refresh();
    }
    input.addEventListener('input',()=>{
      if (!ident) return;
      const old=drafts.get(ident);
      drafts.set(ident,{body:input.value,key:old?.body.trim()===input.value.trim()?old.key:null});
    });
    root.querySelector('form').addEventListener('submit',async event=>{
      event.preventDefault();
      if (!ident||busy||!writable||!input.value.trim()) return;
      const id=ident, requestEpoch=epoch, body=input.value.trim(), old=drafts.get(id);
      const key=old?.body.trim()===body&&old.key?old.key:crypto.randomUUID();
      drafts.set(id,{body:input.value,key});busy=true;controls();status.textContent='Отправляем сообщение.';
      try {
        await api(`/api/${role}/${endpoint}`,{[idField]:id,body,request_key:key});
        drafts.delete(id);
        if (requestEpoch!==epoch) return;
        input.value='';status.textContent='Сообщение отправлено.';await refresh();
      } catch(error) {
        if (requestEpoch===epoch) status.textContent=errorText(error,'Не удалось отправить сообщение. Текст сохранён в форме, повторите отправку.');
      } finally {if(requestEpoch===epoch){busy=false;controls();}}
    });
    root.querySelector('[data-refresh-messages]').addEventListener('click',()=>{status.textContent='';refresh();});
    root.querySelector('[data-close-thread]').addEventListener('click',()=>{root.hidden=true;});
    function reset() {epoch++;ident=null;last=0;marked=0;seen.clear();drafts.clear();list.replaceChildren();input.value='';status.textContent='';root.hidden=true;clearInterval(timer);timer=null;busy=false;writable=true;controls();}
    return {open,refresh,reset};
  }

  function mountChat(buttons, panel, role) {
    const chat=thread(panel,role,'appointment');
    buttons.addEventListener('click',event=>{
      const button=event.target.closest('[data-chat-appointment]');
      if (!button) return;
      const name=button.closest('[data-appointment]').querySelector('h3').textContent;
      chat.open(button.dataset.chatAppointment,'Переписка · '+name);
      panel.scrollIntoView({block:'nearest',behavior:'instant'});
    });
    widgets.push({role,reset:chat.reset});
    return chat;
  }

  function mountSupport(root,role) {
    const subjectId='support-subject-'+crypto.randomUUID(), bodyId='support-body-'+crypto.randomUUID();
    root.innerHTML=`<p class="micro">Здесь можно задать вопрос о работе сервиса. Переписка встречи автоматически в обращение не передаётся.</p><form data-new-ticket ${role==='admin'?'hidden':''}><label for="${subjectId}">Тема обращения</label><input id="${subjectId}" maxlength="150" required><label for="${bodyId}">Что произошло?</label><textarea id="${bodyId}" maxlength="4000" rows="3" required></textarea><button class="primary" data-create-ticket>Создать обращение</button></form><p data-support-status role="status" aria-live="polite"></p><button type="button" class="text-button" data-refresh-tickets>Обновить обращения</button><div data-ticket-list></div><button type="button" class="text-button" data-more-tickets hidden>Показать ещё обращения</button><div data-ticket-actions hidden><button type="button" class="text-button" data-ticket-status></button></div><section data-support-thread class="panel" hidden></section>`;
    const more=root.querySelector('[data-more-tickets]');
    const list=root.querySelector('[data-ticket-list]'), status=root.querySelector('[data-support-status]'), form=root.querySelector('form');
    const subject=root.querySelector('input'), body=root.querySelector('textarea'), actions=root.querySelector('[data-ticket-actions]'), toggle=root.querySelector('[data-ticket-status]');
    let selected=null,tickets=[],active=false,timer=null,pending=null,busy=false,epoch=0,lastList='',visiblePages=1,loading=false;
    const chat=thread(root.querySelector('[data-support-thread]'),role,'support',item=>{selected=item;actions.hidden=false;toggle.textContent=item.status==='open'?'Закрыть обращение':'Открыть снова';});
    async function refresh() {
      if(!active||loading)return;
      const requestEpoch=epoch;loading=true;more.disabled=true;
      try {
        let before=null,loaded=[];
        for(let page=0;page<visiblePages;page++){
          const data=await api(`/api/${role}/support/tickets`+(before?'?before='+encodeURIComponent(before):''));
          if(requestEpoch!==epoch||!active)return;
          loaded.push(...data.tickets);before=data.next_before;if(!before)break;
        }
        more.hidden=!before;tickets=loaded;
        const serialized=JSON.stringify(tickets);
        if(serialized!==lastList){
          lastList=serialized;list.replaceChildren();
          if(!tickets.length){const empty=document.createElement('p');empty.textContent='Обращений пока нет.';list.append(empty);}
          for(const item of tickets){const button=document.createElement('button');button.type='button';button.className='profile-item';button.dataset.ticket=item.id;button.textContent=item.subject+' · '+(item.status==='open'?'Открыто':'Закрыто')+(role==='admin'?' · '+item.owner_name:'');list.append(button);}
        }
      }catch(error){if(requestEpoch===epoch)status.textContent=errorText(error,'Не удалось загрузить обращения. Попробуйте ещё раз.');}
      finally{if(requestEpoch===epoch){loading=false;more.disabled=false;}}
    }
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy||!subject.value.trim()||!body.value.trim())return;
      const payload={subject:subject.value.trim(),body:body.value.trim()};
      if(!pending||pending.subject!==payload.subject||pending.body!==payload.body)pending={...payload,request_key:crypto.randomUUID()};
      const requestEpoch=epoch;busy=true;subject.disabled=true;body.disabled=true;root.querySelector('[data-create-ticket]').disabled=true;
      try{
        const data=await api(`/api/${role}/support/tickets`,pending);
        if(requestEpoch!==epoch)return;
        pending=null;subject.value='';body.value='';status.textContent='Обращение создано.';
        selected=data.ticket;chat.open(selected.id,selected.subject);await refresh();
      }catch(error){if(requestEpoch===epoch)status.textContent=errorText(error,'Не удалось создать обращение. Текст сохранён, повторите отправку.');}
      finally{if(requestEpoch===epoch){busy=false;subject.disabled=false;body.disabled=false;root.querySelector('[data-create-ticket]').disabled=false;}}
    });
    list.addEventListener('click',event=>{const button=event.target.closest('[data-ticket]');if(!button)return;selected=tickets.find(item=>item.id===button.dataset.ticket);if(selected)chat.open(selected.id,selected.subject);});
    toggle.addEventListener('click',async()=>{
      if(!selected)return;toggle.disabled=true;const requestEpoch=epoch;
      try{const data=await api(`/api/${role}/support/status`,{ticket_id:selected.id,status:selected.status==='open'?'closed':'open',revision:selected.revision});if(requestEpoch!==epoch)return;selected=data.ticket;status.textContent=selected.status==='open'?'Обращение снова открыто.':'Обращение закрыто.';await chat.refresh();await refresh();}
      catch(error){if(requestEpoch===epoch){status.textContent=errorText(error,'Не удалось изменить статус. Повторите попытку.');await chat.refresh();}}
      finally{if(requestEpoch===epoch)toggle.disabled=false;}
    });
    more.addEventListener('click',()=>{visiblePages++;refresh();});
    root.querySelector('[data-refresh-tickets]').addEventListener('click',()=>{status.textContent='';refresh();chat.refresh();});
    function start(){active=true;refresh();if(!timer)timer=setInterval(()=>{if(root.getClientRects().length)refresh();},5000);}
    function reset(){epoch++;active=false;selected=null;tickets=[];lastList='';visiblePages=1;loading=false;more.hidden=true;more.disabled=false;list.replaceChildren();subject.value='';body.value='';subject.disabled=false;body.disabled=false;root.querySelector('[data-create-ticket]').disabled=false;toggle.disabled=false;status.textContent='';actions.hidden=true;pending=null;busy=false;chat.reset();clearInterval(timer);timer=null;}
    widgets.push({role,reset});
    async function open(id){
      const requestEpoch=epoch;
      const data=await api(`/api/${role}/support/messages?ticket_id=${encodeURIComponent(id)}`);
      if(requestEpoch!==epoch||!active)return;
      selected=data.ticket;chat.open(selected.id,selected.subject);root.scrollIntoView({block:'nearest',behavior:'instant'});
    }
    return {start,refresh,reset,open};
  }

  function mountNotifications(root,role,onOpen){
    root.innerHTML='<details><summary>Уведомления · <span data-unread-count aria-live="polite">0</span></summary><p data-unread-messages class="micro"></p><div class="save-row"><button type="button" class="text-button" data-refresh-notifications>Обновить уведомления</button><button type="button" class="text-button" data-read-notifications>Прочитать все</button></div><p data-notification-status role="status"></p><div data-notification-list></div><button type="button" class="text-button" data-more-notifications hidden>Показать ещё уведомления</button></details>';
    const list=root.querySelector('[data-notification-list]'),count=root.querySelector('[data-unread-count]'),status=root.querySelector('[data-notification-status]'),more=root.querySelector('[data-more-notifications]'),readAll=root.querySelector('[data-read-notifications]');
    let active=false,timer=null,epoch=0,loading=false,busy=false,items=[],through=0,pages=1,serialized='';
    async function refresh(){
      if(!active||loading)return;
      const requestEpoch=epoch;loading=true;more.disabled=true;
      try{
        let before=null,loaded=[],first;
        for(let page=0;page<pages;page++){
          const data=await api(`/api/${role}/notifications`+(before?'?before='+before:''));
          if(requestEpoch!==epoch||!active)return;
          if(!first)first=data;
          loaded.push(...data.notifications);before=data.next_before;if(!before)break;
        }
        items=loaded;through=first.through_id;count.textContent=String(first.unread_count);readAll.disabled=busy||!first.unread_count;more.hidden=!before;
        root.querySelector('[data-unread-messages]').textContent=`Непрочитанные сообщения: ${first.unread_by_kind.appointment_message||0}. Поддержка: ${first.unread_by_kind.support_message||0}.`;
        const next=JSON.stringify(items);
        if(next!==serialized){
          serialized=next;list.replaceChildren();
          if(!items.length){const empty=document.createElement('p');empty.textContent='Уведомлений пока нет.';list.append(empty);}
          for(const item of items){
            const button=document.createElement('button');button.type='button';button.className='profile-item';button.dataset.notificationId=item.id;
            const title=document.createElement('b');title.textContent=item.title;
            const meta=document.createElement('small');meta.textContent=date(item.created_at)+' · '+(item.read_at?'Прочитано':'Не прочитано');button.append(title,meta);list.append(button);
          }
        }
      }catch(error){if(requestEpoch===epoch){status.textContent=errorText(error,'Не удалось загрузить уведомления. Попробуйте ещё раз.');if(error.status===401)reset();}}
      finally{if(requestEpoch===epoch){loading=false;more.disabled=false;}}
    }
    list.addEventListener('click',async event=>{
      const button=event.target.closest('[data-notification-id]');if(!button||busy)return;
      const item=items.find(value=>String(value.id)===button.dataset.notificationId);if(!item)return;
      const requestEpoch=epoch;busy=true;button.disabled=true;status.textContent='';
      try{await onOpen(item,()=>requestEpoch===epoch&&active);if(requestEpoch!==epoch)return;await api(`/api/${role}/notifications/read`,{ids:[item.id]});if(requestEpoch===epoch)await refresh();}
      catch(error){if(requestEpoch===epoch)status.textContent=errorText(error,'Не удалось открыть уведомление. Попробуйте ещё раз.');}
      finally{if(requestEpoch===epoch){busy=false;button.disabled=false;readAll.disabled=count.textContent==='0';}}
    });
    readAll.addEventListener('click',async()=>{
      if(busy)return;const requestEpoch=epoch,limit=through;busy=true;readAll.disabled=true;status.textContent='';
      try{await api(`/api/${role}/notifications/read`,{through_id:limit});if(requestEpoch===epoch)await refresh();}
      catch(error){if(requestEpoch===epoch)status.textContent=errorText(error,'Не удалось отметить уведомления. Повторите попытку.');}
      finally{if(requestEpoch===epoch){busy=false;readAll.disabled=count.textContent==='0';}}
    });
    more.addEventListener('click',()=>{pages++;refresh();});
    root.querySelector('[data-refresh-notifications]').addEventListener('click',()=>{status.textContent='';refresh();});
    window.addEventListener('otklik-notifications-changed',event=>{if(event.detail===role)refresh();});
    function start(){active=true;refresh();if(!timer)timer=setInterval(()=>{if(!document.hidden)refresh();},5000);}
    function reset(){epoch++;active=false;loading=false;busy=false;items=[];through=0;pages=1;serialized='';list.replaceChildren();count.textContent='0';status.textContent='';root.querySelector('[data-unread-messages]').textContent='';root.querySelector('details').open=false;more.hidden=true;more.disabled=false;readAll.disabled=true;clearInterval(timer);timer=null;}
    widgets.push({role,reset});return {start,refresh,reset};
  }
  return {mountChat,mountSupport,mountNotifications,reset:role=>{for(const item of widgets)if(item.role===role)item.reset();}};
})();
