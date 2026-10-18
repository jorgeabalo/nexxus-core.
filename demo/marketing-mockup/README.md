# Maqueta navegable · Marketing de Nexxus Manager (demostración)

Carpeta aislada con una maqueta estática de Marketing para evaluar diseño y flujo.
Empresa de ejemplo: **Golden Age Fitness & Training**. Todos los datos son **sintéticos**.

## Cómo verla

Sin build ni dependencias externas:

    cd demo/marketing-mockup
    python3 -m http.server 8080
    # abrir http://localhost:8080/index.html#/resumen

Rutas: `#/resumen`, `#/publicaciones`, `#/calendario`, `#/campanas`.

## Qué simula y qué no

- Los botones de estado (Enviar a revisión, Aprobar, Programar, Quitar programación, Revisar) solo cambian la vista en memoria.
- «Publicar ahora» abre una confirmación que explica que la red debe confirmar antes de mostrar «Publicada».
- Nada se conecta: no hay Supabase, redes sociales, Postiz, Twilio, Remotion, VoiceStudio ni cargos.
- «Nueva publicación» muestra un aviso de demostración; el formulario no existe todavía.
- Recargar la página restaura los datos de ejemplo.

## Idioma y accesibilidad

- Interfaz en español con conmutador ES/EN (diccionario `I18N` en el HTML). Los datos de ejemplo están en español.
- Navegación por teclado, `aria-current`, diálogos nativos (`<dialog>`), enlace «Saltar al contenido», foco visible y modo oscuro según preferencia del sistema.
- Escritorio: barra lateral. Móvil (≤760 px): navegación horizontal, tabla sustituida por lista y calendario por lista por día.

## Logo

No hay logo oficial autorizado. Se usa un monograma de texto «GA» provisional.

## Base visual y licencia

- Se evaluó TailAdmin: la versión **HTML/Tailwind Free** (MIT, Tailwind v4, requiere Node/Webpack/Alpine) y la **React Free** (MIT).
- Se eligió **no** importar ninguna de las dos: el Manager es JavaScript plano sin build, y migrarlo a React o a un build de Node excede el alcance.
- Se toma la estructura visual (barra lateral, cabecera, tarjetas, tablas), **sin copiar código**. El CSS es propio.
- Aviso MIT del proyecto base incluido en `LICENSE-TailAdmin.txt` (Copyright (c) 2023 TailAdmin). Si en el futuro se copia código de la plantilla, debe conservarse ese aviso.
- Ningún componente Pro (7 dashboards, 500+ componentes, Figma) está incluido.

## Pendiente (fuera de esta maqueta)

- Sección **Marketing → Complementos** con tarjetas de Remotion y VoiceStudio (precio, límites, confirmación; sin activación real).
- Vista detallada de campaña con objetivo y publicaciones vinculadas (solo hay tarjeta con objetivo, fechas y avance).
- Formulario real de nueva publicación con previsualización por red.
- Las correcciones de auditoría, cupos concurrentes, permisos y worker siguen pendientes en la rama `fix/marketing-repairs`; esta maqueta no las sustituye.
