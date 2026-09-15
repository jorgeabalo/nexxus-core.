const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');

const attack = '<img src=x onerror="window.compromised=true">';
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const reply = (data, ok=true) => Promise.resolve({ok, status:ok?200:500, json:async()=>data});
function load(file, url='/admin') {
  const errors=[];
  const virtualConsole=new VirtualConsole();
  virtualConsole.on('jsdomError', e=>errors.push(e));
  const dom=new JSDOM(fs.readFileSync(path.join(__dirname,'../..',file),'utf8'), {
    url:'https://example.invalid'+url, runScripts:'dangerously', virtualConsole,
  });
  assert.deepEqual(errors, []);
  return {dom, window:dom.window, doc:dom.window.document, errors};
}
const profile = {
  nombre:attack, fecha_ingreso:'2026-01-01', meses_como_socio:8,
  estado_membresia:'activo', plan_membresia:attack, meses_adeudados:1,
  proximo_vencimiento:'2026-10-01', riesgo:{nivel:'critico',motivos:[attack]},
  asistencia:{visitas_ultimos_30_dias:8,promedio_semanal:2,dias_desde_ultima_visita:3,total_checkins_historico:20},
  ultima_medicion:{peso_kg:78,bmi:24,clasificacion_bmi:'normal'},
  progreso:{delta_peso_kg:-2,delta_bmi:-1,desde:'2026-01-01'},
};
const measurements = [
  {fecha:'2026-01-01',peso_kg:80,bmi:25,cintura_cm:85,notas:attack},
  {fecha:'2026-09-01',peso_kg:78,bmi:24,cintura_cm:82,notas:'Seguimiento'},
];

test('operator panel escapes business names, statuses and plan labels',()=>{
  const {dom,window:w,doc}=load('panel.html');
  w.renderizar({},[{nombre:attack,vertical:attack,plan:attack,estado_suscripcion:attack}],[],null);
  assert.equal(doc.querySelectorAll('img').length,0);
  assert.ok(doc.getElementById('panel-principal').textContent.includes(attack));
  assert.ok(doc.getElementById('panel-secundario').textContent.includes(attack));
  dom.window.close();
});

test('tenant panel preserves links and escapes stored names and notes',()=>{
  const {dom,window:w,doc}=load('panel.html','/golden-age/admin');
  const alerts={en_riesgo:[{cliente_id:'one',nombre:attack,nivel:'critico',motivos:[attack]}],reactivacion:[],total_en_riesgo:1,total_reactivacion:0};
  w.renderizar({},[{id:'one',nombre:attack,meses_adeudados:1,estado_membresia:'activo',plan_membresia:attack}],[],alerts);
  w.renderizarAlertas(alerts);
  assert.equal(doc.querySelectorAll('img').length,0);
  assert.equal(doc.querySelector('#panel-principal a').getAttribute('href'),'/golden-age/admin/clientes/one');
  assert.equal(doc.getElementById('panel-agente-config').hidden,true);
  dom.window.close();
});

test('onboarding previews data and only an explicit click sends confirmation once',async()=>{
  const {dom,window:w,doc}=load('panel.html');
  w.authHeader='Basic synthetic';
  let confirmations=0, finish;
  w.fetch=(url,opts)=>{
    if(url.endsWith('/confirmar')){
      confirmations++;
      assert.equal(opts.method,'POST');
      assert.equal(opts.headers.Authorization,'Basic synthetic');
      assert.equal(opts.headers['Content-Type'],'application/json');
      assert.deepEqual(JSON.parse(opts.body),{confirmar:true});
      return new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({cargados:1})});});
    }
    if(url.endsWith('agente-configuracion')) return reply({respuesta:'Revisa antes de guardar.',historial:[],propuestas:[{
      id:'proposal-1',tool:'cargar_socio',datos:{negocio_slug:'golden-age',nombre:attack},requiere_confirmacion:true,
    }]});
    return reply(url.endsWith('/negocios')?[]:{});
  };
  doc.getElementById('chat-input').value='Añade un socio';
  w.enviarMensajeAgente();
  await settle();
  assert.equal(confirmations,0);
  assert.equal(doc.querySelectorAll('img').length,0);
  assert.ok(doc.getElementById('chat-mensajes').textContent.includes(attack));
  const button=doc.querySelector('#chat-mensajes button');
  assert.equal(button.textContent,'Confirmar cambios');
  button.click(); button.click();
  assert.equal(confirmations,1);
  assert.equal(button.disabled,true);
  finish(); await settle(); await settle();
  assert.ok(doc.getElementById('chat-mensajes').textContent.includes('Cambios guardados.'));
  assert.equal(button.disabled,true);
  dom.window.close();
});

test('HTTP errors do not display a false success and release chat controls',async()=>{
  const {dom,window:w,doc}=load('panel.html');
  w.fetch=()=>reply({detail:'unavailable'},false);
  doc.getElementById('chat-input').value='Hola';
  w.enviarMensajeAgente(); await settle();
  assert.ok(doc.getElementById('chat-mensajes').textContent.includes('No se pudo contactar'));
  assert.equal(doc.getElementById('chat-enviar').disabled,false);
  dom.window.close();
});

test('member profile keeps BMI, attendance, history and chart while escaping text',()=>{
  const {dom,window:w,doc}=load('panel_cliente.html','/golden-age/admin/clientes/one');
  w.renderizar(profile,measurements);
  assert.equal(doc.querySelectorAll('img').length,0);
  assert.ok(doc.getElementById('panel-historial').textContent.includes(attack));
  assert.ok(doc.getElementById('panel-cuenta').textContent.includes(attack));
  assert.ok(doc.getElementById('kpis').textContent.includes('24 (Normal)'));
  assert.ok(doc.getElementById('kpis').textContent.includes('8 · 2/sem'));
  assert.equal(doc.querySelectorAll('#grafico-peso circle').length,2);
  assert.equal(doc.querySelectorAll('#grafico-peso [onmousemove]').length,0);
  dom.window.close();
});

test('measurement form keeps tenant URL, auth and numeric payload',async()=>{
  const {dom,window:w,doc}=load('panel_cliente.html','/golden-age/admin/clientes/one');
  let posted=false;
  w.authHeader='Basic synthetic';
  w.fetch=(url,opts)=>{
    if(opts.method==='POST'){
      posted=true;
      assert.equal(url,'/api/golden-age/clientes/one/mediciones');
      assert.equal(opts.headers.Authorization,'Basic synthetic');
      assert.equal(JSON.parse(opts.body).peso_kg,79.5);
      return reply({medicion_id:'test'});
    }
    return reply(url.endsWith('/perfil')?profile:measurements);
  };
  doc.getElementById('f-peso').value='79.5';
  w.guardarMedicion(); await settle(); await settle();
  assert.equal(posted,true);
  assert.equal(doc.getElementById('medicion-ok').hidden,false);
  dom.window.close();
});
