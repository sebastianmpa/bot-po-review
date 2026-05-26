"""
Script para insertar los datos de scraping en la base de datos po_review_details.
"""

import logging
from typing import List, Dict
from decimal import Decimal
import sys
import os

# Agregar el directorio padre al path para imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.bd_mysql import get_db_connection

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def insert_po_review_details(scraped_data: List[Dict]) -> int:
    """
    Inserta los datos de scraping en la tabla po_review_details.
    
    Args:
        scraped_data: Lista de diccionarios con los datos extraídos del scraping
        
    Returns:
        Número de filas insertadas exitosamente
    """
    if not scraped_data:
        logger.warning("No hay datos para insertar")
        return 0
    
    connection = None
    cursor = None
    inserted_count = 0
    
    try:
        # Obtener conexión a la base de datos
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Query de inserción
        insert_query = """
        INSERT INTO po_review_details 
        (po_number, mfrid, part_number, qty, ideal_cost, supplier_price, available_qty, status, error_message, superseded_from)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        # Insertar cada fila
        for item in scraped_data:
            try:
                # Usar ideal_cost del item si existe, sino list_price del scraping
                ideal_cost_value = item.get('ideal_cost') if 'ideal_cost' in item else item.get('list_price')
                if ideal_cost_value is None:
                    ideal_cost_value = 0.0  # Default a 0.0 si no hay valor
                
                # Usar your_price, si es None usar 0.0
                supplier_price_value = item.get('your_price')
                if supplier_price_value is None:
                    supplier_price_value = 0.0

                # Usar available del scraping, si es None usar 0
                available_qty_value = item.get('available')
                if available_qty_value is None:
                    available_qty_value = 0
                
                # Preparar valores para inserción
                values = (
                    item['po_number'],
                    item['mfrid'],
                    item['part_number'],
                    item['qty'],
                    ideal_cost_value,  # ideal_cost del servicio o list_price del scraping
                    supplier_price_value,  # supplier_price = Your Price o 0.0
                    available_qty_value,  # available_qty = Available del scraping o 0
                    item['status'],
                    item.get('error_message'),
                    item.get('superseded_from')  # Parte original antes del reemplazo del proveedor
                )
                
                # Ejecutar inserción
                cursor.execute(insert_query, values)
                inserted_count += 1
                
                logger.info(
                    f"Insertado: PO={item['po_number']} | "
                    f"Part={item['part_number']} | Status={item['status']}"
                )
                
            except Exception as e:
                logger.error(
                    f"Error insertando parte {item.get('part_number', 'unknown')}: {e}"
                )
                continue
        
        # Commit de todas las inserciones
        connection.commit()
        logger.info(f"✅ {inserted_count} filas insertadas exitosamente en po_review_details")
        
        return inserted_count
        
    except Exception as e:
        logger.error(f"Error en la conexión o inserción a la base de datos: {e}")
        if connection:
            connection.rollback()
        return 0
        
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_po_review_summary(po_number: str) -> Dict:
    """
    Obtiene un resumen del estado de una orden de compra.
    
    Args:
        po_number: Número de orden de compra
        
    Returns:
        Diccionario con el resumen (total, correctos, errores, etc.)
    """
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # Query para obtener resumen por status
        query = """
        SELECT 
            status,
            COUNT(*) as count,
            SUM(qty) as total_qty,
            SUM(supplier_price * qty) as total_amount
        FROM po_review_details
        WHERE po_number = %s
        GROUP BY status
        """
        
        cursor.execute(query, (po_number,))
        results = cursor.fetchall()
        
        # Construir resumen
        summary = {
            'po_number': po_number,
            'total_items': 0,
            'total_qty': 0,
            'total_amount': 0,
            'correct': 0,
            'part_error': 0,
            'out_of_stock': 0,
            'price_mismatch': 0
        }
        
        for row in results:
            status = row['status']
            count = row['count']
            qty = row['total_qty'] or 0
            amount = float(row['total_amount'] or 0)
            
            summary['total_items'] += count
            summary['total_qty'] += qty
            summary['total_amount'] += amount
            
            if status == 'CORRECT':
                summary['correct'] = count
            elif status == 'PART_ERROR':
                summary['part_error'] = count
            elif status == 'OUT_OF_STOCK':
                summary['out_of_stock'] = count
            elif status == 'PRICE_MISMATCH':
                summary['price_mismatch'] = count
        
        return summary
        
    except Exception as e:
        logger.error(f"Error obteniendo resumen de PO {po_number}: {e}")
        return {}
        
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def check_price_mismatch(po_number: str, ideal_cost_data: Dict[str, Decimal]) -> int:
    """
    Compara los precios de supplier con los precios ideales y actualiza el status.
    
    Args:
        po_number: Número de orden de compra
        ideal_cost_data: Diccionario {part_number: ideal_cost} con los costos esperados
        
    Returns:
        Número de filas actualizadas con PRICE_MISMATCH
    """
    connection = None
    cursor = None
    updated_count = 0
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # Obtener todas las filas CORRECT de este PO
        select_query = """
        SELECT id, part_number, ideal_cost, supplier_price
        FROM po_review_details
        WHERE po_number = %s AND status = 'CORRECT'
        """
        
        cursor.execute(select_query, (po_number,))
        rows = cursor.fetchall()
        
        # Query de actualización
        update_query = """
        UPDATE po_review_details
        SET status = 'PRICE_MISMATCH',
            error_message = %s
        WHERE id = %s
        """
        
        for row in rows:
            part_number = row['part_number']
            supplier_price = row['supplier_price']
            ideal_cost = ideal_cost_data.get(part_number)
            
            if ideal_cost and supplier_price:
                # Si hay diferencia de precio (más del 1% de diferencia)
                difference = abs(float(ideal_cost) - float(supplier_price))
                tolerance = float(ideal_cost) * 0.01  # 1% de tolerancia
                
                if difference > tolerance:
                    error_msg = (
                        f"Price mismatch: Expected ${ideal_cost}, "
                        f"Supplier price ${supplier_price}"
                    )
                    cursor.execute(update_query, (error_msg, row['id']))
                    updated_count += 1
                    logger.info(f"Price mismatch detected for {part_number}")
        
        connection.commit()
        logger.info(f"✅ {updated_count} filas actualizadas con PRICE_MISMATCH")
        
        return updated_count
        
    except Exception as e:
        logger.error(f"Error verificando price mismatch: {e}")
        if connection:
            connection.rollback()
        return 0
        
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


if __name__ == "__main__":
    # Testing con datos de ejemplo
    test_data = [
        {
            'po_number': '20260041',
            'mfrid': 'TUF',
            'part_number': '24311000120',
            'qty': 1,
            'list_price': Decimal('2.16'),
            'your_price': Decimal('1.47'),
            'available': 135,
            'status': 'CORRECT',
            'error_message': None
        },
        {
            'po_number': '20260041',
            'mfrid': '',
            'part_number': 'ORG95-626',
            'qty': 1,
            'list_price': None,
            'your_price': None,
            'available': 0,
            'status': 'PART_ERROR',
            'error_message': 'Product not found: ORG95-626'
        },
        {
            'po_number': '20260041',
            'mfrid': 'MTD',
            'part_number': '953-08728',
            'qty': 1,
            'list_price': Decimal('22.49'),
            'your_price': Decimal('14.64'),
            'available': 0,
            'status': 'OUT_OF_STOCK',
            'error_message': 'Product out of stock: 953-08728'
        }
    ]
    
    print("Testing inserción en base de datos...")
    inserted = insert_po_review_details(test_data)
    print(f"\n{inserted} filas insertadas\n")
    
    if inserted > 0:
        print("Obteniendo resumen de la orden...")
        summary = get_po_review_summary('20260041')
        
        print(f"\n{'='*60}")
        print(f"RESUMEN DE ORDEN: {summary['po_number']}")
        print(f"{'='*60}")
        print(f"Total Items: {summary['total_items']}")
        print(f"Total Qty: {summary['total_qty']}")
        print(f"Total Amount: ${summary['total_amount']:.2f}")
        print(f"\nStatus Breakdown:")
        print(f"  ✅ Correct: {summary['correct']}")
        print(f"  ❌ Part Error: {summary['part_error']}")
        print(f"  📦 Out of Stock: {summary['out_of_stock']}")
        print(f"  💰 Price Mismatch: {summary['price_mismatch']}")
        print(f"{'='*60}\n")
