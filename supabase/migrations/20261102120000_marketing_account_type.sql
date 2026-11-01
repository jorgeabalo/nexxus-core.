-- AITA Marketing — tipo de cuenta en el Brand Kit.
--
-- local_business (por defecto, p. ej. Golden Age): negocio con clientes en su zona.
-- content_channel: canal educativo o de divulgación (seguir, guardar, compartir).
-- medical_education: canal médico educativo; el redactor nunca da consejo médico personal y la Biblioteca exige
--   confirmar que cada archivo no identifica al paciente antes de poder usarlo.
alter table public.marketing_brand_profiles
  add column if not exists account_type text not null default 'local_business';
alter table public.marketing_brand_profiles
  drop constraint if exists marketing_brand_profiles_account_type_check;
alter table public.marketing_brand_profiles
  add constraint marketing_brand_profiles_account_type_check
  check (account_type in ('local_business', 'content_channel', 'medical_education'));
