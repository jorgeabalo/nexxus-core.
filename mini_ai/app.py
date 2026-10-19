"""Dedicated Railway entry point. Never imports Claudia, main or business models."""
from fastapi import FastAPI, Request
from mini_ai.api import router

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
from mini_ai.metrics import router as metrics_router
app.include_router(metrics_router)


@app.middleware('http')
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'nexxus-mini-ai'}
