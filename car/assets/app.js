'use strict';
const $=id=>document.getElementById(id);
let pendingExpense=null;
let state,detail,vid,active,watch=null,queue=Promise.resolve(),newVehicle=false,simGenerating=false,contextVersion=0;
const unit=()=>detail?.vehicle.distance_unit||'mi';
const distance=x=>x*(unit()==='km'?1.609344:1);
const canonical=x=>x/(unit()==='km'?1.609344:1);
const money=x=>new Intl.NumberFormat(locale(),{style:'currency',currency:'USD'}).format(x);
const msg=text=>{ $('message').textContent=tr(text); clearTimeout(msg.timer); msg.timer=setTimeout(()=>{$('message').textContent=tr('');},6500); };
async function api(path,method='GET',body){
 const res=await fetch('/api/car'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await res.json(); if(!res.ok){if(res.status===401){stopGPS();closeCockpitMap();contextVersion++;$('app').hidden=true;$('onboarding').hidden=true;$('login').hidden=false;} throw Error(tr(typeof data.detail==='string'?data.detail:'Revisa los campos del formulario'));} return data;
}
function bind(id,fn){$(id).addEventListener('click',async()=>{const b=$(id);b.disabled=true;try{await fn();}catch(e){msg(e.message);}finally{b.disabled=false;if(['simulate','gps','finish','cancel-trip','resume'].includes(id))tripStatus();}});}
function rows(id,list,format){const root=$(id);root.replaceChildren();if(!list.length){const p=document.createElement('p');p.className='muted';p.textContent=tr('Todavía no hay registros.');root.append(p);}for(const x of list){const d=document.createElement('div');d.className='row';d.textContent=tr(format(x));root.append(d);}}
function clearContext(){clearExpenseProposal();closeCockpitMap();$('apps-destination').value='';updateMapLinks();contextVersion++;$('answer').textContent=tr('Estoy listo para ayudarte.');$('question').value='';$('obd').textContent=tr('');$('expense-form').reset();if('speechSynthesis'in window)speechSynthesis.cancel();}
function stopGPS(){if(watch!==null){navigator.geolocation.clearWatch(watch);watch=null;}}
function tripStatus(){ $('flight-trip').textContent=tr(active?'EN CURSO':'EN ESPERA'); $('finish').hidden=!active; $('finish').disabled=simGenerating; $('cancel-trip').hidden=!active; $('resume').hidden=!(active?.source==='gps' && watch===null); $('gps').disabled=!!active; $('simulate').disabled=!!active; $('vehicle').disabled=!!active;
 $('trip-status').textContent=tr(active?`Viaje ${active.source==='gps'?'GPS':'simulado'} activo · ${distance(active.miles).toFixed(2)} ${unit()} · gasolina estimada ${money(active.fuel)} · ${active.points.length} puntos`:'Sin viaje activo.');}
async function refresh(){
 state=await api('/dashboard');await loadConnectors();if(!state.profile.onboarding_completed){$('login').hidden=true;$('app').hidden=true;$('onboarding').hidden=false;return;} $('onboarding').hidden=true;$('hello').textContent=tr(`Hola, ${state.profile.preferred_name||state.profile.name.trim().split(/\s+/)[0]}.`);$('profile-name').value=state.profile.name;$('profile-preferred-name').value=state.profile.preferred_name||state.profile.name.trim().split(/\s+/)[0];for(const key of ['city','state','country'])$('profile-'+key).value=state.profile[key]||'';$('profile-address').value=state.profile.address||'';$('profile-phone').value=state.profile.phone||'';$('plan').value=state.profile.plan;
 $('vehicle').replaceChildren(...state.vehicles.map(v=>{const o=document.createElement('option');o.value=v.id;o.textContent=tr(`${v.make} ${v.model} · ${v.year}`);return o;}));
 if(!state.vehicles.some(v=>v.id===vid))vid=state.profile.primary_vehicle_id||state.vehicles[0]?.id;$('vehicle').value=vid;if(!vid)return;
 detail=await api('/vehicles/'+vid);const v=detail.vehicle;paintVehicleArt(v);
 $('car-title').textContent=tr(`${v.make} ${v.model} ${v.year}`);$('car-mileage').textContent=tr(`${distance(v.odometer).toLocaleString(locale(),{maximumFractionDigits:1})} ${unit()==='km'?'kilómetros':'millas'} · perfil del vehículo`);
 $('total-miles').textContent=tr(distance(detail.trips.filter(x=>x.status==='finished').reduce((s,x)=>s+x.miles,0)).toFixed(1));$('total-expenses').textContent=tr(money(detail.expense_total));$('oil-miles').textContent=tr(`${distance(detail.oil_remaining).toFixed(0)} ${unit()}`);
 $('maintenance-text').textContent=tr(`${v.make} ${v.model} ${v.year} · Próximo aceite a las ${distance(v.oil_due).toFixed(0)} ${unit()==='km'?'kilómetros':'millas'}. ${detail.oil_remaining?'Faltan '+distance(detail.oil_remaining).toFixed(0)+' '+unit()+'.':'Recordatorio vencido: revisa el mantenimiento.'}`);
 const p=detail.parking;$('parking-text').textContent=tr(p?`${p.lat.toFixed(5)}, ${p.lon.toFixed(5)} · ${p.source==='simulated'?'SIMULADO':'GPS'} · ${new Date(p.at).toLocaleString(locale())}`:'Aún no registrado.');$('parking-map').hidden=!p;if(p)$('parking-map').href=`https://www.google.com/maps?q=${p.lat},${p.lon}`;
 rows('trip-list',detail.trips,x=>`${x.source==='simulated'?'SIMULADO':'GPS'} · ${new Date(x.started).toLocaleString(locale())} · ${distance(x.miles).toFixed(2)} ${unit()} · ${x.status==='finished'?money(x.total_estimated)+' estimados (gasolina '+money(x.fuel)+', peajes '+money(x.tolls)+')':x.status==='cancelled'?'Cancelado':'En curso'}`);
 rows('expense-list',detail.expenses,x=>`${x.category} · ${money(x.amount)} · ${x.note} · ${new Date(x.at).toLocaleDateString(locale())}`);
 rows('maintenance-list',detail.maintenance,x=>`Aceite registrado · ${distance(x.odometer).toFixed(0)} ${unit()} · ${new Date(x.at).toLocaleDateString(locale())}`);
 rows('alerts',detail.alerts,x=>x.message);$('security').hidden=true;$('security').textContent=tr(state.profile.plan==='pro'?'Simular alerta Pro':'Pro · $29.99/mes para probar alertas');
 $('vehicle-form').dataset.unit=v.distance_unit||'mi';for(const input of $('vehicle-form').elements){if(input.name)input.value=['odometer','oil_due'].includes(input.name)?distance(v[input.name]).toFixed(1):v[input.name];}newVehicle=false;
 $('odometer-label').textContent=tr('Odómetro')+' ('+unit()+')';$('oil-due-label').textContent=tr('Próximo aceite (odómetro)')+' · '+unit();$('oil-interval-label').textContent=tr('Intervalo de aceite')+' ('+unit()+')';document.querySelector('.metrics p').textContent=tr(unit()==='km'?'Kilómetros registrados':'Millas registradas');
 paintCockpit(detail);active=detail.trips.find(x=>x.status==='active');tripStatus();
}
const runtime=fetch('/api/car/runtime').then(r=>{if(!r.ok)throw Error('Configuración no disponible');return r.json();}).then(c=>{$('secure-login').hidden=!c.requires_login;if(c.requires_login)$('enter').textContent=tr('Iniciar sesión sin cobro');return c;});
async function enter(){const c=await runtime;clearContext();await api('/start','POST',c.requires_login?{email:$('login-email').value,password:$('login-password').value}:{});$('login-password').value='';vid=null;await refresh();$('login').hidden=true;$('app').hidden=!state.profile.onboarding_completed;}
bind('enter',enter);
bind('logout',async()=>{if(active)throw Error('Finaliza el viaje antes de salir.');stopGPS();clearContext();await api('/logout','POST',{});$('app').hidden=true;$('onboarding').hidden=true;$('login').hidden=false;});
$('vehicle').addEventListener('change',async()=>{clearContext();vid=$('vehicle').value;try{await refresh();}catch(e){msg(e.message);}});

for(const btn of document.querySelectorAll('[data-tab]'))btn.addEventListener('click',()=>{for(const p of document.querySelectorAll('[data-panel]'))p.hidden=p.dataset.panel!==btn.dataset.tab;for(const b of document.querySelectorAll('[data-tab]'))b.classList.toggle('selected',b===btn);});
const path=()=>'/vehicles/'+vid;
bind('simulate',async()=>{simGenerating=true;try{active=await api(path()+'/trips','POST',{source:'simulated'});tripStatus();const start=Date.now()/1000-1200;for(let i=0;i<6;i++){active=await api(path()+`/trips/${active.id}/points`,'POST',{lat:41.88+i*.008,lon:-87.63+i*.004,accuracy:5,timestamp:start+i*180});tripStatus();}msg('Ruta simulada lista. Puedes finalizar el viaje.');}finally{simGenerating=false;tripStatus();}});
bind('gps',async()=>{if(!navigator.geolocation)throw Error('Este navegador no ofrece GPS');active=await api(path()+'/trips','POST',{source:'gps'});tripStatus();startWatch();});
function startWatch(){stopGPS();watch=navigator.geolocation.watchPosition(p=>{const trip=active;if(!trip)return;queue=queue.then(async()=>{active=await api(path()+`/trips/${trip.id}/points`,'POST',{lat:p.coords.latitude,lon:p.coords.longitude,accuracy:p.coords.accuracy,timestamp:p.timestamp/1000});tripStatus();}).catch(e=>msg(e.message));},e=>msg(`GPS: ${e.message}. Revisa permisos o cancela el viaje.`),{enableHighAccuracy:true,maximumAge:0,timeout:20000});}
bind('finish',async()=>{stopGPS();await queue;try{await api(path()+`/trips/${active.id}/finish`,'POST',{tolls:Number($('tolls').value)});active=null;await refresh();msg('Viaje guardado. Estacionamiento registrado.');}catch(e){if(active?.source==='gps')startWatch();throw e;}});
const locate=()=>new Promise((resolve,reject)=>{if(!navigator.geolocation)return reject(Error('GPS no disponible'));navigator.geolocation.getCurrentPosition(p=>resolve({lat:p.coords.latitude,lon:p.coords.longitude,accuracy:p.coords.accuracy,timestamp:p.timestamp/1000}),e=>reject(Error(e.message)),{enableHighAccuracy:true,maximumAge:0,timeout:20000});});
bind('park',async()=>{await api(path()+'/parking','POST',await locate());await refresh();msg('Estacionamiento guardado');});
bind('security',async()=>{await api(path()+'/security/simulate','POST',{});await refresh();msg('Alerta Pro simulada guardada');});
bind('scan',async()=>{const d=await api(path()+'/obd');if(!d.connected&&d.source==='unavailable'){$('obd').textContent=tr(d.notice);return;}$('obd').textContent=tr(`${d.notice} · ${d.rpm} RPM · ${d.coolant_c} °C · ${d.battery_v} V. Sin hardware conectado.`);});
bind('oil-done',async()=>{await api(path()+'/maintenance','POST',{interval:canonical(Number($('oil-interval').value))});await refresh();msg('Cambio de aceite registrado');});
$('expense-form').addEventListener('submit',async e=>{e.preventDefault();try{await api(path()+'/expenses','POST',{category:$('expense-category').value,amount:Number($('expense-amount').value),note:$('expense-note').value});e.target.reset();await refresh();msg('Gasto guardado');}catch(e){msg(e.message);}});
$('profile-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/profile','PUT',{preferred_name:$('profile-preferred-name').value,city:$('profile-city').value,state:$('profile-state').value,country:$('profile-country').value,name:$('profile-name').value,address:$('profile-address').value,phone:$('profile-phone').value});await refresh();msg('Perfil guardado');}catch(e){msg(e.message);}});
bind('new-vehicle',async()=>{if(active)throw Error('Finaliza el viaje antes de crear otro vehículo');$('vehicle-form').reset();$('vehicle-form').dataset.unit='mi';$('odometer-label').textContent=tr('Odómetro')+' (mi)';$('oil-due-label').textContent=tr('Próximo aceite (odómetro)')+' · mi';newVehicle=true;msg('Completa los datos y pulsa Guardar cambios para crear el vehículo');});
$('vehicle-form').addEventListener('submit',async e=>{e.preventDefault();try{const b={};for(const input of e.target.elements)if(input.name)b[input.name]=input.type==='number'?Number(input.value):input.value;const factor=b.distance_unit==='km'?1.609344:1;b.odometer/=factor;b.oil_due/=factor;const v=await api(newVehicle?'/vehicles':path(),newVehicle?'POST':'PUT',b);vid=v.id;await refresh();msg('Vehículo guardado');}catch(e){msg(e.message);}});
async function ask(){clearExpenseProposal();const proposal=parseExpense($('question').value);if(proposal){pendingExpense={...proposal,vehicle:vid,version:contextVersion};$('expense-preview').textContent=detail.vehicle.make+' '+detail.vehicle.model+' '+detail.vehicle.year+' · '+tr(proposal.category)+' · '+money(proposal.amount)+' · '+proposal.note;$('expense-proposal').hidden=false;$('answer').textContent=tr('Revisa y confirma el gasto antes de guardarlo.');return;}const version=contextVersion;const d=await api(path()+'/assistant','POST',{question:$('question').value,language:lang});if(version!==contextVersion)return;$('answer').textContent=tr(d.answer);if($('speak').checked&&'speechSynthesis'in window){speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(d.answer);u.lang=locale();speechSynthesis.speak(u);}}
$('ask-form').addEventListener('submit',async e=>{e.preventDefault();try{await ask();}catch(e){msg(e.message);}});
const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
if(!Recognition){$('voice').disabled=true;$('voice').textContent=tr('Voz no disponible');}else bind('voice',async()=>{const recognition=new Recognition();recognition.lang=locale();recognition.interimResults=false;recognition.onresult=async e=>{$('question').value=e.results[0][0].transcript;try{await ask();}catch(err){msg(err.message);}};recognition.onerror=e=>msg('Voz: '+e.error);recognition.start();msg('Escuchando…');});
window.addEventListener('beforeunload',e=>{if(active){e.preventDefault();e.returnValue='';}});
(async()=>{try{await refresh();$('login').hidden=true;$('app').hidden=!state.profile.onboarding_completed;if(active?.source==='gps')msg('Hay un viaje pendiente. Pulsa Reanudar GPS o cancélalo.');}catch{}})();

bind('cancel-trip',async()=>{stopGPS();await queue;await api(path()+`/trips/${active.id}/cancel`,'POST',{});active=null;await refresh();msg('Viaje cancelado sin modificar odómetro');});
bind('resume',async()=>{if(active?.source==='gps'){startWatch();tripStatus();msg('GPS reanudado');}});

$('language').addEventListener('change',async()=>{clearExpenseProposal();lang=$('language').value;try{localStorage.setItem('aita-car-language',lang);}catch{}applyLanguage();$('message').textContent='';$('answer').textContent=tr('Estoy listo para ayudarte.');$('obd').textContent='';contextVersion++;if('speechSynthesis'in window)speechSynthesis.cancel();if(!Recognition)$('voice').textContent=tr('Voz no disponible');if(state){const drafts=['vehicle-form','profile-form','onboarding-form'].map(id=>[id,Array.from($(id).elements).filter(el=>el.name||el.id).map(el=>[el,el.value])]);const wasNew=newVehicle;try{await refresh();for(const [,fields] of drafts)for(const [el,value] of fields)el.value=value;newVehicle=wasNew;}catch(e){msg(e.message);}}});

$('onboarding-form').addEventListener('submit',async e=>{
 e.preventDefault();const btn=$('onboarding-submit');btn.disabled=true;$('onboarding-error').textContent='';
 try{const data=Object.fromEntries(new FormData(e.target));data.year=Number(data.year);data.initial_odometer=Number(data.initial_odometer);data.additional_vehicles=onboardingCars;const result=await api('/onboarding','POST',data);vid=result.vehicle.id;clearContext();await refresh();$('onboarding').hidden=true;$('app').hidden=false;msg('Onboarding completado');}catch(error){$('onboarding-error').textContent=tr(error.message);}finally{btn.disabled=false;}
});
bind('onboarding-exit',async()=>{await api('/logout','POST',{});$('onboarding').hidden=true;$('login').hidden=false;state=null;});
$('distance-unit').addEventListener('change',()=>{const form=$('vehicle-form');const previous=form.dataset.unit||unit();const next=$('distance-unit').value;const factor=next==='km'&&previous==='mi'?1.609344:next==='mi'&&previous==='km'?1/1.609344:1;for(const name of ['odometer','oil_due'])form.elements[name].value=(Number(form.elements[name].value)*factor).toFixed(1);form.dataset.unit=next;$('odometer-label').textContent=tr('Odómetro')+' ('+next+')';});

bind('upgrade-plan',async()=>{await api('/demo-plan','PUT',{plan:'pro'});await refresh();msg('Plan actualizado. Acceso de prueba sin cobro.');});

function updateMapLinks(){
 const destination=$('apps-destination').value.trim();
 const google=new URL('https://www.google.com/maps/search/');google.searchParams.set('api','1');if(destination)google.searchParams.set('query',destination);
 const waze=new URL('https://waze.com/ul');if(destination)waze.searchParams.set('q',destination);
 const apple=new URL('https://maps.apple.com/');if(destination)apple.searchParams.set('q',destination);
 $('open-gmaps').href=destination?google.href:'https://www.google.com/maps';$('open-waze').href=waze.href;$('open-applemaps').href=apple.href;
}
$('apps-destination').addEventListener('input',updateMapLinks);

function applyTheme(theme){document.documentElement.dataset.theme=theme;$('theme').value=theme;try{localStorage.setItem('aita-car-theme',theme);}catch{}}
let savedTheme;try{savedTheme=localStorage.getItem('aita-car-theme');}catch{}
applyTheme(savedTheme==='light'?'light':'dark');
$('theme').addEventListener('change',()=>applyTheme($('theme').value));

setupVehiclePresets();

for(const button of document.querySelectorAll('[data-jump]'))button.addEventListener('click',()=>{
 document.querySelector('[data-tab="'+button.dataset.jump+'"]').click();
 document.querySelector('[data-panel="'+button.dataset.jump+'"]').scrollIntoView({behavior:'smooth',block:'start'});
});
function updateClock(){const now=new Date();$('cockpit-clock').textContent=now.toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'});}
updateClock();setInterval(updateClock,1000);
function paintCockpit(d){
 const v=d.vehicle;const changes=d.maintenance||[];
 const last=changes.reduce((n,x)=>Math.max(n,x.odometer||0),v.initial_odometer_miles??v.odometer);
 const span=Math.max(1,v.oil_due-last);const reserve=Math.max(0,Math.min(1,d.oil_remaining/span));
 $('oil-gauge-arc').setAttribute('stroke-dasharray',`${reserve*100} 100`);
 $('oil-needle').setAttribute('transform',`rotate(${-90+reserve*180} 120 120)`);
 $('oil-reserve').textContent=distance(d.oil_remaining).toLocaleString(locale(),{maximumFractionDigits:0})+' '+unit();
 $('oil-gauge-title').textContent=tr('Próximo aceite')+': '+$('oil-reserve').textContent;
 const days=Array.from({length:7},(_,i)=>{const date=new Date();date.setHours(0,0,0,0);date.setDate(date.getDate()-6+i);return {date,total:0};});
 for(const trip of d.trips){if(trip.status!=='finished')continue;const date=new Date(trip.started);const day=days.find(x=>date.toDateString()===x.date.toDateString());if(day)day.total+=distance(trip.miles);}
 const max=Math.max(1,...days.map(x=>x.total));$('activity-chart').replaceChildren();
 for(const day of days){const col=document.createElement('div');col.className='chart-column';const amount=document.createElement('strong');amount.textContent=day.total.toFixed(1);const bar=document.createElement('div');bar.className='chart-bar';bar.style.height=Math.max(3,day.total/max*100)+'px';const label=document.createElement('small');label.textContent=day.date.toLocaleDateString(locale(),{weekday:'short'});col.append(amount,bar,label);$('activity-chart').append(col);}
 const total=days.reduce((s,x)=>s+x.total,0).toFixed(1)+' '+unit();$('activity-summary').textContent=total;$('activity-chart').setAttribute('aria-label',tr('Distancia registrada · incluye simulaciones')+': '+days.map(x=>x.date.toLocaleDateString(locale())+' '+x.total.toFixed(1)+' '+unit()).join('; '));
}

function closeCockpitMap(){const frame=$('cockpit-map');frame.removeAttribute('src');frame.hidden=true;$('map-close').hidden=true;$('map-status').textContent=tr('El mapa está desactivado.');}
function showCockpitMap(lat,lon,real=false){
 const south=Math.max(-85,lat-.025),north=Math.min(85,lat+.025),west=Math.max(-180,lon-.04),east=Math.min(180,lon+.04);
 const url=new URL('https://www.openstreetmap.org/export/embed.html');url.searchParams.set('bbox',[west,south,east,north].join(','));url.searchParams.set('layer','mapnik');url.searchParams.set('marker',lat+','+lon);
 $('cockpit-map').src=url.href;$('cockpit-map').hidden=false;$('map-close').hidden=false;$('map-status').textContent=tr(real?'Mapa centrado en tu ubicación.':'Mapa de ejemplo · Chicago. No es tu ubicación.');
}
bind('map-demo',()=>showCockpitMap(41.8818,-87.6231));
bind('map-location',async()=>{const version=contextVersion;const p=await locate();if(version!==contextVersion)return;if(Math.abs(p.lat)>85)throw Error('Ubicación fuera de la cobertura del mapa');showCockpitMap(p.lat,p.lon,true);});
bind('map-close',closeCockpitMap);

function clearExpenseProposal(){pendingExpense=null;$('expense-proposal').hidden=true;}
function parseExpense(text){
 if(!/\b(gast[eé]|pagu[eé]|spent|paid|registra|registrar|anota|record|add)(?=\s|[,.]|$)/i.test(text))return null;
 const match=text.match(/(?:\$\s*)?(\d+(?:[.,]\d{1,2})?)(?:\s*(?:d[oó]lares|dollars|usd))?/i);
 if(!match)return null;const amount=Number(match[1].replace(',','.'));if(amount<=0||amount>100000)return null;
 const category=/aceite|oil|manten|repar|repair/i.test(text)?'mantenimiento':/gasolina|combustible|fuel|gas\b/i.test(text)?'gasolina':/peaje|toll/i.test(text)?'peajes':'otros';
 return {amount,category,note:text.trim().slice(0,200)};
}
bind('dismiss-expense',clearExpenseProposal);
bind('confirm-expense',async()=>{
 const proposal=pendingExpense;if(!proposal||proposal.vehicle!==vid||proposal.version!==contextVersion)throw Error('Vuelve a dictar el gasto para el carro seleccionado.');
 await api(path()+'/expenses','POST',{amount:proposal.amount,category:proposal.category,note:proposal.note});clearExpenseProposal();await refresh();$('answer').textContent=tr('Gasto guardado');msg('Gasto guardado');
});



for(const entry of VEHICLE_CATALOG){const o=document.createElement('option');o.value=entry.make;o.textContent=entry.make;$('catalog-make').append(o);}
$('catalog-make').addEventListener('change',()=>{
 const make=$('catalog-make').value;const entry=VEHICLE_CATALOG.find(x=>x.make===make);$('catalog-model').replaceChildren();const first=document.createElement('option');first.value='';first.textContent=tr('Selecciona un modelo');$('catalog-model').append(first);
 const models=[...new Set([...(entry?.models||[]),...VEHICLE_PRESETS.filter(v=>v[0]===make).map(v=>v[1])])].sort();
 for(const model of models){const o=document.createElement('option');o.value=model;o.textContent=model;$('catalog-model').append(o);}$('catalog-model').disabled=!models.length;
});
$('catalog-model').addEventListener('change',()=>{
 if(!$('catalog-model').value)return;if(active){msg('Finaliza el viaje antes de crear otro vehículo');return;}
 $('new-vehicle').click();$('vehicle-form').elements.make.value=$('catalog-make').value;$('vehicle-form').elements.model.value=$('catalog-model').value;$('vehicle-form').elements.year.focus();
});

async function loadConnectors(){
 const c=await api('/connectors');for(const key of ['maps','music','projection','assistant'])$('connector-'+key).value=c.settings[key];$('connector-obd').value=c.settings.obd_name;
 $('preferred-maps').href={apple:'https://maps.apple.com/',google:'https://www.google.com/maps',waze:'https://waze.com/ul'}[c.settings.maps];$('preferred-music').href={spotify:'https://open.spotify.com/',pandora:'https://www.pandora.com/',amazon:'https://music.amazon.com/'}[c.settings.music];
 $('connector-status').textContent=tr('Mapas y música: acceso externo. OBD: sin conexión. Pantalla del carro: requiere integración nativa. Nexxus: datos locales; IA requiere proveedor.');
}
$('connector-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/connectors','PUT',{maps:$('connector-maps').value,music:$('connector-music').value,obd_name:$('connector-obd').value,projection:$('connector-projection').value,assistant:$('connector-assistant').value});await loadConnectors();msg('Preferencias de conectores guardadas');}catch(e){msg(e.message);}});

let onboardingCars=[];
bind('onboarding-add-car',()=>{
 const form=$('onboarding-form');const car={};for(const key of ['make','model','year','initial_odometer','distance_unit']){const el=form.elements[key];if(!el.reportValidity())return;car[key]=el.value;}
 if(onboardingCars.length>=9)throw Error('Límite de 10 vehículos');car.year=Number(car.year);car.initial_odometer=Number(car.initial_odometer);onboardingCars.push(car);
 const row=document.createElement('div');row.className='row';const text=document.createElement('span');text.textContent=car.make+' '+car.model+' '+car.year+' · '+car.initial_odometer+' '+car.distance_unit;const remove=document.createElement('button');remove.type='button';remove.textContent=tr('Quitar');remove.addEventListener('click',()=>{onboardingCars=onboardingCars.filter(x=>x!==car);row.remove();});row.append(text,remove);$('onboarding-car-list').append(row);
 for(const key of ['make','model','year','initial_odometer'])form.elements[key].value='';form.elements.make.focus();msg('Carro añadido. Completa el siguiente carro antes de guardar.');
});
