# Nexxus Mini AI — paquete USB de evaluación

Este paquete prepara la prueba en un **Mac mini Intel Core 2 Duo de 2007 con
Snow Leopard 10.6.8**. Incluye cliente de texto, transporte TLS y grabación local.
No se ha ejecutado todavía en ese equipo físico. El servidor nuevo sigue sin
desplegar y requiere autorización antes de activarlo.

## Copiar y abrir

1. Descomprimir `Nexxus-Mini-AI-USB-evaluacion.zip` en el Mac moderno y copiar la
   carpeta `Nexxus-Mini-AI` al USB. Copiarla después a Documentos del Mac mini.
2. Abrir `Comprobar.command` con doble clic. Comprueba hashes de los archivos,
   arquitectura, inicio de curl/OpenSSL y disponibilidad de Tk. **No conecta a
   internet, no lee tokens ni activa el micrófono**. Conservar su resultado.
3. Abrir `Nexxus.command`. Usa `/usr/bin/python` y Tk del sistema, sin Safari,
   App Store, Electron ni instalación global de MacPorts. Si Tk no está presente,
   el diagnóstico lo señalará. No instalar un Python moderno sobre 10.6 como
   solución automática.
4. Comprobar fecha y hora del Mac antes de usar HTTPS.

El ZIP conserva permisos de ejecución. Si el USB o el descompresor los pierde,
abrir Terminal dentro de la carpeta y ejecutar:

```sh
chmod +x Nexxus.command Comprobar.command record-audio transport/curl
```

El archivo `config.json` contiene solo el origen HTTPS real de Railway. Se puede
editar para apuntar a otro entorno aprobado; no debe contener claves ni tokens.
Se excluye del manifiesto para permitir esa edición. El archivo `.sha256` junto
al ZIP permite comprobar la copia completa con `shasum -a 256` en el Mac moderno.
Los hashes detectan corrupción; no constituyen una firma digital del paquete final.

## Qué se incluye para TLS

- curl **8.23.0**, compilación oficial MacPorts `darwin_10.i386`, con OpenSSL.
- OpenSSL **3.6.5** y sus bibliotecas necesarias, junto a curl, en `transport/lib`.
- CA de Mozilla distribuida por `curl-ca-bundle` **8.23.0**.
- Procedencia, hashes de archivos originales y revisión de dependencias en
  `PROVENANCE.json`; fuentes, licencias y metadatos de compilación en `SOURCES`.

Se verificaron las firmas RSA/RIPEMD160 de los paquetes originales con la clave
pública de MacPorts, antes de extraerlos. Se adaptaron las rutas Mach-O para
cargar las bibliotecas desde esta carpeta; por ello los archivos adaptados tienen
hashes nuevos. No hace falta `/opt/local`. Se comprobaron dependencias, símbolos
importados y versiones mínimas declaradas. Algunas bibliotecas antiguas no
declaran mínimo en Mach-O: su procedencia es el paquete firmado para Darwin 10.
Las versiones actuales de gettext-runtime y libunistring sustituyen dependencias
antiguas que ya no estaban en el espejo; conservan los nombres de biblioteca
requeridos y todos los símbolos importados comprobados. Aun necesitan la prueba
real de carga en el Mac mini.

El cliente exige TLS 1.2 como mínimo, verifica certificado y nombre del servidor
con CA explícitas, y no sigue redirecciones. No usa `--insecure`, HTTP público,
proxies del entorno ni configuración personal de curl/OpenSSL. El token se pasa
por entrada estándar, sin shell y sin exponerlo en los argumentos del proceso.
Las pruebas locales con TLS 1.2 comprobaron conexión válida, CA incorrecta y nombre
incorrecto; en los dos últimos casos no se transmitió Authorization. Esas pruebas
usan el curl del host moderno: no sustituyen la ejecución del curl Intel incluido.

## Activación del chat, todavía pendiente

La URL de Railway es real, pero **la ruta nueva `/api/mini-ai/chat` no está
desplegada**. Este paquete no despliega, no aplica migraciones ni modifica Railway.
Para conversar faltan un despliegue autorizado, la migración de cuota,
`MINI_AI_ENABLED=true` y los UUID permitidos en `MINI_AI_USER_IDS`.

Esta versión de evaluación usa un **access token temporal de usuario Supabase**,
obtenido por el administrador desde una sesión del portal en un equipo moderno.
Se pega en el campo oculto del cliente y vive solo en memoria. No se debe usar
una anon key, refresh token, service-role o clave de Anthropic. No compartir tokens
por chat ni guardarlos en el USB. Falta una vinculación y renovación más cómoda.

Una vez habilitado el servidor, se envían la pregunta y hasta cuatro intercambios
anteriores a Railway y Anthropic. «Nueva conversación» borra el contexto; cambiar
de token lo reinicia al enviar. No se escriben conversaciones en disco.

## Micrófono USB

Seleccionar el USB en Preferencias del Sistema → Sonido → Entrada y comprobar
que se mueve el indicador al hablar. El modelo debe tener compatibilidad con
10.6.8 documentada o comprobada en ese Mac: «compatible con Mac» no basta.

El botón «Micrófono: grabar 10 s» pide un nombre nuevo y graba WAV PCM mono de
16 bits a 16 kHz con Audio Queue Services. Usa la entrada predeterminada y no
sobrescribe archivos. El archivo queda en el Mac, con permisos de usuario.
No se envía audio ni se activa grabación de fondo. Al cerrar la ventana durante
una grabación, el capturador termina cuando se cumplen los diez segundos.

Se incluye un capturador **x86_64** construido con objetivo mínimo **10.6** y SDK
moderno. Solo importa funciones presentes en esa versión; necesita probarse con
el sistema y dispositivo reales. El transporte curl es i386, ejecutable en 10.6;
el capturador requiere Core 2 Duo y no está destinado a un Core Duo de 32 bits.

**La transcripción y las respuestas habladas siguen pendientes.** El botón sirve
para comprobar y guardar audio. Para conversar, por ahora hay que escribir.

## Reconstrucción en un Mac moderno

Los binarios no se guardan en Git. El repositorio contiene el empaquetador y
listas de descargas con versiones y hashes fijados. Requiere Python 3.12 o
posterior, herramientas de Apple (clang, otool, nm, install_name_tool), curl y
OpenSSL con verificación RIPEMD160:

```sh
python3 tools/build_mini_usb.py \
  --work /ruta/nueva/de/trabajo \
  --output /ruta/de/salida \
  --server https://nexxus-core-production.up.railway.app
```

No ejecutar el empaquetador en Snow Leopard. No descarga claves de servidor, no
instala archivos de sistema y no solicita despliegues. `--finish-only` sirve solo
para volver a comprimir una carpeta de preparación ya auditada, no para generar
una auditoría nueva.

Fuentes: [paquetes oficiales MacPorts](https://packages.macports.org/curl/),
[compilación de curl](https://curl.se/docs/install.html),
[verificación de certificados](https://curl.se/docs/sslcerts.html),
[Audio Queue Services de Apple](https://developer.apple.com/documentation/audiotoolbox/audio-queue-services).
