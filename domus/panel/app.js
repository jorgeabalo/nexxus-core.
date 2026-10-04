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
  home = homes.find(h => h.id === id); $('house').hidden = !home; if (!home) return;
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
  step(home.setup_stage);
}
async function refresh(preferred) {
  homes=data(await client.from('domus_homes').select('*').order('created_at'));
  $('homes').replaceChildren(option('','Selecciona una casa'),...homes.map(h=>option(h.id,h.name)));
  $('homes').value=preferred || homes[0]?.id || ''; await loadHome($('homes').value);
}
async function sessionReady(session) {
  user=session?.user; $('login').hidden=!!user; $('workspace').hidden=!user;
  if (user) await refresh(home?.id); else { home=null; homes=[]; $('house').hidden=true; }
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
    client.auth.onAuthStateChange((event,session)=>{if(event==='SIGNED_OUT'){user=null;home=null;$('login').hidden=false;$('workspace').hidden=true;}else if(session)user=session.user;});
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
