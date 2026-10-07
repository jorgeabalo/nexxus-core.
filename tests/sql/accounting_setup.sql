-- Datos previos a la migración de contabilidad (tenants ya existentes antes de aplicarla).
\set ON_ERROR_STOP 1
insert into tenants (id, slug, name, modules) values
 ('11000000-0000-0000-0000-00000000000a','tx','Tenant X','{"accounting":false}'),
 ('11000000-0000-0000-0000-00000000000b','ty','Tenant Y','{}');
insert into tenant_users (tenant_id, user_id, role) values
 ('11000000-0000-0000-0000-00000000000a','00000000-0000-0000-0000-0000000000a1','owner'),
 ('11000000-0000-0000-0000-00000000000a','00000000-0000-0000-0000-0000000000a2','manager'),
 ('11000000-0000-0000-0000-00000000000a','00000000-0000-0000-0000-0000000000a3','staff'),
 ('11000000-0000-0000-0000-00000000000b','00000000-0000-0000-0000-0000000000c1','owner');
