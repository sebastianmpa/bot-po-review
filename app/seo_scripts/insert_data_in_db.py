"""
Script para insertar los datos de scraping en la base de datos po_review_details.
"""

import json
import logging
from typing import List, Dict, Optional
from decimal import Decimal
import sys
import os

# Agregar el directorio padre al path para imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.bd_mysql import get_db_connection, get_pg_connection

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def lookup_product_in_databases(part_number: str, mfrid: Optional[str] = None) -> Dict:
    """
    Busca un part_number en prontoweb.product (MySQL ERP).
    Devuelve un dict con pack_qty si lo encuentra, {} si no.
    """
    result: Dict = {}

    # ── MySQL (ERP prontoweb.product) ─────────────────────────────
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        query = """
            SELECT
                PARTNUMBER      AS part_number,
                MFRID           AS mfrid,
                DESCRIPTION     AS description,
                PACKAGEQUANTITY AS pack_qty
            FROM prontoweb.product
            WHERE PARTNUMBER = %s
        """
        params = [part_number]
        if mfrid:
            query += " AND MFRID = %s"
            params.append(mfrid)
        query += " LIMIT 1"
        cur.execute(query, params)
        row = cur.fetchone()
        if row:
            result.update({k: v for k, v in row.items() if v is not None})
            logger.info(f"🔍 MySQL ERP: encontrado '{part_number}' → pack_qty={row.get('pack_qty')}")
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"⚠️  MySQL lookup falló para '{part_number}': {e}")

    if not result:
        logger.debug(f"  ∅  Sin datos en MySQL para '{part_number}'")

    return result


def lookup_crossovers_and_packs(mfrid: str, part_number: str) -> Dict:
    """
    Busca crossovers y packs en PostgreSQL para un part dado.
    Devuelve {'crossover': [...] | None, 'pack_codes': [...] | None}
    """
    result: Dict = {'crossover': None, 'pack_codes': None}

    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        # ── Crossovers ────────────────────────────────────────────
        cur.execute(
            """
            SELECT mfr_cross, partnumber_cross, priority, notes
            FROM product_crossover
            WHERE mfr = %s AND partnumber = %s
            ORDER BY priority
            """,
            (mfrid, part_number)
        )
        rows = cur.fetchall()
        if rows:
            result['crossover'] = [
                {
                    'mfr':        row[0],
                    'partnumber': row[1],
                    'priority':   row[2],
                    'notes':      row[3] or ''
                }
                for row in rows
            ]

        # ── Packs ─────────────────────────────────────────────────
        cur.execute(
            """
            SELECT mfr_pack, partnumber_pack, pack_qty, notes
            FROM product_packs
            WHERE mfr = %s AND partnumber = %s
            """,
            (mfrid, part_number)
        )
        rows = cur.fetchall()
        if rows:
            result['pack_codes'] = [
                {
                    'mfr':        row[0],
                    'partnumber': row[1],
                    'pack_qty':   row[2],
                    'notes':      row[3] or ''
                }
                for row in rows
            ]

        cur.close()
        conn.close()

        n_cross = len(result['crossover'] or [])
        n_packs = len(result['pack_codes'] or [])
        if n_cross or n_packs:
            logger.info(
                f"🔗 '{mfrid}/{part_number}': "
                f"{n_cross} crossover(s), {n_packs} pack(s)"
            )

    except Exception as e:
        logger.warning(
            f"⚠️  Crossover/Pack lookup falló para '{mfrid}/{part_number}': {e}"
        )

    return result


def fetch_pack_codes_for_po(po_products) -> Dict[tuple, List[Dict]]:
    """
    Pre-consulta product_packs en PostgreSQL para TODOS los productos de la PO
    ANTES de ejecutar la automatización, de forma que los PN de packs puedan
    incluirse en el CSV/automation para obtener sus costos del portal.

    Retorna {(mfrid_upper, pn_upper): [pack_entry_dicts]}.
    Cada pack_entry_dict tiene las claves: mfr, partnumber, pack_qty, notes.
    El campo 'cost' se añade después de la automatización por _apply_pack_costs_and_clean.
    """
    pack_map: Dict[tuple, List[Dict]] = {}
    try:
        conn = get_pg_connection()
        cur = conn.cursor()
        for p in po_products:
            # Soporta tanto PurchaseOrderItemModel como dict
            if isinstance(p, dict):
                mfrid = (p.get('mfrid') or '').strip()
                pn = (p.get('partNumber') or p.get('part_number') or '').strip()
            else:
                mfrid = (getattr(p, 'mfrid', '') or '').strip()
                pn = (getattr(p, 'partNumber', '') or '').strip()
            if not pn:
                continue
            key = (mfrid.upper(), pn.upper())
            try:
                cur.execute(
                    """
                    SELECT mfr_pack, partnumber_pack, pack_qty, notes
                    FROM product_packs
                    WHERE mfr = %s AND partnumber = %s
                    """,
                    (mfrid, pn),
                )
                rows = cur.fetchall()
                if rows:
                    pack_map[key] = [
                        {
                            'mfr':        r[0],
                            'partnumber': r[1],
                            'pack_qty':   r[2],
                            'notes':      r[3] or '',
                        }
                        for r in rows
                    ]
                    logger.info(
                        f"📦 Pre-fetch packs: {mfrid}/{pn} → "
                        f"{len(rows)} pack(s) encontrados."
                    )
            except Exception as _e:
                logger.warning(f"⚠️ Pack pre-fetch fail {mfrid}/{pn}: {_e}")
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"⚠️ fetch_pack_codes_for_po falló: {e}")
    return pack_map


def enrich_ltl_from_db(scraped_data: List[Dict]) -> int:
    """
    Post-scraping: busca cada ítem en prontoweb.shipping_ltl por (mfrid, partnumber).
    Si hay match, marca ltl='Y' en el item del scraped_data.
    No sobreescribe si ltl ya está en 'Y' (ej: Husqvarna detectó LTL via checkout).
    Aplica a TODOS los proveedores.
    Retorna el número de ítems marcados en este paso.

    Mapa de normalización de mfrid hacia los valores usados en shipping_ltl:
      BRS → BS   (Briggs & Stratton: el scraping devuelve 'BRS' pero la tabla usa 'BS')
    """
    if not scraped_data:
        return 0

    # mfrid que llega del scraping → mfrid real en shipping_ltl
    MFRID_NORMALIZE = {
        'BRS': 'BS',
    }

    marked = 0
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        # Recopilar pares (mfrid_normalizado, partnumber) únicos con datos válidos
        # Guardamos también un mapa de vuelta para poder hacer el match contra scraped_data
        # item_mfrid_up → mfrid_ltl_up  (para el lookup del set de resultados)
        pairs = []
        for item in scraped_data:
            raw_mfrid = (item.get('mfrid') or '').strip()
            pn        = (item.get('part_number') or '').strip()
            if raw_mfrid and pn:
                # Normalizar hacia el valor que usa shipping_ltl
                ltl_mfrid = MFRID_NORMALIZE.get(raw_mfrid.upper(), raw_mfrid)
                pairs.append((ltl_mfrid, pn))

        if not pairs:
            cur.close()
            conn.close()
            return 0

        # Una sola query batch para todos los pares
        conditions = ' OR '.join(['(mfrid = %s AND partnumber = %s)'] * len(pairs))
        params     = [val for pair in pairs for val in pair]
        query = f"""
            SELECT mfrid, partnumber
            FROM prontoweb.shipping_ltl
            WHERE {conditions}
        """
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return 0

        # Set de tuplas upper para lookup O(1)
        ltl_set = {(r['mfrid'].upper(), r['partnumber'].upper()) for r in rows}

        for item in scraped_data:
            raw_mfrid = (item.get('mfrid') or '').strip()
            pn_up     = (item.get('part_number') or '').strip().upper()
            # Normalizar mfrid igual que hicimos para la query
            ltl_mfrid_up = MFRID_NORMALIZE.get(raw_mfrid.upper(), raw_mfrid).upper()
            if (ltl_mfrid_up, pn_up) in ltl_set:
                if item.get('ltl') != 'Y':
                    item['ltl'] = 'Y'
                    marked += 1
                    logger.info(
                        f"🚛 LTL desde BD shipping_ltl: "
                        f"{raw_mfrid}(→{ltl_mfrid_up})/{item.get('part_number')}"
                    )

    except Exception as e:
        logger.warning(f"⚠️ enrich_ltl_from_db falló: {e}")

    return marked


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
        
        # Query de inserción — ON DUPLICATE KEY UPDATE para tolerar re-ejecuciones
        insert_query = """
        INSERT INTO po_review_details 
        (po_number, mfrid, mfrid_orig, part_number, partnumber_orig, qty, ideal_cost, supplier_price, available_qty, in_stock, status, error_message, superseded_from, nla, pack_qty, ltl, supplier_code, pack_codes, crossover, supplier_list_price)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            mfrid_orig         = VALUES(mfrid_orig),
            partnumber_orig    = VALUES(partnumber_orig),
            qty                = VALUES(qty),
            ideal_cost         = VALUES(ideal_cost),
            supplier_price     = VALUES(supplier_price),
            available_qty      = VALUES(available_qty),
            in_stock           = VALUES(in_stock),
            status             = VALUES(status),
            error_message      = VALUES(error_message),
            superseded_from    = VALUES(superseded_from),
            nla                = VALUES(nla),
            pack_qty           = VALUES(pack_qty),
            ltl                = VALUES(ltl),
            supplier_code      = VALUES(supplier_code),
            pack_codes         = VALUES(pack_codes),
            crossover          = VALUES(crossover),
            supplier_list_price = VALUES(supplier_list_price)
        """
        
        # Insertar cada fila
        for item in scraped_data:
            try:
                # ── Enriquecer con datos de las BDs ──────────────────────────────
                part_number_lookup = item.get('part_number', '')
                mfrid_lookup = item.get('mfrid', '')
                supplier_code = item.get('supplier_code', '')
                crossover_json = None
                pack_codes_json = None

                # Mapeo supplier_code → MFRID preferido en PostgreSQL
                SUPPLIER_MFRID_MAP = {
                    'HU': 'HUS',   # Husqvarna
                    'SP': 'BRS',   # Briggs & Stratton
                    'GA': 'HST',   # Gardner (Hustler)
                    'FO': 'WALB',  # Florida Outdoor (Walbro)
                }

                if part_number_lookup:
                    # MySQL ERP: pack_qty fallback + MFRID resolution
                    db_data = lookup_product_in_databases(part_number_lookup, mfrid_lookup)
                    if db_data:
                        enriched = []
                        if item.get('pack_qty') is None and db_data.get('pack_qty'):
                            item['pack_qty'] = db_data['pack_qty']
                            enriched.append(f"pack_qty={db_data['pack_qty']}")
                        if enriched:
                            logger.info(f"✨ Enriquecido '{part_number_lookup}': {', '.join(enriched)}")

                    # Resolver MFRID para lookup en PostgreSQL:
                    # Prioridad: 1) item.mfrid  2) supplier_code map  3) ERP fallback
                    if not mfrid_lookup:
                        mfrid_lookup = SUPPLIER_MFRID_MAP.get(supplier_code, '')
                        if mfrid_lookup:
                            logger.debug(f"  🏷️  MFRID desde supplier_map: '{mfrid_lookup}' ({supplier_code}) para '{part_number_lookup}'")
                        elif db_data and db_data.get('mfrid'):
                            mfrid_lookup = db_data['mfrid']
                            logger.info(f"  🏷️  MFRID resuelto desde ERP: '{mfrid_lookup}' para '{part_number_lookup}'")

                    # PostgreSQL: crossovers (siempre) + packs
                    # Si pack_codes ya fue pre-fetched y enriquecido con 'cost' antes
                    # de la automatización, usar esos directamente. Si no, consultar BD.
                    if item.get('pack_codes') is not None:
                        pack_codes_json = json.dumps(item['pack_codes'])
                        # Crossovers se consultan siempre independientemente
                        if mfrid_lookup:
                            cp_data = lookup_crossovers_and_packs(
                                mfrid_lookup, part_number_lookup
                            )
                            if cp_data['crossover']:
                                crossover_json = json.dumps(cp_data['crossover'])
                    elif mfrid_lookup:
                        cp_data = lookup_crossovers_and_packs(mfrid_lookup, part_number_lookup)
                        if cp_data['crossover']:
                            crossover_json = json.dumps(cp_data['crossover'])
                        if cp_data['pack_codes']:
                            pack_codes_json = json.dumps(cp_data['pack_codes'])

                # ── Mapear status al valor aceptado por la columna BD ────────────
                # La columna 'status' acepta: CORRECT, PART_ERROR, SUPERSEDED, PRICE_MISMATCH
                raw_status = item['status']
                status_map = {
                    'MISMATCH': 'PRICE_MISMATCH',
                    'NLA':      'PART_ERROR',
                }
                db_status = status_map.get(raw_status, raw_status)
                # Usar ideal_cost del item si existe, sino list_price del scraping
                ideal_cost_value = item.get('ideal_cost') if 'ideal_cost' in item else item.get('list_price')
                if ideal_cost_value is None:
                    ideal_cost_value = 0.0  # Default a 0.0 si no hay valor
                
                # Usar tiered_price (precio unitario) si existe — Husqvarna lo provee.
                # Para los demás suppliers que no tienen tiered_price, usar your_price.
                # Si ninguno existe, usar 0.0.
                supplier_price_value = item.get('tiered_price')
                if supplier_price_value is None:
                    supplier_price_value = item.get('your_price')
                if supplier_price_value is None:
                    supplier_price_value = 0.0

                # Usar qty_available del scraping, si no existe usar 0
                available_qty_value = item.get('qty_available', 0)

                # in_stock: 'Y'/'N' — si no viene del scraper, derivar de qty_available
                in_stock_value = item.get('in_stock')
                if in_stock_value is None:
                    in_stock_value = 'Y' if available_qty_value > 0 else 'N'
                
                # supplier_list_price: list_price del scraper (SRP en Wesco, List en otros).
                # Husqvarna y CP no tienen este dato aun → None.
                supplier_list_price_value = item.get('list_price')
                if supplier_list_price_value is not None:
                    supplier_list_price_value = float(supplier_list_price_value)

                # Preparar valores para inserción
                values = (
                    item['po_number'],
                    item.get('mfrid'),
                    item.get('mfrid_orig'),
                    item['part_number'],
                    item.get('partnumber_orig') or item['part_number'],  # partnumber_orig
                    item['qty'],
                    ideal_cost_value,
                    supplier_price_value,
                    available_qty_value,
                    in_stock_value,
                    db_status,
                    item.get('error_message'),
                    item.get('superseded_from'),
                    item.get('nla'),           # "Y" o None
                    item.get('pack_qty'),      # int o None
                    item.get('ltl'),           # "Y" o None
                    item.get('supplier_code'), # GA, HU, SP, FO, CP ...
                    pack_codes_json,           # JSON array o None
                    crossover_json,            # JSON array o None
                    supplier_list_price_value, # list_price del scraper (SRP/List)
                )
                
                # Ejecutar inserción
                cursor.execute(insert_query, values)
                inserted_count += 1
                
                logger.info(
                    f"Insertado: PO={item['po_number']} | "
                    f"Part={item['part_number']} | Status={db_status}"
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
