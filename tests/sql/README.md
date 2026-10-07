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

Documentos subidos (cuestionario en papel/PDF):

```bash
psql -d t2 -f tests/sql/bootstrap_local.sql
psql -d t2 -f supabase/migrations/20260930120000_member_progress_onboarding.sql
psql -d t2 -f supabase/migrations/20260930130000_member_home.sql
psql -d t2 -f supabase/migrations/20261001120000_evaluation_documents.sql
psql -d t2 -f tests/sql/test_evaluation_documents.sql   # "ALL DOCUMENT SQL TESTS PASSED"
```
Cubren: solo owner/manager y el propio socio ven documentos (staff raso y otros gimnasios no),
sin escritura directa de usuarios, confirmación que no acepta respuestas inventadas para lo
ilegible/en blanco, idempotencia, inmutabilidad y retención del original, y auditoría.

Contabilidad básica:

```bash
psql -d t3 -f tests/sql/bootstrap_local.sql
psql -d t3 -f tests/sql/accounting_setup.sql            # tenants que existían antes de la migración
psql -d t3 -f supabase/migrations/20261006120000_accounting_basic.sql
psql -d t3 -f supabase/migrations/20261006120000_accounting_basic.sql   # segunda vez: idempotente
psql -d t3 -f tests/sql/test_accounting.sql             # "ALL ACCOUNTING SQL TESTS PASSED"
```
Cubren: categorías iniciales (idempotentes, también para tenants nuevos), cantidades/estados/tipos
inválidos, categoría de otro tipo u otro tenant, pago de socio enlazado una sola vez, RLS por rol
(manager edita; staff consulta y registra a su nombre; nadie borra), socio y otro gimnasio sin acceso, anon bloqueado.
