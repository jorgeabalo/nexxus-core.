// Run with NODE_PATH pointing at a development install of @electric-sql/pglite.
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {PGlite}=require('@electric-sql/pglite');
const db=new PGlite();
await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
create schema auth; create table auth.users(id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
grant usage on schema public,auth to anon,authenticated,service_role; grant execute on function auth.uid() to anon,authenticated,service_role;
create table public.business_sentinel(value text); insert into public.business_sentinel values('Golden Age unchanged');`);
await db.exec(await readFile(new URL('../../supabase/migrations/20261003120000_domus_onboarding.sql',import.meta.url),'utf8'));
const a='00000000-0000-0000-0000-000000000001',b='00000000-0000-0000-0000-000000000002',c='00000000-0000-0000-0000-000000000003';
await db.query('insert into auth.users values($1),($2),($3)',[a,b,c]);
let checks=0;
async function asUser(id,sql,args=[],expected){
 await db.exec('begin; set local role authenticated');
 await db.query("select set_config('request.jwt.claim.sub',$1,true)",[id]);
 try {const result=await db.query(sql,args); if(expected)assert.fail('expected SQLSTATE '+expected);await db.exec('commit');return result.rows;}
 catch(e){await db.exec('rollback');if(!expected)throw e;assert.equal(e.code,expected);checks++;}
}
const create="select public.domus_create_home($1,'America/Chicago','es-US',$2) as id";
const request='10000000-0000-0000-0000-000000000001';
const h1=(await asUser(a,create,['Home A',request]))[0].id;
assert.equal((await asUser(a,create,['Duplicate',request]))[0].id,h1);checks++;
const h2=(await asUser(b,create,['Home B','10000000-0000-0000-0000-000000000002']))[0].id;
assert.equal((await asUser(c,'select * from public.domus_homes')).length,0);checks++;
assert.deepEqual((await asUser(a,'select id from public.domus_homes')).map(r=>r.id),[h1]);checks++;
await asUser(a,'insert into public.domus_rooms(home_id,name) values($1,$2)',[h2,'intruder'],'42501');
await asUser(a,'insert into public.domus_home_users(home_id,user_id,role) values($1,$2,$3)',[h2,a,'owner'],'42501');
await asUser(a,"select public.domus_create_home('Bad','invented/timezone','es-US',$1)",['10000000-0000-0000-0000-000000000003'],'22023');
await asUser(a,'select public.domus_complete_onboarding($1)',[h1],'22023');
await asUser(a,"insert into public.domus_rooms(home_id,name) values($1,'Sala')",[h1]);
await asUser(b,"insert into public.domus_rooms(home_id,name) values($1,'Sala B')",[h2]);
const room2=(await asUser(b,'select id from public.domus_rooms where home_id=$1',[h2]))[0].id;
// Even an owner of both homes cannot attach a room from one home to the other.
await db.query("insert into public.domus_home_users(home_id,user_id,role) values($1,$2,'owner')",[h2,a]);
await asUser(a,"insert into public.domus_devices(home_id,room_id,name,kind) values($1,$2,'TV','tv')",[h1,room2],'23503');
await asUser(a,"insert into public.domus_channels(home_id,platform,connection_status) values($1,'alexa','connected')",[h1],'42501');
await asUser(a,"insert into public.domus_channels(home_id,platform) values($1,'alexa')",[h1]);
await asUser(a,"update public.domus_channels set connection_status='connected' where home_id=$1",[h1],'42501');
await asUser(a,'select public.domus_complete_onboarding($1)',[h1]);
assert.equal((await asUser(a,'select connection_status from public.domus_channels where home_id=$1',[h1]))[0].connection_status,'pending');checks++;
await db.query("insert into public.domus_home_users(home_id,user_id,role) values($1,$2,'guest')",[h1,c]);
assert.equal((await asUser(c,'select * from public.domus_rooms')).length,1);checks++;
await asUser(c,"insert into public.domus_rooms(home_id,name) values($1,'Guest change')",[h1],'42501');
await asUser(c,'select public.domus_complete_onboarding($1)',[h1],'42501');
await asUser(c,"insert into public.domus_consents(home_id,scope,granted) values($1,'anthropic_voice_queries_v1',true)",[h1]);
assert.equal((await asUser(a,'select * from public.domus_consents')).length,0);checks++;
await asUser(c,"insert into public.domus_consents(home_id,user_id,scope,granted) values($1,$2,'anthropic_voice_queries_v1',true)",[h1,a],'42501');
await asUser(c,'update public.domus_consents set granted=false',[],'42501');
await asUser(c,'delete from public.domus_consents',[],'42501');
await asUser(c,"insert into public.domus_consents(home_id,scope,granted) values($1,'anthropic_voice_queries_v1',false)",[h1]);
assert.equal((await asUser(c,'select * from public.domus_consents')).length,2);checks++;
await db.exec('begin;set local role anon');
try{await db.query(create,['Anon','10000000-0000-0000-0000-000000000004']);assert.fail('anon access');}catch(e){assert.equal(e.code,'42501');checks++;}finally{await db.exec('rollback');}
assert.equal((await db.query('select value from public.business_sentinel')).rows[0].value,'Golden Age unchanged');checks++;
await db.close();console.log(`${checks} Domus SQL security checks passed`);
