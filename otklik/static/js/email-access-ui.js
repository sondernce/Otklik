'use strict';
window.EmailAccessUI=(()=>{
  function mount(root,role){
    let epoch=0,active=false,load=0;
    const path='/api/'+role+'/email/';
    async function refresh(){
      const generation=epoch,request=++load;
      try{
        const data=await HttpAPI.request(path+'status');
        if(!active||generation!==epoch||request!==load)return;
        root.replaceChildren();
        const heading=document.createElement('h3');heading.textContent='Подтверждение почты';root.append(heading);
        const state=document.createElement('p');state.textContent=data.email_verified?'Электронная почта подтверждена.':'Электронная почта пока не подтверждена.';root.append(state);
        if(data.transport==='local'){const note=document.createElement('p');note.className='micro';note.textContent='Локальная проверка: внешняя отправка ещё не настроена.';root.append(note);}
        const message=document.createElement('p');message.setAttribute('role','status');message.setAttribute('aria-live','polite');root.append(message);
        if(!data.email_verified){
          const send=document.createElement('button');send.type='button';send.className='text-button';send.textContent='Отправить письмо подтверждения';
          send.addEventListener('click',async()=>{
            const current=epoch;send.disabled=true;message.textContent='';
            try{const result=await HttpAPI.request(path+'request-verification',{});if(active&&current===epoch)message.textContent=result.message;}
            catch(error){if(active&&current===epoch)message.textContent=error.message;}
            finally{if(active&&current===epoch)send.disabled=false;}
          });root.append(send);
        }
        const update=document.createElement('button');update.type='button';update.className='text-button';update.textContent='Обновить подтверждение';update.addEventListener('click',refresh);root.append(update);
      }catch(error){if(active&&generation===epoch&&request===load){root.replaceChildren();const text=document.createElement('p');text.textContent='Не удалось проверить подтверждение почты. '+error.message;root.append(text);}}
    }
    return {start(){active=true;epoch++;refresh();},stop(){active=false;epoch++;root.replaceChildren();}};
  }
  return {mount};
})();
