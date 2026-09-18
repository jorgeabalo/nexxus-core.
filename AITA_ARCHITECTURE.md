# AITA — Arquitectura de agentes

## Objetivo

Convertir NEXXUS Core en el núcleo operativo de AITA: una plataforma SaaS multi-tenant donde cada negocio conserva su información aislada y varios agentes especializados trabajan sobre ese mismo contexto.

La arquitectura existente se conserva: `models.py`, `generic_service.py`, `verticals/`, facturación, progreso y riesgo siguen siendo componentes válidos. La nueva capa `aita_orchestrator.py` coordina capacidades sin duplicarlas.

## Principio de diseño

**Una empresa = un tenant = una fuente de verdad.**

Los agentes no deben mantener bases de datos paralelas. Todos reciben `negocio_id`, operan únicamente dentro de ese tenant y dejan trazabilidad de las acciones relevantes.

## Agentes iniciales

1. **Configuration / Onboarding** — perfil empresarial, marca, catálogo de servicios y canales.
2. **Reception** — conversación, consultas, reservas y transferencia humana.
3. **Customer Success** — seguimiento, riesgo de abandono y reactivación.
4. **Technical Support** — diagnóstico y escalado de problemas de la plataforma.
5. **Marketing** — estrategia, contenido, campañas, consistencia de marca y resultados.
6. **Sales Intelligence** — búsqueda y calificación de empresas; inicialmente prepara prospectos para el comercial humano, no hace llamadas autónomas.
7. **Business Insights / Finance** — KPIs, ingresos/gastos y anomalías; no sustituye la contabilidad profesional.
8. **Inventory** — existencias, alertas y propuestas de reposición.

## Coordinador

`AITAOrchestrator` es el registro y guardián de permisos. Un LLM puede interpretar la intención del usuario, pero **no decide sus propios permisos**. El coordinador valida agente + capacidad y marca acciones externas para aprobación humana.

Ejemplo futuro:

`Golden Age quiere recuperar socios inactivos`

→ Customer Success consulta alertas y candidatos
→ Marketing prepara campaña y oferta
→ Orchestrator marca el envío como `requires_human_approval=true`
→ humano aprueba
→ conector Twilio/SMS/email ejecuta
→ Analytics mide respuestas, citas y reactivaciones

## Política de acciones externas

Hasta tener autenticación robusta, auditoría y conectores productivos, requieren aprobación humana: publicar contenido, enviar campañas/mensajes, contactar prospectos, gastar presupuesto publicitario, modificar configuración, iniciar pagos, modificar registros contables y hacer pedidos.

Esto permite automatizar la preparación sin permitir que un prompt o un error de IA genere gastos o comunicaciones externas por sí solo.

## Perfil de marca por negocio — siguiente bloque

Cada tenant debe tener un `BrandProfile` con, como mínimo: descripción, audiencia, propuesta de valor, tono, idiomas, colores, restricciones visuales, CTA, redes, productos/servicios prioritarios y palabras/claims prohibidos. Marketing debe leer este perfil antes de crear contenido.

## Marketing orientado a ingresos

El Marketing Agent no debe ser una fábrica de posts. Su ciclo será:

**objetivo → audiencia → campaña → contenido → aprobación → publicación → métricas → aprendizaje**.

KPIs por negocio: leads, conversaciones, reservas/citas, reactivaciones, ventas atribuibles cuando puedan medirse, coste de campaña y conversión. Likes/impresiones son secundarios.

## Sales Intelligence

Primera versión: localizar empresas con datos públicos, calificarlas según ICP y generar un brief para el socio comercial. No automatizar llamadas en frío hasta definir consentimiento, listas de exclusión, horarios, identidad del llamante y reglas legales/operativas.

## Seguridad mínima antes de producción

- PostgreSQL en lugar de SQLite.
- Autenticación por usuario/tenant y roles; retirar credenciales Basic compartidas.
- Secrets solo en variables de entorno/secret manager; nunca en Git.
- Auditoría de acciones de agentes y conectores.
- Idempotencia en webhooks y acciones externas.
- Rate limiting compartido (Redis u opción equivalente) si hay varios workers.
- Backups y política de retención/borrado de datos.
- Cifrado TLS y protección de datos sensibles.

## Orden de implementación recomendado

**Fase A — ahora:** Orchestrator + Agent Registry + tests.

**Fase B:** BusinessProfile/BrandProfile + onboarding real de Golden Age.

**Fase C:** conectar Anthropic y validar tool-calling end-to-end.

**Fase D:** Twilio como canal de Reception; mantener la lógica de negocio fuera de Twilio.

**Fase E:** Customer Success + Marketing para campaña de reactivación de Golden Age.

**Fase F:** Analytics/ROI; demostrar cuánto negocio produjo AITA.

**Fase G:** Sales Intelligence para generar prospectos para el equipo comercial.

## Regla para futuras contribuciones de IA

Antes de añadir un agente nuevo, responder:

1. ¿Qué problema empresarial resuelve?
2. ¿Qué datos necesita y a qué tenant pertenecen?
3. ¿Qué capacidades son solo lectura/preparación?
4. ¿Qué acciones tienen efecto externo y requieren aprobación?
5. ¿Cómo se medirá el resultado económico?

Si estas cinco respuestas no están claras, no añadir todavía el agente.
