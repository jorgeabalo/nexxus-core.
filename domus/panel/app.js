'use strict';
const $ = id => document.getElementById(id);
let client, user, home, role, homes = [], rooms = [], channels = [], devices = [], busy = false;
const platformNames = {alexa:'Amazon Alexa',google:'Google',app:'Panel AITA',other:'Otro asistente'};
const statusNames = {pending:'Conexión pendiente',connected:'Conectado',attention:'Requiere atención'};
function notice(text) { $('message').textContent = text; }
function data(result) { if (result.error) throw result.error; return result.data; }
async function action(fn) {
  if (busy) return;
  busy = true; $('homes').disabled = true; document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { notice(''); await fn(); } catch (e) {
    const code = e.code || '';
    notice(code === '23505' ? 'Ese registro ya existe en esta casa.' : code === '22023' ? 'Revisa la zona horaria y añade al menos una habitación y un asistente antes de finalizar.' : code === '42501' ? 'Tu cuenta no tiene permiso para realizar este cambio.' : 'No pudimos guardar el cambio. Revisa tu conexión y vuelve a intentarlo.');
  } finally { busy = false; $('homes').disabled = false; document.querySelectorAll('button').forEach(b => b.disabled = false); }
}
function list(id, rows, describe) {
  $(id).replaceChildren();
  for (const row of rows) { const li = document.createElement('li'); li.textContent = describe(row); $(id).append(li); }
  if (!rows.length) { const li = document.createElement('li'); li.textContent = 'Todavía no has añadido ninguno.'; $(id).append(li); }
}
function option(value, label) { const item = document.createElement('option'); item.value=value; item.textContent=label; return item; }
function step(name) {
  document.querySelectorAll('[data-page]').forEach(e => e.hidden = e.dataset.page !== name);
  document.querySelectorAll('[data-step]').forEach(e => e.setAttribute('aria-current', e.dataset.step === name ? 'step' : 'false'));
}
async function loadHome(id) {
  home = homes.find(h => h.id === id); rooms=[]; channels=[]; devices=[]; role=null; $('house').hidden = !home || !setupOpen; renderDashboard(); if (!home) return;
  const results = await Promise.all([
    client.from('domus_home_users').select('role').eq('home_id',id).eq('user_id',user.id).eq('active',true).single(),
    client.from('domus_rooms').select('*').eq('home_id',id).order('created_at'),
    client.from('domus_channels').select('*').eq('home_id',id).order('created_at'),
    client.from('domus_devices').select('*').eq('home_id',id).order('created_at'),
    client.from('domus_consents').select('granted').eq('home_id',id).eq('user_id',user.id).order('recorded_at',{ascending:false}).limit(1)
  ]);
  role=data(results[0]).role; rooms=data(results[1]); channels=data(results[2]); devices=data(results[3]);
  $('consent-value').checked = data(results[4])[0]?.granted === true;
  $('home-title').textContent=home.name; $('role').textContent=role === 'owner' ? 'Puedes configurar este hogar.' : 'Puedes consultar este hogar y guardar tu preferencia personal.';
  document.querySelectorAll('.owner').forEach(e => e.hidden=role !== 'owner');
  list('rooms-list',rooms,r=>r.name); list('channels-list',channels,r=>`${platformNames[r.platform]} · ${statusNames[r.connection_status]}`);
  list('devices-list',devices,r=>`${r.name}${r.brand ? ' · '+r.brand : ''} · ${rooms.find(x=>x.id===r.room_id)?.name || 'Sin habitación'} · ${statusNames[r.connection_status]}`);
  $('device-room').replaceChildren(option('','Sin habitación'),...rooms.map(r=>option(r.id,r.name)));
  $('overview').textContent=`${rooms.length} ${rooms.length === 1 ? 'habitación' : 'habitaciones'}, ${channels.length} ${channels.length === 1 ? 'asistente' : 'asistentes'} y ${devices.length} ${devices.length === 1 ? 'dispositivo registrado' : 'dispositivos registrados'}.`;
  $('complete-status').textContent=home.onboarding_completed_at ? 'Registro finalizado. Las conexiones pendientes se configurarán en una etapa posterior.' : '';
  step(home.setup_stage); renderDashboard();
}
async function refresh(preferred) {
  homes=data(await client.from('domus_homes').select('*').order('created_at'));
  $('homes').replaceChildren(option('','Selecciona una casa'),...homes.map(h=>option(h.id,h.name)));
  $('homes').value=preferred || homes[0]?.id || ''; await loadHome($('homes').value);
}
async function sessionReady(session) {
  user=session?.user; document.body.classList.toggle('signed-in',!!user); $('login').hidden=!!user; $('workspace').hidden=!user;
  if (user) await refresh(home?.id); else { home=null; homes=[]; rooms=[];channels=[];devices=[]; $('house').hidden=true; renderDashboard(); }
}
$('signin').onsubmit=e=>{e.preventDefault();action(async()=>{
  const form=new FormData(e.target); const result=await client.auth.signInWithPassword({email:form.get('email').trim(),password:form.get('password')});
  if(result.error) { notice('No pudimos iniciar sesión. Revisa tu correo y contraseña.'); return; }
  e.target.reset(); await sessionReady(result.data.session);
});};
$('logout').onclick=()=>action(async()=>{data(await client.auth.signOut());await sessionReady(null);});
$('homes').onchange=()=>action(()=>loadHome($('homes').value));
$('create').onsubmit=e=>{e.preventDefault();action(async()=>{
  const form=new FormData(e.target), storageKey='domus-create-'+user.id;
  let requestKey=sessionStorage.getItem(storageKey); if(!requestKey){requestKey=crypto.randomUUID();sessionStorage.setItem(storageKey,requestKey);}
  const id=data(await client.rpc('domus_create_home',{p_name:form.get('name').trim(),p_timezone:form.get('timezone').trim(),p_locale:form.get('locale'),p_request_key:requestKey}));
  sessionStorage.removeItem(storageKey); e.target.reset(); $('timezone').value=Intl.DateTimeFormat().resolvedOptions().timeZone; await refresh(id);
});};
for(const [formId,table] of [['room','domus_rooms'],['channel','domus_channels'],['device','domus_devices']]) {
  $(formId).onsubmit=e=>{e.preventDefault();action(async()=>{
    if(!home || role!=='owner') return;
    const payload=Object.fromEntries(new FormData(e.target)); payload.home_id=home.id;
    if('name' in payload) payload.name=payload.name.trim(); if('room_id' in payload) payload.room_id=payload.room_id || null;
    data(await client.from(table).insert(payload)); const current=document.querySelector('[data-page]:not([hidden])').dataset.page;
    e.target.reset(); await loadHome(home.id); step(current); notice('Registro guardado.');
  });};
}
document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>action(async()=>{
  if(!home) return;
  if(role==='owner'){data(await client.from('domus_homes').update({setup_stage:b.dataset.step}).eq('id',home.id));home.setup_stage=b.dataset.step;}
  step(b.dataset.step);
}));
$('consent').onsubmit=e=>{e.preventDefault();action(async()=>{
  if(!home) return;
  data(await client.from('domus_consents').insert({home_id:home.id,scope:'anthropic_voice_queries_v1',granted:$('consent-value').checked})); notice('Preferencia guardada. Puedes cambiarla cuando quieras.');
});};
$('complete').onclick=()=>action(async()=>{data(await client.rpc('domus_complete_onboarding',{p_home:home.id}));await refresh(home.id);});
(async()=>{
  try {
    $('timezone').value=Intl.DateTimeFormat().resolvedOptions().timeZone;
    const response=await fetch('/api/domus/onboarding/config',{cache:'no-store'}); if(!response.ok)throw Error('config');
    const config=await response.json(); client=supabase.createClient(config.supabaseUrl,config.supabaseAnonKey,{auth:{storageKey:'aita-domus-auth-v1'}});
    const result=await client.auth.getSession(); if(result.error) throw result.error; await sessionReady(result.data.session);
    client.auth.onAuthStateChange((event,session)=>{if(event==='SIGNED_OUT'){user=null;home=null;document.body.classList.remove('signed-in');$('login').hidden=false;$('workspace').hidden=true;rooms=[];channels=[];devices=[];renderDashboard();}else if(session)user=session.user;});
  } catch(e) {notice('El registro de hogares aún no está disponible. Contacta con el administrador.');$('signin').querySelector('button').disabled=true;}
})();

// Decorative motion is independent of assistant/device activity.
const motionButton = $('motion-toggle');
function setMotion(paused) {
  document.body.classList.toggle('motion-paused', paused);
  motionButton.setAttribute('aria-pressed', String(paused));
  motionButton.textContent = paused ? 'Activar movimiento' : 'Pausar movimiento';
}
try { setMotion(localStorage.getItem('domus-motion-paused') === 'true' || window.matchMedia('(prefers-reduced-motion: reduce)').matches); } catch (_) { setMotion(window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
motionButton.onclick = () => {
  const paused = !document.body.classList.contains('motion-paused'); setMotion(paused);
  try { localStorage.setItem('domus-motion-paused', String(paused)); } catch (_) {}
};


let setupOpen = false, asking = false, homeGeneration = 0;
const moduleInfo = {
  music:['Música','Tus altavoces y WiiM. La reproducción y el volumen estarán disponibles al conectar sus adaptadores.',['music']],
  tv:['Televisión','El control de tu televisor está pendiente de conectar su marca y modelo.',['tv']],
  cleaning:['Limpieza','Robots registrados. Las órdenes de limpieza aún no están conectadas.',['vacuum']],
  calendar:['Calendario','Google, Outlook y Apple/iCloud: la autorización de cuentas todavía está pendiente.',[]],
  reminders:['Recordatorios','Los recordatorios personales y del hogar aún no están disponibles. No se han creado tareas.',[]],
  inventory:['Inventario y caducidad','La despensa, cantidades y alertas de caducidad están pendientes de implementación.',[]],
  economy:['Economía doméstica','Gastos, facturas y fotografías de documentos: este módulo aún no está conectado.',[]],
  cloud:['Archivos en la nube','Google Drive, OneDrive y otros servicios requieren una conexión autorizada. No hay archivos cargados.',[]],
  weather:['Clima local','Falta configurar la ciudad de este hogar y conectar el servicio meteorológico. No hay un pronóstico disponible.',[]]
};
function updateClock() {
  if(!home?.timezone){$('local-time').textContent='—';$('local-date').textContent='Selecciona un hogar';return;}
  try {
    const now=new Date(); $('local-time').textContent=new Intl.DateTimeFormat('es',{timeZone:home.timezone,hour:'2-digit',minute:'2-digit'}).format(now);
    $('local-date').textContent=new Intl.DateTimeFormat('es',{timeZone:home.timezone,weekday:'long',day:'numeric',month:'long'}).format(now);
  } catch(_) { $('local-time').textContent='—';$('local-date').textContent='Zona horaria no disponible'; }
}
function renderDashboard() {
  homeGeneration++; $('nexxus-question').value='';$('question-consent').checked=false; $('dashboard-summary').textContent=home ? 'Tu casa está registrada. Cada conexión se activará cuando su servicio esté preparado.' : 'Selecciona o crea tu hogar para comenzar.'; $('dashboard-name').textContent=home?.name || 'Selecciona tu hogar';updateClock();
  $('module-detail').hidden=true;$('nexxus-answer').textContent='Pregúntame la hora de tu hogar o explora sus módulos.';
  $('nexxus-state').textContent=home ? 'Aquí, contigo.' : 'Selecciona o crea tu hogar.';
}
function showSetup() {
  setupOpen=true;$('house').hidden=!home;$('setup-toggle').setAttribute('aria-expanded','true');
  $('setup-toggle').textContent='Ocultar configuración'; if(home)$('house').scrollIntoView({behavior:document.body.classList.contains('motion-paused')?'instant':'smooth',block:'start'});
}
$('setup-toggle').onclick=()=>{if(!setupOpen)showSetup();else{setupOpen=false;$('house').hidden=true;$('setup-toggle').setAttribute('aria-expanded','false');$('setup-toggle').textContent='Configurar mi hogar';}};
$('module-setup').onclick=()=>{showSetup();step('devices');};
$('close-module').onclick=()=>{$('module-detail').hidden=true;};
document.querySelectorAll('[data-module]').forEach(b=>b.onclick=()=>{
  const [title,description,kinds]=moduleInfo[b.dataset.module]; $('module-title').textContent=title;$('module-description').textContent=description;
  $('module-items').replaceChildren();
  for(const item of devices.filter(d=>kinds.includes(d.kind))){const li=document.createElement('li');li.textContent=item.name+' · '+statusNames[item.connection_status];$('module-items').append(li);}
  if(kinds.length&&!$('module-items').children.length){const li=document.createElement('li');li.textContent='No hay dispositivos registrados de este tipo.';$('module-items').append(li);}
  $('module-setup').hidden=!kinds.length || !home || role!=='owner';$('module-detail').hidden=false;$('module-detail').focus({preventScroll:true});
});
$('ask-nexxus').onsubmit=async e=>{
  e.preventDefault(); if(asking)return;
  if(!home){$('nexxus-answer').textContent='Selecciona o crea una casa primero.';return;}
  const question=$('nexxus-question').value.trim(); if(!question)return;
  const generation=homeGeneration; asking=true;$('ask-nexxus').querySelector('button').disabled=true;
  $('dashboard-avatar').classList.add('is-thinking');$('nexxus-state').textContent='Procesando tu pregunta…';
  try {
    let answer;
    if(/(qué|que) hora (es|tenemos)|hora (actual|local)|^(nexxus[, ]*)?(la )?hora[?. ]*$|fecha (de hoy|actual)|^(qué|que) (día|dia) es (hoy)?/i.test(question)) { updateClock(); answer='En tu hogar son las '+$('local-time').textContent+'. '+$('local-date').textContent+'.'; }
    else if(!$('question-consent').checked) answer='Para una respuesta inteligente, autoriza el envío de esta pregunta a Anthropic. La hora de tu hogar se consulta sin enviar datos.';
    else {
      const session=data(await client.auth.getSession()).session;
      if(!session?.access_token)throw Error('session');
      const response=await fetch('/api/domus/panel/ask',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+session.access_token},body:JSON.stringify({home_id:home.id,text:question,consent:true}),signal:AbortSignal.timeout(12000)});
      if(!response.ok)throw Error('answer'); answer=(await response.json()).answer;
    }
    if(generation===homeGeneration){$('nexxus-answer').textContent=answer;$('nexxus-state').textContent='Aquí, contigo.';}
  } catch(_) {if(generation===homeGeneration){$('nexxus-answer').textContent='No pude responder. Revisa tu sesión y vuelve a intentarlo.';$('nexxus-state').textContent='Conexión no disponible.';}}
  finally{asking=false;$('dashboard-avatar').classList.remove('is-thinking');$('ask-nexxus').querySelector('button').disabled=false;}
};
setInterval(updateClock,1000);
