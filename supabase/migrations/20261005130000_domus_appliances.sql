-- Additive appliance categories; no remote execution or credential storage.
begin;
alter table public.domus_devices drop constraint domus_devices_kind_check;
alter table public.domus_devices add constraint domus_devices_kind_check
 check(kind in ('tv','music','plug','thermostat','vacuum','security','other','washer','dryer','dishwasher'));
commit;
