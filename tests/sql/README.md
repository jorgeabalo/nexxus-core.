# Pruebas SQL (RLS, línea base, evaluaciones)

Se ejecutan en un Postgres local desechable (no tocan Supabase):

```bash
createdb t1
psql -d t1 -f tests/sql/bootstrap_local.sql          # emula auth.uid(), roles y tablas previas
psql -d t1 -f supabase/migrations/20260930120000_member_progress_onboarding.sql
psql -d t1 -f tests/sql/test_member_progress.sql      # termina con "ALL SQL TESTS PASSED"
```

Cubren: evaluación sin cuestionario (no marca onboarding completo), borrador, envío idempotente
(`client_ref`), línea base única e inmutable, reevaluación separada, normalización del ejercicio,
autoría (socio/staff) decidida por el servidor, aislamiento entre socios y entre gimnasios,
validación de respuestas contra una definición **de prueba** y bloqueo de versiones ya usadas.
