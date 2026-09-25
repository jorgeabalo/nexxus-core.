from recepcionista_service import RecepcionistaIAService
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
recepcionista = RecepcionistaIAService()

class IniciarRequest(BaseModel):
    sesion_id: str

class MensajeRequest(BaseModel):
    sesion_id: str
    mensaje: str

class FinalizarSesionRequest(BaseModel):
    sesion_id: str

@app.post("/api/iniciar")
async def iniciar(request: IniciarRequest):
    return recepcionista.iniciar_sesion(request.sesion_id)

@app.post("/api/mensaje")
async def mensaje(request: MensajeRequest):
    respuesta = recepcionista.procesar_mensaje(request.sesion_id, request.mensaje)
    return {"respuesta": respuesta}

@app.post("/api/finalizar")
async def finalizar(request: FinalizarSesionRequest):
    return recepcionista.finalizar_sesion(request.sesion_id)
