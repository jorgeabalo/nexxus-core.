# AITA Marketing — línea base de pruebas (23 fallos preexistentes)

Actualizado: 2026-10-10, tras rebasear el PR #14 sobre `main` (`f0ea707`, después de los PR #12, #15, #16 y #17).
Python 3.13.x, macOS. Marketing **no añade fallos**: la lista es idéntica en `main` y en el PR.

## Comando

```bash
PYTHONDONTWRITEBYTECODE=1 PGLITE_NODE_PATH=/ruta/node_modules python3 -m pytest -q -p no:cacheprovider -rf
```

## Resultados

| Rama | Commit | Resultado |
|---|---|---|
| `main` | `f0ea707` | **23 failed, 246 passed** |
| `feature/aita-marketing` (PR #14) | rebasado sobre `f0ea707` | **23 failed, 310 passed** (64 pruebas nuevas, 0 omitidas) |

`diff` de los nombres de los tests fallidos en ambas ramas: **idéntico** (0 fallos nuevos, 0 arreglados).
Respecto a la línea base anterior (24), `tests/test_api.py::test_health` pasa desde el PR #15 (`GET /health`).

## Los 23 fallos (mismos nombres en las dos ramas)

**`tests/test_api.py` (21)** — prueban la API heredada (`/api/negocios…`, mediciones, alertas, páginas HTML
antiguas) que ya no existe en `main.py`: responden 404 o falta `cliente_id`.

- `tests/test_api.py::test_alertas_requiere_auth_admin`
- `tests/test_api.py::test_alertas_separa_en_riesgo_y_reactivacion`
- `tests/test_api.py::test_checkin_por_voz_se_refleja_en_el_perfil`
- `tests/test_api.py::test_crear_negocio_requiere_auth_operador`
- `tests/test_api.py::test_crear_negocio_slug_duplicado_409`
- `tests/test_api.py::test_crear_negocio_vertical_desconocido_400`
- `tests/test_api.py::test_estadisticas_con_auth_200`
- `tests/test_api.py::test_estadisticas_globales_requiere_auth_de_operador`
- `tests/test_api.py::test_estadisticas_sin_auth_401`
- `tests/test_api.py::test_flujo_completo_alta_de_socio_y_cobro`
- `tests/test_api.py::test_flujo_completo_llamada_iniciar_mensaje_finalizar`
- `tests/test_api.py::test_listar_negocios_operador`
- `tests/test_api.py::test_mediciones_cliente_de_otro_negocio_404`
- `tests/test_api.py::test_mediciones_requiere_auth_admin`
- `tests/test_api.py::test_mensaje_llamada_inexistente`
- `tests/test_api.py::test_negocio_suspendido_rechaza_llamadas`
- `tests/test_api.py::test_paginas_html_cargan`
- `tests/test_api.py::test_panel_cliente_html_carga`
- `tests/test_api.py::test_perfil_cliente_completo`
- `tests/test_api.py::test_perfil_incluye_riesgo`
- `tests/test_api.py::test_registrar_y_listar_mediciones`

**`tests/test_domus.py` (2)** — necesitan `ask-sdk-webservice-support` (`requirements-domus.txt`), que no está
instalado en este entorno local (`ModuleNotFoundError: ask_sdk_webservice_support`).

- `tests/test_domus.py::test_real_verifier_missing_signature`
- `tests/test_domus.py::test_sdk_signature_and_tamper`
