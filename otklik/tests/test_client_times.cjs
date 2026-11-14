const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const storage=new Map();
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{hidden:false,innerHTML:'',textContent:'',addEventListener(){},focus(){},querySelector(){return element('child');},querySelectorAll(){return []}});return elements.get(id);}
const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[]},window:{scrollTo(){}},requestAnimationFrame:fn=>fn(),Intl,Date,console,crypto:require('node:crypto'),location:{href:'/',search:''},URLSearchParams,sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)}});
vm.runInContext(fs.readFileSync('app.js','utf8')+`
 const testProfile={id:'test',name:'Тест',initials:'Т',approach:'КПТ',education:'Вуз',experience:3,bio:'Тестовый профиль',local:true,topics:['anxiety'],price:3000,slots:['2099-01-15T09:30:00Z','2099-01-15T11:45:00Z','2099-01-16T09:00:00Z']};
 state.answers={topic:'anxiety',budget:'5000',gender:'any',time:'any',style:'any'};
 testProfile.times=testProfile.slots.map(slotClock);state.person=testProfile;state.days=localDays(testProfile);profile();
 globalThis.output={clock:slotClock(testProfile.slots[0]),days:state.days,html:content.innerHTML};
 state.time='12:30';confirm();globalThis.output.destination=location.href;
 globalThis.output.draft=JSON.parse(sessionStorage.getItem('otklik-booking-draft'));
 globalThis.output.answers=JSON.parse(sessionStorage.getItem('otklik-flow-answers'));
 state.answers.time='day';globalThis.output.day=['11:59','12:00','17:00','17:01'].filter(timeMatches);
 state.answers.time='evening';globalThis.output.evening=['17:59','18:00','21:00','21:01'].filter(timeMatches);
`,context);
assert.equal(context.output.clock,'12:30');
assert.deepEqual(Array.from(context.output.days[0].times),['12:30','14:45']);
assert.match(context.output.html,/data-time="12:30">12:30/);
assert.match(context.output.html,/data-time="14:45">14:45/);
assert.equal(context.output.destination,'/client#book');
assert.equal(context.output.draft.start_at,'2099-01-15T09:30:00Z');
assert.equal(context.output.draft.psychologist_id,'test');
assert.equal(context.output.answers.topic,'anxiety');
assert.deepEqual(Array.from(context.output.day),['12:00','17:00']);
assert.deepEqual(Array.from(context.output.evening),['18:00','21:00']);
console.log('Minutes, exact booking timestamp, saved answers and time boundaries verified.');
