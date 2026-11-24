'use strict';
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const origin = process.env.OTKLIK_TEST_ORIGIN;
if (process.env.OTKLIK_BROWSER_TEST !== 'isolated-server' || !origin || new URL(origin).hostname !== '127.0.0.1') {
  throw Error('Run this suite via python3 -B tests/run_browser.py. It creates an isolated test database.');
}
let browser, admin;
before(async () => {
  browser = await chromium.launch({
    headless:true,
    ...(process.env.BROWSER_EXECUTABLE_PATH ? {executablePath:process.env.BROWSER_EXECUTABLE_PATH} : {}),
    args:process.env.BROWSER_NO_SANDBOX === '1' ? ['--no-sandbox'] : [],
  });
  admin = await browser.newContext();
  await post(admin, '/api/setup', {password:'test-admin-password'});
});
after(async () => {await browser?.close();});

async function post(context, path, data) {
  let response = await context.request.post(origin+path, {data, headers:{Origin:origin}});
  // The whole suite shares one local server. Keep its real authentication limits enabled.
  const deadline=Date.now()+65000;
  while(response.status()===429&&/\/api\/(client|psychologist)\/(register|login)$/.test(path)&&Date.now()<deadline){
    await new Promise(resolve=>setTimeout(resolve,1000));
    response=await context.request.post(origin+path,{data,headers:{Origin:origin}});
  }
  const body = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(body));
  return body;
}
async function get(context, path) {
  const response = await context.request.get(origin+path);
  assert.equal(response.status(), 200);
  return response.json();
}
async function capture(page, name) {
  const folder=process.env.OTKLIK_TEST_ARTIFACTS;
  if(!folder)return;
  fs.mkdirSync(folder,{recursive:true});
  await page.screenshot({path:path.join(folder,name),fullPage:true});
}
async function fixture(t, {registerClient=true, name='Тестовый психолог'}={}) {
  const errors = [];
  const psy = await browser.newContext(), client = await browser.newContext();
  for (const context of [psy,client]) context.on('page', page => {
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
  });
  t.after(async () => {await psy.close(); await client.close(); assert.deepEqual(errors, []);});
  const suffix = randomUUID();
  await post(psy, '/api/psychologist/register', {email:`psy-${suffix}@test.invalid`,password:'test-psychologist-password'});
  let profile = (await get(psy, '/api/psychologist/me')).profile;
  profile = (await post(psy, '/api/psychologist/profile', {
    revision:profile.revision,name,gender:'female',price:3000,experience:5,style:'gentle',approach:'КПТ',
    bio:'Профиль для проверки записи и изменения встреч.',education:'Учебное заведение и программа',topics:['anxiety'],photo:'',contact:'private-test-contact',
  })).profile;
  profile = (await post(psy, '/api/psychologist/submit', {revision:profile.revision})).profile;
  profile = (await post(admin, '/api/admin/review', {id:profile.id,revision:profile.revision,action:'approve'})).profile;
  const start = new Date(Date.now()+3*86400000); start.setUTCHours(14,30,0,0);
  const slots = [0,2,4].map(n => new Date(start.getTime()+n*3600000).toISOString().replace('.000Z','+00:00'));
  profile = (await post(psy, '/api/psychologist/schedule', {revision:profile.revision,slots})).profile;
  const email = `client-${suffix}@test.invalid`;
  if (registerClient) await post(client, '/api/client/register', {email,name:'Тестовый клиент',password:'test-client-password'});
  const page = await client.newPage(), psyPage = await psy.newPage();
  return {psy,client,page,psyPage,profile,slots,email};
}
async function bookingDraft(f) {
  await f.page.goto(origin+'/');
  await f.page.evaluate(({profile,slots,key}) => {
    sessionStorage.setItem('otklik-booking-draft', JSON.stringify({psychologist_id:profile.id,start_at:slots[0],request_key:key,name:profile.name,price:profile.price}));
    sessionStorage.setItem('otklik-flow-answers', JSON.stringify({topic:'anxiety',budget:'5000',gender:'any',time:'any',style:'any'}));
  }, {profile:f.profile,slots:f.slots,key:randomUUID()});
  await f.page.goto(origin+'/client#book');
  await f.page.locator('#book').waitFor({state:'visible'});
}
async function confirmed(f) {
  const item = (await post(f.client, '/api/client/appointments', {psychologist_id:f.profile.id,start_at:f.slots[0],request_key:randomUUID()})).appointment;
  await post(f.psy, '/api/psychologist/appointments/decision', {id:item.id,action:'confirm'});
  return item;
}

test('questionnaire → registration → booking → confirmation, including mobile and reload', async t => {
  const f = await fixture(t, {registerClient:false});
  await f.page.goto(origin+'/'); await f.page.locator('[data-start]').first().click();
  for (const value of ['anxiety','5000','any','any','any']) {
    await f.page.locator(`input[type=radio][value="${value}"]`).check(); await f.page.locator('#next').click();
  }
  // Other test profiles must never make this scenario choose a different specialist.
  await f.page.locator(`[data-person="${f.profile.id}"]`).click();
  await f.page.locator('[data-time="17:30"]').click(); await f.page.locator('#confirm').click();
  await f.page.waitForURL('**/client#book');
  await f.page.locator('#client-name').fill('Тестовый клиент');
  await f.page.locator('#client-age').fill('30');
  await f.page.locator('#client-email').fill(f.email); await f.page.locator('#client-password').fill('test-client-password');
  await f.page.locator('#client-auth-submit').click(); await f.page.locator('#book').waitFor({state:'visible'});
  assert.equal(await f.page.evaluate(() => JSON.parse(sessionStorage.getItem('otklik-flow-answers')).topic), 'anxiety');
  await f.page.locator('#book').click(); await f.page.getByText('Заявка отправлена. Психолог ответит').waitFor();
  const item = (await get(f.client, '/api/client/appointments')).appointments[0];
  await f.psyPage.goto(origin+'/psychologist');
  await f.psyPage.getByRole('button',{name:'Подтвердить',exact:true}).click();
  await f.psyPage.getByRole('button',{name:'Перенести',exact:true}).waitFor();
  const weekContains = await f.psyPage.evaluate(startAt => {
    const meetingDay = new Intl.DateTimeFormat('en-CA', {timeZone:'Europe/Moscow', year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date(startAt));
    return !!document.querySelector(`[data-cell-date="${meetingDay}"]`);
  }, item.start_at);
  if (!weekContains) await f.psyPage.locator('#week-next').click();
  await f.psyPage.locator(`[data-booking-id="${item.id}"]`).click();
  const meetingCard = f.psyPage.locator(`[data-appointment="${item.id}"]`);
  assert.equal(await meetingCard.evaluate(card => document.activeElement === card), true);
  await meetingCard.getByRole('button', {name:'Ссылка на видеовстречу'}).click();
  await meetingCard.getByLabel('Ссылка HTTPS').fill('https://example.test/meeting');
  await meetingCard.getByRole('button', {name:'Сохранить ссылку'}).click();
  await meetingCard.getByRole('link', {name:/Присоединиться/}).waitFor();

  await f.page.reload(); await f.page.locator('[data-client-tab=meetings]').click(); await f.page.locator(`[data-appointment="${item.id}"]`).getByText(/Подтверждена/).waitFor();
  assert.equal(await f.page.getByRole('link',{name:/Присоединиться/}).getAttribute('href'),'https://example.test/meeting');
  await f.page.locator('[data-client-tab=settings]').click();
  assert.equal(await f.page.locator('#client-settings input[name=age]').inputValue(),'30');
  await f.page.locator('[data-client-tab=overview]').click();
  await f.page.setViewportSize({width:390,height:844});
  assert.ok(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Mobile horizontal overflow');
});

test('both parties can transfer and cancel; drafts do not replace approved profiles', async t => {
  const f = await fixture(t), item = await confirmed(f);
  await f.page.goto(origin+'/client'); await f.page.locator('[data-client-tab=meetings]').click(); await f.page.getByRole('button',{name:'Перенести',exact:true}).click();
  await f.page.locator('[data-new-time]').selectOption(f.slots[1]);
  await f.page.getByRole('button',{name:'Предложить время',exact:true}).click();
  await f.page.getByText('Предложение отправлено. До ответа действует исходная встреча.').waitFor();
  assert.equal((await get(f.client,'/api/client/appointments')).appointments[0].start_at, f.slots[0]);
  await f.psyPage.goto(origin+'/psychologist'); await f.psyPage.getByRole('button',{name:'Принять перенос',exact:true}).click();
  await f.psyPage.getByText('Встреча перенесена.',{exact:true}).waitFor();
  await f.psyPage.locator('[data-page="application"]').click();
  await f.psyPage.locator('[name="price"]').fill('4500'); await f.psyPage.locator('#save').click();
  await f.psyPage.waitForFunction(() => document.getElementById('application-status').textContent.includes('Заявка на рассмотрении'));
  assert.equal((await get(f.client,'/api/profiles')).profiles.find(p=>p.id===f.profile.id).price, 3000);
  const p = (await get(f.psy,'/api/psychologist/me')).profile;
  await post(admin,'/api/admin/review',{id:p.id,revision:p.revision,action:'approve'});
  assert.equal((await get(f.client,'/api/profiles')).profiles.find(candidate=>candidate.id===p.id).price, 4500);
  await f.page.locator('#client-refresh').click();
  await f.page.getByText('Предложен перенос на').waitFor({state:'hidden'});
  assert.equal((await get(f.client,'/api/client/appointments')).appointments[0].price, 3000);
  await f.page.getByRole('button',{name:'Отменить встречу',exact:true}).click(); await f.page.getByText('Встреча отменена.',{exact:true}).waitFor();
  await f.page.getByRole('link',{name:'Записаться ещё раз',exact:true}).click();
  await f.page.locator('[data-time]').first().click(); await f.page.locator('#confirm').click(); await f.page.waitForURL('**/client#book');
  await f.page.locator('#book').waitFor({state:'visible'}); await f.page.locator('#book').click();
  await f.page.getByText('Заявка отправлена. Психолог ответит').waitFor();
  const second = (await get(f.client,'/api/client/appointments')).appointments.find(a=>a.status==='pending');
  await f.psyPage.goto(origin+'/psychologist'); const card = f.psyPage.locator(`[data-appointment="${second.id}"]`);
  await card.getByRole('button',{name:'Подтвердить',exact:true}).click(); await card.getByRole('button',{name:'Перенести',exact:true}).click();
  await card.getByRole('button',{name:'Предложить время',exact:true}).click(); await f.psyPage.getByText('Предложение отправлено. До ответа действует исходная встреча.').waitFor();
  await f.page.locator('#client-refresh').click(); await f.page.locator(`[data-appointment="${second.id}"]`).getByRole('button',{name:'Принять перенос',exact:true}).click();
  await f.page.getByText('Встреча перенесена.',{exact:true}).waitFor();
  await f.psyPage.locator('#reload').click(); await card.getByRole('button',{name:'Отменить встречу',exact:true}).click();
  await f.psyPage.getByText('Встреча отменена.',{exact:true}).waitFor();
  assert.equal((await get(f.client,'/api/client/appointments')).appointments.find(a=>a.id===second.id).cancelled_by,'psychologist');
});

for (const commitBeforeDrop of [false,true]) {
  test(`network failure ${commitBeforeDrop?'after commit':'before request'} keeps draft; retry creates one booking`, async t => {
    const f = await fixture(t); await bookingDraft(f);
    const route = '**/api/client/appointments';
    await f.page.route(route, async request => {
      if (commitBeforeDrop) {const response = await request.fetch(); assert.equal(response.status(),200);}
      await request.abort('failed');
    });
    await f.page.locator('#book').click();
    await f.page.waitForFunction(() => !document.getElementById('book').disabled && document.getElementById('client-message').textContent.length>0);
    assert.ok(await f.page.evaluate(() => sessionStorage.getItem('otklik-booking-draft')));
    assert.equal((await get(f.client,'/api/client/appointments')).appointments.length, commitBeforeDrop?1:0);
    await f.page.unroute(route); await f.page.locator('#book').click(); await f.page.getByText('Заявка отправлена. Психолог ответит').waitFor();
    assert.equal((await get(f.client,'/api/client/appointments')).appointments.length,1);
    assert.equal(await f.page.evaluate(() => sessionStorage.getItem('otklik-booking-draft')),null);
  });
}

test('expired login preserves selected time and asks for authentication', async t => {
  const f = await fixture(t); await bookingDraft(f); await f.client.clearCookies();
  await f.page.locator('#book').click(); await f.page.locator('#client-auth').waitFor({state:'visible'});
  assert.ok(await f.page.evaluate(() => sessionStorage.getItem('otklik-booking-draft')));
  await f.page.locator('#client-login-tab').click(); await f.page.locator('#client-email').fill(f.email);
  await f.page.locator('#client-password').fill('test-client-password'); await f.page.locator('#client-auth-submit').click();
  await f.page.locator('#book').waitFor({state:'visible'}); await f.page.locator('#book').click();
  await f.page.getByText('Заявка отправлена. Психолог ответит').waitFor();
  assert.equal((await get(f.client,'/api/client/appointments')).appointments[0].start_at,f.slots[0]);
});

test('untrusted profile text is displayed as text, including repeat booking', async t => {
  const name = '<img src=x onerror="window.injected=1">';
  const f = await fixture(t,{name}); await confirmed(f);
  await f.page.goto(origin+'/client'); await f.page.locator('[data-client-tab=meetings]').click(); assert.equal(await f.page.locator('.appointment-card h3').innerText(),name);
  await f.page.getByRole('link',{name:'Записаться ещё раз',exact:true}).click();
  await f.page.locator('#flow-title').waitFor(); assert.equal(await f.page.locator('#flow-title').innerText(),name);
  assert.equal(await f.page.locator('img[onerror]').count(),0);
  assert.equal(await f.page.evaluate(() => window.injected),undefined);
});

test('private messages refresh without losing a draft and disappear on logout', async t => {
  const f=await fixture(t),item=await confirmed(f);
  await f.page.goto(origin+'/client'); await f.page.locator('[data-client-tab=meetings]').click();await f.page.locator(`[data-chat-appointment="${item.id}"]`).click();
  const chat=f.page.locator('#client-chat'),body='<img src=x onerror="window.injected=1">\nВопрос о встрече';
  await chat.locator('[data-message-body]').fill(body);await chat.getByRole('button',{name:'Отправить',exact:true}).click();
  await chat.locator('[data-message-text]').getByText('Вопрос о встрече',{exact:false}).waitFor();
  await f.psyPage.goto(origin+'/psychologist');await f.psyPage.locator(`[data-chat-appointment="${item.id}"]`).click();
  const peer=f.psyPage.locator('#psychologist-chat');await peer.locator('[data-message-text]').waitFor();
  assert.equal(await peer.locator('[data-message-text]').innerText(),body);
  assert.equal(await peer.locator('img[onerror]').count(),0);
  await chat.locator('[data-message-body]').fill('Ещё не отправленный текст');
  await peer.locator('[data-message-body]').fill('Да, встреча состоится.');await peer.getByRole('button',{name:'Отправить',exact:true}).click();
  await chat.getByText('Да, встреча состоится.',{exact:true}).waitFor();
  assert.equal(await chat.locator('[data-message-body]').inputValue(),'Ещё не отправленный текст');
  await f.page.reload(); await f.page.locator('[data-client-tab=meetings]').click();await f.page.locator(`[data-chat-appointment="${item.id}"]`).click();
  await chat.getByText('Да, встреча состоится.',{exact:true}).waitFor();
  assert.equal(await chat.locator('[data-message-text]').count(),2);
  await f.page.setViewportSize({width:390,height:844});
  assert.ok(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await capture(f.page,'messages-mobile.png');
  await f.page.locator('#client-logout').click();await f.page.locator('#client-auth').waitFor({state:'visible'});
  assert.equal(await chat.locator('[data-message-text]').count(),0);
});

test('client and psychologist support tickets stay separate; admin can answer and close', async t => {
  const f=await fixture(t);
  await f.page.goto(origin+'/client'); await f.page.locator('[data-client-tab=support]').click(); const support=f.page.locator('#client-support');
  await support.getByLabel('Тема обращения').fill('Вопрос о моей записи');
  await support.getByLabel('Что произошло?').fill('Нужна помощь с переносом.');
  await support.getByRole('button',{name:'Создать обращение',exact:true}).click();
  await support.getByText('Нужна помощь с переносом.',{exact:true}).waitFor();
  const ticket=(await get(f.client,'/api/client/support/tickets')).tickets[0];
  const adminPage=await admin.newPage(),errors=[];adminPage.on('pageerror',error=>errors.push(error.message));
  t.after(async()=>{await adminPage.close();assert.deepEqual(errors,[]);});
  await adminPage.goto(origin+'/admin#admin-support');const adminSupport=adminPage.locator('#admin-support');
  await adminSupport.locator(`[data-ticket="${ticket.id}"]`).click();
  await adminSupport.getByText('Нужна помощь с переносом.',{exact:true}).waitFor();
  await adminSupport.locator('[data-message-body]').fill('Поможем разобраться.');
  await adminSupport.getByRole('button',{name:'Отправить',exact:true}).click();
  await adminSupport.getByText('Поможем разобраться.',{exact:true}).waitFor();
  await support.getByText('Поможем разобраться.',{exact:true}).waitFor();
  await adminSupport.getByRole('button',{name:'Закрыть обращение',exact:true}).click();
  await support.getByText('Обращение закрыто. Его можно открыть снова.',{exact:true}).waitFor();
  assert.equal(await support.locator('[data-message-body]').isDisabled(),true);
  await support.getByRole('button',{name:'Открыть снова',exact:true}).click();
  await support.getByRole('button',{name:'Закрыть обращение',exact:true}).waitFor();
  assert.equal(await support.locator('[data-message-body]').isDisabled(),false);
  assert.equal(await support.getByText('Обращение закрыто. Его можно открыть снова.',{exact:true}).count(),0);
  await f.psyPage.goto(origin+'/psychologist#support');const psySupport=f.psyPage.locator('#psychologist-support');
  await psySupport.getByLabel('Тема обращения').fill('Вопрос психолога');await psySupport.getByLabel('Что произошло?').fill('Вопрос по анкете.');
  await psySupport.getByRole('button',{name:'Создать обращение',exact:true}).click();
  await psySupport.getByText('Вопрос по анкете.',{exact:true}).waitFor();
  const own=(await get(f.client,'/api/client/support/tickets')).tickets;
  assert.equal(own.length,1);assert.equal(own[0].id,ticket.id);
  await f.page.setViewportSize({width:390,height:844});
  assert.ok(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await capture(f.page,'support-mobile.png');
});

for(const commitBeforeDrop of [false,true]) {
  test(`message retry after network failure ${commitBeforeDrop?'after commit':'before delivery'} sends once`,async t=>{
    const f=await fixture(t),item=await confirmed(f);
    await f.page.goto(origin+'/client'); await f.page.locator('[data-client-tab=meetings]').click();await f.page.locator(`[data-chat-appointment="${item.id}"]`).click();
    const chat=f.page.locator('#client-chat'),route=/\/api\/client\/messages(?:\?.*)?$/;
    await chat.locator('[data-message-body]').fill('Сообщение с повторной отправкой');
    await f.page.route(route,async request=>{
      if(request.request().method()!=='POST'){await request.continue();return;}
      if(commitBeforeDrop){const response=await request.fetch();assert.equal(response.status(),200);}
      await request.abort('failed');
    });
    await chat.getByRole('button',{name:'Отправить',exact:true}).click();
    await chat.getByText('Не удалось отправить сообщение. Текст сохранён в форме, повторите отправку.',{exact:true}).waitFor();
    assert.equal(await chat.locator('[data-message-body]').inputValue(),'Сообщение с повторной отправкой');
    const before=await get(f.client,`/api/client/messages?appointment_id=${item.id}`);
    assert.equal(before.messages.length,commitBeforeDrop?1:0);
    await f.page.unroute(route);await chat.getByRole('button',{name:'Отправить',exact:true}).click();
    await f.page.waitForFunction(()=>document.querySelector('#client-chat [data-message-body]').value==='');
    assert.equal((await get(f.client,`/api/client/messages?appointment_id=${item.id}`)).messages.length,1);
  });
}

test('notifications open a private conversation, update unread counts and retain read state',async t=>{
  const f=await fixture(t),item=await confirmed(f);
  await post(f.psy,'/api/psychologist/messages',{appointment_id:item.id,body:'Встречаемся по расписанию.',request_key:randomUUID()});
  await f.page.goto(origin+'/client');const notices=f.page.locator('#client-notifications');
  await notices.locator('summary').click();
  await notices.locator('[data-notification-id]').first().waitFor();
  const data=await get(f.client,'/api/client/notifications');
  assert.equal(data.unread_by_kind.appointment_message,1);
  const message=data.notifications.find(n=>n.kind==='appointment_message');
  await notices.locator(`[data-notification-id="${message.id}"]`).click();
  await f.page.locator('#client-chat').getByText('Встречаемся по расписанию.',{exact:true}).waitFor();
  await f.page.waitForFunction(()=>document.querySelector('[data-unread-messages]').textContent==='Непрочитанные сообщения: 0. Поддержка: 0.');
  await notices.getByRole('button',{name:'Прочитать все',exact:true}).click();
  await f.page.waitForFunction(()=>document.querySelector('[data-unread-count]').textContent==='0');
  await f.page.reload();await notices.locator('summary').click();
  await notices.locator('[data-notification-id]').first().waitFor();
  assert.equal(await notices.locator('[data-unread-count]').innerText(),'0');
  await post(f.psy,'/api/psychologist/messages',{appointment_id:item.id,body:'Новое сообщение после прочтения.',request_key:randomUUID()});
  await notices.getByRole('button',{name:'Обновить уведомления',exact:true}).click();
  await f.page.waitForFunction(()=>document.querySelector('[data-unread-count]').textContent==='1');
  await f.page.setViewportSize({width:390,height:844});
  assert.ok(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await capture(f.page,'notifications-mobile.png');
  await f.page.locator('#client-logout').click();await f.page.locator('#client-auth').waitFor({state:'visible'});
  assert.equal(await notices.locator('[data-notification-id]').count(),0);
});

test('psychologist and admin notifications navigate to the correct meeting or support ticket',async t=>{
  const f=await fixture(t);
  const item=(await post(f.client,'/api/client/appointments',{psychologist_id:f.profile.id,start_at:f.slots[0],request_key:randomUUID()})).appointment;
  await f.psyPage.goto(origin+'/psychologist#settings');const peer=f.psyPage.locator('#psychologist-notifications');
  await peer.locator('summary').click();await peer.getByRole('button',{name:/Новая заявка на встречу/}).click();
  await f.psyPage.locator(`[data-appointment="${item.id}"]`).getByRole('button',{name:'Подтвердить',exact:true}).waitFor();
  assert.equal(new URL(f.psyPage.url()).hash,'#overview');
  const ticket=(await post(f.psy,'/api/psychologist/support/tickets',{subject:'Помощь психологу',body:'Вопрос о работе кабинета.',request_key:randomUUID()})).ticket;
  const adminPage=await admin.newPage();t.after(()=>adminPage.close());
  await adminPage.goto(origin+'/admin');const adminNotices=adminPage.locator('#admin-notifications');
  await adminNotices.locator('summary').click();
  const data=await get(admin,'/api/admin/notifications'),notice=data.notifications.find(n=>n.target_id===ticket.id);
  assert.ok(notice);
  await adminNotices.locator(`[data-notification-id="${notice.id}"]`).click();
  await adminPage.locator('#admin-support').getByText('Вопрос о работе кабинета.',{exact:true}).waitFor();
  await post(admin,'/api/admin/support/messages',{ticket_id:ticket.id,body:'Ответ на вопрос психолога.',request_key:randomUUID()});
  await peer.getByRole('button',{name:'Обновить уведомления',exact:true}).click();
  await peer.getByRole('button',{name:/Ответ поддержки/}).click();
  await f.psyPage.locator('#psychologist-support').getByText('Ответ на вопрос психолога.',{exact:true}).waitFor();
  assert.equal(new URL(f.psyPage.url()).hash,'#support');
});

test('late notification response cannot restore private data after logout',async t=>{
  const f=await fixture(t);await confirmed(f);
  let release,delivered,captured;
  const gate=new Promise(resolve=>{release=resolve;}),ready=new Promise(resolve=>{captured=resolve;}),done=new Promise(resolve=>{delivered=resolve;});
  await f.page.route('**/api/client/notifications',async route=>{
    const response=await route.fetch();captured();await gate;await route.fulfill({response});delivered();
  });
  await f.page.goto(origin+'/client');await ready;
  await f.page.locator('#client-logout').click();await f.page.locator('#client-auth').waitFor({state:'visible'});
  release();await done;
  await f.page.waitForFunction(()=>document.querySelector('#client-notifications [data-unread-count]').textContent==='0');
  assert.equal(await f.page.locator('#client-notifications [data-notification-id]').count(),0);
});

test('logout can be retried after the server revokes the session but the response is lost',async t=>{
  const f=await fixture(t);await confirmed(f);
  await f.page.goto(origin+'/client');
  const route='**/api/client/logout';
  await f.page.route(route,async request=>{
    const response=await request.fetch();assert.equal(response.status(),200);
    await request.abort('failed');
  });
  await f.page.locator('#client-logout').click();
  await f.page.waitForFunction(()=>document.getElementById('client-message').textContent.length>0);
  assert.equal((await f.client.request.get(origin+'/api/client/me')).status(),401);
  await f.page.unroute(route);
  await f.page.locator('#client-logout').click();
  await f.page.locator('#client-auth').waitFor({state:'visible'});
  assert.equal(await f.page.locator('#client-notifications [data-notification-id]').count(),0);
});

for(const role of ['client','psychologist']){
  test(`late appointment response cannot repopulate ${role} workspace after switching account`,async t=>{
    const f=await fixture(t),item=await confirmed(f),page=role==='client'?f.page:f.psyPage;
    await page.goto(origin+'/'+role);
    const cards=page.locator(`#${role}-appointments`);
    await cards.locator(`[data-appointment="${item.id}"]`).waitFor({state:'attached'});
    let release,captured,delivered,held=false;
    const gate=new Promise(resolve=>{release=resolve;}),ready=new Promise(resolve=>{captured=resolve;}),done=new Promise(resolve=>{delivered=resolve;});
    await page.route(`**/api/${role}/appointments`,async route=>{
      if(held||route.request().method()!=='GET'){await route.continue();return;}
      held=true;const response=await route.fetch();captured();await gate;await route.fulfill({response});delivered();
    });
    try{
      await page.evaluate(role=>{window.pendingAppointmentRefresh=role==='client'?refresh():loadAppointments();},role);await ready;
      await page.locator(role==='client'?'#client-logout':'#logout').click();
      await page.locator(role==='client'?'#client-auth':'#auth').waitFor({state:'visible'});
      const email=`new-${role}-${randomUUID()}@test.invalid`,password='new-account-password';
      await post(role==='client'?f.client:f.psy,`/api/${role}/register`,{email,password,name:'Другой пользователь'});
      await page.locator(role==='client'?'#client-login-tab':'#login-tab').click();
      await page.locator(role==='client'?'#client-email':'#email').fill(email);
      await page.locator(role==='client'?'#client-password':'#password').fill(password);
      await page.locator(role==='client'?'#client-auth-submit':'#auth-submit').click();
      await page.locator(role==='client'?'#client-workspace':'#workspace').waitFor({state:'visible'});
      release();await done;await page.evaluate(()=>window.pendingAppointmentRefresh);
      if(role==='client')await page.locator('#client-next').getByText(/Найдите того/).waitFor();
      assert.equal(await cards.locator('[data-appointment]').count(),0,'Previous account appointments leaked into the new workspace');
      if(role==='client')assert.match(await page.locator('#client-next').innerText(),/Найдите того/);
      else assert.equal(await page.locator('#profile-form [name=name]').inputValue(),'');
    }finally{release();}
  });
}

for(const committed of [false,true]){
  test(`booking timeout ${committed?'after commit':'before delivery'} releases controls and retries once`,async t=>{
    const f=await fixture(t);await bookingDraft(f);await f.page.clock.install();
    let release,captured,delivered;
    const gate=new Promise(resolve=>{release=resolve;}),ready=new Promise(resolve=>{captured=resolve;}),done=new Promise(resolve=>{delivered=resolve;});
    const route='**/api/client/appointments';
    await f.page.route(route,async request=>{
      if(request.request().method()!=='POST'){await request.continue();return;}
      const response=committed?await request.fetch():null;captured();await gate;
      try{if(response)await request.fulfill({response});else await request.abort('failed');}catch{}finally{delivered();}
    });
    try{
      await f.page.locator('#book').click();await ready;await f.page.clock.runFor(15001);
      assert.equal(await f.page.locator('#book').isDisabled(),false,'Timed out booking must be retryable');
      assert.ok(await f.page.evaluate(()=>sessionStorage.getItem('otklik-booking-draft')));
      assert.equal((await get(f.client,'/api/client/appointments')).appointments.length,committed?1:0);
    }finally{release();await done;await f.page.unroute(route);}
    await f.page.locator('#book').click();await f.page.getByText('Заявка отправлена. Психолог ответит').waitFor();
    assert.equal((await get(f.client,'/api/client/appointments')).appointments.length,1);
  });
  test(`reschedule network failure ${committed?'after commit':'before delivery'} preserves chosen time and retry key`,async t=>{
    const f=await fixture(t),item=await confirmed(f);await f.page.goto(origin+'/client');
    await f.page.locator('[data-client-tab=meetings]').click();
    const card=f.page.locator(`[data-appointment="${item.id}"]`);
    await card.getByRole('button',{name:'Перенести',exact:true}).click();await card.locator('[data-new-time]').selectOption(f.slots[2]);
    const key=await card.locator('[data-change-form]').getAttribute('data-request-key'),route='**/api/client/appointments/reschedule/propose';
    await f.page.route(route,async request=>{if(committed)assert.equal((await request.fetch()).status(),200);await request.abort('failed');});
    await card.getByRole('button',{name:'Предложить время',exact:true}).click();
    await f.page.waitForFunction(()=>document.getElementById('client-message').textContent.length>0);
    assert.equal(await card.locator('[data-new-time]').count(),1,'Network error must preserve the reschedule form');
    assert.equal(await card.locator('[data-new-time]').inputValue(),f.slots[2]);
    assert.equal(await card.locator('[data-change-form]').getAttribute('data-request-key'),key);
    await f.page.unroute(route);await card.getByRole('button',{name:'Предложить время',exact:true}).click();
    await f.page.getByText('Предложение отправлено. До ответа действует исходная встреча.').waitFor();
    const saved=(await get(f.client,'/api/client/appointments')).appointments[0];
    assert.equal(saved.change.start_at,f.slots[2]);assert.equal(saved.history.filter(event=>event.action==='change_proposed').length,1);
  });
}

for(const committed of [false,true]){
  test(`profile timeout ${committed?'after commit':'before delivery'} preserves edits and prevents stale overwrite`,async t=>{
    const f=await fixture(t),page=f.psyPage;
    await page.goto(origin+'/psychologist#application');
    await page.locator('#profile-form [name=price]').fill('4200');await page.clock.install();
    let release,captured,delivered;
    const gate=new Promise(resolve=>{release=resolve;}),ready=new Promise(resolve=>{captured=resolve;}),done=new Promise(resolve=>{delivered=resolve;});
    const route='**/api/psychologist/profile';
    await page.route(route,async request=>{
      const response=committed?await request.fetch():null;captured();await gate;
      try{if(response)await request.fulfill({response});else await request.abort('failed');}catch{}finally{delivered();}
    });
    try{
      await page.locator('#save').click();await ready;await page.clock.runFor(15001);
      assert.equal(await page.locator('#save').isDisabled(),false);
      assert.equal(await page.locator('#profile-form [name=price]').inputValue(),'4200');
      assert.match(await page.locator('#dashboard-message').innerText(),/15 секунд/);
      assert.equal((await get(f.psy,'/api/psychologist/me')).profile.price,committed?4200:3000);
    }finally{release();await done;await page.unroute(route);}
    const response=page.waitForResponse(r=>r.url().endsWith('/api/psychologist/profile')&&r.request().method()==='POST');
    await page.locator('#save').click();assert.equal((await response).status(),committed?409:200);
    if(committed){
      await page.getByText('Анкета изменилась. Обновите страницу и повторите действие.',{exact:true}).waitFor();
      assert.equal(await page.locator('#profile-form [name=price]').inputValue(),'4200');
    }else await page.waitForFunction(()=>document.getElementById('application-status').textContent.includes('Заявка на рассмотрении'));
    const saved=(await get(f.psy,'/api/psychologist/me')).profile;
    assert.equal(saved.price,4200);
    // A successful approved-profile save also submits it for review, a separate revision.
    assert.equal(saved.revision,f.profile.revision+(committed?1:2));
    assert.equal(saved.status,committed?'draft':'pending');
  });
}

test('admin overview shows real counts, safe audit filters and links to related profiles',async t=>{
  const f=await fixture(t),page=await admin.newPage();t.after(()=>page.close());
  await post(admin,'/api/admin/review',{id:f.profile.id,revision:f.profile.revision,action:'hide',note:'PRIVATE-AUDIT-NOTE'});
  const expected=await get(admin,'/api/admin/overview');
  await page.goto(origin+'/admin');
  await page.getByText('Обзор обновлён. Время указано по Москве.',{exact:true}).waitFor();
  assert.equal(await page.locator('#monitor-users .metric-card').filter({has:page.locator('dt',{hasText:'Всего пользователей'})}).locator('dd').innerText(),String(expected.users.total));
  assert.match(await page.locator('#monitor-service').innerText(),/Не настроены/);
  await page.locator('.admin-navigation a[href="#monitor-journal"]').click();
  await page.locator('#monitor-category').selectOption('admin');await page.locator('#monitor-kind').selectOption('profile_hidden');
  await page.locator('#monitor-filter').getByRole('button',{name:'Применить фильтры',exact:true}).click();
  const event=page.locator('#monitor-events li').filter({has:page.locator(`[data-monitor-id="${f.profile.id}"]`),hasText:'Профиль скрыт'});
  await event.getByText('Профиль скрыт',{exact:true}).waitFor();
  assert.ok(!(await page.locator('#monitor-events').innerText()).includes('PRIVATE-AUDIT-NOTE'));
  await event.getByRole('button',{name:'Открыть',exact:true}).click();
  await page.locator('#detail h2').getByText('Тестовый психолог',{exact:true}).waitFor();
  assert.equal(await page.locator('#status-filter').inputValue(),'all');
  await page.locator('#decision-note').fill('Несохранённое решение администратора');
  await page.bringToFront();
  await page.clock.install();
  const automatic=page.waitForResponse(response=>response.url().includes('/api/admin/overview?'));
  await page.clock.runFor(30001);await automatic;
  assert.equal(await page.locator('#decision-note').inputValue(),'Несохранённое решение администратора');
  assert.equal(await page.locator('#monitor-category').inputValue(),'admin');
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Admin monitoring must fit mobile');
  await capture(page,'admin-monitor-mobile.png');
});

test('admin overview timeout keeps filters and can be manually retried',async t=>{
  const page=await admin.newPage();t.after(()=>page.close());
  await page.goto(origin+'/admin');await page.getByText('Обзор обновлён. Время указано по Москве.',{exact:true}).waitFor();
  await page.clock.install();let release,captured,delivered;
  const gate=new Promise(resolve=>{release=resolve;}),ready=new Promise(resolve=>{captured=resolve;}),done=new Promise(resolve=>{delivered=resolve;});
  const route='**/api/admin/overview?*';
  await page.route(route,async request=>{captured();await gate;try{await request.abort('failed');}catch{}finally{delivered();}});
  try{
    await page.locator('#monitor-days').selectOption('30');
    await page.locator('#monitor-period').getByRole('button',{name:'Обновить обзор и журнал',exact:true}).click();
    await ready;await page.clock.runFor(15001);
    assert.match(await page.locator('#monitor-message').innerText(),/15 секунд/);
    assert.equal(await page.locator('#monitor-days').inputValue(),'30');
  }finally{release();await done;await page.unroute(route);}
  await page.locator('#monitor-period').getByRole('button',{name:'Обновить обзор и журнал',exact:true}).click();
  await page.getByText('Обзор обновлён. Время указано по Москве.',{exact:true}).waitFor();
});

test('late admin monitoring response cannot restore data after logout',async t=>{
  const isolated=await browser.newContext(),page=await isolated.newPage();t.after(()=>isolated.close());
  await post(isolated,'/api/login',{password:'test-admin-password'});
  let release,captured,delivered;
  const gate=new Promise(resolve=>{release=resolve;}),ready=new Promise(resolve=>{captured=resolve;}),done=new Promise(resolve=>{delivered=resolve;});
  await page.route('**/api/admin/overview?*',async request=>{const response=await request.fetch();captured();await gate;try{await request.fulfill({response});}catch{}finally{delivered();}});
  try{
    await page.goto(origin+'/admin');await ready;
    await page.locator('#logout').click();await page.locator('#auth').waitFor({state:'visible'});
  }finally{release();await done;}
  assert.equal(await page.locator('#monitor-users').innerText(),'');
  assert.equal(await page.locator('#monitor-events').innerText(),'');
  assert.equal(await page.locator('#monitor-service').innerText(),'');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('#monitor-users').innerText(),'');
});

test('client home opens its real meeting, keeps section on reload and validates registration inline', async t => {
  const f=await fixture(t),item=await confirmed(f);
  await f.page.goto(origin+'/client');
  await f.page.locator('#client-specialist').getByText('Тестовый психолог',{exact:true}).waitFor();
  await f.page.locator('#client-next [data-home-appointment]').first().click();
  await f.page.locator(`[data-appointment="${item.id}"]`).waitFor({state:'visible'});
  assert.equal(new URL(f.page.url()).hash,'#meetings');
  await f.page.reload();
  await f.page.locator('[data-client-view="meetings"]').waitFor({state:'visible'});
  await f.page.locator('[data-client-tab="settings"]').click();
  await f.page.goBack();
  await f.page.locator('[data-client-view="meetings"]').waitFor({state:'visible'});
  const anon=await browser.newContext();t.after(()=>anon.close());const page=await anon.newPage();
  await page.goto(origin+'/client');await page.locator('#client-auth-submit').click();
  for(const id of ['client-name','client-age','client-email','client-password'])assert.equal(await page.locator('#'+id).getAttribute('aria-invalid'),'true');
});

function localAccessLink(email,purpose){
  const {execFileSync}=require('node:child_process');
  const script=`import sys,re
from pathlib import Path
from email import policy
from email.parser import BytesParser
import database,mail_worker
path=Path(sys.argv[1])
with database.connect(path) as db:
    row=(db.execute('SELECT mail_id FROM email_changes WHERE new_email=? ORDER BY created_at DESC LIMIT 1',(sys.argv[2],)) if sys.argv[3]=='change' else db.execute('SELECT mail_id FROM email_tokens WHERE email=? AND purpose=? ORDER BY created_at DESC LIMIT 1',(sys.argv[2],sys.argv[3]))).fetchone()
    if row is None:raise RuntimeError('Missing test email')
    ident=row['mail_id']
mail_worker.run_once(path,limit=100)
message=BytesParser(policy=policy.default).parsebytes((path.parent/'mail-preview'/(ident+'.eml')).read_bytes())
print(re.search(r'http[^\\s]+/account-access#[^\\s]+',message.get_content()).group())`;
  assert.ok(process.env.OTKLIK_TEST_DATABASE);
  return execFileSync('python',['-B','-c',script,process.env.OTKLIK_TEST_DATABASE,email,purpose],{encoding:'utf8'}).trim();
}

test('client email confirmation uses a local message, keeps booking draft and requires explicit action',async t=>{
  const f=await fixture(t);await bookingDraft(f);
  const draft=await f.page.evaluate(()=>sessionStorage.getItem('otklik-booking-draft'));
  const link=localAccessLink(f.email,'verify'),token=new URL(link).hash;
  await f.page.goto(link);
  assert.equal(new URL(f.page.url()).hash,'','Bearer secret must be removed from the visible URL');
  assert.equal((await get(f.client,'/api/client/email/status')).email_verified,false);
  await f.page.getByRole('button',{name:'Подтвердить почту',exact:true}).click();
  await f.page.getByText('Почта подтверждена.',{exact:true}).waitFor();
  assert.equal((await get(f.client,'/api/client/email/status')).email_verified,true);
  assert.equal(await f.page.evaluate(()=>sessionStorage.getItem('otklik-booking-draft')),draft);
  await f.page.goto(origin+'/client#settings');
  await f.page.locator('#client-email-access').getByText('Электронная почта подтверждена.',{exact:true}).waitFor();
  assert.ok(!(await f.page.content()).includes(token));
});

test('password recovery preserves fields and draft after lost reply and supports login with the new password',async t=>{
  const f=await fixture(t);await bookingDraft(f);
  const draft=await f.page.evaluate(()=>sessionStorage.getItem('otklik-booking-draft'));
  await f.page.locator('#client-logout').click();await f.page.locator('#client-auth').waitFor({state:'visible'});
  await f.page.getByRole('link',{name:'Забыли пароль?',exact:true}).click();
  await f.page.locator('#access-email').fill(f.email);await f.page.getByRole('button',{name:'Получить ссылку',exact:true}).click();
  await f.page.getByText(/Если аккаунт с такой почтой существует/).waitFor();
  const link=localAccessLink(f.email,'reset');await f.page.goto(link);
  await f.page.setViewportSize({width:390,height:844});
  await f.page.locator('#access-reset').waitFor({state:'visible'});
  assert.ok(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await capture(f.page,'password-recovery-mobile.png');
  await f.page.locator('#access-password').fill('new-browser-password');await f.page.locator('#access-password-repeat').fill('different-password');
  await f.page.getByRole('button',{name:'Сохранить новый пароль',exact:true}).click();
  await f.page.getByText('Пароли должны совпадать.',{exact:true}).waitFor();
  await f.page.locator('#access-password-repeat').fill('new-browser-password');
  const route='**/api/client/password/reset';
  let release,captured;
  const gate=new Promise(resolve=>{release=resolve;}),ready=new Promise(resolve=>{captured=resolve;});
  await f.page.route(route,async request=>{const response=await request.fetch();assert.equal(response.status(),200);captured();await gate;await request.abort('failed');});
  try{
    await f.page.getByRole('button',{name:'Сохранить новый пароль',exact:true}).click();await ready;
    assert.equal(await f.page.locator('#access-password').isDisabled(),true,'Password must not change while the server processes the submitted value');
  }finally{release();}
  await f.page.waitForFunction(()=>document.getElementById('access-message').textContent.includes('Не удалось получить ответ'));
  assert.equal(await f.page.locator('#access-password').inputValue(),'new-browser-password');
  await f.page.unroute(route);
  await f.page.getByRole('button',{name:'Сохранить новый пароль',exact:true}).click();
  await f.page.getByText(/Если пароль уже меняли по этой ссылке/).waitFor();
  await f.page.getByRole('link',{name:'Вернуться к входу',exact:true}).click();
  await f.page.locator('#client-email').fill(f.email);await f.page.locator('#client-password').fill('new-browser-password');
  await f.page.locator('#client-auth-submit').click();await f.page.locator('#client-workspace').waitFor({state:'visible'});
  assert.equal(await f.page.evaluate(()=>sessionStorage.getItem('otklik-booking-draft')),draft);
});

test('psychologist can confirm email from local preview and refresh confirmation in settings',async t=>{
  const f=await fixture(t);await f.psyPage.goto(origin+'/psychologist#settings');
  const email=(await get(f.psy,'/api/psychologist/me')).email;
  await f.psyPage.locator('#psychologist-email-access').getByText('Электронная почта пока не подтверждена.',{exact:true}).waitFor();
  const link=localAccessLink(email,'verify'),confirmation=await f.psy.newPage();t.after(()=>confirmation.close());
  await confirmation.goto(link);await confirmation.getByRole('button',{name:'Подтвердить почту',exact:true}).click();
  await confirmation.getByText('Почта подтверждена.',{exact:true}).waitFor();
  await f.psyPage.locator('#psychologist-email-access').getByRole('button',{name:'Обновить подтверждение',exact:true}).click();
  await f.psyPage.locator('#psychologist-email-access').getByText('Электронная почта подтверждена.',{exact:true}).waitFor();
  await f.psyPage.setViewportSize({width:390,height:844});
  assert.ok(await f.psyPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
});

test('account email change confirms the new address and ends sessions for both roles',async t=>{
  const f=await fixture(t);
  for(const [role,page,context] of [['client',f.page,f.client],['psychologist',f.psyPage,f.psy]]){
    await page.goto(origin+'/'+role+'#settings');
    const root=page.locator('#'+role+'-account-management');await root.getByText('Сменить почту',{exact:true}).click();
    const form=root.locator('[data-account-email]'),newEmail=role+'-new-'+randomUUID()+'@test.invalid';
    await form.locator('[name=email]').fill(newEmail);
    await form.locator('[name=password]').fill(role==='client'?'test-client-password':'test-psychologist-password');
    const route='**/api/'+role+'/account/email-change';let originalKey;
    await page.route(route,async request=>{originalKey=request.request().postDataJSON().request_key;assert.equal((await request.fetch()).status(),200);await request.abort('failed');});
    await form.getByRole('button').click();await form.getByText(/Не удалось получить ответ/).waitFor();
    assert.ok(await form.locator('[name=password]').inputValue());
    await page.unroute(route);await form.getByRole('button').click();await form.getByText(/Проверьте письмо на новом адресе/).waitFor();
    await form.locator('[name=password]').fill(role==='client'?'test-client-password':'test-psychologist-password');
    const sending=page.waitForRequest(request=>request.url().endsWith('/account/email-change'));
    await form.getByRole('button').click();
    assert.notEqual((await sending).postDataJSON().request_key,originalKey,'A new request after an acknowledged response must allow resending later');
    await form.getByText(/Если адрес доступен/).waitFor();
    const link=localAccessLink(newEmail,'change');await page.goto(link);
    assert.equal(new URL(page.url()).hash,'');
    await page.getByRole('button',{name:'Подтвердить новую почту',exact:true}).click();await page.getByText(/Адрес изменён/).waitFor();
    assert.equal((await context.request.get(origin+'/api/'+role+'/me')).status(),401);
    await page.getByRole('link',{name:'Вернуться к входу',exact:true}).click();
    await page.locator(role==='client'?'#client-email':'#email').fill(newEmail);
    await page.locator(role==='client'?'#client-password':'#password').fill(role==='client'?'test-client-password':'test-psychologist-password');
    await page.locator(role==='client'?'#client-auth-submit':'#auth-submit').click();
    await page.locator(role==='client'?'#client-workspace':'#workspace').waitFor({state:'visible'});
    await page.goto(origin+'/'+role+'#settings');
    await page.locator('#'+role+'-email-access').getByText('Электронная почта подтверждена.',{exact:true}).waitFor();
  }
});

test('admin can block and unblock accounts and late account lists do not survive logout',async t=>{
  const f=await fixture(t),page=await admin.newPage();t.after(()=>page.close());
  await page.goto(origin+'/admin#admin-accounts');
  const root=page.locator('#admin-accounts');await root.locator('[name=search]').fill(f.email);await root.getByRole('button',{name:'Найти аккаунты',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('#admin-accounts article').length===1);
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await capture(page,'admin-accounts-mobile.png');
  page.on('dialog',dialog=>dialog.accept());
  await root.getByRole('button',{name:'Заблокировать аккаунт',exact:true}).click();
  await root.getByRole('button',{name:'Разблокировать аккаунт',exact:true}).waitFor();
  assert.equal((await f.client.request.get(origin+'/api/client/me')).status(),401);
  await root.getByRole('button',{name:'Разблокировать аккаунт',exact:true}).click();
  await root.getByRole('button',{name:'Заблокировать аккаунт',exact:true}).waitFor();
  assert.equal((await f.client.request.get(origin+'/api/client/me')).status(),401);
  await post(f.client,'/api/client/login',{email:f.email,password:'test-client-password'});
  let release,captured;const gate=new Promise(resolve=>{release=resolve;}),ready=new Promise(resolve=>{captured=resolve;});
  await page.route('**/api/admin/accounts?*',async route=>{const response=await route.fetch();captured();await gate;await route.fulfill({response});});
  await root.getByRole('button',{name:'Найти аккаунты',exact:true}).click();await ready;
  await page.locator('#logout').click();await page.locator('#auth').waitFor({state:'visible'});release();
  await page.waitForTimeout(100);assert.equal(await root.locator('[data-account-list]').textContent(),'');
  await page.unroute('**/api/admin/accounts?*');await post(admin,'/api/login',{password:'test-admin-password'});
});

test('account deletion requires cancelling meetings and retains conversation history',async t=>{
  const f=await fixture(t),item=await confirmed(f);
  await post(f.client,'/api/client/messages',{appointment_id:item.id,body:'История сохраняется после удаления',request_key:randomUUID()});
  await f.page.goto(origin+'/client#settings');await f.page.setViewportSize({width:390,height:844});
  const root=f.page.locator('#client-account-management');await root.locator('summary').getByText('Удалить аккаунт',{exact:true}).click();
  const form=root.locator('[data-account-delete]');
  await form.locator('[name=password]').fill('test-client-password');await form.locator('[name=confirmation]').fill('УДАЛИТЬ');
  assert.ok(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await capture(f.page,'account-deletion-mobile.png');
  await form.getByRole('button',{name:'Удалить аккаунт',exact:true}).click();await form.getByText(/Сначала отмените будущие встречи/).waitFor();
  const actual=(await get(f.client,'/api/client/appointments')).appointments.find(x=>x.id===item.id);
  await post(f.client,'/api/client/appointments/cancel',{id:item.id,revision:actual.revision});
  await form.getByRole('button',{name:'Удалить аккаунт',exact:true}).click();await f.page.locator('#client-auth').waitFor({state:'visible'});
  assert.equal((await f.client.request.get(origin+'/api/client/me')).status(),401);
  assert.equal(await form.locator('[name=password]').inputValue(),'');
  const history=await get(f.psy,'/api/psychologist/messages?appointment_id='+item.id);
  assert.equal(history.messages[0].body,'История сохраняется после удаления');
});

function finishTestMeeting(id){
  const {execFileSync}=require('node:child_process');
  assert.ok(process.env.OTKLIK_TEST_DATABASE);
  execFileSync('python',['-B','-c',`import sys,database
with database.connect(sys.argv[1]) as db:
    db.execute("UPDATE appointments SET start_at='2000-01-01T12:00:00+00:00',end_at='2000-01-01T12:50:00+00:00' WHERE id=?",(sys.argv[2],))`,process.env.OTKLIK_TEST_DATABASE,id]);
}

test('meeting outcomes retain a lost response, flag disagreement and allow admin resolution',async t=>{
  const f=await fixture(t),item=await confirmed(f);finishTestMeeting(item.id);
  await f.page.goto(origin+'/client#meetings');
  const card=f.page.locator('[data-appointment="'+item.id+'"]');
  await card.locator('[data-outcome-value]').selectOption('held');
  const route='**/api/client/appointments/outcome';
  await f.page.route(route,async request=>{assert.equal((await request.fetch()).status(),200);await request.abort('failed');});
  await card.getByRole('button',{name:'Сохранить ответ о встрече',exact:true}).click();await f.page.getByText(/Не удалось получить ответ/).waitFor();
  assert.equal(await card.locator('[data-outcome-value]').inputValue(),'held');
  await f.page.unroute(route);await card.getByRole('button',{name:'Сохранить ответ о встрече',exact:true}).click();
  await card.getByText('Ожидается ответ второй стороны',{exact:true}).waitFor();
  await f.psyPage.goto(origin+'/psychologist#overview');const psyCard=f.psyPage.locator('[data-appointment="'+item.id+'"]');
  await psyCard.locator('[data-outcome-value]').selectOption('client_absent');await psyCard.getByRole('button',{name:'Сохранить ответ о встрече',exact:true}).click();
  await psyCard.getByText('Ответы различаются, требуется разбор администратора',{exact:true}).waitFor();
  const page=await admin.newPage();t.after(()=>page.close());await page.goto(origin+'/admin#admin-appointments');
  await page.locator('#admin-outcome-filter').selectOption('disputed');
  const adminCard=page.locator('[data-appointment="'+item.id+'"]');await adminCard.locator('[data-outcome-value]').selectOption('held');
  page.on('dialog',dialog=>dialog.accept());await adminCard.getByRole('button',{name:'Сохранить итог администратора',exact:true}).click();
  await adminCard.waitFor({state:'detached'});
  await f.page.reload();await card.getByText('Результат определён администратором: Встреча состоялась',{exact:true}).waitFor();
  assert.equal(await card.locator('[data-outcome-value]').count(),0);
  await f.page.setViewportSize({width:390,height:844});assert.ok(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await capture(f.page,'meeting-outcome-mobile.png');
});
