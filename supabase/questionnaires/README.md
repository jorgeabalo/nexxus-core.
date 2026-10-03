# Cuestionario de onboarding (pendiente: falta el original)

El cuestionario original de Golden Age **no está en el repositorio, en Drive ni en el correo**.
El sistema está listo para usarlo, pero **no se ha inventado ninguna pregunta**. Mientras no se
cargue, la evaluación guarda solo medidas e indicadores y el onboarding queda como pendiente
(`members.onboarding_completed = false`).

## Cómo cargarlo (cuando Jorge entregue el original)

1. Transcribir el cuestionario **tal cual** (mismas preguntas, opciones, orden y significado)
   al formato de `plantilla.json`. Tipos admitidos: `single`, `multi`, `yesno`, `number`
   (`min`/`max`), `date`, `text`. `required: true` para las obligatorias.
2. Revisarlo con Jorge/Roberto antes de activarlo.
3. Cargarlo (SQL Editor de Supabase):

```sql
insert into public.questionnaires (tenant_id, code, version, title, definition, source_note, active)
select t.id, 'onboarding', 1, '{"es":"Evaluación inicial","en":"Initial evaluation"}',
       '<PEGAR AQUÍ EL JSON DE "definition">'::jsonb,
       'Transcrito del cuestionario original entregado por Jorge el AAAA-MM-DD', true
from public.tenants t where t.slug = 'golden_age';
```

* Para cambiar preguntas se crea **una versión nueva** (`version = 2`, `active = true` y la
  anterior `active = false`). Una versión ya respondida no se puede editar (lo impide un trigger),
  así cada evaluación conserva la versión con la que se respondió.
* El servidor valida cada respuesta contra la definición (opciones existentes, obligatorias, rangos).
