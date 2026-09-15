# Integración de Claude con AITA

## Base verificada y alcance

- Repositorio privado real: `jorgeabalo/nexxus-core.` (incluye el punto final).
- `main` comparado: `36be325c5ec27c5778ef5ebd63ac1f51a0e33ecf`.
- Base de integración: `aita-orchestrator-foundation` en `de9cd000269e592abf0145e5d50d9d9cd804483c`.
- Árbol base reconstruido y verificado: `3520d2a9fff3b2fb71ac2b127cc607d929614ddb`.
- La rama AITA estaba cinco commits por delante de main, sin retraso. Añadía Orchestrator, BusinessProfile, arquitectura y sus pruebas; no había modificado los cinco archivos existentes entregados por Claude.
- Se compararon los siete adjuntos antes de aplicar cambios. Esta integración va en una rama/PR separada dirigida a la rama AITA. No implica fusión en main ni despliegue.

## Decisiones por archivo

| Archivo de Claude | Integración | Descartado o adaptado |
| --- | --- | --- |
| `agente_configuracion.py` | Chat interno del operador, consultas, propuestas de altas individuales/lotes y configuración. | Se reemplaza su escritura directa y lógica duplicada por `onboarding_service`; AITA valida capacidades. No puede confirmar sus propias propuestas, cobrar ni crear suscripciones. Se corrige la descripción que sugería cargar un socio para actualizarlo. |
| `main.py` | Rutas de chat/confirmación, comprobación del negocio en mensajes/finalización, límite de creación de llamadas, advertencia de credenciales. | No se confía directamente en X-Forwarded-For. Credenciales ausentes/de ejemplo cierran acceso. Validación y altas compartidas. Se conservan las demás rutas existentes. |
| `generic_service.py` | Comprobación de pertenencia de llamada al negocio. | Mantiene su función de recepción y su API interna compatible; no se convierte en otro Orchestrator. |
| `billing_service.py` | Protección de firmas. | Se rechaza también el modo implícito de webhooks sin firma cuando no hay claves; los handlers locales siguen disponibles para pruebas. |
| `panel.html` | Chat del operador, escape HTML de datos almacenados, enlaces a socios. | Se añaden propuestas revisables, confirmación explícita, control de doble clic y errores HTTP. Ninguna escritura se ejecuta al enviar el chat. |
| `panel_cliente.html` | Escape HTML de estado, plan, notas y motivos. | Se conservan peso, BMI, evolución, asistencia, membresía y alertas: ya estaban en la base. Se eliminan handlers de tooltip construidos con valores almacenados. |
| `panel_cliente_1.html` | Ningún archivo duplicado. | Es idéntico byte por byte a `panel_cliente.html`: se utiliza el nombre que ya sirve la aplicación. |

Los dos paneles de socio comparten SHA-256 `27bcfc7d4d8cab519dbc85eaa3ce980262cbcc74532a365de31425c6276b0000`.

## Flujo y límites de la adaptación

1. La autenticación de operador protege chat y confirmaciones. Las credenciales de admin de negocio no permiten acceder al nuevo agente.
2. El modelo sólo consulta o propone. Las herramientas registradas se validan con esquemas que rechazan campos adicionales y con `AITAOrchestrator`.
3. `BusinessProfile` se proyecta desde el `Negocio` existente al consultar; no se crea una base/configuración empresarial paralela. El nombre legal permanece provisional y explícitamente sin verificar.
4. Cada propuesta guarda datos normalizados, operador y tenant. El panel muestra el contenido para revisión. Una propuesta caduca en 30 minutos.
5. Confirmar envía JSON explícito; el servidor utiliza el contenido guardado, no acepta reemplazar los datos desde el navegador, vuelve a validar el tenant y los permisos, y escribe en una transacción.
6. Altas, lote completo y resultado de confirmación se guardan juntos. Un reintento o confirmación concurrente devuelve el mismo resultado, sin duplicar las altas. Un lote inválido no guarda filas parcialmente.
7. El historial reenviado contiene sólo texto, con límites de mensajes/caracteres; los bloques internos del SDK no se reenvían como historial del navegador. Errores del proveedor no exponen su excepción ni sus datos.

La tabla nueva `onboarding_actions` es aditiva. Conserva propuestas/resultados para confirmación y trazabilidad, incluyendo los datos de socios propuestos; debe recibir el mismo control de acceso y política de retención que los registros de socios. No es un Business Vault ni una auditoría inmutable. Las tablas existentes no cambian de columnas. El arranque actual de `models.py` crea las tablas que faltan.

La API manual conserva creación de suscripciones cuando se proporciona `email_facturacion`; el agente no ejecuta ese flujo. Los nuevos IDs de negocio/socio usan UUID completos; los IDs existentes se mantienen. Se validan nombres, slugs, planes, fechas, membresías y alturas en las altas. Entradas que antes se aceptaban sin validación pueden recibir 422/400. Actualizar un socio existente sigue usando su endpoint PATCH; cargar crea un socio nuevo.

Para un negocio nuevo se confirma primero su alta y después se propone la carga de socios. Una propuesta idéntica pendiente se reutiliza; esto no intenta deduplicar personas en solicitudes nuevas e independientes.

## Golden Age

- La plantilla `golden_age_template()` registra amarillo/negro. `#FFD700` es un tono provisional, no un color corporativo verificado.
- Se preservan sin editar los logos suministrados en la conversación: `assets/golden-age/logo-full.png` (800 × 800) y `logo-mark.png` (150 × 150).
- `BrandProfile.logo_asset_refs` referencia ambos originales; son recursos para el futuro CRM, todavía no se sirven como rutas web ni se aplican a todos los tenants.
- El logo completo conserva sus colores originales. No se recolorea ni se rediseña el CRM genérico en esta integración.

## Validación reproducible

Entorno aislado Python 3.12 con las versiones originales de `requirements.txt`, sin modificarlas. El Python 3.9 del sistema no pudo resolver todas esas versiones; Python 3.12 sí. No se utilizaron credenciales ni bases de producción.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m pip check
cd tests/ui
npm ci --ignore-scripts
npm test
```

Resultados:

- Base AITA: **91 pruebas aprobadas** antes de la integración.
- Integración: **133 pruebas Python aprobadas**, incluidas las existentes adaptadas para firmas válidas; **6 pruebas DOM del panel aprobadas**.
- Cobertura añadida: aislamiento de llamadas, permisos/auth, payloads e historial inválidos, propuestas sin escritura, caducidad, confirmaciones repetidas/concurrentes, lote atómico, tenant recreado, política revocada, contexto BusinessProfile, respuestas SDK simuladas, errores sin filtración, firmas válidas/alteradas y esquema SQLite aditivo preservando datos.
- Pruebas DOM: texto almacenado malicioso, perfil y gráfico, enlaces, envío de mediciones, errores HTTP, propuestas y doble clic de confirmación.
- Dependencias coherentes (`pip check`) y diff sin errores de espacios.
- Las advertencias de deprecación de SQLAlchemy/datetime y librerías siguen presentes; no son fallos de las pruebas.

## Pendiente antes de producción

- Revisión humana de esta PR y de la base AITA; no se ha fusionado ninguna rama.
- Prueba de staging con una clave de Anthropic autorizada y configuración de Stripe de prueba. Las pruebas actuales usan respuestas simuladas y firmas sintéticas; no prueban una llamada real al proveedor.
- La revisión básica de escritorio ya se completó en el navegador interno de Codex sobre localhost: login de operador/admin, propuesta, confirmación, perfil y guardado de mediciones. Quedan pruebas de dispositivos móviles y del entorno de staging.
- Configurar contraseñas administrativas propias y `STRIPE_WEBHOOK_SECRET`; esta versión rechaza configuraciones inseguras antes permitidas.
- Revisar proxy de confianza en ASGI/Railway. El límite sigue siendo por proceso; sin proxy confiable puede agrupar tráfico por IP del proxy. Redis/límite distribuido queda fuera de alcance.
- La autenticación Basic compartida existente no se ha migrado a usuarios/roles por tenant. La comprobación de tenant añadida a llamadas no sustituye autenticación de cada socio.
- Backups, retención de propuestas, migración productiva y rollback deben validarse en staging. Para rollback de código, conservar la tabla aditiva sin borrarla; los negocios/socios confirmados son datos reales que requieren revisión antes de revertirlos.
- La idempotencia de confirmaciones sí está cubierta; la deduplicación y ordenación de todos los eventos Stripe sigue siendo trabajo previo pendiente.

No se incluyeron archivos `.env`, claves, bases SQLite ni resultados de pruebas con datos reales. No se configuró ni ejecutó despliegue, auto-merge o cobro.

## Validación posterior en navegador

Se ejecutó la aplicación del commit `3aa7b621ac564c05fcc23ea6762da6826b1c8115` en `127.0.0.1`, usando una base SQLite temporal, usuarios ficticios y respuestas de Anthropic simuladas. No se utilizaron datos ni claves reales.

- Alta propuesta: cero socios nuevos antes de confirmar; una propuesta pendiente.
- Confirmación por el botón del panel: un socio nuevo, una propuesta confirmada y botón deshabilitado con el mensaje «Cambios guardados».
- Perfil: gráfico de tres mediciones, BMI, asistencia, membresía, alerta e historial visibles.
- Guardado desde el formulario: la medición ficticia de 78.5 kg se persistió una sola vez; el gráfico, BMI e historial se actualizaron y apareció «Medición guardada».
- No se observaron bloqueos de uso en la vista de escritorio revisada. Esto no sustituye pruebas de un proveedor real ni certifica todos los tamaños de pantalla.

Railway mostró su pantalla de login en el navegador disponible. Todavía no se ha podido confirmar si existe un entorno staging; requiere que el usuario inicie sesión. No se crearon entornos ni se modificó producción.
