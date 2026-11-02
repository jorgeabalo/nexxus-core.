# Matriz AITA — conexión con los módulos actuales

## Resultado de este bloque

La matriz empieza sobre `main`, usando los tenants UUID, JWT de Supabase y
servicios actuales. No fusiona las ramas antiguas de los PR #2 y #3: su modelo
`Negocio`/SQLite no es la identidad de los módulos actuales del Manager.

`AITAOrchestrator` valida usuario, pertenencia activa, rol owner/manager,
existencia de empresa y módulo explícitamente habilitado (`true`). También
respeta las restricciones de módulos del manager. El servicio de destino
revalida sus propios permisos. El solicitante no puede aportar rol,
contexto alternativo ni una aprobación que desbloquee otras acciones.

## Agentes y responsabilidad objetivo

| Agente | Responsabilidad | Conexión en este bloque |
|---|---|---|
| Coordinación | Descomponer objetivos, asignar tareas y verificar resultados | Pendiente |
| Alta y configuración | Crear empresas, asignar servicios, planes y marca | Pendiente |
| Recepción | Consultas, citas, derivaciones | Adaptador de matriz pendiente; Claudia existente no cambia |
| Atención al cliente | Seguimiento, solicitudes, retención y reactivación | Pendiente |
| Soporte técnico | Diagnóstico y resolución de incidencias | Pendiente |
| Marketing | Marca, contenido, campañas, resultados | Consulta de marca, campañas y conteos de contenido |
| Ventas | Prospectos, propuestas y oportunidades | Pendiente |
| Contabilidad | Ingresos, gastos, cobros y reportes | Resumen financiero del período predeterminado del servicio |
| Inventario | Existencias, alertas y reposición | Pendiente |
| Web | Creación y mantenimiento de sitios y SEO | Pendiente |
| Integraciones | Canales, servicios y fallos de conectores | Pendiente |
| Costos | Consumo y presupuestos por empresa | Pendiente |
| Calidad | Resultados de tareas, marca y aislamiento | Pendiente |

El catálogo es determinista: una responsabilidad definida **no** es un agente
autónomo operativo. `available` solo significa que este adaptador de consulta
está implementado y autorizado; no verifica migraciones, conectores ni salud
del proveedor. `planned`, `disabled` y `restricted` no ofrecen capacidades.

## API

- `GET /api/manager/aita/agents?tenant_id=<UUID>`: catálogo para una empresa
  existente. Solo expone id, nombre, zona horaria y responsabilidad/capacidades.
- `POST /api/manager/aita/read`, con JWT Bearer y este cuerpo:

```json
{"tenant_id":"<UUID>","agent":"marketing","capability":"brand"}
```

Operaciones admitidas: `marketing.brand`, `marketing.campaigns`,
`marketing.dashboard` y `finance.summary`. El dashboard devuelve conteos
de estados almacenados y contenido próximo; no ejecuta la sincronización
de trabajos del dashboard completo ni devuelve configuración interna.

Las respuestas incluyen empresa, agente, capacidad, `read_only: true` y datos
del servicio existente. No hay LLM, llamadas a proveedores de IA, escrituras,
publicaciones, creación de empresas, pagos, migraciones ni cambios de plan.
Supabase sí se consulta mediante el acceso existente del backend.

## Siguiente bloque de ejecución

1. Identidad del operador AITA, separada del owner/manager de cada cliente.
   Ser owner de Golden Age no debe otorgar acceso global a otras empresas.
2. Tareas persistentes: objetivo, empresa, agente responsable, dependencias,
   estado, resultado y trazabilidad; reintentos sin duplicar acciones.
3. Alta transaccional de empresa y asignación de módulos/plan, reutilizando
   la identidad de Supabase. Cambios propuestos y ejecutados deben quedar
   separados y ligados al usuario autorizado.
4. Conectar el flujo marca → campaña → contenido con los servicios actuales;
   sus políticas de aprobación y límites siguen siendo la autoridad.
5. Añadir adaptadores de escritura y políticas explícitas por empresa.
   Un agente solo podrá ejecutar herramientas registradas y permisos
   delegados; el modelo no podrá concedérselos a sí mismo.

Objetivo final: recibir una petición, repartir el trabajo entre especialistas,
ejecutar dentro de las reglas autorizadas y comprobar el resultado. No se
consigue solo añadiendo nombres o prompts al catálogo.

## Validación

Pruebas HTTP con la aplicación real y servicios reales sobre Supabase simulado:
autenticación, roles, empresas distintas, cambio de empresa, cuenta inactiva,
empresa eliminada, módulos mal formados, restricciones del manager, rechazo
de escrituras y de contexto/aprobaciones inyectados, ausencia de mutaciones y
errores seguros. No se validó contra producción.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_aita_matrix.py
```
