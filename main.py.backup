from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uuid

app = FastAPI()

# Importar el servicio
try:
    from recepcionista_service import RecepcionistaIAService
    recepcionista = RecepcionistaIAService()
except Exception as e:
    print(f"Error al inicializar RecepcionistaIAService: {e}")
    recepcionista = None

@app.get("/")
async def root():
    return {"status": "ok"}

@app.post("/api/iniciar")
async def iniciar(request: Request):
    try:
        data = await request.json()
        sesion_id = data.get("sesion_id", str(uuid.uuid4()))
        return recepcionista.iniciar_sesion(sesion_id)
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/mensaje")
async def mensaje(request: Request):
    try:
        data = await request.json()
        sesion_id = data.get("sesion_id")
        mensaje_texto = data.get("mensaje")
        
        if not sesion_id or not mensaje_texto:
            return {"error": "sesion_id y mensaje requeridos"}
        
        respuesta = recepcionista.procesar_mensaje(sesion_id, mensaje_texto)
        return {"respuesta": respuesta}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/finalizar")
async def finalizar(request: Request):
    try:
        data = await request.json()
        sesion_id = data.get("sesion_id")
        return recepcionista.finalizar_sesion(sesion_id)
    except Exception as e:
        return {"error": str(e)}
