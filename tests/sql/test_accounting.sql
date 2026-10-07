-- Pruebas SQL: contabilidad básica (base local desechable, datos solo de prueba).
\set ON_ERROR_STOP 1
insert into auth.users (id, email) values
 ('00000000-0000-0000-0000-0000000000a1','owner@x.test'),
 ('00000000-0000-0000-0000-0000000000a2','mgr@x.test'),
 ('00000000-0000-0000-0000-0000000000a3','staff@x.test'),
 ('00000000-0000-0000-0000-0000000000b1','socio@x.test'),
 ('00000000-0000-0000-0000-0000000000c1','owner@y.test');
-- tenants y equipo: tests/sql/accounting_setup.sql (cargado ANTES de la migración)

create or replace function pg_temp.as_user(u text) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims', json_build_object('sub', u)::text, false); execute 'set role authenticated'; end $$;
create or replace function pg_temp.reset() returns void language plpgsql as $$
begin execute 'reset role'; perform set_config('request.jwt.claims', '', false); end $$;

-- 1. Categorías iniciales: 14 por tenant, también para tenants nuevos (trigger), sin duplicar
do $$ begin
  assert (select count(*) from accounting_categories where tenant_id = '11000000-0000-0000-0000-00000000000a') = 14, 'seed tenant X';
  assert (select count(*) from accounting_categories where tenant_id = '11000000-0000-0000-0000-00000000000b') = 14, 'seed tenant Y';
  assert (select count(*) from accounting_categories where type = 'income' and tenant_id = '11000000-0000-0000-0000-00000000000a') = 5, '5 de ingreso';
  assert (select (modules->>'accounting')::boolean from tenants where slug = 'tx'), 'módulo activado';
  assert (select count(*) from accounting_transactions) = 0, 'sin movimientos ficticios';
  perform private.seed_accounting_categories('11000000-0000-0000-0000-00000000000a');
  assert (select count(*) from accounting_categories where tenant_id = '11000000-0000-0000-0000-00000000000a') = 14, 'seed idempotente';
end $$;
insert into tenants (id, slug, name) values ('11000000-0000-0000-0000-00000000000c','tz','Tenant Z');
do $$ begin
  assert (select count(*) from accounting_categories where tenant_id = '11000000-0000-0000-0000-00000000000c') = 14, 'tenant nuevo con categorías';
end $$;

-- 2. Restricciones: cantidades, estados, tipos, categoría del mismo tipo y tenant
do $$
declare inc uuid; exp uuid; other uuid;
begin
  select id into inc from accounting_categories where tenant_id = '11000000-0000-0000-0000-00000000000a' and name = 'Masajes';
  select id into exp from accounting_categories where tenant_id = '11000000-0000-0000-0000-00000000000a' and name = 'Alquiler';
  select id into other from accounting_categories where tenant_id = '11000000-0000-0000-0000-00000000000b' and name = 'Masajes';
  begin insert into accounting_transactions (tenant_id, transaction_date, type, description, amount, created_by)
        values ('11000000-0000-0000-0000-00000000000a', '2026-10-01', 'income', 'x', -1, '00000000-0000-0000-0000-0000000000a1');
        raise exception 'negativo debió fallar'; exception when check_violation then null; end;
  begin insert into accounting_transactions (tenant_id, transaction_date, type, description, amount, status, created_by)
        values ('11000000-0000-0000-0000-00000000000a', '2026-10-01', 'income', 'x', 1, 'refunded', '00000000-0000-0000-0000-0000000000a1');
        raise exception 'estado inválido debió fallar'; exception when check_violation then null; end;
  begin insert into accounting_transactions (tenant_id, transaction_date, type, description, amount, payment_method, created_by)
        values ('11000000-0000-0000-0000-00000000000a', '2026-10-01', 'income', 'x', 1, 'zelle', '00000000-0000-0000-0000-0000000000a1');
        raise exception 'método inválido debió fallar'; exception when check_violation then null; end;
  begin insert into accounting_transactions (tenant_id, transaction_date, type, category_id, description, amount, created_by)
        values ('11000000-0000-0000-0000-00000000000a', '2026-10-01', 'income', exp, 'x', 1, '00000000-0000-0000-0000-0000000000a1');
        raise exception 'categoría de otro tipo debió fallar'; exception when check_violation then null; end;
  begin insert into accounting_transactions (tenant_id, transaction_date, type, category_id, description, amount, created_by)
        values ('11000000-0000-0000-0000-00000000000a', '2026-10-01', 'income', other, 'x', 1, '00000000-0000-0000-0000-0000000000a1');
        raise exception 'categoría de otro tenant debió fallar'; exception when foreign_key_violation or check_violation then null; end;
  begin insert into accounting_obligations (tenant_id, obligation_type, counterparty, amount, due_date, created_by)
        values ('11000000-0000-0000-0000-00000000000a', 'loan', 'x', 1, '2026-10-01', '00000000-0000-0000-0000-0000000000a1');
        raise exception 'tipo de pendiente inválido debió fallar'; exception when check_violation then null; end;
  insert into accounting_transactions (tenant_id, transaction_date, type, category_id, description, amount, created_by)
    values ('11000000-0000-0000-0000-00000000000a', '2026-10-01', 'income', inc, 'Masaje', 80, '00000000-0000-0000-0000-0000000000a1');
end $$;

-- 3. Un pago de socio solo puede enlazarse una vez (mientras no esté cancelado)
insert into members (id, tenant_id, first_name) values ('21000000-0000-0000-0000-0000000000b1','11000000-0000-0000-0000-00000000000a','Eva');
insert into payments (id, tenant_id, member_id, amount, payment_status) values
 ('31000000-0000-0000-0000-000000000001','11000000-0000-0000-0000-00000000000a','21000000-0000-0000-0000-0000000000b1', 50, 'paid');
insert into accounting_transactions (id, tenant_id, transaction_date, type, description, amount, source_type, source_id, created_by)
  values ('41000000-0000-0000-0000-000000000001','11000000-0000-0000-0000-00000000000a','2026-10-02','income','Pago Eva',50,
          'payment','31000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1');
do $$ begin
  begin insert into accounting_transactions (tenant_id, transaction_date, type, description, amount, source_type, source_id, created_by)
        values ('11000000-0000-0000-0000-00000000000a','2026-10-02','income','Pago Eva',50,'payment','31000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1');
        raise exception 'duplicado debió fallar'; exception when unique_violation then null; end;
  update accounting_transactions set status = 'cancelled' where id = '41000000-0000-0000-0000-000000000001';
  insert into accounting_transactions (tenant_id, transaction_date, type, description, amount, source_type, source_id, created_by)
    values ('11000000-0000-0000-0000-00000000000a','2026-10-02','income','Pago Eva',50,'payment','31000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1');
  assert (select count(*) from payments) = 1, 'payments intacta';
end $$;

-- 4. RLS por rol y tenant
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a2');   -- manager
do $$ begin
  assert (select count(*) from accounting_transactions) = 3, 'manager ve su tenant';
  update accounting_transactions set notes = 'ok' where description = 'Masaje';
  assert found, 'manager edita';
  insert into accounting_obligations (tenant_id, obligation_type, counterparty, amount, due_date, created_by)
    values ('11000000-0000-0000-0000-00000000000a','payable','Landlord',1500,'2026-10-01','00000000-0000-0000-0000-0000000000a2');
  delete from accounting_transactions;
  raise exception 'delete debió fallar';
exception when insufficient_privilege then null;
end $$;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000a3');   -- staff
do $$ begin
  assert (select count(*) from accounting_transactions) = 3, 'staff consulta';
  insert into accounting_transactions (tenant_id, transaction_date, type, description, amount, created_by)
    values ('11000000-0000-0000-0000-00000000000a','2026-10-03','expense','Toallas',20,'00000000-0000-0000-0000-0000000000a3');
  update accounting_transactions set amount = 1 where description = 'Masaje';
  assert not found, 'staff no edita';
  begin insert into accounting_transactions (tenant_id, transaction_date, type, description, amount, created_by)
        values ('11000000-0000-0000-0000-00000000000a','2026-10-03','expense','x',1,'00000000-0000-0000-0000-0000000000a1');
        raise exception 'staff no registra a nombre de otro'; exception when insufficient_privilege then null; end;
  begin insert into accounting_obligations (tenant_id, obligation_type, counterparty, amount, due_date, created_by)
        values ('11000000-0000-0000-0000-00000000000a','payable','x',1,'2026-10-01','00000000-0000-0000-0000-0000000000a3');
        raise exception 'staff no crea pendientes'; exception when insufficient_privilege then null; end;
end $$;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000b1');   -- socio (no está en tenant_users)
update members set user_id = '00000000-0000-0000-0000-0000000000b1' where id = '21000000-0000-0000-0000-0000000000b1';
do $$ begin
  assert (select count(*) from accounting_transactions) = 0, 'socio no ve contabilidad';
  assert (select count(*) from accounting_categories) = 0, 'socio no ve categorías';
  assert (select count(*) from accounting_obligations) = 0, 'socio no ve pendientes';
  begin insert into accounting_transactions (tenant_id, transaction_date, type, description, amount, created_by)
        values ('11000000-0000-0000-0000-00000000000a','2026-10-03','income','x',1,'00000000-0000-0000-0000-0000000000b1');
        raise exception 'socio no escribe'; exception when insufficient_privilege then null; end;
end $$;
select pg_temp.as_user('00000000-0000-0000-0000-0000000000c1');   -- owner de otro gimnasio
do $$ begin
  assert (select count(*) from accounting_transactions) = 0, 'otro gimnasio no ve nada de X';
  assert (select count(*) from accounting_categories) = 14, 've solo sus categorías';
  update accounting_transactions set amount = 1;
  assert not found, 'otro gimnasio no modifica';
end $$;
select pg_temp.reset();
set role anon;
do $$ begin
  perform 1 from accounting_transactions;
  raise exception 'anon debió fallar';
exception when insufficient_privilege then null;
end $$;
reset role;
select 'ALL ACCOUNTING SQL TESTS PASSED' as result;
