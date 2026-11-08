'use strict';
// Local curated catalog. Never query external image services with owner data.
const VEHICLE_ART = [
 {make:'kia',model:'sportage',year:2021,file:'kia-sportage-2021.png'},
 {make:'toyota',model:'corolla',year:2022,file:'toyota-corolla-2022.png'},
 {make:'kia',model:'rio',year:2023,file:'kia-rio-2023.png'}
];
function normalizeVehicleName(value){return String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim().replace(/\s+/g,' ');}
function resolveVehicleArt(vehicle){
 const art=VEHICLE_ART.find(a=>a.make===normalizeVehicleName(vehicle.make)&&a.model===normalizeVehicleName(vehicle.model)&&a.year===Number(vehicle.year));
 return {src:'/car/assets/vehicles/'+(art?.file||'generic.svg'),matched:!!art};
}
function paintVehicleArt(vehicle){
 const art=resolveVehicleArt(vehicle);const img=document.getElementById('vehicle-image');
 img.onerror=()=>{img.onerror=null;img.src='/car/assets/vehicles/generic.svg';img.alt=tr('Ilustración genérica de vehículo');document.getElementById('vehicle-art-caption').textContent=tr('Imagen de este modelo pendiente · ilustración genérica');};
 img.src=art.src;
 img.alt=art.matched?`${vehicle.make} ${vehicle.model} ${vehicle.year} · ${tr('Ilustración generada')}`:tr('Ilustración genérica de vehículo');
 document.getElementById('vehicle-art-caption').textContent=tr(art.matched?'Ilustración del modelo · apariencia orientativa':'Imagen de este modelo pendiente · ilustración genérica');
}

const VEHICLE_PRESETS=[
 ['Kia','Sportage',2021],['Kia','Rio',2023],['Kia','Sorento',2023],['Kia','Telluride',2023],
 ['Toyota','Corolla',2022],['Toyota','Camry',2023],['Toyota','RAV4',2023],
 ['Honda','Civic',2023],['Honda','CR-V',2023],['Hyundai','Tucson',2023],
 ['Ford','F-150',2023],['Chevrolet','Equinox',2023],['Nissan','Rogue',2023],['Tesla','Model Y',2023]
];
function setupVehiclePresets(){
 const catalog=typeof VEHICLE_CATALOG==='undefined'?[]:VEHICLE_CATALOG;
 for(const note of document.querySelectorAll('.catalog-note'))note.textContent=tr('Busca tu marca y luego tu modelo. Puedes escribir otro si no aparece.');
 const option=value=>{const o=document.createElement('option');o.value=value;return o;};
 document.getElementById('vehicle-makes').replaceChildren(...[...new Set([...catalog.map(v=>v.make),...VEHICLE_PRESETS.map(v=>v[0])])].sort().map(option));
 for(const [formId,listId] of [['onboarding-form','onboarding-models'],['vehicle-form','settings-models']]){
  const form=document.getElementById(formId);form.elements.model.setAttribute('list',listId);
  const update=()=>{
   const make=normalizeVehicleName(form.elements.make.value);
   const entry=catalog.find(v=>normalizeVehicleName(v.make)===make);
   const models=[...new Set([...(entry?.models||[]),...VEHICLE_PRESETS.filter(v=>normalizeVehicleName(v[0])===make).map(v=>v[1])])].sort();
   document.getElementById(listId).replaceChildren(...models.map(option));
  };
  form.elements.make.addEventListener('input',update);form.elements.model.addEventListener('focus',update);
  update();
 }
 for(const select of document.querySelectorAll('.vehicle-preset')){
  VEHICLE_PRESETS.forEach((v,i)=>{const o=document.createElement('option');o.value=String(i);o.textContent=v.join(' ');select.append(o);});
  select.addEventListener('change',()=>{
   if(select.value==='')return;
   if(select.id==='settings-preset'&&active){msg('Finaliza el viaje antes de crear otro vehículo');select.value='';return;}
   const [make,model,year]=VEHICLE_PRESETS[Number(select.value)];
   const form=document.getElementById(select.id==='onboarding-preset'?'onboarding-form':'vehicle-form');
   if(select.id==='settings-preset'){form.reset();newVehicle=true;form.dataset.unit='mi';document.getElementById('odometer-label').textContent=tr('Odómetro')+' (mi)';document.getElementById('oil-due-label').textContent=tr('Próximo aceite (odómetro)')+' · mi';msg('Completa los datos y pulsa Guardar cambios para crear el vehículo');}
   form.elements.make.value=make;form.elements.model.value=model;form.elements.year.value=year;
   select.value='';
  });
 }
}
