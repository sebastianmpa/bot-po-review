from fastapi import APIRouter, BackgroundTasks
from models.purchase_model import SeoCategoryRequestModel
from services.purchase_order_service import start_purchase_order_automation
from threading import Lock, Thread
import logging
import json

# Configurar logger
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

router = APIRouter()

# Variable global para controlar el estado del servicio
service_lock = Lock()
is_running = False

@router.post("/start-purchase-order-automation")
def start_purchase_order_endpoint(request: SeoCategoryRequestModel, background_tasks: BackgroundTasks):
    """
    Endpoint para iniciar la automatización de órdenes de compra.
    
    :param request: Objeto SeoCategoryRequestModel que contiene el chunkId y la lista de items
    :param background_tasks: Tareas en segundo plano para ejecutar la automatización
    :return: Respuesta inicial del servicio
    """
    # Logs detallados para debugging
    logger.info("="*60)
    logger.info("ÓRDENES DE COMPRA - DATOS RECIBIDOS:")
    logger.info(f"ChunkId: {request.chunkId}")
    
    # Detectar formato para logging
    purchase_orders = []
    if request.data:
        if hasattr(request.data, 'poNumber') and request.data.poNumber:
            # Formato antiguo
            purchase_orders = [request.data]
            logger.info("📋 Formato: ANTIGUO (data con PO directa)")
        elif hasattr(request.data, 'productToReview') and request.data.productToReview:
            # Formato nuevo
            purchase_orders = request.data.productToReview
            logger.info("📋 Formato: NUEVO (data.productToReview array)")
    elif request.poNumber and request.supplerID and request.products:
        # Formato antiguo (campos directos)
        purchase_orders = [request]
        logger.info("📋 Formato: ANTIGUO (campos directos)")
    
    logger.info(f"Total de órdenes: {len(purchase_orders)}")
    
    for idx, po in enumerate(purchase_orders, 1):
        logger.info(f"Orden {idx}: PO={po.poNumber}, Supplier={po.supplerID}, Productos={len(po.products)}")
        if po.products:
            first_product = po.products[0]
            logger.info(f"  Primer producto: MFRID={first_product.mfrid}, Part={first_product.partNumber}, Qty={first_product.qty}, Cost=${first_product.idealCost}")
    
    logger.info(f"Request completo: {request.model_dump_json(indent=2)}")
    logger.info("="*60)
    
    global is_running

    # Verificar si el servicio ya está en ejecución
    with service_lock:
        if is_running:
            logger.warning("⚠️ El servicio ya está en ejecución. Rechazando nueva solicitud.")
            return {
                "chunkId": request.chunkId,
                "status": "FAILED",
                "message": "El servicio ya está procesando otra orden de compra."
            }

        # Marcar el servicio como en ejecución
        is_running = True

    # Agregar la ejecución del servicio como una tarea en segundo plano
    background_tasks.add_task(run_purchase_order_service_in_background, request)

    # Responder inmediatamente con el estado inicial
    total_products = sum(len(po.products) for po in purchase_orders)
    return {
        "chunkId": request.chunkId,
        "status": "OK",
        "message": f"Procesando {len(purchase_orders)} órdenes de compra con {total_products} productos totales."
    }


def run_purchase_order_service_in_background(request: SeoCategoryRequestModel):
    """
    Ejecuta el servicio de órdenes de compra en segundo plano y libera el estado al finalizar.
    """
    global is_running

    def _worker():
        global is_running
        try:
            logger.info("🚀 Iniciando procesamiento en segundo plano...")
            start_purchase_order_automation(request)
            logger.info("✅ Procesamiento completado exitosamente.")
        except Exception as e:
            logger.error(f"❌ Error en el procesamiento: {e}")
        finally:
            # Liberar el estado del servicio
            with service_lock:
                is_running = False
            logger.info("🔓 Servicio liberado para nuevas solicitudes.")

    # Ejecutar en un hilo dedicado para aislar Playwright Sync del event loop de FastAPI
    worker_thread = Thread(target=_worker, name="purchase-order-worker", daemon=True)
    worker_thread.start()


@router.post("/debug-purchase-order-request")
async def debug_purchase_order_request(body: dict):
    """
    Endpoint de debug para ver qué datos están llegando exactamente.
    No tiene validación estricta de Pydantic.
    """
    logger.info("="*60)
    logger.info("DEBUG - PURCHASE ORDER RAW REQUEST BODY:")
    logger.info(json.dumps(body, indent=2, ensure_ascii=False))
    logger.info("="*60)
    
    # Validar campos requeridos manualmente
    missing_fields = []
    if "chunkId" not in body:
        missing_fields.append("chunkId")
    if "data" not in body:
        missing_fields.append("data")
    elif isinstance(body.get("data"), dict):
        data = body["data"]
        if "po_number" not in data and "poNumber" not in data:
            missing_fields.append("data.po_number or data.poNumber")
        if "suppler_id" not in data and "supplerID" not in data:
            missing_fields.append("data.suppler_id or data.supplerID")
        if "products" not in data:
            missing_fields.append("data.products")
        elif isinstance(data.get("products"), list) and len(data["products"]) > 0:
            first_product = data["products"][0]
            if "mfrid" not in first_product:
                missing_fields.append("data.products[0].mfrid")
            if "part_number" not in first_product and "partNumber" not in first_product:
                missing_fields.append("data.products[0].part_number or partNumber")
            if "qty" not in first_product:
                missing_fields.append("data.products[0].qty")
    else:
        missing_fields.append("data debe ser un objeto")
    
    if missing_fields:
        logger.error(f"Campos faltantes o incorrectos: {missing_fields}")
        return {
            "status": "ERROR",
            "message": "Campos faltantes o incorrectos",
            "missing_fields": missing_fields,
            "received_body": body
        }
    
    return {
        "status": "OK",
        "message": "Request válido",
        "total_products": len(body.get("data", {}).get("products", [])),
        "received_body": body
    }
