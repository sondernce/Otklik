'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function fixture(fetch){
  const timers=new Map();let sequence=0;
  const context=vm.createContext({window:{},fetch,AbortController,Intl,Date,
    setTimeout(fn,ms){const id=++sequence;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);}});
  const transport=path.join(__dirname,'../static/js/http.js');
  vm.runInContext(fs.readFileSync(transport,'utf8'),context);
  context.HttpAPI=context.window.HttpAPI;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/js/booking-ui.js'),'utf8'),context);
  return {api:context.window.BookingUI.api,timers};
}
async function main(){
  let signal,calls=0,deliver;
  const stalled=fixture((_,options)=>{calls++;signal=options.signal;return new Promise(resolve=>{deliver=resolve;});});
  const pending=stalled.api('/api/client/appointments',{request_key:'same-request-key'});
  assert.equal(stalled.timers.size,1,'Every request needs a deadline, even when fetch never responds');
  const timeout=[...stalled.timers.values()][0];assert.equal(timeout.ms,15000);
  const rejected=assert.rejects(pending,error=>error.network===true&&error.timeout===true);
  timeout.fn();await rejected;assert.equal(signal.aborted,true);assert.equal(stalled.timers.size,0);
  deliver({ok:true,json:async()=>({ok:true})});await Promise.resolve();assert.equal(calls,1,'POST is never retried automatically');

  const body=fixture(async()=>({ok:true,json:()=>new Promise(()=>{})}));
  const waiting=body.api('/api/client/me');
  const bodyRejected=assert.rejects(waiting,error=>error.timeout===true);
  [...body.timers.values()][0].fn();await bodyRejected;assert.equal(body.timers.size,0);

  const success=fixture(async()=>({ok:true,json:async()=>({saved:true})}));
  assert.equal((await success.api('/api/client/settings',{name:'Name'})).saved,true);assert.equal(success.timers.size,0);
  const conflict=fixture(async()=>({ok:false,status:409,json:async()=>({error:'Конфликт',field:'name',request_id:'test-reference'})}));
  await assert.rejects(conflict.api('/api/psychologist/profile',{}),error=>error.status===409&&error.field==='name'&&error.requestId==='test-reference'&&!error.network);
  assert.equal(conflict.timers.size,0);
  const disconnected=fixture(async()=>{throw TypeError('PRIVATE-NETWORK-DETAIL');});
  await assert.rejects(disconnected.api('/api/client/settings',{}),error=>error.network===true&&!error.message.includes('PRIVATE-'));
  const malformed=fixture(async()=>({ok:true,json:async()=>{throw SyntaxError('PRIVATE-RESPONSE');}}));
  await assert.rejects(malformed.api('/api/client/settings',{}),error=>error.network===true&&!error.message.includes('PRIVATE-'));
  console.log('Request and body deadlines, cleanup, safe errors and absence of automatic retries verified.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
