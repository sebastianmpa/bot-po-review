"""
Script para hacer scraping de los resultados de Quick Order en Gardner Inc.
Extrae la información de la tabla después de cargar el CSV y la guarda en la base de datos.
"""

from playwright.sync_api import sync_playwright, Page
import time
import logging
from typing import List, Dict, Optional
from decimal import Decimal

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_table_data(page: Page, po_number: str) -> List[Dict]:
    """
    Extrae todos los datos de la tabla Quick Order después de cargar el CSV.
    
    Args:
        page: Página de Playwright
        po_number: Número de orden de compra
        
    Returns:
        Lista de diccionarios con los datos de cada fila
    """
    results = []
    
    try:
        # Esperar a que la tabla esté presente
        page.wait_for_selector('.q-table', timeout=10000)
        logger.info("Tabla Quick Order encontrada")
        
        # Obtener todas las filas de la tabla (excluyendo header y botones)
        rows = page.locator('.q-table-row').all()
        logger.info(f"Total de filas encontradas: {len(rows)}")
        
        for idx, row in enumerate(rows):
            try:
                # Obtener las clases de la fila para determinar si tiene error
                row_class = row.get_attribute('class') or ''
                has_error = 'q-table-row--error' in row_class
                
                # Extraer MFR Code (col-1 - select option selected)
                mfr_select = row.locator('.q-col-1 select option[selected], .q-col-1 select option:first-child')
                mfr_value = mfr_select.get_attribute('value') if mfr_select.count() > 0 else ''
                
                # Si no hay value o es vacío, intentar obtener del input
                if not mfr_value or mfr_value == '':
                    # Verificar si es una fila de error sin manufacturer seleccionado
                    if has_error:
                        # En filas de error, el manufacturer puede no estar seleccionado
                        mfr_value = ''
                    else:
                        # En filas normales, obtener el valor seleccionado
                        selected_option = row.locator('.q-col-1 select').evaluate('el => el.value')
                        mfr_value = selected_option if selected_option else ''
                
                # Extraer Part Number (col-2 - input value)
                part_number_input = row.locator('.q-col-2 input')
                part_number = part_number_input.get_attribute('value') if part_number_input.count() > 0 else ''
                
                # Extraer Quantity (col-3 - input value)
                qty_input = row.locator('.q-col-3 input')
                qty_str = qty_input.get_attribute('value') if qty_input.count() > 0 else '0'
                qty = int(qty_str) if qty_str.isdigit() else 0
                
                # Extraer Item Name (col-4 - p text)
                item_name_elem = row.locator('.q-col-4 p')
                item_name = item_name_elem.inner_text() if item_name_elem.count() > 0 else ''
                
                # Extraer List Price (col-5 - p text)
                list_price_elem = row.locator('.q-col-5 p')
                list_price_text = list_price_elem.inner_text() if list_price_elem.count() > 0 else ''
                list_price = parse_price(list_price_text)
                
                # Extraer Your Price (col-6 - p text)
                your_price_elem = row.locator('.q-col-6 p')
                your_price_text = your_price_elem.inner_text() if your_price_elem.count() > 0 else ''
                your_price = parse_price(your_price_text)
                
                # Extraer Available (col-7 - div text)
                available_elem = row.locator('.q-col-7 div')
                available_text = available_elem.inner_text().strip() if available_elem.count() > 0 else '0'
                available = int(available_text) if available_text.isdigit() else 0
                
                # Determinar el status y error message
                if has_error:
                    if item_name == 'Product Not Found' or not item_name:
                        status = 'PART_ERROR'
                        error_message = f"Product not found: {part_number}"
                    elif available == 0:
                        status = 'OUT_OF_STOCK'
                        error_message = f"Product out of stock: {part_number}"
                    else:
                        status = 'PART_ERROR'
                        error_message = f"Error with part: {part_number}"
                else:
                    if available == 0:
                        status = 'OUT_OF_STOCK'
                        error_message = f"Product out of stock: {part_number}"
                    else:
                        status = 'CORRECT'
                        error_message = None
                
                # Solo agregar filas que tienen part_number
                if part_number:
                    row_data = {
                        'po_number': po_number,
                        'mfrid': mfr_value,
                        'part_number': part_number,
                        'qty': qty,
                        'item_name': item_name,
                        'list_price': list_price,
                        'your_price': your_price,
                        'available': available,
                        'status': status,
                        'error_message': error_message
                    }
                    
                    results.append(row_data)
                    logger.info(f"Fila {idx}: {part_number} - Status: {status}")
                
            except Exception as e:
                logger.error(f"Error procesando fila {idx}: {e}")
                continue
        
        logger.info(f"Total de filas procesadas: {len(results)}")
        return results
        
    except Exception as e:
        logger.error(f"Error extrayendo datos de la tabla: {e}")
        return []


def parse_price(price_text: str) -> Optional[Decimal]:
    """
    Convierte texto de precio a Decimal.
    Ejemplo: "$23.07" -> Decimal("23.07")
    
    Args:
        price_text: Texto del precio (ej: "$23.07")
        
    Returns:
        Decimal o None si no se puede parsear
    """
    try:
        # Remover $ y espacios
        clean_price = price_text.replace('$', '').replace(',', '').strip()
        if clean_price:
            return Decimal(clean_price)
        return None
    except Exception:
        return None


def scrape_gardner_results(page: Page, po_number: str) -> List[Dict]:
    """
    Función principal para hacer scraping de los resultados después de cargar el CSV.
    
    Args:
        page: Página de Playwright ya posicionada en Quick Order
        po_number: Número de orden de compra
        
    Returns:
        Lista de diccionarios con los datos extraídos
    """
    logger.info("Esperando 7 segundos para que la tabla se cargue completamente...")
    time.sleep(7)
    
    logger.info("Iniciando extracción de datos de la tabla...")
    results = extract_table_data(page, po_number)
    
    logger.info(f"Scraping completado. {len(results)} filas extraídas.")
    return results


def scrape_gardner_results_standalone(po_number: str, email: str, password: str, csv_filename: str) -> List[Dict]:
    """
    Función standalone para testing - hace todo el proceso completo.
    
    Args:
        po_number: Número de orden de compra
        email: Email para login
        password: Password para login
        csv_filename: Nombre del archivo CSV a cargar
        
    Returns:
        Lista de diccionarios con los datos extraídos
    """
    with sync_playwright() as p:
        # Iniciar navegador
        browser = p.chromium.launch(channel="msedge", headless=False)
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        
        try:
            # Navegar y login
            logger.info("Navegando a Gardner Inc...")
            page.goto("https://www.gardnerinc.com/")
            page.wait_for_load_state('networkidle')
            
            # Login
            logger.info("Haciendo login...")
            page.locator('button:has-text("Sign In")').first.click()
            page.wait_for_selector('input[type="email"]', timeout=10000)
            
            page.fill('input[type="email"]', email)
            page.fill('input[type="password"]', password)
            page.locator('button:has-text("Sign in")').click()
            page.wait_for_load_state('networkidle')
            time.sleep(3)
            
            # Navegar a Quick Order
            logger.info("Navegando a Quick Order...")
            page.goto("https://www.gardnerinc.com/quick-order")
            page.wait_for_load_state('networkidle')
            time.sleep(2)
            
            # Hacer click en Open File Dialog
            logger.info("Abriendo diálogo de archivo...")
            page.locator('button:has-text("Open File Dialog")').click()
            time.sleep(1)
            
            # Usar pynput para escribir la ruta del archivo
            from pynput.keyboard import Controller, Key
            keyboard = Controller()
            
            csv_path = f"C:\\Users\\{os.getlogin()}\\Downloads\\{csv_filename}"
            logger.info(f"Escribiendo ruta del archivo: {csv_path}")
            
            time.sleep(0.5)
            keyboard.type(csv_path)
            time.sleep(0.5)
            keyboard.press(Key.enter)
            keyboard.release(Key.enter)
            
            # Scrape results
            results = scrape_gardner_results(page, po_number)
            
            return results
            
        finally:
            browser.close()


if __name__ == "__main__":
    # Testing standalone
    import os
    
    test_po = "20260041"
    test_email = "jacobn.prontomowers+75145@gmail.com"
    test_password = "Pronto123#"
    test_csv = "PO_20260041_GA_20260522_123456.csv"
    
    results = scrape_gardner_results_standalone(test_po, test_email, test_password, test_csv)
    
    print(f"\n{'='*80}")
    print(f"RESULTADOS DEL SCRAPING - PO: {test_po}")
    print(f"{'='*80}\n")
    
    for idx, item in enumerate(results, 1):
        print(f"{idx}. {item['mfrid']} - {item['part_number']}")
        print(f"   Qty: {item['qty']} | List: ${item['list_price']} | Your Price: ${item['your_price']}")
        print(f"   Available: {item['available']} | Status: {item['status']}")
        if item['error_message']:
            print(f"   Error: {item['error_message']}")
        print()
