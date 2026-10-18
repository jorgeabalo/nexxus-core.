# Nexxus Mini AI — instalación de desarrollo por USB

Estado: prototipo de texto y grabación local. No es todavía un instalador listo
para un usuario final. No requiere Safari, App Store, Electron ni una IA local.
La API de texto es nueva; no reutiliza rutas de Claudia. No se ha desplegado.

## Preparación en un Mac moderno

1. Revisar `docs/NEXXUS_MINI_AI.md` y configurar un entorno de pruebas del servidor.
   No cambiar la rama de producción de Railway ni aplicar migraciones sin autorización.
2. Copiar esta carpeta completa al USB. Renombrar `config.example.json` como
   `config.json` y poner el **origen HTTPS real del entorno aprobado**, sin ruta,
   contraseña, token ni claves. No se presupone que el endpoint nuevo esté desplegado.
3. Preparar `transport/curl` y `transport/cacert.pem`. Hace falta un curl con TLS
   moderno, compatible con Intel y con **Mac OS X 10.6.8**, más sus dependencias
   y un almacén de CA actualizado. Debe soportar `--proto`, `--tlsv1.2`,
   `--cacert`, `--noproxy` y el límite de tamaño durante transferencias sin
   Content-Length (curl 8.4.0 o posterior). No basta con copiar `/usr/bin/curl`
   desde un Mac moderno: su arquitectura y dependencias pueden ser incompatibles.
   Esta entrega NO incluye ni certifica ese binario. Falta construirlo, revisar
   sus licencias, fijar versiones y hashes, y probarlo en Snow Leopard.
4. Compilar `record-audio.c` usando un SDK y compilador de Apple compatibles con
   10.6 (por ejemplo un entorno Xcode legado legalmente obtenido). Desde esa carpeta:

   ```sh
   gcc -std=c99 -arch i386 -mmacosx-version-min=10.6 \
     -framework AudioToolbox -framework CoreFoundation \
     record-audio.c -o record-audio
   chmod +x record-audio Nexxus.command transport/curl
   ```

   `i386` es el objetivo conservador para el Intel antiguo. Un compilador moderno
   puede compilar la fuente para el Mac actual, pero eso NO crea ni valida un
   ejecutable para 10.6. Comprobar arquitectura con `file`, dependencias con
   `otool -L` y hashes con `shasum -a 256`; guardar un manifiesto junto al paquete.
5. No poner `.env`, claves de Anthropic, claves service-role de Supabase,
   contraseñas ni tokens de sesión en el USB. Transferir solo la carpeta cliente.

## En el Mac mini 2007, Snow Leopard 10.6.8

1. Copiar la carpeta del USB a Documentos. Mantener juntos sus archivos.
2. Comprobar que el Python del sistema y Tk abren una ventana:

   ```sh
   /usr/bin/python -c 'import Tkinter; r=Tkinter.Tk(); r.mainloop()'
   ```

   El cliente se escribió con sintaxis de Python 2.6 y usa su módulo `json`.
   La disponibilidad real de Tk debe comprobarse en ese equipo; no se instala
   un Python actual sobre 10.6 como solución automática.
3. Verificar la fecha y hora del Mac. Abrir `Nexxus.command` con doble clic.
4. Para esta versión de desarrollo, iniciar sesión en el portal existente de
   Nexxus desde un equipo moderno y obtener, mediante un procedimiento de
   administrador, un **access token temporal del usuario Supabase autorizado**.
   Pegar ese token en el campo de sesión oculto. No usar anon key, refresh token
   ni service-role como token. No compartirlo por chat ni guardarlo en archivos.
   No hay aún un flujo cómodo de vinculación entre dispositivos o renovación:
   al caducar, hay que pegar uno nuevo. Ese flujo queda pendiente para la versión final.
5. Escribir y pulsar Enviar. Se transmiten la pregunta y hasta cuatro intercambios
   previos a Railway y Anthropic. El historial vive solo en memoria del cliente;
   «Nueva conversación» lo borra. Cambiar de token reinicia el contexto al enviar.

## Micrófono USB

Seleccionar el USB en Preferencias del Sistema → Sonido → Entrada y comprobar
que se mueve el indicador al hablar. Comprar únicamente un modelo cuya
compatibilidad con **10.6.8** esté documentada o que se haya probado en ese Mac;
que diga «USB» o «compatible con Mac» no es una garantía suficiente.

El botón «Micrófono: grabar 10 s» pide un nombre nuevo y graba diez segundos de
WAV PCM mono de 16 bits a 16 kHz mediante Audio Queue Services. Usa la entrada
predeterminada; si el dispositivo no acepta la configuración, mostrará error.
No sobrescribe archivos existentes. El archivo se crea con permisos de usuario.
No hay grabación de fondo ni activación por palabra clave. No se envía el audio.
Si se cierra la ventana mientras graba, el proceso termina al cumplir los diez segundos.

Falta probar volumen, ruido, duración y formato en el Mac real con el micrófono
comprado. La transcripción automática y las respuestas habladas **no están
implementadas**: Anthropic se reutiliza para texto, y no se ha supuesto una API
de reconocimiento de voz inexistente en el repositorio. Por ahora, grabar sirve
para comprobar la entrada; para conversar hay que escribir.

## TLS y límites de compatibilidad

El cliente no usa el TLS del Python 2.6 ni el Safari de Snow Leopard. Invoca el
curl preparado, verifica certificado y nombre del servidor con CA explícitas,
exige TLS 1.2 como mínimo y no sigue redirecciones. No usa `--insecure`, HTTP
público, proxies del entorno ni configuración personal de curl. Los tokens viajan
por la entrada estándar del proceso, no como argumentos visibles en la lista de procesos.

Si no hay un transporte verificado compatible con el equipo, la conexión queda
bloqueada. No se ha demostrado aún compatibilidad de extremo a extremo en 10.6.
La compilación actual del capturador solo verifica la fuente en el host moderno.
El sistema operativo y Python legados no reciben mantenimiento moderno; limitar
el uso del equipo a este cliente y evitar guardar información sensible en él.

Referencias: [Audio Queue Services de Apple](https://developer.apple.com/documentation/audiotoolbox/audio-queue-services),
[AudioQueueNewInput](https://developer.apple.com/documentation/audiotoolbox/audioqueuenewinput(_:_:_:_:_:_:_:)),
[opciones TLS y límites de curl](https://curl.se/docs/manpage.html).
