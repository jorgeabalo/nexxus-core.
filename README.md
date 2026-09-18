# 🎧 Recepcionista IA - NEXXUS AI SUPPORT

Sistema de recepcionista virtual inteligente, amable y multiidioma construido con Claude API y FastAPI.

## ✅ Corregido esta noche (v1.0.1)

Esta versión corrige varios bugs críticos encontrados al auditar el MVP antes de lanzarlo. Ver el detalle completo en `CAMBIOS.md`. En resumen:

1. **El modelo de Claude no existía** (`claude-opus-4-1`) → toda respuesta caía siempre al fallback, nunca se usaba IA real. Corregido a `claude-haiku-4-5-20251001`.
2. **Bug de concurrencia crítico**: el historial de conversación vivía en el propio servicio (compartido entre TODAS las llamadas). Dos clientes chateando a la vez mezclaban sus conversaciones. Corregido: el historial se reconstruye desde la base de datos por `llamada_id`.
3. **`.env` nunca se cargaba** (faltaba `load_dotenv()`) → la API key nunca se leía aunque la configuraras.
4. **"Iniciar Llamada" no funcionaba desde el navegador**: Pydantic rechazaba el `numero_cliente: null` que manda el frontend. Corregido.
5. **Multiidioma real**: antes solo 3 de los 12 idiomas "soportados" tenían prompt propio; el resto caía a un prompt en español que forzaba responder en español. Ahora los 12 idiomas generan su propio prompt.
6. **Protección de costos**: límite de longitud de mensaje + rate limiting básico por IP.
7. **Panel admin protegido** con usuario/contraseña (antes cualquiera con la URL veía las estadísticas).
8. **Escalado a supervisor real**: antes era solo una instrucción "esperanzada" al modelo; ahora el backend calcula la duración real y expone `requiere_supervisor: true` a los 20 minutos.

## 🎨 Rediseño visual (v1.0.2)

Se rediseñaron el chat (`recepcionista.html`) y el panel admin (`admin_recepcionista.html`) con una identidad visual propia (azul, sistema de color accesible, modo claro/oscuro automático, íconos SVG inline). Cero cambios de comportamiento — mismos IDs y funciones JS, solo visual. Detalle en `CAMBIOS.md`.

## ⚠️ Lo que este proyecto NO incluye todavía

Para que no haya sorpresas en el lanzamiento: este zip es **solo el motor de chat de la recepcionista + panel de estadísticas**. No incluye pagos con Stripe, sistema de reservas, ni telefonía real (Twilio) — eso son los siguientes pasos de la Fase 2, no algo ya construido.

## ☎️ Telefonía Twilio (rama de endurecimiento)

La telefonía usa webhooks firmados. Antes de configurarla:

1. Define `PUBLIC_BASE_URL`, `TWILIO_AUTH_TOKEN` y las demás variables de `.env.example` como secretos del servidor.
2. Configura el número de Twilio para enviar llamadas entrantes por `POST` a:
   `https://TU_DOMINIO/webhooks/twilio/voice/SLUG_DEL_NEGOCIO`
3. No desactives la validación de `X-Twilio-Signature`.
4. Para transferencias reales, define `SUPERVISOR_PHONE_NUMBER` en formato E.164.

El webhook de estado disponible es:
`POST /webhooks/twilio/status/SLUG_DEL_NEGOCIO?llamada_id=...`.
La asociación automática de ese callback se completará al aprovisionar cada número.

## ✨ Características

- **Multiidioma**: Detecta y responde en 12 idiomas (español, inglés, francés, alemán, italiano, portugués, japonés, chino, ruso, árabe, hindi, coreano)
- **IA Conversacional**: Powered by Claude API - respuestas naturales y contextuales, con historial correcto por llamada
- **Fallbacks Robustos**: En caso de error, responde con templates pre-grabados en el idioma del usuario
- **Logging Completo**: Historial de todas las llamadas en SQLite
- **Panel Admin**: Dashboard con estadísticas en tiempo real, protegido con usuario/contraseña
- **Responsivo**: Funciona en desktop y mobile
- **Escalado a supervisor**: señal real al superar 20 minutos de llamada
- **Protección básica de costos**: límite de longitud de mensaje + rate limiting por IP

## 🚀 Instalación Rápida

### 1. Clonar/Descargar el proyecto
```bash
cd recepcionista-ia-proyecto
```

### 2. Crear ambiente virtual
```bash
python -m venv venv

# En Windows
venv\Scripts\activate

# En Mac/Linux
source venv/bin/activate
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno
```bash
# Copiar template
cp .env.example .env

# Editar .env y agregar:
#  - tu ANTHROPIC_API_KEY
#  - ADMIN_USER / ADMIN_PASSWORD (para el panel admin)
```

### 5. Ejecutar el servidor
```bash
python main.py
```

El servidor estará disponible en: **http://localhost:8000**

## 📱 Acceso

- **Chat (Cliente)**: http://localhost:8000/recepcionista
- **Admin (Dashboard)**: http://localhost:8000/admin-recepcionista *(pide usuario/contraseña — configuralos en `.env`)*
- **API Docs**: http://localhost:8000/docs

## 🎯 Flujo de Uso

### 1. Cliente llama (HTTP POST)
```bash
POST /api/recepcionista/iniciar
```

### 2. Cliente envía mensaje
```bash
POST /api/recepcionista/mensaje
{
  "llamada_id": "abc123",
  "mensaje": "¿Puedo agendar una cita?"
}
```

### 3. Recepcionista IA responde
```json
{
  "respuesta": "Por supuesto, con gusto te ayudo a agendar...",
  "idioma": "es",
  "fallback": false,
  "requiere_supervisor": false,
  "duracion_segundos": 42.1
}
```

### 4. Finalizar llamada
```bash
POST /api/recepcionista/finalizar
{
  "llamada_id": "abc123",
  "resultado": "completada"
}
```

## 📊 API Endpoints

### Recepcionista
- `POST /api/recepcionista/iniciar` - Inicia nueva llamada
- `POST /api/recepcionista/mensaje` - Procesa mensaje del usuario (rate-limited)
- `POST /api/recepcionista/finalizar` - Finaliza llamada
- `GET /api/recepcionista/estadisticas` - Obtiene estadísticas *(requiere autenticación admin)*

### Frontend
- `GET /recepcionista` - Interfaz de chat
- `GET /admin-recepcionista` - Panel de admin *(requiere autenticación admin)*

### Salud
- `GET /health` - Verifica estado del servidor

## 💰 Modelo de Costo

| Componente | Costo |
|-----------|-------|
| Claude API (Haiku 4.5, ~$1/$5 por millón de tokens input/output) | Del orden de unos pocos USD/mes con volumen bajo — medí tu uso real, no lo des por sentado |
| Hosting | Variable |

Con el modelo Opus mal configurado que traía el proyecto original, el costo real hubiera sido mucho más alto que "$0-5 USD/mes" en cuanto hubiera tráfico real — ya está corregido a Haiku, que es el que efectivamente rinde esos números.

### Precio de venta sugerido
- **$30-50 USD/mes** por cliente (a validar con el costo real medido en producción)

## 🛠️ Personalización

### Cambiar el nombre de la recepcionista
En `recepcionista_service.py`, dentro de `_generar_prompt_sistema()`:
```python
"Eres María, una recepcionista virtual..." → "Eres [NOMBRE], una recepcionista virtual..."
```

### Cambiar el modelo de Claude
Variable de entorno `MODELO_CLAUDE` en `.env` (por defecto `claude-haiku-4-5-20251001`).

### Agregar/editar idiomas
Editar los diccionarios `NOMBRES_IDIOMA` y `FALLBACKS` en `recepcionista_service.py`. El prompt del sistema se genera dinámicamente para cualquier idioma que agregues ahí — no hace falta tocar nada más.

## 📈 Escalabilidad - Fase 2

Próximas mejoras planificadas (no incluidas en este entregable):
- Pagos online (Stripe) y confirmación por SMS/email
- Sistema de reservas integrado
- Integración con Twilio (llamadas telefónicas reales, no solo chat)
- Transferencia real a un operador humano (hoy solo se marca `requiere_supervisor`, no hay integración con un sistema de colas/telefonía)
- Multi-tenant (varios negocios en la misma instancia, cada uno con sus propios datos aislados)
- Análisis de sentimiento y reportes automáticos

## 🔒 Seguridad

- API Key almacenada en variables de entorno (nunca se sube al repo)
- Panel admin protegido con HTTP Basic Auth (usuario/contraseña por variable de entorno)
- Rate limiting básico por IP en los endpoints de llamada (protección de costos)
- Límite de longitud por mensaje (2000 caracteres)
- Validación en 3 capas (frontend, backend, IA)
- HTTPS recomendado en producción (no lo provee este proyecto — depende de tu hosting/proxy)
- **Pendiente para producción real**: el rate limiting es en memoria de un solo proceso (no sirve si corrés varios workers/instancias); no hay aislamiento multi-tenant; no hay HTTPS ni dominio propio configurados acá.

## 📝 Estructura del Proyecto

```
recepcionista-ia-proyecto/
├── main.py                    # FastAPI backend (rate limiting, auth admin)
├── models.py                  # Modelos de BD (SQLAlchemy)
├── recepcionista_service.py   # Lógica de IA (multiidioma, historial por llamada)
├── recepcionista.html         # Frontend de chat
├── admin_recepcionista.html   # Dashboard admin
├── requirements.txt           # Dependencias Python
├── .env.example               # Template de variables
├── CAMBIOS.md                 # Detalle completo de los fixes de esta noche
└── README.md                  # Este archivo
```

## 🚨 Troubleshooting

### Error: "ANTHROPIC_API_KEY not found" / respuestas siempre en fallback
- Verificá que creaste `.env` (copiado de `.env.example`) con tu API key real
- Reiniciá el servidor después de crear/editar `.env`
- Revisá los logs del servidor: si la key es inválida o el modelo no existe, se imprime el error ahí

### El panel admin pide usuario/contraseña que no tengo
- Configurá `ADMIN_USER` y `ADMIN_PASSWORD` en tu `.env`. Si no lo hacés, usa una contraseña temporal (`cambiar-esta-clave`) que se imprime como advertencia al arrancar — cambiala antes de exponer el servidor públicamente.

### Error: "Database is locked"
- La BD está siendo accedida por múltiples procesos
- Cerrá otras instancias del servidor
- Borrá `recepcionista_db.sqlite` y reiniciá (perdés el historial)

### Recibo 429 "Demasiadas solicitudes"
- Es el rate limiter (protección de costos). Ajustá `RATE_LIMIT_MAX_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` en `.env` si es muy restrictivo para tu volumen real.

## 📞 Soporte

Para preguntas o reportar bugs, contacta a Jorge.

---

**Versión**: 1.0.1
**Última actualización**: Agosto 2026
**Status**: MVP funcional para un solo negocio / bajo volumen. Revisar sección "Pendiente para producción real" antes de escalar a múltiples clientes.
