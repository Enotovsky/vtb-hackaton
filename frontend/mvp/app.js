import {budgetGroups, familyBudget} from './budget.js';
import {api} from './api.js';
import {$,esc,money,cents,monthLabel,dateLabel,monthEnd,icon,avatar,button,heading,field,select,errorSlot,person,account,childList,planFor,planStatuses,inviteStatuses,badge,empty,sourceLabel,offerList} from './ui.js';
import * as views from './views.js';

let s=null, refreshing=false, offline=false, authEpoch=0, toastTimer, restoreFocus, dialogState=null, searchItems=[], paymentDraft=null;
const c={route:'home',tab:'overview',month:'',from:'',to:'',childId:'max',accountId:'family',who:'all',category:null};
const sheet=$('#sheet'), main=$('#main');
const monthRange=m=>{c.month=m;c.from=m+'-01';c.to=m+'-'+monthEnd(m);};
const canManage=()=>s?.family&&s.user.role!=='child';
const safeURL=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:null;}catch{return null;}};
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),4200);}
function error(message,code=''){
  const target=sheet.open?sheet.querySelector('.form-error'):$('#login-error');
  if(target){target.innerHTML=esc(message)+(code==='BUDGET_CONFLICT'?button('Загрузить актуальные суммы','reload-budget','',''):code==='CONFLICT'&&dialogState?.type==='plan'?button('Загрузить актуальный план','reload-plan','',''):code==='NETWORK'&&dialogState?.type==='payment'?button('Проверить эту же операцию','retry-payment','',''):'');target.focus();}else toast(message);
}
function show(html,state={}){const already=sheet.open;if(!already)restoreFocus=document.activeElement;$('#sheet-content').innerHTML=html;dialogState=state;if(!already)sheet.showModal();requestAnimationFrame(()=>{const target=sheet.querySelector('input:not([type=hidden]), select, textarea')||sheet.querySelector('.close-sheet');target?.focus({preventScroll:true});});}
function close(){if(sheet.open)sheet.close();dialogState=null;const target=restoreFocus?.isConnected?restoreFocus:main;target?.focus({preventScroll:true});}
sheet.addEventListener('cancel',()=>{dialogState=null;requestAnimationFrame(()=>restoreFocus?.isConnected&&restoreFocus.focus());});
sheet.addEventListener('click',e=>{if(e.target===sheet){const r=sheet.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close();}});
function searchClose(){$('#family-search-panel').hidden=true;}
function go(route){searchClose();close();if(location.hash==='#'+route)render();else location.hash=route;}
function connection(message){const node=$('#connection-status');node.hidden=!message;node.innerHTML=message?`${esc(message)} ${button('Обновить','refresh','','')}`:'';}
function setSnapshot(value){const revoked=s?.family&&!value.family;const oldUser=s?.user.id;s=value;if(!c.month)monthRange(s.today.slice(0,7));if(!s.accounts?.some(a=>a.id===c.accountId))c.accountId=s.accounts?.[0]?.id||'family';if(revoked||oldUser&&oldUser!==s.user.id){close();searchClose();$('#family-search-input').value='';}render();}
async function refresh(force=false){
  if(refreshing&&!force)return;const epoch=authEpoch;refreshing=true;
  try{const value=await api.snapshot();if(epoch!==authEpoch)return;if(s&&value.user.id===s.user.id&&value.revision<s.revision)return;const changed=!s||value.revision!==s.revision||value.user.id!==s.user.id;offline=false;connection('');if(changed||force)setSnapshot(value);updatePayment();}
  catch(e){if(epoch!==authEpoch)return;if(e.status===401){s=null;api.token='';sessionStorage.removeItem('vtb-mvp-session');close();searchClose();render();}else{offline=true;connection((s?'Показаны последние загруженные данные. ':'')+e.message);if(!s){main.innerHTML=`<div class="empty-state">${icon('refresh')}Не удалось загрузить семейное пространство.${button('Повторить загрузку','refresh','','primary-btn full section-gap')}</div>`;}}}
  finally{refreshing=false;}
}
function render(){
  const oldFocus=document.activeElement;const focusKey=oldFocus?.dataset.action,focusId=oldFocus?.dataset.id;
  c.route=location.hash.slice(1).split('/')[0]||'home';
  const authenticated=Boolean(s);const member=Boolean(s?.family);const adult=canManage();
  $('.global-search').hidden=!member;
  $('#profile-actions').innerHTML=authenticated?`${adult?button(icon('bell'),'reminders','','icon-btn','aria-label="Уведомления"'):''}<button class="avatar avatar-${s.user.tone} small" data-action="profile" aria-label="Профиль ${esc(s.user.name)}">${esc(s.user.name[0])}</button>`:'';
  if(!authenticated){main.innerHTML=views.login();$('.bottom-nav').innerHTML='';document.title='ВТБ Семья · Вход';return;}
  if(!member){main.innerHTML=views.disconnected(s);$('.bottom-nav').innerHTML='';document.title='ВТБ Семья · Приглашения';return;}
  if(!adult){main.innerHTML=views.kids(s,c,s.user.id);$('.bottom-nav').innerHTML=button(icon('card')+'Моя карта','home','','nav-item active')+button(icon('people')+'Профиль','profile','','nav-item');}
  else {
    const renderers={home:()=>views.home(s,c),budget:()=>views.budget(s,c),kids:()=>views.kids(s,c),child:()=>views.kids(s,c,location.hash.split('/')[1]),offers:()=>views.offers(s),notes:()=>views.notes(s),more:()=>views.more(s)};
    main.innerHTML=(renderers[c.route]||renderers.home)();
    $('.bottom-nav').innerHTML=[['home','home','Главная'],['budget','chart','Бюджет'],['kids','people','Дети'],['offers','gift','Для вас'],['more','more','Ещё']].map(([id,i,t])=>`<a href="#${id}" class="nav-item ${c.route===id||id==='kids'&&c.route==='child'?'active':''}" ${c.route===id?'aria-current="page"':''}>${icon(i)}${t}</a>`).join('');
  }
  document.title='ВТБ Семья · '+({home:'Главная',budget:'Бюджет',kids:'Дети',child:'Детская карта',offers:'Для вас',notes:'Заметки',more:'Моя семья'}[c.route]||'Главная');
  requestAnimationFrame(()=>main.querySelectorAll('[data-dash]').forEach(n=>n.setAttribute('stroke-dasharray',n.dataset.dash)));
  if(oldFocus&&main.contains(oldFocus)===false&&focusKey&&!sheet.open)[...main.querySelectorAll('[data-action]')].find(n=>n.dataset.action===focusKey&&n.dataset.id===focusId)?.focus({preventScroll:true});
  if(!$('#family-search-panel').hidden)search($('#family-search-input').value);
}
async function mutate(path,body,method='POST'){const result=await api.mutate(path,body,method);await refresh(true);return result;}
const childrenOptions=()=>childList(s).map(m=>({id:m.id,name:m.name}));
function profile(){show(heading('Вы вошли как '+esc(s.user.name),'Демонстрационный профиль для проверки сценария')+`<div class="selected-person">${avatar(s.user)}<div><strong>${esc(s.user.name)}</strong><small>${s.user.role==='owner'?'Организатор':s.user.role==='child'?'Ребёнок':'Второй взрослый'}</small></div></div>${s.personalAccount?`<div class="summary-box"><div class="between"><span>Моя личная карта</span><b>${money(s.personalAccount.balance)}</b></div><small class="muted">Этот остаток виден только в вашем профиле.</small></div>`:''}<a href="/?mobile=1&login=1#home" target="_blank" rel="noopener">Открыть другой вход в новой вкладке ↗</a>${button('Сменить профиль','logout','','secondary-btn full section-gap')}${errorSlot}`);}
function members(){show(heading('Семья '+esc(s.family.name),'Общие деньги видны взрослым, личные покупки закрыты.')+`<div class="row-list">${s.members.map(m=>`<div class="list-row">${avatar(m,true)}<div class="grow"><strong>${esc(m.name)}${m.id===s.user.id?' · вы':''}</strong><small>${m.role==='owner'?'Организатор':m.role==='child'?'Ребёнок':'Партнёр'}</small></div>${m.id==='maria'&&s.user.role==='owner'?button('Отключить','remove-member',m.id,'text-link'):''}</div>`).join('')}</div>${s.user.role==='owner'&&!s.members.some(m=>m.id==='maria')?button('Пригласить Марию','invite','','primary-btn full section-gap'):''}${s.invitations.length?`<h3 class="small-section">История приглашений</h3>${s.invitations.slice().reverse().map(i=>`<div class="invite-status"><div class="row"><strong>${esc(i.recipientName)}</strong>${badge(...inviteStatuses[i.status])}</div>${i.status==='sent'?`<div class="button-pair">${button('Открыть','invite',i.id,'secondary-btn')}${button('Отменить','cancel-invite',i.id,'text-link')}</div>`:''}</div>`).join('')}`:' '}${errorSlot}`);}
function invite(){const i=s.invitations.slice().reverse().find(i=>i.status==='sent');show(heading(i?'Приглашение отправлено':'Пригласите Марию','Близкий увидит, что станет общим, и сам подтвердит доступ.')+`<div class="selected-person">${avatar({name:'Мария',tone:'partner'})}<div><strong>Мария</strong><small>Второй взрослый · тестовый профиль</small></div></div><ul class="privacy-points"><li>${icon('check')}Одинаковый общий остаток и история</li><li>${icon('calendar')}Совместное согласование детского бюджета</li><li>${icon('lock')}Личные остатки и покупки остаются закрыты</li></ul>${i?`${badge(...inviteStatuses[i.status])}<p class="data-caption">Действует до ${dateLabel(i.expiresAt)}</p><div class="button-pair">${button('Скопировать ссылку','copy-invite',i.id,'primary-btn')}${button('Отменить','cancel-invite',i.id,'secondary-btn')}</div><div class="demo-tools"><a href="/?mobile=1&login=1&invite=${i.id}#home" target="_blank" rel="noopener">Открыть приглашение во втором входе ↗</a><p>Выберите Марию в новой вкладке. Ссылка локальная и работает на этом компьютере.</p><details class="disclosure"><summary>Проверить истёкшее приглашение</summary>${button('Завершить срок в демо','expire-invite',i.id,'secondary-btn')}</details></div>`:button('Создать приглашение','send-invite','','primary-btn full')}${errorSlot}`);}
function invitation(id){const i=s.invitations.find(i=>i.id===id);if(!i)return;show(heading('Александр приглашает вас','В семейное пространство Ивановых')+`<ul class="privacy-points"><li>${icon('people')}Обоим взрослым доступны общий счёт, общая история, детские расходы и заметки.</li><li>${icon('lock')}Ваш личный счёт и покупки на нём останутся видны только вам.</li><li>${icon('shield')}Организатор управляет составом. Можно выйти из семьи и прекратить доступ.</li></ul><form id="accept-form"><input type="hidden" name="id" value="${i.id}"><label class="inline-check"><input type="checkbox" name="consent" required><span>Понимаю условия и согласен(на) на совместный доступ.</span></label>${errorSlot}<button class="primary-btn full" type="submit">Принять приглашение</button></form>${button('Отклонить','decline-invite',i.id,'text-link full section-gap')}`);}
function accounts(){show(heading('Откуда списываются деньги','У каждой карты указан её счёт и владелец.')+`<div class="row-list">${s.accounts.map(a=>`<button class="list-row" data-action="switch-account" data-id="${a.id}"><span class="tile-icon">${icon('card')}</span><span class="grow"><strong>${esc(a.name)}</strong><small>Карта ${esc(s.user.name)} · общий доступ взрослых</small><small>Доступно ${money(a.balance)}</small></span>${c.accountId===a.id?icon('check'):icon('chevron')}</button>`).join('')}${childList(s).map(m=>`<div class="list-row"><span class="tile-icon purple">${icon('child')}</span><div class="grow"><strong>Детская карта · ${esc(m.name)}</strong><small>Отдельный детский счёт · ${money(s.children[m.id].balance)}</small></div></div>`).join('')}</div>${s.user.role==='owner'?button('Создать общий счёт','new-account','','secondary-btn full section-gap'):''}<p class="permission-info">Детские балансы в демо отдельные. У дополнительной карты к общему счёту самостоятельного остатка нет.</p>${errorSlot}`);}
function period(){show(heading('Выберите период','Диапазон изменится на текущем экране.')+`<div class="filters">${button('Этот месяц','period-preset','current','filter-chip')}${button('Прошлый месяц','period-preset','previous','filter-chip')}${button('Последние 7 дней','period-preset','week','filter-chip')}</div><form id="period-form"><div class="form-two">${field('С','from',c.from,{type:'date'})}${field('По','to',c.to,{type:'date'})}</div>${errorSlot}<button class="primary-btn full" type="submit">Применить период</button></form>`);}
function editPlan(){const p=planFor(s,c.childId,c.month),defaults={sections:600000,clubs:0,allowance:200000};const due=c.month+'-'+Math.min(25,monthEnd(c.month));show(heading('План ребёнка · '+esc(person(s,c.childId).name),monthLabel(c.month)+' · суммы не резервируют деньги')+`<form id="plan-form"><input name="version" type="hidden" value="${p?.version||0}"><input name="childId" type="hidden" value="${c.childId}"><input name="month" type="hidden" value="${c.month}">${[['sections','Секции'],['clubs','Кружки'],['allowance','Карманные деньги']].map(([id,name])=>{const i=p?.items.find(i=>i.category===id);return `<h3 class="small-section">${name}</h3><div class="form-two">${field('Сумма, ₽',id,(i?.amount??defaults[id])/100,{type:'number',attrs:'min="0"'})}${field('До какого числа',id+'Due',i?.due||due,{type:'date',attrs:`min="${c.month}-01" max="${c.month}-${monthEnd(c.month)}"`})}</div>`;}).join('')}<p class="permission-info">После сохранения создаётся новая версия. Даже согласованный план нужно будет отправить на согласование заново.</p>${errorSlot}<button class="primary-btn full" type="submit">Сохранить черновик</button></form>`,{type:'plan'});}
function settings(id){const ch=s.children[id];show(heading('Лимит и заморозка',esc(person(s,id).name)+' · правила учебных покупок')+`<form id="child-settings-form"><input type="hidden" name="id" value="${id}">${field('Месячный лимит, ₽','limit',ch.limit/100,{type:'number',attrs:'min="0"'})}<label class="inline-check"><input type="checkbox" name="frozen" ${ch.frozen?'checked':''}><span>Заморозить карту — покупки будут отклоняться.</span></label><div class="summary-box"><div class="between"><span>На карте</span><b>${money(ch.balance)}</b></div><div class="between"><span>Потрачено по лимиту</span><b>${money(ch.spentThisMonth)}</b></div></div><p class="permission-info">Повышение лимита не пополняет карту. Пополнение не сбрасывает потраченный лимит. Занятия, оплаченные родителем, не расходуют лимит ребёнка.</p>${errorSlot}<button class="primary-btn full" type="submit">Сохранить настройки</button></form>`);}
function reminders(){show(heading('Напоминания и события','Оплата, ручная отметка и переход на сайт — разные действия.')+`${s.plans.filter(p=>p.status==='pending'&&p.authorId!==s.user.id).map(p=>`<button class="list-row" data-action="open-plan" data-id="${p.id}"><span class="tile-icon">${icon('calendar')}</span><span class="grow"><strong>Согласуйте план ребёнка · ${esc(person(s,p.childId).name)}</strong><small>${monthLabel(p.month)}</small></span>${icon('chevron')}</button>`).join('')}<div class="row-list">${s.reminders.map(r=>`<button class="list-row" data-action="reminder" data-id="${r.id}"><span class="tile-icon">${icon('bell')}</span><span class="grow"><strong>${esc(r.title)}</strong><small>${dateLabel(r.due)} · ${money(r.amount)}</small>${badge(r.status==='paid'?'Оплачено в демо':r.status==='manual'?'Отмечено вручную':'К оплате',r.status==='open'?'warning':'success')}</span>${icon('chevron')}</button>`).join('')}</div>${button('Добавить напоминание','edit-reminder','','primary-btn full section-gap')}${errorSlot}`);}
function reminder(id){const r=s.reminders.find(r=>r.id===id);if(!r)return;const pending=s.operations.find(o=>o.reminderId===id&&o.status==='pending');show(heading(esc(r.title),esc(person(s,r.childId).name)+' · до '+dateLabel(r.due))+`<div class="summary-box"><div class="between"><span>Сумма</span><b>${money(r.amount)}</b></div><div class="between"><span>Статья</span><b>${s.categories.find(a=>a.id===r.category)?.name}</b></div><div class="between"><span>Статус</span><b>${r.status==='paid'?'Оплачено в демо':r.status==='manual'?'Отмечено пользователем':pending?'Ожидает результата':'К оплате'}</b></div></div>${r.status==='open'?`${button(pending?'Проверить оплату':'Тестовая оплата',pending?'operation':'pay-reminder',pending?.id||id,'primary-btn full')}<div class="button-pair">${r.url?`<a class="secondary-btn" href="${esc(safeURL(r.url)||'#')}" target="_blank" rel="noopener noreferrer">Открыть сайт ${icon('external')}</a>`:button('Добавить ссылку','edit-reminder',id,'secondary-btn')}${button('Отметить вручную','manual-reminder',id,'secondary-btn')}</div>${button('Изменить напоминание','edit-reminder',id,'text-link full section-gap')}`:r.operationId?button('Посмотреть операцию','operation',r.operationId,'primary-btn full'):''}<p class="permission-info">Переход на сайт ничего не списывает. Ручная отметка означает, что вы сообщили об оплате; она не подтверждает банковскую операцию и не меняет бюджет.</p>${r.url?`<p class="external-link">Сайт оплаты: ${esc(new URL(r.url).hostname)}</p>`:''}${errorSlot}`);}
function editReminder(id){const r=s.reminders.find(r=>r.id===id);show(heading(r?'Изменить напоминание':'Новое семейное дело','Ссылку на оплату достаточно сохранить один раз.')+`<form id="reminder-form"><input type="hidden" name="id" value="${r?.id||''}">${field('Название','title',r?.title||'',{attrs:'maxlength="100" placeholder="Например, бассейн"'})}${select('Для ребёнка','childId',childrenOptions(),r?.childId||c.childId)}${select('Статья бюджета','category',[...s.categories,{id:'allowance',name:'Карманные деньги'}],r?.category||'sections')}<div class="form-two">${field('Сумма, ₽','amount',(r?.amount||600000)/100,{type:'number'})}${field('Дата оплаты','due',r?.due||s.today,{type:'date'})}</div>${field('Ссылка на сайт оплаты','url',r?.url||'',{type:'url',required:false,attrs:'placeholder="https://…"',hint:'Откроется отдельной кнопкой. Факт оплаты по ссылке не проверяется.'})}${errorSlot}<button type="submit" class="primary-btn full">Сохранить напоминание</button></form>`);}
function paymentForm(kind,id='',reminderId=''){
  const pending=sessionStorage.getItem('vtb-pending-payment');if(pending){try{paymentDraft=JSON.parse(pending);return checkPending();}catch{sessionStorage.removeItem('vtb-pending-payment');}}
  const r=reminderId?s.reminders.find(r=>r.id===reminderId):null;
  const childId=r?.childId||id||c.childId;
  const configs={expense:['Оплатить семейный расход','Расход увидят оба взрослых.'],allowance:['Карманные деньги','Внутренний перевод не увеличивает расходы семьи.'],child_purchase:['Тестовая покупка','Проверим баланс, лимит и заморозку.'],topup:['Пополнить общий счёт','Деньги спишутся только с вашей личной карты.'],goal:['Ближе к мечте','Взнос в цель не считается расходом.']};
  const [title,desc]=configs[kind];
  const childChoice=['allowance','child_purchase'].includes(kind)||kind==='expense';
  const sourceOptions=kind==='child_purchase'?[{id:'child:'+childId,name:'Детский счёт · '+person(s,childId).name+' · '+money(s.children[childId].balance)}]:s.accounts.map(a=>({id:a.id,name:a.name+' · '+money(a.balance)}));
  show(heading(title,desc)+`<form id="payment-form"><input type="hidden" name="kind" value="${kind}"><input type="hidden" name="reminderId" value="${r?.id||''}">${kind==='topup'?`<div class="summary-box"><div class="between"><span>Списать с</span><b>Моя личная карта · ${esc(s.user.name)}</b></div><div class="between"><span>Доступно</span><b>${money(s.personalAccount.balance)}</b></div></div>`:''}${select(kind==='topup'?'На какой счёт':'Счёт списания','sourceId',sourceOptions,kind==='child_purchase'?'child:'+childId:c.accountId)}${childChoice?kind==='child_purchase'?`<input type="hidden" name="childId" value="${childId}">`:r?`<input type="hidden" name="childId" value="${r.childId}"><p class="help-inline">Ребёнок: ${esc(person(s,r.childId).name)}</p>`:select('Для кого','childId',kind==='expense'?[{id:'',name:'Для всей семьи'},...childrenOptions()]:childrenOptions(),childId):''}${['expense','child_purchase'].includes(kind)?field('За что платим','title',r?.title||'',{attrs:`maxlength="100" placeholder="${kind==='child_purchase'?'Например, обед':'Например, секция'}" ${r?'readonly':''}`}):''}${field('Сумма, ₽','amount',r?r.amount/100:'',{type:'number',attrs:r?'readonly':''})}${['expense','child_purchase'].includes(kind)?r?`<input type="hidden" name="category" value="${r.category}">`:select('Категория','category',s.categories,kind==='child_purchase'?'food':'sections'):''}<details class="disclosure"><summary>Условия теста</summary>${select('Ответ учебного банка','simulate',[{id:'success',name:'Успешно'},{id:'decline',name:'Отказ без списания'},{id:'delay',name:'Задержка ответа на 6 секунд'}],'success')}</details>${errorSlot}<button class="primary-btn full" type="submit">Проверить перед оплатой</button></form>`,{type:'payment-form'});
}
function confirmPayment(d){
  paymentDraft={...d,amount:cents(d.amount),idempotencyKey:crypto.randomUUID()};
  if(d.kind==='topup')paymentDraft.title='Взнос в общий счёт';
  if(d.kind==='allowance')paymentDraft.title='Карманные деньги · '+person(s,d.childId).name;
  if(d.kind==='goal')paymentDraft.title='Вклад в цель «'+s.goal.name+'»';
  paymentDraft.title=paymentDraft.title.trim();
  show(heading('Проверим всё перед оплатой','Источник не изменится автоматически, даже если денег не хватит.')+paymentSummary(paymentDraft)+`<p class="permission-info">${d.kind==='allowance'?'Перевод попадёт в «Выдано». Расходом станет покупка ребёнка.':d.kind==='child_purchase'?'Оплата зависит от остатка, лимита и заморозки карты.':'Это учебная операция с виртуальными деньгами.'}</p>${errorSlot}${button('Подтвердить · '+money(paymentDraft.amount),'confirm-payment','','primary-btn full')}${button('Вернуться к вводу','back-payment','','text-link full section-gap')}`,{type:'payment-confirm'});
}
function paymentSummary(op){const source=sourceLabel(s,op);const balance=op.kind==='topup'?(!op.payerId||op.payerId===s.user.id?s.personalAccount?.balance:undefined):op.kind==='child_purchase'?s.children[op.childId]?.balance:s.accounts?.find(a=>a.id===op.sourceId)?.balance;return `<div class="summary-box"><div class="between"><span>Списать с</span><b>${esc(source)}</b></div><div class="between"><span>На счёте сейчас</span><b>${balance===undefined?'Остаток недоступен':money(balance)}</b></div><div class="between"><span>Назначение</span><b>${esc(op.title)}</b></div>${op.childId?`<div class="between"><span>Для ребёнка</span><b>${esc(person(s,op.childId).name)}</b></div>`:''}${op.kind==='topup'?`<div class="between"><span>Зачислить на</span><b>${esc(account(s,op.sourceId).name)}</b></div>`:''}<div class="between"><span>Сумма</span><b>${money(op.amount)}</b></div></div>`;}
async function sendPayment(){
  if(!paymentDraft)return;sessionStorage.setItem('vtb-pending-payment',JSON.stringify(paymentDraft));
  show(heading('Проверяем результат','Не закрывайте вкладку, если хотите сразу увидеть подтверждение.')+`<div class="payment-result"><div class="success-icon waiting"><span class="spinner"></span></div><p>Отправляем одну операцию. Повторного списания не будет.</p></div>${errorSlot}`,{type:'payment',key:paymentDraft.idempotencyKey});
  try{const result=await api.mutate('/payments',paymentDraft);sessionStorage.setItem('vtb-pending-operation-id',result.operation.id);await refresh(true);operation(result.operation.id);}
  catch(e){if(e.status){sessionStorage.removeItem('vtb-pending-payment');}error(e.message,e.code);}
}
async function checkPending(){
  if(!paymentDraft)return;
  try{const {operation:op}=await api.request('/operations/lookup?key='+encodeURIComponent(paymentDraft.idempotencyKey));if(op){sessionStorage.setItem('vtb-pending-operation-id',op.id);await refresh(true);operation(op.id);}else await sendPayment();}
  catch(e){show(heading('Результат пока неизвестен','Сохранили запрос. Проверим его с тем же номером, без повторного списания.')+`${errorSlot}${button('Проверить эту же операцию','retry-payment','','primary-btn full')}`,{type:'payment',key:paymentDraft.idempotencyKey});error(e.message,e.code);}
}
function operation(id){const op=s.operations.find(o=>o.id===id);if(!op)return;const pending=op.status==='pending',failed=op.status==='failed';if(!pending&&id===sessionStorage.getItem('vtb-pending-operation-id')){sessionStorage.removeItem('vtb-pending-payment');sessionStorage.removeItem('vtb-pending-operation-id');paymentDraft=null;}show(`<div class="payment-result"><div class="success-icon ${pending?'waiting':failed?'failed':''}">${pending?'<span class="spinner"></span>':icon(failed?'close':'check')}</div>${heading(pending?'Ожидаем результат':failed?'Оплата не выполнена':'Операция выполнена')}<div class="success-amount money">${money(op.amount)}</div><p>${esc(op.title)}</p>${pending?'<p>Проверяем эту же операцию. Новая оплата не создаётся. Можно закрыть окно и вернуться из истории.</p>':failed?`<div class="form-error">${esc(op.error)}<br>Остатки не изменились.</div>`:'<p>Результат сохранён и доступен участникам с соответствующими правами.</p>'}${paymentSummary(op)}${errorSlot}${pending?button('Обновить результат','refresh-payment',id,'secondary-btn full'):button('Готово','close','','primary-btn full')}${failed&&s.user.role!=='child'&&op.kind!=='topup'?(op.kind==='child_purchase'?button('Настройки детской карты','child-settings',op.childId,'text-link full section-gap')+button('Перевести ребёнку','allowance',op.childId,'text-link full'):button('Пополнить выбранный счёт','topup',op.sourceId,'text-link full section-gap')):''}</div>`,{type:'operation',id,status:op.status});}
function updatePayment(){if(!s)return;if(dialogState?.type==='operation'){const op=s.operations?.find(o=>o.id===dialogState.id);if(op&&op.status!==dialogState.status)operation(op.id);}}

function editNote(id){const n=s.notes.find(n=>n.id===id);show(heading(n?'Изменить заметку':'О чём договоримся?','Заметка доступна обоим взрослым семьи.')+`<form id="note-form"><input type="hidden" name="id" value="${n?.id||''}"><input type="hidden" name="done" value="${n?.done?'yes':''}">${field('Название','title',n?.title||'',{attrs:'maxlength="100"'})}<label class="form-field"><span>Подробности</span><textarea name="text" maxlength="1000">${esc(n?.text||'')}</textarea></label>${errorSlot}<button type="submit" class="primary-btn full">Сохранить заметку</button></form>`);}
function confirm(title,desc,action,id){show(heading(title,desc)+errorSlot+`<div class="button-pair">${button('Отмена','close','','secondary-btn')}${button('Подтвердить',action,id,'primary-btn')}</div>`);}
function offer(id){const o=offerList.find(o=>o.id===id);show(heading(o.title,o.desc)+`<div class="coupon-code">${o.code}</div><p class="permission-info">Пример условий для демо: предложение для взрослых участников семьи. Проверка выполнения и реальные начисления ещё не подключены. Купон не действует в магазинах.</p>${button(s.savedOffers.includes(id)?'Убрать из сохранённого':'Сохранить для семьи','save-offer',id,'primary-btn full')}${errorSlot}`);}

function allocate(){
 const b=familyBudget(s,c);
 show(heading('На что планируем тратить',monthLabel(c.month)+' · общий план семьи')+`<form id="monthly-budget-form"><input type="hidden" name="month" value="${c.month}"><input type="hidden" name="version" value="${b.plan?.version||0}"><div class="allocation-total"><span>Всего на месяц</span><strong id="allocation-total">${money(b.planned)}</strong></div>${budgetGroups.map(g=>`<label class="allocation-field" style="--category:${g.color};--category-soft:${g.soft}"><span class="category-icon">${icon(g.icon)}</span><span class="grow"><strong>${g.name}</strong><small>${g.id==='kids'?'Секции и кружки':'План расходов'}</small></span><span class="allocation-input"><input aria-label="${g.name}, план в рублях" name="${g.id}" value="${b.plan?.limits[g.id]/100||0}" inputmode="decimal" required><span>₽</span></span></label>`).join('')}<p class="data-caption">План виден обоим взрослым. Он не резервирует деньги и не ограничивает оплаты. Детские договорённости не прибавляются к нему автоматически.</p>${errorSlot}<button class="primary-btn full section-gap" type="submit">Сохранить бюджет</button></form>`,{type:'monthly-budget'});
}
function groupDetails(id){
 const b=familyBudget(s,c),g=b.groups.find(g=>g.id===id);if(!g)return;
 const ops=s.operations.filter(o=>o.status==='completed'&&['expense','child_purchase'].includes(o.kind)&&g.categories.includes(o.category)&&o.date>=c.from&&o.date<=c.to);
 show(heading(g.name,dateLabel(c.from)+' — '+dateLabel(c.to))+`<div class="category-detail-total" style="--category:${g.color};--category-soft:${g.soft}"><span class="category-icon">${icon(g.icon)}</span><div><small>Потратили вместе</small><strong>${money(g.spent)}</strong></div></div>${b.fullMonth?`<p class="data-caption">План на месяц: ${money(g.planned)}${g.id==='kids'?' · секции и кружки':''}</p>`:''}${ops.map(o=>viewsOperationRow(o)).join('')||`<p class="compact-empty">Покупок в этой категории пока нет.</p>`}${button('Изменить план','allocate','','secondary-btn full section-gap')}`);
}
function viewsOperationRow(o){return `<button class="list-row" data-action="operation" data-id="${o.id}"><span class="tile-icon">${icon('card')}</span><span class="grow"><strong>${esc(o.title)}</strong><small>${esc(person(s,o.payerId).name)} · ${dateLabel(o.date)}</small></span><b class="amount">−${money(o.amount)}</b></button>`;}

const actions={
  allocate,'reload-budget':async()=>{await refresh(true);allocate();},'group-details':groupDetails,'show-stats':()=>{c.tab='stats';c.who='all';c.category=null;go('budget');},
  close,refresh:()=>refresh(true),profile,members,invite,accounts,period,reminders,privacy:()=>show(heading('Общее — вместе. Личное — вам.')+`<ul class="privacy-points"><li>${icon('people')}Взрослые видят общий остаток, взносы, семейные покупки и детские расходы.</li><li>${icon('lock')}Личные остатки и покупки партнёра не приходят в интерфейс, поиск и уведомления.</li><li>${icon('child')}Ребёнок видит только свою карту, лимит, покупки и пополнения.</li><li>${icon('shield')}После выхода из семьи сервер прекращает выдавать общие данные.</li></ul><p class="permission-info">В демонстрационной версии вход осуществляется выбором тестового профиля. Для реального продукта нужны банковская авторизация и согласования.</p>`),
  async login(id){authEpoch++;s=await api.login(id);setSnapshot(s);if(s.family){history.replaceState(null,'',location.pathname+'?mobile=1#home');render();}else{const id=new URLSearchParams(location.search).get('invite');if(id&&s.invitations.some(i=>i.id===id&&i.status==='sent'))invitation(id);}},
  async logout(){authEpoch++;await api.logout();s=null;paymentDraft=null;close();searchClose();$('#family-search-input').value='';render();},
  home:()=>go('home'),budget:()=>{c.tab='overview';go('budget');},history:()=>{c.tab='history';go('budget');},kids:()=>go('kids'),offers:()=>go('offers'),notes:()=>go('notes'),more:()=>go('more'),child:id=>go('child/'+id),
  member:id=>show(heading(esc(person(s,id).name),id===s.user.id?'Ваш профиль':'Участник семьи')+`<div class="selected-person">${avatar(person(s,id))}<div><strong>${esc(person(s,id).name)}</strong><small>Общий доступ к семейным данным</small></div></div><p class="permission-info">Личный счёт и покупки участника остаются закрытыми.</p>`),
  'budget-tab':id=>{c.tab=id;c.category=null;render();},'filter-person':id=>{c.who=id;c.category=null;render();},'filter-category':id=>{c.category=c.category===id?null:id;render();},
  'switch-account':id=>{c.accountId=id;close();render();},
  'new-account':()=>show(heading('Новый общий счёт','Виртуальный счёт появится с нулевым остатком.')+`<form id="account-form">${field('Название','name','',{attrs:'maxlength="45" placeholder="Например, семейный отпуск"'})}${errorSlot}<button class="primary-btn full" type="submit">Создать счёт</button></form>`),
  'send-invite':async()=>{await mutate('/invitations',{recipientId:'maria'});invite();},
  'cancel-invite':id=>confirm('Отменить приглашение?','По старой ссылке нельзя будет присоединиться.','confirm-cancel-invite',id),
  'confirm-cancel-invite':async id=>{await mutate('/invitations/'+id+'/cancel',{});members();},
  'expire-invite':async id=>{await mutate('/invitations/'+id+'/expire',{});members();},
  'copy-invite':async id=>{const url=location.origin+'/?mobile=1&login=1&invite='+id+'#home';try{await navigator.clipboard.writeText(url);toast('Ссылка скопирована');}catch{show(heading('Ссылка на приглашение')+field('Скопируйте ссылку','link',url,{attrs:'readonly'})+button('Готово','close','','primary-btn full'));}},
  invitation,'decline-invite':async id=>{await mutate('/invitations/'+id+'/respond',{decision:'decline'});close();},
  'remove-member':id=>confirm('Отключить Марию от семьи?','Доступ к общим данным прекратится. История семейных операций сохранится.','confirm-remove',id),
  'confirm-remove':async id=>{await mutate('/members/'+id,{},'DELETE');members();},
  leave:()=>confirm('Выйти из семьи?','Вы потеряете доступ к общему счёту, планам и истории.','confirm-leave'),
  'confirm-leave':async()=>{await mutate('/family/leave',{});close();},
  'edit-plan':editPlan,'reload-plan':async()=>{await refresh(true);editPlan();},
  'child-plan':id=>{c.childId=id;c.tab='plan';go('budget');},
  'open-plan':id=>{const p=s.plans.find(p=>p.id===id);if(p){c.childId=p.childId;monthRange(p.month);c.tab='plan';go('budget');}},
  'submit-plan':async id=>{const p=s.plans.find(p=>p.id===id);await mutate('/plans/'+id+'/submit',{version:p.version});toast('План отправлен второму взрослому');},
  'approve-plan':async id=>{const p=s.plans.find(p=>p.id===id);await mutate('/plans/'+id+'/approve',{version:p.version});toast('План согласован обоими взрослыми');},
  'return-plan':id=>{const p=s.plans.find(p=>p.id===id);show(heading('Что нужно изменить?','Комментарий увидит второй взрослый.')+`<form id="return-form"><input type="hidden" name="id" value="${id}"><input type="hidden" name="version" value="${p.version}"><label class="form-field"><span>Комментарий</span><textarea name="comment" required maxlength="500" placeholder="Например, снизить сумму на кружки"></textarea></label>${errorSlot}<button type="submit" class="primary-btn full">Вернуть на доработку</button></form>`);},
  'child-settings':settings,topup:id=>{if(s.accounts?.some(a=>a.id===id))c.accountId=id;paymentForm('topup');},expense:()=>paymentForm('expense'),allowance:id=>paymentForm('allowance',id),purchase:id=>paymentForm('child_purchase',id),goal:()=>paymentForm('goal'),
  'pay-reminder':id=>paymentForm(s.reminders.find(r=>r.id===id)?.category==='allowance'?'allowance':'expense','',id),reminder,'edit-reminder':editReminder,
  'manual-reminder':id=>confirm('Отметить оплату вручную?','Это ваша отметка, а не подтверждение банка. Остатки и расходы не изменятся.','confirm-manual',id),
  'confirm-manual':async id=>{await mutate('/reminders/'+id+'/manual',{});reminder(id);},
  'confirm-payment':sendPayment,'retry-payment':checkPending,operation,'refresh-payment':async id=>{await refresh(true);operation(id);},
  'back-payment':()=>{const d=paymentDraft;paymentDraft=null;paymentForm(d.kind,d.childId,d.reminderId);const form=$('#payment-form');if(form)for(const [key,v]of Object.entries(d)){const input=form.elements.namedItem(key);if(input)input.value=key==='amount'?v/100:v;}},
  'period-preset':id=>{const today=s.today;let start=today.slice(0,7)+'-01',end=today.slice(0,7)+'-'+monthEnd(today.slice(0,7));if(id==='previous'){const dt=new Date(today.slice(0,7)+'-15T12:00:00');dt.setMonth(dt.getMonth()-1);const m=dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0');start=m+'-01';end=m+'-'+monthEnd(m);}if(id==='week'){const dt=new Date(today+'T12:00:00');dt.setDate(dt.getDate()-6);start=dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')+'-'+String(dt.getDate()).padStart(2,'0');end=today;}$('#period-form [name=from]').value=start;$('#period-form [name=to]').value=end;},
  'edit-note':editNote,'toggle-note':async id=>{const n=s.notes.find(n=>n.id===id);await mutate('/notes',{...n,done:!n.done});},
  'delete-note':id=>confirm('Удалить заметку?','Она исчезнет у обоих взрослых.','confirm-delete-note',id),
  'confirm-delete-note':async id=>{await mutate('/notes/'+id,{},'DELETE');close();},offer,
  'save-offer':async id=>{await mutate('/offers/save',{id});offer(id);},
  reset:()=>confirm('Начать сценарий заново?','Учебные операции, приглашения и планы будут сброшены для всех тестовых профилей.','confirm-reset'),
  'confirm-reset':async()=>{sessionStorage.removeItem('vtb-pending-payment');sessionStorage.removeItem('vtb-pending-operation-id');paymentDraft=null;await mutate('/demo/reset',{});monthRange(s.today.slice(0,7));c.accountId='family';close();go('home');toast('Сценарий готов: общий остаток 8 000 ₽');}
};

document.addEventListener('click',async e=>{
  const control=e.target.closest('[data-action]');if(!control||control.disabled)return;
  const fn=actions[control.dataset.action];if(!fn)return;e.preventDefault();
  control.disabled=true;control.setAttribute('aria-busy','true');
  try{await fn(control.dataset.id);}catch(err){error(err.message,err.code);if(err.status===403)await refresh(true);}
  finally{if(control.isConnected){control.disabled=false;control.removeAttribute('aria-busy');}}
});
document.addEventListener('submit',async e=>{
  const form=e.target,formId=form.getAttribute('id');if(formId==='family-search-form'){e.preventDefault();$('#family-search-results button')?.focus();return;}
  if(!formId||!form.closest('#sheet')||form.dataset.busy)return;e.preventDefault();
  const d=Object.fromEntries(new FormData(form));const submit=form.querySelector('[type=submit]');form.dataset.busy='true';if(submit){submit.disabled=true;submit.setAttribute('aria-busy','true');}
  form.querySelector('.form-error').textContent='';
  try{
    switch(formId){
      case 'accept-form':await mutate('/invitations/'+d.id+'/respond',{decision:'accept',consent:d.consent==='on'});close();go('home');toast('Теперь общие планы доступны вам обоим');break;
      case 'monthly-budget-form':await mutate('/monthly-budget',{month:d.month,expectedVersion:Number(d.version),limits:Object.fromEntries(budgetGroups.map(g=>[g.id,cents(d[g.id],true)]))},'PUT');close();toast('План на месяц сохранён для семьи');break;
      case 'account-form':{const result=await mutate('/accounts',{name:d.name.trim()});c.accountId=result.account.id;close();render();break;}
      case 'period-form':if(d.from>d.to)throw Error('Начало периода должно быть не позже окончания.');c.from=d.from;c.to=d.to;c.month=d.from.slice(0,7);c.category=null;close();render();break;
      case 'plan-form':await mutate('/plans',{childId:d.childId,month:d.month,expectedVersion:Number(d.version),items:['sections','clubs','allowance'].map(category=>({category,amount:cents(d[category],true),due:d[category+'Due']}))},'PUT');close();toast('Черновик сохранён. Отправьте его на согласование.');break;
      case 'return-form':await mutate('/plans/'+d.id+'/return',{version:Number(d.version),comment:d.comment});close();toast('План возвращён на доработку');break;
      case 'child-settings-form':await mutate('/children/'+d.id,{limit:cents(d.limit,true),frozen:d.frozen==='on'},'PUT');close();toast('Правила детской карты сохранены');break;
      case 'reminder-form':if(d.url&&!safeURL(d.url))throw Error('Добавьте ссылку https:// без логина и пароля.');{const result=await mutate('/reminders',{...d,title:d.title.trim(),amount:cents(d.amount),url:d.url?safeURL(d.url):''});reminder(result.reminder.id);}break;
      case 'payment-form':confirmPayment(d);break;
      case 'note-form':await mutate('/notes',{...d,title:d.title.trim(),done:d.done==='yes'});close();toast('Заметка сохранена для семьи');break;
    }
  }catch(err){error(err.message,err.code);if(['CONFLICT','BUDGET_CONFLICT'].includes(err.code)||err.status===403)await refresh(true);}
  finally{delete form.dataset.busy;if(submit?.isConnected){submit.disabled=false;submit.removeAttribute('aria-busy');}}
});
document.addEventListener('input',e=>{if(e.target.closest('#monthly-budget-form')){try{const data=new FormData(e.target.form);$('#allocation-total').textContent=money(budgetGroups.reduce((n,g)=>n+cents(data.get(g.id),true),0));}catch{$('#allocation-total').textContent='Проверьте суммы';}}});
document.addEventListener('change',e=>{
  if(e.target.id==='plan-child'){c.childId=e.target.value;render();}
  if(e.target.id==='plan-month'&&/^\d{4}-(0[1-9]|1[0-2])$/.test(e.target.value)){monthRange(e.target.value);render();}
});
function normalize(v){return String(v).toLowerCase().replace(/ё/g,'е').replace(/[^a-zа-я0-9]+/g,' ').trim();}
function buildIndex(){
  if(!s?.family)return [];
  const list=[];const add=(title,detail,action,id='',ic='note')=>list.push({title,detail,action,id,ic});
  if(s.user.role==='child'){add('Моя карта','Баланс и лимит','home','','card');add('Тестовая покупка','Оплатить своей картой','purchase',s.user.id,'card');}
  else{
    [['Распределить бюджет','Суммы на месяц по категориям','allocate'],['Семейный бюджет','Планы и согласование','budget'],['История операций','Платежи и взносы','history'],['Детские карты','Баланс и лимиты','kids'],['Пригласить близкого','Приглашение Марии','invite'],['Напоминания','Оплаты и сохранённые ссылки','reminders'],['Новая заметка','Записать договорённость','edit-note'],['Пополнить счёт','Перевести свои деньги','topup'],['Оплатить расход','Тестовая семейная оплата','expense'],['Карманные деньги','Перевести ребёнку','allowance'],['Семейная выгода','Купоны и предложения','offers'],['Общие счета','Карты и источник списания','accounts']].forEach(([t,d,a])=>add(t,d,a));
    s.members.forEach(m=>add(m.name,m.role==='child'?'Детская карта и лимит':'Участник семьи',m.role==='child'?'child':'member',m.id,'people'));
    s.accounts.forEach(a=>add(a.name,money(a.balance),'accounts','','card'));
    s.plans.forEach(p=>add('План ребёнка · '+person(s,p.childId).name,monthLabel(p.month)+' · '+planStatuses[p.status][0],'open-plan',p.id,'calendar'));
    s.reminders.forEach(r=>add(r.title,person(s,r.childId).name+' · '+money(r.amount)+' · '+dateLabel(r.due),'reminder',r.id,'bell'));
    s.notes.forEach(n=>add(n.title,n.text+' · '+person(s,n.authorId).name,'edit-note',n.id));
    offerList.forEach(o=>add(o.title,o.desc+' '+o.code,'offer',o.id,'gift'));
    add(s.goal.name,'Копилка · '+money(s.goal.saved),'goal','','gift');
  }
  s.operations.forEach(o=>add(o.title,person(s,o.payerId).name+' '+(o.childId?person(s,o.childId).name:'')+' '+money(o.amount)+' '+o.date+' '+(s.categories.find(c=>c.id===o.category)?.name||''),'operation',o.id,'card'));
  return list;
}
function search(q){
  const terms=normalize(q).split(' ').filter(Boolean);searchItems=buildIndex().filter(i=>terms.every(t=>normalize(i.title+' '+i.detail).includes(t))).slice(0,30);
  $('#family-search-panel').hidden=false;$('#family-search-status').textContent=terms.length?'Найдено: '+searchItems.length:'Разделы и действия';
  $('#family-search-results').innerHTML=searchItems.map(i=>`<button class="search-result" data-action="${i.action}" data-id="${esc(i.id)}">${icon(i.ic)}<span><strong>${esc(i.title)}</strong><small>${esc(i.detail.length>105?i.detail.slice(0,105)+'…':i.detail)}</small></span></button>`).join('');
  $('#family-search-empty').hidden=searchItems.length>0;$('#family-search-empty').textContent='Ничего не нашли. Попробуйте имя, сумму или название дела.';
  $('#family-search-clear').hidden=!q;
}
$('#family-search-input').addEventListener('input',e=>search(e.target.value));
$('#family-search-input').addEventListener('focus',e=>{if(s?.family)search(e.target.value);});
$('#family-search-input').addEventListener('keydown',e=>{if(e.key==='ArrowDown'){e.preventDefault();$('#family-search-results button')?.focus();}if(e.key==='Escape')searchClose();});
$('#family-search-panel').addEventListener('click',e=>{if(e.target.closest('.search-result'))searchClose();});
$('#family-search-panel').addEventListener('keydown',e=>{const nodes=[...$('#family-search-results').querySelectorAll('button')],index=nodes.indexOf(document.activeElement);if(e.key==='Escape'){searchClose();$('#family-search-input').focus();searchClose();}if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();nodes[(index+(e.key==='ArrowDown'?1:-1)+nodes.length)%nodes.length]?.focus();}});
$('#family-search-clear').addEventListener('click',()=>{$('#family-search-input').value='';search('');$('#family-search-input').focus();});
$('#family-search-close').addEventListener('click',searchClose);
document.addEventListener('click',e=>{if(!e.target.closest('.global-search'))searchClose();});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'&&s?.family){e.preventDefault();$('#family-search-input').focus();}});
$('#view-toggle').addEventListener('click',()=>{document.body.classList.toggle('phone-mode');$('#view-toggle').textContent=document.body.classList.contains('phone-mode')?'Широкий вид':'Вид смартфона';});
if(new URLSearchParams(location.search).get('mobile')==='1')document.body.classList.add('phone-mode');
else $('#view-toggle').textContent='Вид смартфона';
window.addEventListener('hashchange',()=>{render();window.scrollTo(0,0);main.focus({preventScroll:true});});
window.addEventListener('online',()=>api.token&&refresh(true));
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&api.token)refresh();});
setInterval(()=>{if(api.token&&!document.hidden)refresh();},1800);
if(new URLSearchParams(location.search).get('login')==='1'){api.token='';sessionStorage.removeItem('vtb-mvp-session');sessionStorage.removeItem('vtb-pending-payment');}
if(api.token){await refresh(true);try{const pending=sessionStorage.getItem('vtb-pending-payment');if(pending&&s?.family){paymentDraft=JSON.parse(pending);await checkPending();}}catch{sessionStorage.removeItem('vtb-pending-payment');}}
else render();
