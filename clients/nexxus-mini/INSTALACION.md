# Nexxus Mini AI — paquete USB de evaluación

Este paquete prepara la prueba en un **Mac mini Intel Core 2 Duo de 2007 con
Snow Leopard 10.6.8**. Incluye cliente de texto, transporte TLS y métricas del equipo.
No se ha ejecutado todavía en ese equipo físico. La conexión de esta entrega apunta al servicio independiente
`https://nexxus-mini-ai-production.up.railway.app`, autorizado para tu cuenta.

## Copiar y abrir

1. Descomprimir el ZIP actualizado de AITA Nexxus Mini AI en el Mac moderno y copiar la
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
chmod +x Nexxus.command Comprobar.command system-metrics transport/curl
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

## Primera versión sin usuario ni contraseña

Abre Nexxus.command: aparece directamente Nexxus Domus y su ventana flotante.
No hay formulario de acceso. Puedes ver reloj, calendario del mes y métricas
locales. El paquete no incluye correo, contraseña, token ni claves privadas.

El chat y los datos privados de Domus quedan pendientes de vinculación segura
sin contraseña. No se ha abierto el servidor a consultas anónimas ni se han
incluido credenciales dentro del USB. La autenticación del servicio existente
se conserva. Esta edición local no conecta automáticamente a una cuenta.

## Métricas del mini y Domus

Pulsa **Ver metricas**. CPU, RAM ocupada (incluye caché), disco del sistema libre
y tiempo encendido se actualizan cada cinco segundos y permanecen en el Mac.
El lector nativo x86_64 usa funciones de Mach y sysctl con objetivo mínimo 10.6.
Requiere Core 2 Duo; la comprobación física en Snow Leopard sigue pendiente.
No se incluye grabación de audio ni se accede al micrófono.

Domus se consulta cada minuto, o con **Actualizar Domus**, cuando exista vinculación segura.
Muestra hogares, habitaciones, dispositivos y asistentes registrados, estados
**guardados** de conexión y cuota de chat del día UTC. No mide consumo eléctrico,
temperaturas, ni confirma en vivo que un aparato funcione. Un fallo de lectura
se muestra como «No disponible», nunca como cero.

El endpoint nuevo `GET /api/mini-ai/metrics` requiere activar esta versión del
servicio Mini AI. Hasta autorizar y completar esa actualización, las métricas
locales funcionan y Domus puede mostrar «No disponible». Lee las tablas Domus
con tu sesión y la clave pública bajo RLS; las claves privadas quedan en servidor.
Solo muestra los hogares a los que tu usuario tiene acceso. No crea registros.

## Reconstrucción en un Mac moderno

Los binarios no se guardan en Git. El repositorio contiene el empaquetador y
listas de descargas con versiones y hashes fijados. Requiere Python 3.12 o
posterior, herramientas de Apple (clang, otool, nm, install_name_tool), curl y
OpenSSL con verificación RIPEMD160:

```sh
python3 tools/build_mini_usb.py \
  --work /ruta/nueva/de/trabajo \
  --output /ruta/de/salida \
  --server https://nexxus-mini-ai-production.up.railway.app
```

No ejecutar el empaquetador en Snow Leopard. No descarga claves de servidor, no
instala archivos de sistema y no solicita despliegues. `--finish-only` sirve solo
para volver a comprimir una carpeta de preparación ya auditada, no para generar
una auditoría nueva.

Fuentes: [paquetes oficiales MacPorts](https://packages.macports.org/curl/),
[compilación de curl](https://curl.se/docs/install.html),
[verificación de certificados](https://curl.se/docs/sslcerts.html).

## Pantalla AITA simplificada

Esta edición usa la marca tipográfica AITA y la paleta violeta del panel Domus.
Esta primera versión abre directamente sin formulario ni correo preparado.
«Ver metricas» abre el estado del mini y los contadores de Domus.

La comprobacion visual de esta pantalla sigue pendiente en el Mac mini; el
entorno de esta sesion no permitio iniciar Tk para una vista previa local.

## Nexxus flotante

El botón Nexxus abre una ventana movible con una nebulosa violeta inspirada en
el panel Domus, reloj local y calendario del mes. Mantener flotante permite
verla sobre otras ventanas; Animacion suave permite detener el movimiento.
La animación dibuja 56 partículas cuatro veces por segundo sin navegador ni GPU.
Cierra esa ventana para detener el temporizador. No representa actividad real
de la IA. La ciudad y el proveedor del calendario personal siguen pendientes
de confirmar; todavía no hay previsión del tiempo ni eventos personales.
El calendario visible usa la fecha y zona horaria configuradas en el Mac.
