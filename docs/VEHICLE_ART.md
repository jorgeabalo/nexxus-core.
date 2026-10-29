# Imágenes de vehículos

Generadas con la herramienta integrada image_gen, no fotografías oficiales. Guardadas en car/assets/vehicles/kia-sportage-2021.png y toyota-corolla-2022.png; alpha RGBA verificado. Catálogo local en car/assets/vehicles.js: coincidencia exacta normalizada marca/modelo/año. Selección automática al registrar, editar o cambiar vehículo. No se llama a proveedores ni se transmiten datos del dueño. Otros modelos/años muestran la ilustración genérica identificada; añadir su imagen y entrada al catálogo permite ampliar cobertura sin cambiar el flujo.

Verificación: cinco comprobaciones del catálogo, incluyendo año/modelo desconocidos y rutas maliciosas. Cambio Corolla → Sportage y carga real de PNG comprobados en navegador.

## Prompts utilizados

Sportage:

> Use case: product-mockup. Asset: vehicle cutout for AITA Car black/red/white cockpit UI. Generate one realistic studio product render of a white 2021 Kia Sportage fourth-generation QL compact SUV, front three-quarter view pointing left, full car visible with generous small transparent margin. Correct 2021 body: rounded swept-back headlights high on hood, compact tiger-nose grille, lower fog light housings, silver alloy wheels, black window trim, SUV proportions. Not the newer boomerang-headlight 2023 design. Premium realistic paint, crisp tires, subtle red rim light reflecting a dark cockpit environment, restrained contact shadow. Transparent background with actual alpha. No scenery, floor plane, text, captions, watermark, people, or license plate lettering. This is an illustrative car render, not an official manufacturer photo.

Corolla:

> Use case: product-mockup. Asset: AITA Car cockpit vehicle illustration. One photorealistic white 2022 Toyota Corolla four-door sedan, front three-quarter view pointing left, all of the vehicle visible with small margins, swept narrow headlights, wide black lower grille, silver alloy wheels and black glass. Realistic polished studio render, restrained red rim lighting suitable for a black/red UI. Genuine transparent background alpha, no environment, text, captions, watermark, people, or readable license plate. Illustrative car render, not official manufacturer photography.

## Kia Rio 2023
Asset: `car/assets/vehicles/kia-rio-2023.png`. Generated illustration, not an official photo.
Prompt: Create a photorealistic automotive UI cutout illustration of a white 2023 Kia Rio four-door sedan, US-market styling, front three-quarter view facing left, whole car including all wheels, accurate compact sedan proportions, slim headlights and small black grille, subtle red rim lighting to match a black red white cockpit dashboard, clean studio lighting, no text, no scenery. Transparent background. Landscape composition with generous margins. This is an illustrative rendering not an official photograph.
