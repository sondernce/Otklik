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
  await adminPage.goto(origin+'/admin');const adminSupport=adminPage.locator('#admin-support');
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
