'use strict';
window.HttpAPI=(()=>{
  const TIMEOUT_MS=15000;
  function networkError(timeout,write){
    const error=Error((timeout?'Сервер не ответил за 15 секунд.':'Не удалось получить ответ сервера.')+
      (write?' Введённые данные сохранены в форме. Запрос мог быть выполнен; проверьте результат или повторите отправку.':' Проверьте соединение и повторите попытку.'));
    error.network=true;error.timeout=timeout;return error;
  }
  async function request(path,data){
    const controller=new AbortController(),write=data!==undefined;
    let timer,expired=false;
    const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{expired=true;controller.abort();reject(networkError(true,write));},TIMEOUT_MS);});
    const operation=(async()=>{
      const response=await fetch(path,{signal:controller.signal,...(write?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{})});
      const body=await response.json();
      if(!body||typeof body!=='object'||Array.isArray(body))throw networkError(false,write);
      if(!response.ok){const error=Error(body.error||'Не удалось выполнить действие.');error.status=response.status;error.field=body.field;error.requestId=body.request_id;throw error;}
      return body;
    })();
    try{return await Promise.race([operation,deadline]);}
    catch(error){if(error.status)throw error;throw networkError(expired,write);}
    finally{clearTimeout(timer);}
  }
  return {request};
})();
