# Cambios — auditoría y corrección previa al lanzamiento (25/08/2026)

Se recibió el proyecto `recepcionista-ia-proyecto` para pulirlo antes del lanzamiento de mañana. Esto es lo que se encontró y se corrigió, probado end-to-end con un servidor local real (`uvicorn` + `sqlite`).

## 🔴 Críticos (rompían el producto por completo)

**1. Modelo de Claude inexistente (`claude-opus-4-1`)**
El identificador no corresponde a ningún modelo actual de la API de Anthropic. Cada llamada a `client.messages.create()` fallaba silenciosamente (capturada por el `try/except` genérico) y el sistema caía SIEMPRE al mensaje de fallback pre-grabado. En la práctica, la "IA conversacional" nunca respondía con IA real — probado en este entorno (sin API key) y confirmado también por revisión de la documentación oficial de modelos.
→ Corregido a `claude-haiku-4-5-20251001`, configurable por `MODELO_CLAUDE` en `.env`. Se eligió Haiku por velocidad/costo — coincide con la estimación de "$0-5 USD/mes" que traía el README (con Opus esa estimación hubiera sido incorrecta por un margen grande).

**2. Estado de conversación compartido entre TODAS las llamadas (bug de concurrencia)**
`RecepcionistaIAService` guardaba `self.conversacion_historia` y `self.llamada_actual` como atributos de instancia — una única instancia del servicio atiende todas las llamadas del servidor. Con dos clientes chateando al mismo tiempo (o incluso de forma secuencial rápida), `iniciar_llamada()` reseteaba `self.conversacion_historia = []`, borrando el historial de cualquier llamada en curso, y los mensajes de una llamada podían terminar mezclados en el contexto de otra.
→ Corregido: el historial ahora se reconstruye desde la tabla `conversaciones` filtrando por `llamada_id` en cada mensaje (`_reconstruir_historial`). El servicio ya no guarda estado de conversación en memoria. Verificado con una prueba de dos llamadas intercaladas (A → B → A): cada una mantuvo su propio historial sin cruzarse.

**3. `.env` nunca se cargaba**
`requirements.txt` incluía `python-dotenv`, el README indicaba crear un `.env`, pero ningún archivo llamaba a `load_dotenv()`. La `ANTHROPIC_API_KEY` del `.env` nunca llegaba a `os.getenv()` salvo que estuviera exportada manualmente en la shell.
→ Corregido: `load_dotenv()` al importar `recepcionista_service.py`.

**4. "Iniciar Llamada" no funcionaba desde el navegador**
El frontend manda `{"numero_cliente": null}`. El modelo Pydantic tenía `numero_cliente: str = None`, que Pydantic v2 valida como *string obligatorio*, no opcional — rechazaba cualquier request con `null`. Confirmado con una prueba real: la request fallaba con `422 Unprocessable Entity` antes del fix.
→ Corregido a `Optional[str] = None`.

## 🟠 Altos (riesgo de seguridad/costo para un lanzamiento público)

**5. Multiidioma incompleto**
`_generar_prompt_sistema()` solo tenía prompts para `es`, `en`, `fr`. Los otros 9 idiomas "soportados" (de/it/pt/ja/zh/ru/ar/hi/ko) caían al prompt en español, que terminaba con la línea `"Idioma: Español"` — es decir, un cliente detectado como hablante de alemán recibía instrucciones (para el modelo) que decían explícitamente que respondiera en español.
→ Corregido: un único prompt parametrizado por idioma (`NOMBRES_IDIOMA`), válido para los 12 idiomas, que le indica a Claude explícitamente responder en el idioma detectado.

**6. Sin protección de costos**
Cualquiera podía mandar mensajes arbitrariamente largos o en ráfaga contra `/api/recepcionista/mensaje`, cada uno disparando una llamada a la API de Claude a costa de la cuenta del negocio.
→ Agregado: límite de 2000 caracteres por mensaje, y rate limiting por IP (`RATE_LIMIT_MAX_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`, por defecto 20 req/min) en `/iniciar` y `/mensaje`. Probado: la request número 6 dentro de la ventana devuelve `429`.

**7. Panel admin sin autenticación**
`/admin-recepcionista` y `/api/recepcionista/estadisticas` eran públicos — cualquiera con la URL veía las estadísticas del negocio.
→ Agregado: HTTP Basic Auth (`ADMIN_USER` / `ADMIN_PASSWORD` en `.env`). Probado: sin credenciales devuelve `401`, con credenciales correctas devuelve `200`.

**8. Escalado a supervisor no verificado en el backend**
El único mecanismo era una instrucción al modelo ("después de 20 minutos, ofrece transferir") — nada garantizaba que el LLM la cumpliera, y no había forma de saber desde fuera si una llamada ya debía escalar.
→ Agregado: cálculo real de duración desde `fecha_inicio`; a partir de 1200 segundos, la respuesta incluye `"requiere_supervisor": true` y la llamada se marca en la base de datos. El frontend de chat ahora muestra un aviso visible cuando esto ocurre. Probado moviendo `fecha_inicio` 25 minutos atrás en la BD: el siguiente mensaje devolvió `requiere_supervisor: true` correctamente. **Importante**: esto es una señal, no una transferencia real — no hay integración con un sistema de telefonía/colas que mueva la llamada a un humano automáticamente. Eso sigue pendiente (ver README, sección Fase 2).

## 🟡 Menores / mantenimiento
- Las sesiones de base de datos (`SessionLocal()`) no se cerraban nunca (fuga de conexiones); ahora todas usan `try/finally: db.close()`.
- El panel admin ahora muestra también "Escaladas a Supervisor" como KPI.
- `.env.example` actualizado con todas las variables nuevas (`MODELO_CLAUDE`, `ADMIN_USER`, `ADMIN_PASSWORD`, `RATE_LIMIT_*`).

## Lo que se probó de punta a punta (sin API key real, en este entorno)
- `GET /health`
- `POST /api/recepcionista/iniciar` (con `numero_cliente: null`, el caso real del frontend)
- `POST /api/recepcionista/mensaje` — mensaje vacío (400), mensaje normal (200, cae a fallback por no haber API key configurada acá)
- Dos llamadas intercaladas → historiales aislados correctamente en la BD
- Rate limiting → `429` al exceder el límite configurado
- `GET /api/recepcionista/estadisticas` sin auth (401) y con auth (200)
- Escalado a supervisor simulando 25 minutos de duración → `requiere_supervisor: true`
- `POST /api/recepcionista/finalizar`

## Lo que NO se probó (requiere tu API key real)
- Una conversación real con Claude respondiendo (acá siempre cae a fallback porque no hay `ANTHROPIC_API_KEY` configurada en este entorno)
- Detección de idioma real con texto en los 12 idiomas
- Costo real por conversación con Haiku 4.5

**Recomendación antes de lanzar mañana**: correr el servidor con tu `ANTHROPIC_API_KEY` real, probar 3-4 conversaciones de punta a punta vos mismo (incluyendo al menos un idioma que no sea español), y confirmar que las respuestas y el costo por llamada son los esperados.

## 🎨 Rediseño visual (28/08/2026)

Se rediseñaron `recepcionista.html` (chat) y `admin_recepcionista.html` (dashboard) — sin identidad de marca previa, se definió una desde cero: azul como color primario, sistema de colores validado para accesibilidad (contraste y daltonismo), modo claro/oscuro automático según el sistema del usuario, íconos SVG inline (sin dependencias externas, sigue funcionando 100% offline).

Cambios concretos:
- **Chat**: header con avatar, burbujas de mensaje diferenciadas (usuario/IA/aviso), banner de escalado a supervisor con ícono, botones con íconos.
- **Dashboard**: 4 tarjetas KPI (incluye badge de severidad "Bajo/Medio/Alto" en la tasa de fallback), gráfico de barras de llamadas exitosas vs. fallback con leyenda, lista de idiomas detectados ordenada por volumen con barras de color por idioma.
- **Compatibilidad**: se mantuvieron todos los IDs de elementos y nombres de funciones JS originales — cero cambios de comportamiento, solo visual. No se tocó ningún endpoint del backend.

**Probado con servidor local real** (`uvicorn` + `sqlite`, capturas de pantalla vía navegador headless): llamada iniciada → mensaje enviado → burbujas renderizadas correctamente; escalado a supervisor simulado (llamada de 25 min) → banner de aviso visible; dashboard con datos reales → las 4 tarjetas, el gráfico de resumen y las barras de idioma cargan y se ven correctamente, tanto con 401 (sin auth) como 200 (con auth). No se tocó el modo oscuro automático (activa según el sistema operativo del usuario, no se pudo capturar en este entorno pero usa el mismo sistema de tokens de color ya validado).

## 🔴 Bug adicional encontrado (04/09/2026): dependencia `anthropic` incompatible con `httpx` reciente

Al instalar `requirements.txt` desde cero en una Mac (fecha posterior al pulido original), `pip` resolvió una versión nueva de `httpx` (>=0.28) que **le quitó** el parámetro `proxies` que usa internamente `anthropic==0.25.0`. Resultado: el servidor ni siquiera arrancaba — crasheaba al crear el cliente de Anthropic con `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`. Esto no apareció en la corrección del 25/08 porque en ese momento `pip` resolvió una versión de `httpx` todavía compatible; es el típico bug de "funcionaba ayer" por no fijar una dependencia transitiva.
→ Corregido: agregado `httpx<0.28` a `requirements.txt`.

**Nota (solo si instalás en macOS y `python3 -m venv` falla con error de `ensurepip`)**: es un problema conocido de algunas instalaciones de Python en Mac, no del proyecto. Si te pasa, salteate el entorno virtual y instalá directo con `pip3 install --user -r requirements.txt` — funciona igual de bien para correr esto localmente.

Verificado end-to-end en una Mac real (macOS/arm64) tras el fix: `/health` (200), `/recepcionista` (200), `/admin-recepcionista` sin auth (401) y con auth (200), e iniciar una llamada real vía la API — todo correcto.
