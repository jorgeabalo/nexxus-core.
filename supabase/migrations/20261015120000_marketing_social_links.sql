-- AITA Marketing — direcciones públicas de las redes de la marca (Brand Kit).
--
-- Solo guarda los enlaces a los perfiles (p. ej. https://instagram.com/goldenage):
-- no son credenciales ni autorizan a publicar. La validación fina (red permitida,
-- dominio de cada red, https) la hace services/marketing_domain.py → social_links;
-- aquí solo se asegura la forma para que nadie guarde algo arbitrario.
alter table public.marketing_brand_profiles
  add column if not exists social_links jsonb not null default '{}'::jsonb;

alter table public.marketing_brand_profiles
  drop constraint if exists marketing_brand_profiles_social_links_check;
alter table public.marketing_brand_profiles
  add constraint marketing_brand_profiles_social_links_check
  check (jsonb_typeof(social_links) = 'object' and length(social_links::text) <= 4000);
