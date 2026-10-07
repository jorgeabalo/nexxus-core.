# Centro de control Domus

Después de iniciar sesión, el panel muestra Nexxus en el centro, con tarjetas laterales para música, TV, limpieza, calendario, recordatorios, inventario, economía y archivos. La configuración de hogares sigue disponible en «Configurar mi hogar».

La hora y fecha usan la zona horaria de la casa seleccionada, calculadas en el navegador. El clima no está conectado: la tarjeta informa de la ubicación pendiente. Los dispositivos mostrados proceden de los registros del hogar mediante Supabase/RLS; no se ejecutan órdenes mientras sus adaptadores no estén preparados. Los módulos pendientes explican su estado y no muestran eventos, gastos ni existencias ficticias.

## Preguntas a Nexxus

El campo de preguntas responde consultas sencillas de hora local sin transmitir datos. Para otras consultas, el usuario debe marcar una autorización explícita para enviar esa pregunta a Anthropic. No se exportan los registros del hogar, calendarios, identificadores ni datos de Golden Age. La casilla se restablece al cambiar de hogar o cerrar sesión. No hay memoria conversacional ni control de dispositivos desde este campo.

`POST /api/domus/panel/ask` exige JWT de usuario, verifica la sesión contra Supabase Auth y comprueba la visibilidad del hogar con el mismo JWT y la clave pública, de modo que RLS gobierna el acceso. No utiliza service-role. Exige consentimiento en la solicitud; aplica límites de longitud y tiempos de espera y devuelve errores sin credenciales. Reutiliza DOMUS_AI_ENABLED y el cliente de inteligencia existente, con un mensaje de sistema genérico para el panel. Alexa conserva su configuración anterior.

La animación acelerada de la nebulosa dura solo mientras el panel procesa una consulta. No representa escucha por micrófono ni conectividad de aparatos. Continúan el control de pausa y la preferencia de movimiento reducido.

## Validación

Pruebas de backend cubren JWT ausente o inválido, hogar no visible, consentimiento ausente, límite de texto y envío exclusivo de la pregunta. La vista previa se recorrió con una sesión ficticia local, comprobando creación de hogar, hora, módulos pendientes y controles. La autenticación real y una consulta autorizada al proveedor requieren una prueba en el panel publicado con la cuenta del usuario.
