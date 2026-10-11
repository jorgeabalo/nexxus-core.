# AITA Marketing — plan simple de publicaciones (PR #21)

Documento del operador. Describe lo que ve el owner/manager, cómo se registran las ampliaciones, la
configuración propuesta para el piloto (**no aplicada**) y una propuesta corta de CRM para un PR futuro.

## 1. Lo que ve el cliente

Solo lenguaje comercial: publicaciones incluidas, utilizadas y disponibles; ampliaciones mensuales;
Biblioteca; campañas; resultados. **Nunca** presupuestos técnicos de IA, proveedores, modelos, precios
unitarios, márgenes, distribución de costos ni objetivos internos de costo. Lo comprueban pruebas sobre
todas las respuestas de la API del tenant y todos los archivos servidos al Manager.

* **Plan base:** 8 publicaciones al mes. Una publicación puede ser imagen, carrusel, reel o video y cuenta
  como **una**, sin importar cómo se produzca.
* **Dos modos de creación:** "Usar mi Biblioteca y mejorar con IA" (exige elegir material permitido) y
  "Crear completamente con IA" (el backend rechaza `media_ids`/`derivative_ids`: `fully_ai_uses_no_media`).
* **Aprobación humana siempre:** nada se genera ni se publica sin aprobación. Este PR no publica nada.
* **Objetivos:** conseguir llamadas, conseguir mensajes, reservar una visita o clase de prueba, conseguir
  membresías, reactivar clientes, obtener reseñas.
* **Resultados (futuro, con conectores autorizados):** alcance y visualizaciones; me gusta, comentarios,
  compartidos y guardados; clics; mensajes y llamadas; visitas o clases de prueba; clientes y ventas
  atribuidas. Nunca se promete viralidad, vistas ni likes: "AITA analiza los resultados para aumentar la
  probabilidad de alcance, interacción y conversiones."

## 2. Ampliaciones ("¿Necesitas más publicaciones?", en Campañas)

| Opción | Precio mensual |
|---|---|
| +1 publicación | $6 |
| +4 publicaciones | $20 |
| +8 publicaciones | $35 |
| Personalizada | combinación exacta más económica de +8, +4 y +1 |

Ejemplos: 12 = 8 + 4 = $55 · 22 = 8 + 8 + 4 + 1 + 1 = $102 · 37 = 8 + 8 + 8 + 8 + 4 + 1 = $166.

**Frecuencia semanal.** La facturación y la cuota son siempre **mensuales**. "Por semana" solo distribuye:
N por semana equivale a **4N adicionales por ciclo** (1/sem = 4 = $20; 2/sem = 8 = $35) y nunca da más que esa
cuota mensual. La interfaz lo avisa: "Se distribuirán durante cuatro semanas del ciclo mensual. Una quinta
semana no añade publicaciones automáticamente."

**Una sola ampliación vigente por tenant y ciclo.**
* Sin ampliación vigente, una autorización empieza en el ciclo actual.
* Con una vigente, un cambio **reemplaza la cantidad del próximo ciclo**. La vigente termina al cierre del mes
  (`replaced`) y la nueva empieza el día 1 siguiente; nunca se acumulan.
* La misma cantidad otra vez no crea nada (`unchanged`).
* La base lo garantiza con un bloqueo por tenant y una comprobación de solapamiento.

**Límite efectivo** de un ciclo = publicaciones incluidas (8) + ampliación vigente en ese ciclo:
* plan base 8; +1 → 9; +4 → 12; +8 → 16; personalizada 12 → 20.
* Se aplica al **programar**, según el mes de la fecha de publicación en la zona horaria del tenant.
* Lo comprueban el backend y la base: el disparador `marketing_content_post_limit` cuenta las publicaciones
  programadas del ciclo bajo un bloqueo por tenant, así que dos programaciones simultáneas no superan el límite.
* Cancelar mantiene la ampliación hasta el cierre del ciclo y el siguiente vuelve a 8. Nunca es retroactiva.

**Quién.**
* El manager **solicita**.
* El owner **autoriza, rechaza o cancela**.
* Un manager solo autoriza si el owner activó "Permitir que los gerentes autoricen ampliaciones", y solo el
  owner concede o retira ese permiso.
* Staff y socios no leen ni modifican ampliaciones.
* Todo lo exigen la base (funciones, disparadores y RLS) y el backend.

**Facturación: autorización ≠ pago capturado.** Este PR no conecta Stripe ni cobra.
* La autorización del owner crea un registro comercial inequívoco en `marketing_publication_addons` con estado
  **`authorized_uninvoiced`**: el owner autorizó el cargo, todavía no existe factura ni pago, y AITA debe
  incluirlo como **línea recurrente** en la siguiente factura mensual.
* El registro conserva:
  * tenant y owner que autorizó (`authorized_by`);
  * cantidad, precio mensual (`amount_cents`) y combinación de paquetes (`blocks_8/4/1`), calculados por la base;
  * fecha de autorización, ciclo de inicio (`starts_cycle`) y de fin (`ends_cycle`);
  * estado y fecha de cancelación;
  * `idempotency_key`;
  * la línea comercial `billing_line`, por ejemplo "Publicaciones adicionales de Marketing: 12 por mes — $55".
* El historial es inmutable: `marketing_publication_addon_events` registra cada alta y cada transición y hace
  de outbox para la facturación futura. Solo lo lee `service_role`.
* La API del tenant no expone el estado de facturación, la línea ni los costos internos.
* `billing_service.py` (Stripe sobre los modelos SQLAlchemy `Negocio`/`Suscripcion`) no está ligado a los tenants
  de Supabase ni tiene líneas recurrentes, así que no se reutilizó. La integración con la factura mensual es un
  PR aparte.

**Cómo se graba.** Funciones atómicas solo para `service_role`, idempotentes por `idempotency_key` (la misma
clave con otra cantidad o frecuencia da 409):
* `marketing_submit_publication_addon` (solicitar o autorizar);
* `marketing_decide_publication_addon` (aprobar o rechazar);
* `marketing_cancel_publication_addon` (cancelar).

Las transiciones son de un solo sentido: `requested → authorized_uninvoiced | declined` y
`authorized_uninvoiced → cancelled | replaced`. Nada se borra. Migración `20261013120000_marketing_publication_addons.sql`, **sin aplicar**.

## 3. Biblioteca: configuración propuesta para el piloto (NO aplicada)

La Biblioteca forma parte del plan comercial, pero solo permite subir archivos si la cuota del backend es > 0.
* Con cuota configurada: "Biblioteca incluida — X utilizados de 1 GB".
* Sin configurar (0): "Biblioteca incluida — pendiente de activación", con la subida deshabilitada. Nunca
  "no disponible en este plan".
* El almacenamiento es **temporal** (cada archivo se elimina al vencer) y se muestra usado, pendiente de
  eliminación, reservado y disponible.

Propuesta para Golden Age, a autorizar **después** de fusionar y desplegar el PR #20 (purgador y archivos
vencidos en producción). No se ejecuta en este PR:

```sql
-- Requiere autorización explícita. Solo después del despliegue del PR #20.
update public.marketing_settings
   set library_storage_limit_bytes = 1073741824,   -- 1 GB de almacenamiento temporal
       max_retention_days          = 30,           -- retención predeterminada y máxima: 30 días
       max_upload_bytes            = 52428800      -- 50 MB por archivo (ya es el valor por defecto)
 where tenant_id = '<golden-age-tenant-id>';
```

Antes: ejecutar el purgador en dry-run y confirmar que no hay archivos sin fecha de vencimiento.

## 4. Propuesta para un PR futuro: CRM propio de Nexxus (NO implementado)

Inspiración de producto (sin instalar, cargar, bifurcar ni copiar el repositorio de Twenty):
**Clientes → Embudo → Seguimientos → Actividad → Resultados.**

* Ficha integral del cliente; embudo Kanban; vistas de tabla, calendario y filtros; tareas, notas y cronología;
  automatizaciones; IA contextual.
* Atribución: **publicación → contacto → visita/prueba → cliente → ingreso**, para medir qué campañas
  generan llamadas, mensajes, visitas, membresías y ventas.
* El análisis de competencia también queda para un PR posterior.
