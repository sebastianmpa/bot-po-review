from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from routes.bot_reset_route import router as bot_reset_router
from routes.purchase_order_route import router as purchase_order_router
import logging
import json

# Configurar logger
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

# Middleware para capturar el body raw en caso de error 422
@app.middleware("http")
async def log_request_body(request: Request, call_next):
    # Leer el body
    body = await request.body()
    
    # Log del body recibido
    if body:
        try:
            body_json = json.loads(body.decode())
            logger.info("="*60)
            logger.info(f"REQUEST to {request.method} {request.url.path}")
            logger.info(f"Body recibido: {json.dumps(body_json, indent=2, ensure_ascii=False)}")
            logger.info("="*60)
        except:
            logger.info(f"Body (raw): {body.decode()}")
    
    # Recrear el request con el body
    async def receive():
        return {"type": "http.request", "body": body}
    
    request._receive = receive
    
    response = await call_next(request)
    return response

# Manejador personalizado para errores de validación 422
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error("="*60)
    logger.error("ERROR 422 - VALIDACIÓN FALLIDA:")
    logger.error(f"Errores: {json.dumps(exc.errors(), indent=2, ensure_ascii=False)}")
    logger.error(f"Body recibido: {exc.body}")
    logger.error("="*60)
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors(), "body": exc.body},
    )

# Incluir los routers de productos, categorías y órdenes de compra
app.include_router(bot_reset_router)
app.include_router(purchase_order_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8015, reload=True)