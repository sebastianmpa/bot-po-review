import csv
import os
import logging
from models.purchase_model import SeoCategoryRequestModel, build_state_date
from seo_scripts.gardner_login_playwright import gardner_login_automation_playwright

logger = logging.getLogger(__name__)

EMAIL = "jacobn.prontomowers+75145@gmail.com"
PASSWORD = "Pronto123#"


def create_csv_from_products(products: list, csv_filename: str) -> str:
    """
    Crea un archivo CSV a partir de la lista de productos recibidos.
    """
    temp_dir = os.path.join(os.path.expanduser("~"), "Downloads", "temp_purchase_orders")
    os.makedirs(temp_dir, exist_ok=True)
    csv_path = os.path.join(temp_dir, csv_filename)

    logger.info(f"📝 Creando archivo CSV en: {csv_path}")

    with open(csv_path, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['MANUFACTURER', 'PART NUMBER', 'QUANTITY'])
        for item in products:
            writer.writerow([item.mfrid, item.partNumber, item.qty])

    logger.info(f"✅ CSV creado: {csv_filename} ({len(products)} productos)")
    return csv_path


def start_purchase_execution(request: SeoCategoryRequestModel):
    """
    Servicio que SOLO crea el CSV y ejecuta la automatización de Playwright para
    realizar la compra en Gardner. No inserta datos en BD ni consulta precios.
    """
    logger.info("=" * 60)
    logger.info("🛒 INICIANDO SERVICIO DE EJECUCIÓN DE COMPRA")
    logger.info(f"ChunkId: {request.chunkId}")

    # Resolver lista de órdenes
    purchase_orders = []
    if request.data:
        if hasattr(request.data, 'productToReview') and request.data.productToReview:
            purchase_orders = request.data.productToReview
        elif hasattr(request.data, 'poNumber') and request.data.poNumber:
            purchase_orders = [request.data]
    elif request.poNumber and request.supplerID and request.products:
        purchase_orders = [request]

    if not purchase_orders:
        logger.error("❌ No se encontraron órdenes de compra en el request.")
        return {
            "chunkId": request.chunkId,
            "status": "Failed",
            "message": "No se encontraron órdenes de compra en el request.",
            "state_date": build_state_date()
        }

    logger.info(f"📋 Total de órdenes a procesar: {len(purchase_orders)}")

    results = []

    for po in purchase_orders:
        po_number = po.poNumber
        logger.info(f"📦 Ejecutando compra para PO: {po_number} ({len(po.products)} productos)")

        try:
            # 1. Crear CSV con los productos de la PO
            csv_filename = f"PO_{po_number}.csv"
            create_csv_from_products(po.products, csv_filename)

            # 2. Ejecutar Playwright — subir archivo y realizar compra
            logger.info(f"🌐 Lanzando Playwright para PO {po_number}...")
            gardner_login_automation_playwright(EMAIL, PASSWORD, csv_filename)
            logger.info(f"✅ Compra ejecutada exitosamente para PO {po_number}")

            results.append({
                "poNumber": po_number,
                "status": "Success",
                "products": len(po.products)
            })

        except Exception as e:
            logger.error(f"❌ Error al ejecutar compra para PO {po_number}: {e}", exc_info=True)
            results.append({
                "poNumber": po_number,
                "status": "Failed",
                "error": str(e)
            })

    overall_status = "Success" if all(r["status"] == "Success" for r in results) else "Failed"

    logger.info(f"🏁 Ejecución de compra finalizada. Estado: {overall_status}")
    logger.info("=" * 60)

    return {
        "chunkId": request.chunkId,
        "status": overall_status,
        "results": results,
        "state_date": build_state_date()
    }
