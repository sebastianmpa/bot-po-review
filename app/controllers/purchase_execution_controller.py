from fastapi import APIRouter, BackgroundTasks
from models.purchase_model import SeoCategoryRequestModel
from services.purchase_execution_service import start_purchase_execution
from threading import Lock, Thread
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

router = APIRouter()

service_lock = Lock()
is_running = False


@router.post("/start-purchase-execution")
def start_purchase_execution_endpoint(request: SeoCategoryRequestModel, background_tasks: BackgroundTasks):
    """
    Endpoint para ejecutar la compra en Gardner.
    Solo sube el CSV mediante Playwright. No inserta datos en BD ni compara precios.
    """
    logger.info("=" * 60)
    logger.info("🛒 PURCHASE EXECUTION - DATOS RECIBIDOS:")
    logger.info(f"ChunkId: {request.chunkId}")
    logger.info(f"Request: {request.model_dump_json(indent=2)}")
    logger.info("=" * 60)

    global is_running

    with service_lock:
        if is_running:
            logger.warning("⚠️ El servicio ya está en ejecución. Rechazando nueva solicitud.")
            return {
                "chunkId": request.chunkId,
                "status": "FAILED",
                "message": "El servicio ya está procesando una compra."
            }
        is_running = True

    background_tasks.add_task(run_purchase_execution_in_background, request)

    return {
        "chunkId": request.chunkId,
        "status": "OK",
        "message": "Ejecución de compra iniciada en segundo plano."
    }


def run_purchase_execution_in_background(request: SeoCategoryRequestModel):
    """
    Ejecuta el servicio de compra en un hilo dedicado para aislar
    Playwright Sync del event loop de FastAPI.
    """
    global is_running

    def _worker():
        global is_running
        try:
            logger.info("🚀 Iniciando ejecución de compra en segundo plano...")
            start_purchase_execution(request)
            logger.info("✅ Ejecución de compra completada exitosamente.")
        except Exception as e:
            logger.error(f"❌ Error en la ejecución de compra: {e}", exc_info=True)
        finally:
            with service_lock:
                is_running = False
            logger.info("🔓 Servicio de compra liberado para nuevas solicitudes.")

    worker_thread = Thread(target=_worker, name="purchase-execution-worker", daemon=True)
    worker_thread.start()
