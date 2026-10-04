# Calendarios para AITA Domus

Requisito de producto: cada usuario podrá conectar Google Calendar, Outlook/Microsoft 365 y calendarios Apple/iCloud o fuentes iCalendar compatibles. Nexxus consultará las conexiones de ese usuario, dentro del hogar autenticado. Estado actual: diseño de integración; ningún proveedor está conectado todavía.

## Experiencia prevista

En el registro del hogar habrá una sección «Calendarios»: elegir proveedor, autorizar en su página oficial, elegir los calendarios que se desean consultar y decidir si alguno se comparte con el hogar. Los calendarios personales serán privados por defecto. Se podrá desconectar cada cuenta y revocar sus permisos.

Primera entrega: consulta de agenda, por ejemplo «¿Qué tengo hoy?» y «¿A qué hora es mi próxima cita?». Crear, cambiar o cancelar citas quedará para una etapa posterior con permisos adicionales y confirmación de la acción concreta. El usuario elegirá con qué proveedor hacer la primera prueba.

## Proveedores

| Proveedor | Conexión prevista | Configuración pendiente |
| --- | --- | --- |
| Google Calendar | OAuth del usuario, lectura; selección de calendarios y eventos | Proyecto Google Cloud, Calendar API habilitada, cliente OAuth de aplicación web, pantalla de consentimiento, usuarios de prueba y URL de retorno HTTPS |
| Outlook / Microsoft 365 | OAuth delegado del usuario mediante Microsoft Graph; lectura de calendario | Registro de aplicación Entra, cuentas personales y/o organizaciones permitidas, permisos delegados adecuados y URL de retorno HTTPS |
| Apple / iCloud | Evaluar autorización oficial compatible o CalDAV con credencial específica de aplicación | Elegir y validar el mecanismo disponible para esta aplicación antes de solicitar credenciales |
| iCalendar / ICS | Importación de archivo inicialmente; una suscripción remota requiere controles adicionales | Parser con soporte de zonas horarias y recurrencia; límites de tamaño, horizonte temporal y eventos |

Apple Calendar es una aplicación y puede mostrar cuentas Google/Outlook; debe conectarse el proveedor que contiene los eventos. iCalendar/ICS es un formato: no implica sincronización bidireccional ni acceso a una cuenta Apple.

Fuentes oficiales: [permisos Google Calendar](https://developers.google.com/workspace/calendar/api/auth), [permisos Microsoft Graph](https://learn.microsoft.com/en-us/graph/permissions-reference), [autorización de aplicaciones Apple](https://support.apple.com/en-us/102654). Elegir el permiso mínimo que cubra los campos realmente necesarios; no pedir acceso global a la organización ni escritura para una consulta de agenda.

## Separación del hogar y de las credenciales

La futura conexión se asociará a `home_id` y `user_id`, con validación de membresía activa en servidor. Las credenciales OAuth/CalDAV se guardarán cifradas en un almacén del servidor; nunca en tablas accesibles con la clave pública, en el modelo Alexa, en registros ni en el repositorio. Renovación y revocación de tokens pertenecen al servidor.

El intercambio OAuth requerirá estado aleatorio, de un solo uso y con caducidad, vinculado a la sesión y al hogar; PKCE donde corresponda. La URL de retorno se fijará en configuración y no procederá de parámetros libres. El endpoint no aceptará un `user_id` aportado por el navegador como identidad autenticada.

Google y Outlook se adaptarán a un contrato común: listar calendarios autorizados y consultar eventos en un intervalo acotado. Los adaptadores tendrán URLs oficiales fijas, tiempo de espera, paginación limitada, renovación de tokens y respuestas de error sin secretos. Normalizarán fechas, zona horaria, eventos de día completo, recurrencias y cancelaciones. Una agenda que no respondió se mostrará como no disponible; no equivale a una agenda vacía.

La suscripción ICS remota no se habilitará como simple descarga de una URL arbitraria: podría permitir acceso a servicios privados del servidor. Requiere validar destino y redirecciones, bloquear redes internas y limitar descarga y procesamiento. No solicitar enlaces públicos de calendarios privados como atajo.

## Voz y privacidad

La skill actual sigue vinculada al hogar de Jorge. Antes de exponer agenda por voz a otras casas debe completarse la vinculación de cuentas y el control de identidad. Un Echo compartido no identifica de forma fiable al miembro que habla solo por la identidad de la cuenta de Alexa. Por defecto, voz solo mostrará calendarios expresamente compartidos para el hogar; el panel autenticado podrá consultar los personales.

Para agenda simple, Nexxus puede redactar una respuesta determinista desde eventos autorizados. No enviar títulos, participantes, ubicación ni notas de eventos a Anthropic sin una autorización específica que cubra esos datos; el consentimiento existente para consultas de voz no se tratará como autorización automática para exportar una agenda.

## Activación y validación pendientes

No se añaden variables de OAuth ficticias ni endpoints de retorno sin verificación. Después de elegir el primer proveedor, registrar su aplicación oficial, implementar el flujo completo y probar renovación/revocación antes de activar producción. El usuario completará su autorización en la página oficial del proveedor; no enviará su contraseña en el chat.

Pruebas requeridas: estado OAuth inválido/reutilizado/caducado, aislamiento entre hogares y usuarios, permisos mínimos, desconexión y renovación, calendario no autorizado, recurrencias/día completo/cambio de hora, paginación, errores y ausencia de datos personales en registros o respuestas de otros usuarios.
