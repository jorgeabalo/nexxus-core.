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
**Frecuencia semanal** = forma de repartir la cuota: N por semana = 4N al mes (1/sem = 4 = $20; 2/sem = 8 = $35).

* Antes de confirmar se muestran: publicaciones incluidas, adicionales, total mensual, precio adicional
  mensual, fecha de inicio y que **el cargo es recurrente hasta cancelación**. La frase exacta, p. ej.
  "Autorizo 12 publicaciones adicionales por $55 al mes.", se desmarca si cambia cantidad, frecuencia o precio.
* **Quién:** el manager **solicita**; el owner **autoriza** (o un manager, si el owner activó "Permitir que los
  gerentes autoricen ampliaciones"). La base lo exige también (disparador), no solo el backend.
* **Vigencia:** desde el mes en que se autoriza, cada mes, hasta cancelarla; la cancelación (owner o
  delegado) tiene efecto el mes siguiente (el mes en curso ya está en uso). Nunca retroactiva.
* **Sin cobro en este PR:** se registra la solicitud y la autorización (`marketing_publication_addons`) para
  incorporarla a la factura mensual. El importe lo calcula PostgreSQL (columnas generadas); el navegador nunca
  envía ni cambia el precio. Idempotente por `idempotency_key` (misma clave con otra cantidad = 409).
* Migración `20261013120000_marketing_publication_addons.sql` (**sin aplicar**): tablas nuevas con RLS
  (owner/manager leen su tenant), `service_role` inserta y solo actualiza las columnas de transición
  (`requested → authorized | declined`, `authorized → cancelled`); nada se borra.

## 3. Biblioteca: configuración propuesta para el piloto (NO aplicada)

La Biblioteca se presenta como **incluida en el plan**. Mientras el espacio sea 0, el cliente ve "La
Biblioteca está incluida en tu plan. Estamos preparando tu espacio…", nunca "no habilitada". El
almacenamiento es **temporal** (cada archivo se elimina al vencer) y se muestra usado, pendiente de
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
