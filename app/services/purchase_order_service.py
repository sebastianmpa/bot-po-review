import csv
import os
import json
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict
from models.purchase_model import (
    SeoCategoryRequestModel, 
    ResponseBlogModel, 
    PurchaseOrderResponseProduct,
    PurchaseOrderResponseData,
    PurchaseOrderDataModel,
    build_state_date
)
from seo_scripts.gardner_login_playwright import gardner_login_automation_playwright
from seo_scripts.insert_data_in_db import insert_po_review_details, get_po_review_summary
from seo_scripts.rest_consumer_management import register_chunk_item

def create_csv_from_json(products: list, csv_filename: str) -> str:
    """
    Crea un archivo CSV a partir de los datos JSON recibidos.
    
    :param products: Lista de items con mfrid, partNumber, qty
    :param csv_filename: Nombre del archivo CSV a crear
    :return: Ruta completa del archivo CSV creado
    """
    try:
        # Crear la carpeta temp si no existe
        temp_dir = os.path.join(os.path.expanduser("~"), "Downloads", "temp_purchase_orders")
        os.makedirs(temp_dir, exist_ok=True)
        
        # Ruta completa del archivo CSV
        csv_path = os.path.join(temp_dir, csv_filename)
        
        print(f"📝 Creando archivo CSV en: {csv_path}")
        
        # Escribir el CSV con las columnas MANUFACTURER, PART NUMBER, QUANTITY
        with open(csv_path, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            
            # Escribir encabezados
            writer.writerow(['MANUFACTURER', 'PART NUMBER', 'QUANTITY'])
            
            # Escribir datos (mapeo: mfrid -> MANUFACTURER, partNumber -> PART NUMBER, qty -> QUANTITY)
            for item in products:
                writer.writerow([
                    item.mfrid,
                    item.partNumber,
                    item.qty
                ])
        
        print(f"✅ Archivo CSV creado exitosamente: {csv_filename}")
        print(f"📊 Total de items: {len(products)}")
        
        return csv_path
        
    except Exception as e:
        print(f"❌ Error al crear el archivo CSV: {e}")
        raise


def delete_csv_file(csv_path: str) -> bool:
    """
    Elimina el archivo CSV después de ser procesado.
    
    :param csv_path: Ruta completa del archivo CSV a eliminar
    :return: True si se eliminó correctamente, False en caso contrario
    """
    try:
        if os.path.exists(csv_path):
            os.remove(csv_path)
            print(f"🗑️ Archivo CSV eliminado: {csv_path}")
            
            # Intentar eliminar la carpeta temp si está vacía
            temp_dir = os.path.dirname(csv_path)
            try:
                os.rmdir(temp_dir)
                print(f"🗑️ Carpeta temporal eliminada: {temp_dir}")
            except OSError:
                # La carpeta no está vacía o no se puede eliminar
                pass
            
            return True
        else:
            print(f"⚠️ El archivo no existe: {csv_path}")
            return False
            
    except Exception as e:
        print(f"❌ Error al eliminar el archivo CSV: {e}")
        return False


def process_single_purchase_order(po_data: PurchaseOrderDataModel, chunkId: str):
    """
    Procesa una única orden de compra.
    
    :param po_data: Datos de la orden de compra
    :param chunkId: ID del chunk
    :return: PurchaseOrderResponseData con los resultados
    """
    print("="*60)
    print("🚀 PROCESANDO ORDEN DE COMPRA")
    print(f"📦 PO Number: {po_data.poNumber}")
    print(f"🏢 Supplier ID: {po_data.supplerID}")
    print(f"📦 Total de productos: {len(po_data.products)}")
    print("="*60)
    
    csv_path = None
    final_csv_path = None
    
    try:
        # 1. Generar nombre único para el archivo CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"PO_{po_data.poNumber}_{po_data.supplerID}_{timestamp}.csv"
        
        # 2. Crear el archivo CSV
        print("📝 Creando archivo CSV...")
        csv_path = create_csv_from_json(po_data.products, csv_filename)
        
        # 3. Crear diccionario de ideal_cost
        ideal_costs: Dict[str, float] = {}
        for product in po_data.products:
            ideal_costs[product.partNumber] = product.idealCost
        
        print(f"💰 Precios ideales cargados: {len(ideal_costs)} productos")
        
        # 4. Ejecutar automatización
        EMAIL = "jacobn.prontomowers+75145@gmail.com"
        PASSWORD = "Pronto123#"
        
        downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
        final_csv_path = os.path.join(downloads_path, csv_filename)
        
        import shutil
        shutil.copy2(csv_path, final_csv_path)
        print(f"📋 Archivo copiado a Downloads: {final_csv_path}")
        
        print("🤖 Ejecutando automatización completa (carga + scraping)...")
        scraped_data = gardner_login_automation_playwright(EMAIL, PASSWORD, csv_filename)
        
        if not scraped_data:
            raise Exception("No se obtuvieron datos del scraping")
        
        print(f"✅ Automatización completada. {len(scraped_data)} filas extraídas")
        
        # 5. Procesar datos y comparar precios
        print("📋 Procesando datos y comparando precios...")
        response_products = []
        
        # Crear diccionario por mfrid+partNumber concatenado
        products_by_concat = {}
        for product in po_data.products:
            concat_key = f"{product.mfrid}{product.partNumber}"
            products_by_concat[concat_key] = product
        
        for item in scraped_data:
            item['po_number'] = po_data.poNumber
            
            part_number = item['part_number']
            supplier_price = item.get('your_price')  # Precio scrapeado del supplier
            
            # Buscar ideal_cost
            ideal_cost = ideal_costs.get(part_number, 0.0)
            
            # Buscar MFRID si está vacío
            if not item['mfrid'] and ideal_cost == 0.0:
                matched_product = products_by_concat.get(part_number)
                if matched_product:
                    item['mfrid'] = matched_product.mfrid
                    ideal_cost = matched_product.idealCost
                    print(f"🔍 Match por concatenado {part_number}: MFRID={matched_product.mfrid}, Cost=${ideal_cost}")
            
            if ideal_cost == 0.0:
                for product in po_data.products:
                    if product.partNumber == part_number:
                        if not item['mfrid']:
                            item['mfrid'] = product.mfrid
                        ideal_cost = product.idealCost
                        print(f"🔍 Match por partNumber {part_number}: MFRID={product.mfrid}, Cost=${ideal_cost}")
                        break
            
            item['ideal_cost'] = ideal_cost if ideal_cost > 0 else 0.0
            
            if supplier_price is None:
                supplier_price = 0.0
            
            # Determinar status para respuesta al chunk
            status = "CORRECT"
            if item['status'] == 'PART_ERROR':
                status = "PART_ERROR"
                item['status'] = 'PART_ERROR'  # Valor del enum en BD
            elif supplier_price > 0 and ideal_cost > 0:
                difference = abs(ideal_cost - float(supplier_price))
                tolerance = ideal_cost * 0.01
                
                print(f"💵 Comparando {part_number}: Ideal=${ideal_cost:.2f}, Supplier=${float(supplier_price):.2f}, Diff=${difference:.2f}, Tol=${tolerance:.2f}")
                
                if difference > tolerance:
                    status = "MISMATCH"
                    item['status'] = 'PRICE_MISMATCH'  # Valor del enum en BD
                    item['error_message'] = f"Price mismatch: Expected ${ideal_cost:.2f}, Supplier ${float(supplier_price):.2f}"
                    print(f"💰 ❌ MISMATCH - Diferencia detectada en {part_number}")
                else:
                    item['status'] = 'CORRECT'
                    print(f"✅ CORRECT - Precios coinciden para {part_number}")
            elif supplier_price == 0 and ideal_cost > 0:
                status = "PART_ERROR"
                item['status'] = 'PART_ERROR'  # Sin precio = error de parte, valor del enum en BD
                if not item.get('error_message'):
                    item['error_message'] = f"No supplier price available for {part_number}"
                print(f"⚠️ PART_ERROR - Sin precio del supplier para {part_number}")
            
            # SUPERSEDED: el scraper ya lo marcó, respetar ese status y mostrarlo en respuesta
            if item.get('status') == 'SUPERSEDED':
                status = "SUPERSEDED"
                print(f"🔄 SUPERSEDED - {part_number} reemplazado por: {item.get('superseded_from')}")
            
            # Agregar SIEMPRE a la respuesta del chunk (éxito o error)
            response_product = PurchaseOrderResponseProduct(
                mfrid=item['mfrid'],
                partNumber=part_number,
                qty=item['qty'],
                idealCost=ideal_cost if ideal_cost > 0 else 0.0,
                supplierPrice=float(supplier_price) if supplier_price is not None else 0.0,
                status=status
            )
            response_products.append(response_product)
        
        # 6. Guardar en BD
        print("💾 Guardando datos en la base de datos...")
        inserted_count = insert_po_review_details(scraped_data)
        print(f"✅ {inserted_count} filas insertadas en po_review_details")
        
        # 7. Crear respuesta
        response_data = PurchaseOrderResponseData(
            poNumber=po_data.poNumber,
            supplerID=po_data.supplerID,
            products=response_products
        )
        
        print(f"📊 Total productos en respuesta: {len(response_products)}")
        print(f"✅ Status CORRECT: {sum(1 for p in response_products if p.status == 'CORRECT')}")
        print(f"⚠️ Status MISMATCH: {sum(1 for p in response_products if p.status == 'MISMATCH')}")
        print(f"❌ Status PART_ERROR: {sum(1 for p in response_products if p.status == 'PART_ERROR')}")
        
        return response_data
        
    finally:
        # Limpiar archivos
        if csv_path and os.path.exists(csv_path):
            delete_csv_file(csv_path)
        if final_csv_path and os.path.exists(final_csv_path):
            delete_csv_file(final_csv_path)


def start_purchase_order_automation(request: SeoCategoryRequestModel):
    """
    Servicio principal para procesar múltiples órdenes de compra.
    Soporta dos formatos:
    1. Nuevo: data.productToReview = [array de órdenes]
    2. Antiguo: data con poNumber, supplerID, products directamente
    
    :param request: Objeto SeoCategoryRequestModel
    :return: Respuesta con el estado de la operación
    """
    print("="*60)
    print("🚀 INICIANDO AUTOMATIZACIÓN DE ÓRDENES DE COMPRA")
    print(f"📋 ChunkId: {request.chunkId}")
    
    # Detectar formato y convertir si es necesario
    purchase_orders = []
    
    if request.data:
        # Formato antiguo: data es PurchaseOrderDataModel directo (tiene poNumber)
        if isinstance(request.data, PurchaseOrderDataModel):
            print("📋 Detectado formato ANTIGUO (data es PurchaseOrderDataModel)")
            purchase_orders = [request.data]
        # Formato antiguo alternativo: data tiene poNumber como atributo
        elif hasattr(request.data, 'poNumber') and request.data.poNumber:
            print("📋 Detectado formato ANTIGUO (data con poNumber)")
            po_data = PurchaseOrderDataModel(
                poNumber=request.data.poNumber,
                supplerID=request.data.supplerID,
                products=request.data.products
            )
            purchase_orders = [po_data]
        # Formato nuevo: data.productToReview es un array
        elif hasattr(request.data, 'productToReview') and request.data.productToReview:
            print("📋 Detectado formato NUEVO (data.productToReview array)")
            purchase_orders = request.data.productToReview
        else:
            raise ValueError("data no tiene ni poNumber ni productToReview")
    # Fallback: poNumber, supplerID, products directamente en request
    elif request.poNumber and request.supplerID and request.products:
        print("📋 Detectado formato ANTIGUO (campos directos en request)")
        po_data = PurchaseOrderDataModel(
            poNumber=request.poNumber,
            supplerID=request.supplerID,
            products=request.products
        )
        purchase_orders = [po_data]
    else:
        raise ValueError("No se pudo detectar el formato de datos. Verifica la estructura del JSON.")
    
    print(f"📦 Total de órdenes: {len(purchase_orders)}")
    print("="*60)
    
    all_responses = []
    processing_errors = []

    try:
        
        # Procesar cada orden de compra
        for idx, po_data in enumerate(purchase_orders, 1):
            print(f"\n{'='*60}")
            print(f"📦 Procesando orden {idx}/{len(purchase_orders)}")
            print(f"{'='*60}\n")
            
            try:
                po_response = process_single_purchase_order(po_data, request.chunkId)
                all_responses.append(po_response)
            except Exception as po_error:
                print(f"❌ Error procesando PO {po_data.poNumber}: {po_error}")

                # Incluir también la PO fallida en la respuesta al chunk
                failed_products = []
                for product in po_data.products:
                    failed_products.append(
                        PurchaseOrderResponseProduct(
                            mfrid=product.mfrid,
                            partNumber=product.partNumber,
                            qty=product.qty,
                            idealCost=product.idealCost,
                            supplierPrice=0.0,
                            status="PART_ERROR"
                        )
                    )

                all_responses.append(
                    PurchaseOrderResponseData(
                        poNumber=po_data.poNumber,
                        supplerID=po_data.supplerID,
                        products=failed_products
                    )
                )
                processing_errors.append({
                    "poNumber": po_data.poNumber,
                    "message": str(po_error)
                })
        
        # Crear respuesta final
        final_status = "Failed" if processing_errors else "Success"

        response = ResponseBlogModel(
            chunkId=request.chunkId,
            item=all_responses,
            status=final_status
        )
        
        # Enviar al chunk API
        print("\n📤 Enviando respuesta al TaskHub...")
        chunk_response = register_chunk_item(response.dict())
        if chunk_response and "error" in chunk_response:
            print(f"⚠️ Chunk API reportó un error: {chunk_response['error']}")
        else:
            print(f"✅ Chunk API confirmó recepción: {chunk_response}")
        
        print("\n" + "="*60)
        print("✅ PROCESO COMPLETADO - Todas las órdenes procesadas")
        print(f"📊 Total órdenes procesadas: {len(all_responses)}")
        print(f"❌ Órdenes con error: {len(processing_errors)}")
        print("="*60)
        
        return response.dict()
        
    except Exception as e:
        print(f"❌ Error durante la automatización: {e}")

        # Respuesta de error: incluir todo lo que ya se alcanzó a procesar
        processed_orders = [po.model_dump() for po in all_responses]
        error_item = {
            "message": str(e),
            "orders": processed_orders
        }

        error_payload = {
            "chunkId": request.chunkId,
            "item": error_item,
            "status": "Failed",
            "state_date": build_state_date()
        }

        try:
            register_chunk_item(error_payload)
        except Exception as chunk_error:
            print(f"⚠️ No se pudo enviar error al chunk API: {chunk_error}")

        return error_payload
    """
    Servicio principal para procesar órdenes de compra.
    
    :param request: Objeto SeoCategoryRequestModel con los datos de la orden
    :return: Respuesta con el estado de la operación
    """
    print("="*60)
    print("🚀 INICIANDO AUTOMATIZACIÓN DE ORDEN DE COMPRA")
    print(f"📋 ChunkId: {request.chunkId}")
    print(f"📦 PO Number: {request.data.poNumber}")
    print(f"🏢 Supplier ID: {request.data.supplerID}")
    print(f"📦 Total de productos: {len(request.data.products)}")
    print("="*60)
    
    csv_path = None
    final_csv_path = None
    
    try:
        # 1. Generar nombre único para el archivo CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"PO_{request.data.poNumber}_{request.data.supplerID}_{timestamp}.csv"
        
        # 2. Crear el archivo CSV desde el JSON (usando la lista de productos)
        print("📝 Paso 1: Creando archivo CSV...")
        csv_path = create_csv_from_json(request.data.products, csv_filename)
        
        # 3. Crear diccionario de ideal_cost para comparación posterior
        ideal_costs: Dict[str, float] = {}
        for product in request.data.products:
            ideal_costs[product.partNumber] = product.idealCost
        
        print(f"💰 Precios ideales cargados: {len(ideal_costs)} productos")
        
        # 4. Ejecutar la automatización de Playwright
        print("🤖 Paso 2: Ejecutando automatización de carga...")
        
        # Credenciales de Gardner Inc
        EMAIL = "jacobn.prontomowers+75145@gmail.com"
        PASSWORD = "Pronto123#"
        
        # Mover el archivo a la carpeta de Downloads para que el script lo encuentre
        downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
        final_csv_path = os.path.join(downloads_path, csv_filename)
        
        # Copiar el archivo a Downloads
        import shutil
        shutil.copy2(csv_path, final_csv_path)
        print(f"📋 Archivo copiado a Downloads: {final_csv_path}")
        
        # Ejecutar la automatización (incluye carga del CSV + scraping integrado)
        print("🤖 Paso 3: Ejecutando automatización completa (carga + scraping)...")
        scraped_data = gardner_login_automation_playwright(EMAIL, PASSWORD, csv_filename)
        
        if not scraped_data:
            raise Exception("No se obtuvieron datos del scraping")
        
        print(f"✅ Automatización completada. {len(scraped_data)} filas extraídas")
        
        # 5. Agregar po_number a cada item de scraped_data y preparar productos para respuesta
        print("📋 Paso 4: Procesando datos y comparando precios...")
        response_products = []
        
        # Crear diccionario por mfrid+partNumber concatenado para búsqueda de productos con error
        products_by_concat = {}
        for product in request.data.products:
            concat_key = f"{product.mfrid}{product.partNumber}"
            products_by_concat[concat_key] = product
        
        for item in scraped_data:
            # Agregar po_number para la base de datos
            item['po_number'] = request.data.poNumber
            
            part_number = item['part_number']
            supplier_price = item.get('your_price')  # Precio del supplier (scraping)
            
            # Buscar el ideal_cost y mfrid correcto
            ideal_cost = ideal_costs.get(part_number, 0.0)
            
            # Si mfrid está vacío Y no encontramos ideal_cost, buscar por concatenado
            if not item['mfrid'] and ideal_cost == 0.0:
                # Intentar buscar en el diccionario concatenado
                matched_product = products_by_concat.get(part_number)
                if matched_product:
                    item['mfrid'] = matched_product.mfrid
                    ideal_cost = matched_product.idealCost
                    print(f"🔍 Match por concatenado {part_number}: MFRID={matched_product.mfrid}, Cost=${ideal_cost}")
            
            # Si aún no hay ideal_cost, buscar por partNumber directo
            if ideal_cost == 0.0:
                for product in request.data.products:
                    if product.partNumber == part_number:
                        if not item['mfrid']:
                            item['mfrid'] = product.mfrid
                        ideal_cost = product.idealCost
                        print(f"🔍 Match por partNumber {part_number}: MFRID={product.mfrid}, Cost=${ideal_cost}")
                        break
            
            # Asegurar que ideal_cost nunca sea None para la BD
            item['ideal_cost'] = ideal_cost if ideal_cost > 0 else 0.0
            
            # Asegurar que supplier_price nunca sea None, usar 0.0
            if supplier_price is None:
                supplier_price = 0.0
            
            # Determinar status: CORRECT o MISMATCH
            status = "CORRECT"
            if item['status'] == 'PART_ERROR':
                # Si ya tiene error del scraping, mantener como error
                status = "MISMATCH"
                item['status'] = 'PART_ERROR'  # Mantener en BD como PART_ERROR
            elif supplier_price > 0 and ideal_cost > 0:
                # Comparar precios (tolerancia de 1%)
                difference = abs(ideal_cost - float(supplier_price))
                tolerance = ideal_cost * 0.01
                
                print(f"💵 Comparando {part_number}: Ideal=${ideal_cost:.2f}, Supplier=${float(supplier_price):.2f}, Diff=${difference:.2f}, Tol=${tolerance:.2f}")
                
                if difference > tolerance:
                    status = "MISMATCH"
                    item['status'] = 'PRICE_MISMATCH'
                    item['error_message'] = f"Price mismatch: Expected ${ideal_cost:.2f}, Supplier ${float(supplier_price):.2f}"
                    print(f"💰 ❌ MISMATCH - Diferencia de precio detectada en {part_number}: Ideal=${ideal_cost:.2f}, Supplier=${float(supplier_price):.2f}")
                else:
                    # Precios coinciden
                    item['status'] = 'CORRECT'
                    print(f"✅ CORRECT - Precios coinciden para {part_number}")
            elif supplier_price == 0 and ideal_cost > 0:
                # No hay precio del supplier pero sí ideal cost
                status = "MISMATCH"
                item['status'] = 'PART_ERROR'
                if not item.get('error_message'):
                    item['error_message'] = f"No supplier price available for {part_number}"
                print(f"⚠️ PART_ERROR - Sin precio del supplier para {part_number}")
            
            # Crear objeto de respuesta SOLO si tiene supplierPrice válido (productos correctos)
            if supplier_price > 0:
                response_product = PurchaseOrderResponseProduct(
                    mfrid=item['mfrid'],
                    partNumber=part_number,
                    qty=item['qty'],
                    idealCost=ideal_cost if ideal_cost > 0 else 0.0,
                    supplierPrice=float(supplier_price),
                    status=status
                )
                response_products.append(response_product)
        
        # 6. Guardar los datos en la base de datos
        print("💾 Paso 5: Guardando datos en la base de datos...")
        inserted_count = insert_po_review_details(scraped_data)
        
        print(f"✅ {inserted_count} filas insertadas en po_review_details")
        
        # 7. Crear respuesta exitosa
        print("📋 Paso 6: Generando respuesta...")
        
        response_data = PurchaseOrderResponseData(
            poNumber=request.data.poNumber,
            supplerID=request.data.supplerID,
            products=response_products
        )
        
        response = ResponseBlogModel(
            chunkId=request.chunkId,
            item=response_data,
            status="success"
        )
        
        # 8. Enviar respuesta al chunk API
        print("📤 Paso 7: Enviando respuesta al TaskHub...")
        chunk_response = register_chunk_item(response.dict())
        
        print(f"✅ Respuesta del chunk API: {chunk_response}")
        
        print("✅ Proceso completado exitosamente")
        print(f"📊 Total productos: {len(response_products)}")
        print(f"✅ Status CORRECT: {sum(1 for p in response_products if p.status == 'CORRECT')}")
        print(f"⚠️ Status MISMATCH: {sum(1 for p in response_products if p.status == 'MISMATCH')}")
        
        return response.dict()
        
    except Exception as e:
        print(f"❌ Error durante la automatización: {e}")
        
        # Crear respuesta de error
        response = ResponseBlogModel(
            chunkId=request.chunkId,
            item=PurchaseOrderResponseData(
                poNumber=request.data.poNumber if request.data else "unknown",
                supplerID=request.data.supplerID if request.data else "unknown",
                products=[]
            ),
            status="failed"
        )
        
        # Intentar enviar el error al chunk API
        try:
            register_chunk_item(response.dict())
        except Exception as chunk_error:
            print(f"⚠️ No se pudo enviar error al chunk API: {chunk_error}")
        
        return response.dict()
        
    finally:
        # 9. Limpiar: Eliminar el archivo CSV temporal
        print("🧹 Paso final: Limpiando archivos temporales...")
        if csv_path and os.path.exists(csv_path):
            delete_csv_file(csv_path)
        
        # Eliminar también el archivo de Downloads si existe
        if final_csv_path and os.path.exists(final_csv_path):
            delete_csv_file(final_csv_path)
        
        print("="*60)
        print("✅ PROCESO FINALIZADO")
        print("="*60)
