"""
purchase_order_service.py
-------------------------
Orquestador principal de órdenes de compra.

Responsabilidades:
  - Detectar el formato del payload (nuevo / antiguo)
  - Iterar sobre cada PurchaseOrderDataModel
  - Delegar la ejecución al servicio concreto del proveedor (patrón Strategy)
  - Construir y enviar la respuesta final al TaskHub

El proveedor correcto se resuelve mediante SupplierFactory según `supplerID`.
Para agregar un nuevo proveedor: ver services/suppliers/supplier_factory.py
"""

from models.purchase_model import (
    SeoCategoryRequestModel,
    ResponseBlogModel,
    PurchaseOrderResponseProduct,
    PurchaseOrderResponseData,
    PurchaseOrderDataModel,
    build_state_date,
)
from services.suppliers import get_supplier_service, SupplierNotFoundError
from seo_scripts.rest_consumer_management import register_chunk_item


def _resolve_purchase_orders(request: SeoCategoryRequestModel) -> list:
    """
    Detecta el formato del payload y retorna la lista de PurchaseOrderDataModel.
    Soporta:
      - Formato nuevo:  data.productToReview = [...]
      - Formato antiguo directo:  data.poNumber / data.supplerID / data.products
      - Formato antiguo en raíz:  request.poNumber / request.supplerID / request.products
    """
    if request.data:
        if isinstance(request.data, PurchaseOrderDataModel):
            print("📋 Formato ANTIGUO (data es PurchaseOrderDataModel)")
            return [request.data]

        if hasattr(request.data, "poNumber") and request.data.poNumber:
            print("📋 Formato ANTIGUO (data con poNumber)")
            return [PurchaseOrderDataModel(
                poNumber=request.data.poNumber,
                supplerID=request.data.supplerID,
                products=request.data.products,
            )]

        if hasattr(request.data, "productToReview") and request.data.productToReview:
            print("📋 Formato NUEVO (data.productToReview array)")
            return request.data.productToReview

        raise ValueError("data no tiene ni poNumber ni productToReview")

    if request.poNumber and request.supplerID and request.products:
        print("📋 Formato ANTIGUO (campos directos en request)")
        return [PurchaseOrderDataModel(
            poNumber=request.poNumber,
            supplerID=request.supplerID,
            products=request.products,
        )]

    raise ValueError(
        "No se pudo detectar el formato de datos. Verifica la estructura del JSON."
    )


def start_purchase_order_automation(request: SeoCategoryRequestModel):
    """
    Orquestador principal de órdenes de compra.

    Flujo:
      1. Detectar formato del payload
      2. Para cada PO → obtener el SupplierService correcto via Factory
      3. Ejecutar supplier_service.execute(po_data, chunk_id)
      4. Construir respuesta final y enviar al TaskHub
    """
    print("=" * 60)
    print("🚀 INICIANDO AUTOMATIZACIÓN DE ÓRDENES DE COMPRA")
    print(f"📋 ChunkId: {request.chunkId}")

    purchase_orders = _resolve_purchase_orders(request)

    print(f"📦 Total de órdenes: {len(purchase_orders)}")
    print("=" * 60)

    all_responses = []
    processing_errors = []

    try:
        for idx, po_data in enumerate(purchase_orders, 1):
            print(f"\n{'='*60}")
            print(f"📦 Procesando orden {idx}/{len(purchase_orders)} | "
                  f"PO: {po_data.poNumber} | Proveedor: {po_data.supplerID}")
            print(f"{'='*60}\n")

            try:
                # ── Factory: seleccionar la estrategia según supplerID ──
                supplier_service = get_supplier_service(po_data.supplerID)

                # ── Template Method: ejecutar el flujo completo del proveedor ──
                po_response = supplier_service.execute(po_data, request.chunkId)
                all_responses.append(po_response)

            except SupplierNotFoundError as snfe:
                print(f"❌ Proveedor no soportado para PO {po_data.poNumber}: {snfe}")
                _append_failed_po(all_responses, processing_errors, po_data, str(snfe))

            except Exception as po_error:
                print(f"❌ Error procesando PO {po_data.poNumber}: {po_error}")
                _append_failed_po(all_responses, processing_errors, po_data, str(po_error))

        # ── Construir y enviar respuesta final ──
        final_status = "Failed" if processing_errors else "Success"
        response = ResponseBlogModel(
            chunkId=request.chunkId,
            item=all_responses,
            status=final_status,
        )

        print("\n📤 Enviando respuesta al TaskHub...")
        chunk_response = register_chunk_item(response.dict())
        if chunk_response and "error" in chunk_response:
            print(f"⚠️ Chunk API reportó un error: {chunk_response['error']}")
        else:
            print(f"✅ Chunk API confirmó recepción: {chunk_response}")

        print("\n" + "=" * 60)
        print("✅ PROCESO COMPLETADO")
        print(f"📊 Total órdenes procesadas: {len(all_responses)}")
        print(f"❌ Órdenes con error: {len(processing_errors)}")
        print("=" * 60)

        return response.dict()

    except Exception as e:
        print(f"❌ Error crítico durante la automatización: {e}")

        error_payload = {
            "chunkId": request.chunkId,
            "item": {
                "message": str(e),
                "orders": [po.model_dump() for po in all_responses],
            },
            "status": "Failed",
            "state_date": build_state_date(),
        }

        try:
            register_chunk_item(error_payload)
        except Exception as chunk_error:
            print(f"⚠️ No se pudo enviar error al chunk API: {chunk_error}")

        return error_payload


def _append_failed_po(all_responses, processing_errors, po_data, error_msg):
    """Agrega una PO fallida con todos sus productos en PART_ERROR."""
    all_responses.append(
        PurchaseOrderResponseData(
            poNumber=po_data.poNumber,
            supplerID=po_data.supplerID,
            products=[
                PurchaseOrderResponseProduct(
                    mfrid=p.mfrid,
                    partNumber=p.partNumber,
                    qty=p.qty,
                    idealCost=p.idealCost,
                    supplierPrice=0.0,
                    status="PART_ERROR",
                )
                for p in po_data.products
            ],
        )
    )
    processing_errors.append({"poNumber": po_data.poNumber, "message": error_msg})
