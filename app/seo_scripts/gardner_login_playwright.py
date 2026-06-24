from playwright.sync_api import sync_playwright, expect, Page
from pynput.keyboard import Key, Controller
import time
import os
from typing import List, Dict, Optional
from decimal import Decimal


def parse_price(price_text: str) -> Optional[Decimal]:
    """
    Convierte texto de precio a Decimal.
    Ejemplo: "$23.07" -> Decimal("23.07")
    """
    try:
        clean_price = price_text.replace('$', '').replace(',', '').strip()
        if clean_price:
            return Decimal(clean_price)
        return None
    except Exception:
        return None

def extract_table_data(page: Page) -> List[Dict]:
    """
    Extrae todos los datos de la tabla Quick Order después de cargar el CSV.
    ✅ Procesa TODOS los items, incluidos duplicados (sin deduplicación).
    fix_concatenated_part_numbers() debe haber limpiado los MFRID concatenados ANTES.
    """
    results = []
    
    try:
        # Esperar a que la tabla esté presente
        page.wait_for_selector('.q-table', timeout=10000)
        print("📊 Tabla Quick Order encontrada")
        
        # Obtener todas las filas de la tabla (excluyendo header y botones)
        rows = page.locator('.q-table-row').all()
        print(f"📋 Total de filas encontradas: {len(rows)}")
        
        for idx, row in enumerate(rows):
            try:
                # Obtener las clases de la fila para determinar si tiene error
                row_class = row.get_attribute('class') or ''
                has_error = 'q-table-row--error' in row_class
                
                # Debug: mostrar contenido de la fila
                row_text = row.inner_text()
                print(f"  DEBUG Fila {idx}: {row_text[:100]}")
                
                # Extraer MFR Code (col-1 - select option selected)
                mfr_value = ''
                try:
                    mfr_select = row.locator('.q-col-1 select')
                    if mfr_select.count() > 0:
                        mfr_value = mfr_select.evaluate('el => el.value')
                except:
                    pass
                
                # Extraer Part Number (col-2 - input value)
                part_number = ''
                try:
                    part_number_input = row.locator('.q-col-2 input')
                    if part_number_input.count() > 0:
                        part_number = part_number_input.get_attribute('value') or ''
                        print(f"    Part Number encontrado: '{part_number}'")
                    else:
                        print(f"    No se encontró input en .q-col-2")
                except Exception as e:
                    print(f"    Error extrayendo part_number: {e}")
                
                # Si la fila tiene error (PART_ERROR), NO quitar las 3 letras aquí
                # fix_concatenated_part_numbers() DEBE haber corregido esto ya en Gardner
                # Si aquí aún hay error con MFRID, significa fix_concatenated_part_numbers no funcionó
                if has_error and part_number and len(part_number) > 3:
                    potential_mfrid = part_number[:3]
                    if potential_mfrid.isalpha():
                        # AQUÍ no deberíamos entrar si fix_concatenated_part_numbers() funcionó
                        print(f"    ⚠️ ALERTA: Fila {idx} aún tiene MFRID concatenado: {part_number}")
                        # NO lo limpiamos aquí, solo alertamos
                
                # Extraer Quantity (col-3 - input value)
                qty = 0
                try:
                    qty_input = row.locator('.q-col-3 input')
                    if qty_input.count() > 0:
                        qty_str = qty_input.get_attribute('value') or '0'
                        qty = int(qty_str) if qty_str.isdigit() else 0
                except:
                    pass
                
                # Extraer Item Name (col-4 - p text) y detectar Superseded
                item_name = ''
                superseded_from = None
                superseded_text = None
                try:
                    item_name_elem = row.locator('.q-col-4 p')
                    if item_name_elem.count() > 0:
                        item_name = item_name_elem.first.inner_text().strip()
                    # Detectar texto "Superseded from XXXX" en toda la columna col-4
                    col4_full_text = row.locator('.q-col-4').inner_text()
                    if 'superseded from' in col4_full_text.lower():
                        for line in col4_full_text.splitlines():
                            if 'superseded from' in line.lower():
                                superseded_text = line.strip()
                                # Extraer la parte original: "Superseded from HYG73257" -> "HYG73257"
                                parts = superseded_text.split()
                                if len(parts) >= 3:
                                    superseded_from = parts[-1].strip()
                                break
                except:
                    pass
                
                # Extraer List Price (col-5 - p text)
                list_price = None
                try:
                    list_price_elem = row.locator('.q-col-5 p')
                    if list_price_elem.count() > 0:
                        list_price_text = list_price_elem.inner_text()
                        list_price = parse_price(list_price_text)
                except:
                    pass
                
                # Extraer Your Price (col-6 - p text)
                your_price = None
                try:
                    your_price_elem = row.locator('.q-col-6 p')
                    if your_price_elem.count() > 0:
                        your_price_text = your_price_elem.inner_text()
                        your_price = parse_price(your_price_text)
                except:
                    pass
                
                # Extraer Available (col-7 - div text)
                available = 0
                try:
                    available_elem = row.locator('.q-col-7 div')
                    if available_elem.count() > 0:
                        available_text = available_elem.inner_text().strip()
                        available = int(available_text) if available_text.isdigit() else 0
                except:
                    pass
                
                # Determinar el status y error message
                # SUPERSEDED = tiene texto "Superseded from" en el nombre (no es error, solo reemplazo)
                # PART_ERROR = tiene la clase q-table-row--error
                # CORRECT = no tiene error
                if superseded_from:
                    status = 'SUPERSEDED'
                    error_message = superseded_text  # Ej: "Superseded from HYG73257"
                elif has_error:
                    status = 'PART_ERROR'
                    if item_name == 'Product Not Found' or not item_name:
                        error_message = f"Product not found: {part_number}"
                    elif available == 0:
                        error_message = f"Product out of stock: {part_number}"
                    else:
                        error_message = f"Error with part: {part_number}"
                else:
                    # No tiene error = CORRECT
                    status = 'CORRECT'
                    error_message = None
                
                # Solo agregar filas que tienen part_number
                if part_number:
                    # ✅ Procesar TODOS los items, sin deduplicación
                    # (aunque sean duplicados, los incluimos todos)
                    
                    row_data = {
                        'mfrid': mfr_value,
                        'part_number': part_number,
                        'qty': qty,
                        'item_name': item_name,
                        'list_price': list_price,
                        'your_price': your_price,
                        'qty_available': available,
                        'in_stock': 'Y' if available > 0 else 'N',
                        'status': status,
                        'error_message': error_message,
                        'superseded_from': superseded_from,
                        'nla': None,       # Se rellena en check_nla_for_errors
                        'pack_qty': None,  # No aplica para Gardner (QTY viene del CSV)
                    }
                    
                    results.append(row_data)
                    print(f"  ✓ Fila {idx}: {part_number} - Status: {status}")
                
            except Exception as e:
                print(f"  ⚠️ Error procesando fila {idx}: {e}")
                continue
        
        print(f"📦 Total items extraídos: {len(results)} (incluidos duplicados, sin deduplicación)")
        return results
        
    except Exception as e:
        print(f"❌ Error extrayendo datos de la tabla: {e}")
        return []


def check_nla_for_errors(page: Page, scraped_data: List[Dict]) -> None:
    """
    Para cada item con status PART_ERROR, busca el part_number en la barra
    de búsqueda de Gardner. Si el resultado muestra indicadores de NLA
    (No Longer Available / Discontinued) → actualiza el item:
      status       = "NLA"
      nla          = "Y"
      error_message = "No Longer Available: {part_number}"

    Modifica scraped_data in-place. El navegador debe estar logueado.
    """
    error_items = [item for item in scraped_data if item.get('status') == 'PART_ERROR']
    if not error_items:
        print("ℹ️ Sin items PART_ERROR → omitiendo verificación NLA.")
        return

    print(f"🔍 Verificando NLA para {len(error_items)} item(s) con error...")

    NLA_INDICATORS = [
        'no longer available',
        'nla',
        'discontinued',
        'not available for purchase',
        'item is no longer',
        'product is unavailable',
    ]

    for item in error_items:
        part_number = item['part_number']
        try:
            search_url = f"https://www.gardnerinc.com/search?q={part_number}"
            page.goto(search_url, wait_until="domcontentloaded")
            time.sleep(2)

            page_text = page.content().lower()
            is_nla = any(indicator in page_text for indicator in NLA_INDICATORS)

            # Verificación adicional: buscar elemento visible con texto NLA
            if not is_nla:
                nla_elem = page.locator(
                    'text="No Longer Available", '
                    'text="NLA", '
                    'text="Discontinued"'
                )
                is_nla = nla_elem.count() > 0

            if is_nla:
                item['nla'] = 'Y'
                item['status'] = 'PART_ERROR'
                item['error_message'] = f"No Longer Available: {part_number}"
                print(f"  🚫 NLA confirmado: {part_number}")
            else:
                print(f"  ℹ️  No NLA (producto no encontrado): {part_number}")

        except Exception as e:
            print(f"  ⚠️ Error verificando NLA para {part_number}: {e}")


def fix_concatenated_part_numbers(page: Page) -> int:
    """
    Detecta filas con q-table-row--error donde el part_number tiene el MFRID concatenado
    (ej: "HOM30400-Z190110-0000" tiene "HOM" al inicio que no debería estar).
    
    Para cada fila con error:
    1. Quita SIEMPRE las primeras 3 letras (sin importar si hay duplicados)
    2. Edita el input en Gardner
    3. Pasa el mouse a la siguiente fila de la tabla completa
    4. Continúa hasta la última fila, sin importar si aparecen duplicados
    
    Una vez termina CON TODAS, hace el scraper de nuevo con todos los items.
    Si queda part_error, se queda así.
    """
    fixed = 0
    processed_rows = set()  # Para evitar procesar la MISMA fila dos veces
    max_iterations = 100    # Prevenir loop infinito
    iteration = 0
    
    try:
        print("🔧 Buscando filas con error para quitar MFRID concatenado...")
        
        # Obtener la lista COMPLETA de filas una sola vez
        all_rows = page.locator('.q-table-row').all()
        print(f"📋 Total de filas en la tabla: {len(all_rows)}")
        
        while iteration < max_iterations:
            iteration += 1
            
            # ✅ BUSCAR FILAS CON ERROR EN CADA ITERACIÓN
            error_rows = page.locator('li.q-table-row--error').all()
            if not error_rows:
                break  # No más filas con error
            
            found_unprocessed = False
            
            for error_idx, error_row in enumerate(error_rows):
                row_id = id(error_row)  # Identificador único de esta fila
                if row_id in processed_rows:
                    continue  # Ya procesada
                
                try:
                    # Encontrar el input de part_number en esta fila (col-2)
                    pn_input = error_row.locator('.q-col-2 input').first
                    if not pn_input or pn_input.count() == 0:
                        processed_rows.add(row_id)
                        continue
                    
                    current_value = (pn_input.get_attribute('value') or '').strip()
                    if not current_value or len(current_value) <= 3:
                        processed_rows.add(row_id)
                        continue
                    
                    # Quitar las primeras 3 letras (MFRID concatenado) - SIN IMPORTAR DUPLICADOS
                    first_three = current_value[:3]
                    if not first_three.isalpha():
                        processed_rows.add(row_id)
                        continue
                    
                    clean_part = current_value[3:]
                    
                    # Editar el input: quitar las 3 letras del MFRID
                    print(f"  🔧 Fila con error {error_idx}: '{current_value}' → '{clean_part}'")
                    pn_input.click()                           # Click en el input
                    pn_input.fill('')                          # Limpiar primero
                    pn_input.type(clean_part, delay=50)        # Escribir carácter por carácter
                    time.sleep(0.5)                            # Esperar a que se escriba
                    
                    # Verificar que el valor se actualizó correctamente
                    updated_value = pn_input.get_attribute('value') or ''
                    print(f"    ✓ Input actualizado: '{updated_value}'")
                    
                    # Disparar cambio: Enter para activar búsqueda en Gardner
                    pn_input.press('Enter')
                    time.sleep(1.5)  # Esperar búsqueda en Gardner
                    
                    fixed += 1
                    processed_rows.add(row_id)
                    found_unprocessed = True
                    
                    # ✅ PASAR EL MOUSE A LA SIGUIENTE FILA (de la tabla completa)
                    # Primero, encontrar el índice de la fila actual en la tabla completa
                    try:
                        current_row_index = all_rows.index(error_row) if error_row in all_rows else -1
                        if current_row_index >= 0 and current_row_index + 1 < len(all_rows):
                            next_row = all_rows[current_row_index + 1]
                            next_row.hover()
                            print(f"    ➡️ Mouse pasado a fila {current_row_index + 1} (siguiente en tabla)")
                            time.sleep(0.5)
                    except Exception as hover_error:
                        # Si no podemos pasar el mouse por índice, intentar con el siguiente error_row
                        if error_idx + 1 < len(error_rows):
                            next_error_row = error_rows[error_idx + 1]
                            next_error_row.hover()
                            print(f"    ➡️ Mouse pasado a siguiente fila con error")
                            time.sleep(0.5)
                    
                except Exception as e:
                    print(f"  ⚠️ Error en fila con error {error_idx}: {e}")
                    processed_rows.add(row_id)
                    continue
            
            if not found_unprocessed:
                break  # Todas las filas ya fueron procesadas
        
        # ✅ UNA VEZ TERMINA CON TODAS, HACER EL SCRAPER DE NUEVO
        if fixed:
            print(f"\n✅ {fixed} fila(s) corregida(s). Esperando a que Gardner procese...")
            time.sleep(4)  # Esperar final a que Gardner actualice todo
            
            print(f"\n🔄 Haciendo el scraper de nuevo con todos los items...")
        else:
            print("ℹ️ No había filas con error para corregir.")
        
    except Exception as e:
        print(f"❌ Error en fix_concatenated_part_numbers: {e}")
        import traceback
        traceback.print_exc()
    
    return fixed


def gardner_login_automation_playwright(email, password, csv_filename):
    """
    Función principal que ejecuta toda la automatización de login en Gardner Inc usando Playwright.
    Retorna el objeto page para que pueda ser usado para scraping.
    """
    print("🚀 Iniciando automatización de login en Gardner Inc con Playwright...")
    
    # Obtener la ruta del archivo CSV
    downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
    csv_path = os.path.join(downloads_path, csv_filename)
    
    if not os.path.exists(csv_path):
        print(f"❌ El archivo {csv_filename} no se encuentra en la carpeta de descargas.")
        print(f"📂 Ruta buscada: {csv_path}")
        return None
    
    print(f"📂 Archivo encontrado: {csv_path}")
    
    p = sync_playwright().start()
    
    # Lanzar Edge en lugar de Chrome para mejor compatibilidad con diálogos de Windows
    print("🌐 Iniciando Microsoft Edge...")
    browser = p.chromium.launch(
        headless=False,
        args=[
            '--start-maximized',
            '--disable-blink-features=AutomationControlled'
        ],
        channel='msedge'  # Usar Microsoft Edge
    )
    
    # Crear contexo sin viewport fijo
    context = browser.new_context(
        no_viewport=True,
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0'
    )
    
    page = context.new_page()
    print("✅ Edge iniciado correctamente.")
    
    try:
        # 1. Ir a la página de Gardner
        print("📍 Navegando a Gardner Inc...")
        page.goto("https://www.gardnerinc.com/", wait_until="domcontentloaded")
        time.sleep(3)
        print("✅ Se cargó la página de Gardner Inc.")
        
        # 2. Hacer clic en Sign in
        print("🔐 Buscando y haciendo clic en 'Sign in'...")
        # Buscar el primer botón visible de Sign in
        page.wait_for_selector("a.ms-header__signin-button", timeout=10000)
        sign_in_button = page.locator("a.ms-header__signin-button").first
        sign_in_button.click()
        time.sleep(3)
        print("✅ Se hizo clic en 'Sign in'.")
        
        # 3. Ingresar email
        print(f"📧 Ingresando email: {email}")
        email_input = page.locator("input#email.ms-sign-in__account-item-email")
        email_input.fill(email)
        time.sleep(1)
        print("✅ Email ingresado.")
        
        # 4. Ingresar contraseña
        print("🔑 Ingresando contraseña...")
        password_input = page.locator("input#password.ms-sign-in__account-item-password")
        password_input.fill(password)
        time.sleep(1)
        print("✅ Contraseña ingresada.")
        
        # 5. Enviar formulario
        print("📤 Enviando formulario de login...")
        password_input.press("Enter")
        time.sleep(5)
        print("✅ Formulario enviado.")
        
        # 6. Verificar login exitoso
        print("🔍 Verificando login exitoso...")
        page.wait_for_load_state("networkidle")
        current_url = page.url
        print(f"🌐 URL actual: {current_url}")
        
        if "signin" not in current_url.lower():
            print("✅ Login exitoso.")
        else:
            print("⚠️ El login puede no haber sido exitoso.")
        
        # 7. Hacer clic en Quick Order
        print("🛒 Haciendo clic en 'Quick Order'...")
        quick_order_button = page.locator("a.ms-header__quick-order-button[href='/quickorder']").first
        quick_order_button.click()
        time.sleep(3)
        print("✅ Se hizo clic en 'Quick Order'.")
        
        # 8. Cargar el archivo CSV
        print("📁 Preparando para cargar el archivo...")

        time.sleep(5)  # Esperar a que la página se estabilice completamente
        
        # Localizar el botón
        open_file_button = page.locator("button:has-text('Open File Dialog')")
        
        # Hacer scroll suave al elemento
        open_file_button.scroll_into_view_if_needed()
        time.sleep(1)
        print("✅ Botón 'Open File Dialog' encontrado.")
        
        print("🎯 Haciendo clic en el botón para abrir el cuadro de diálogo...")
        
        # Hacer hover y click para abrir el diálogo
        open_file_button.hover()
        time.sleep(0.5)
        open_file_button.click(delay=100)
        print("✅ Click ejecutado en el botón.")
        
        # Esperar a que el cuadro de diálogo de Windows se abra
        print("⏳ Esperando a que se abra el cuadro de diálogo de Windows...")
        time.sleep(3)
        
        # Usar pynput para escribir la ruta del archivo (como en el ejemplo de YouTube)
        print(f"⌨️ Escribiendo la ruta del archivo: {csv_path}")
        keyboard = Controller()
        keyboard.type(csv_path)
        time.sleep(1)
        
        # Presionar Enter para confirmar
        print("⏎ Presionando Enter para confirmar...")
        keyboard.press(Key.enter)
        keyboard.release(Key.enter)
        time.sleep(2)
        print(f"✅ Se cargó el archivo {csv_filename} correctamente usando pynput.")
        
        # 9. Esperar 7 segundos para que la tabla se cargue completamente
        print("⏳ Esperando 7 segundos para que se cargue la tabla de resultados...")
        time.sleep(7)

        # 9b. Corregir filas donde Gardner concaténó MFRID + part number
        corrected = fix_concatenated_part_numbers(page)
        if corrected:
            print(f"🔄 {corrected} fila(s) corregidas por concatenación MFRID+PN.")
            # ✅ HACER EL SCRAPER DE NUEVO DESPUÉS DE QUITAR LAS 3 LETRAS
            print("🔍 Haciendo scraper de nuevo con todos los items (incluidos los corregidos)...")
            time.sleep(2)

        # 10. Hacer scraping de la tabla de resultados
        print("🔍 Iniciando scraping de los resultados...")
        scraped_data = extract_table_data(page)
        print(f"✅ Scraping completado. {len(scraped_data)} filas extraídas.")

        # 11. Verificar NLA para items con PART_ERROR
        check_nla_for_errors(page, scraped_data)

        print("✅ Automatización completa exitosa con Playwright.")
        
        # Cerrar el navegador
        browser.close()
        print("🚪 Navegador cerrado correctamente.")
        
        # Retornar los datos del scraping
        return scraped_data
        
    except Exception as e:
        print(f"❌ Error durante la automatización: {e}")
        import traceback
        traceback.print_exc()
        
        # Cerrar el navegador en caso de error
        browser.close()
        return None

if __name__ == "__main__":
    # Credenciales de acceso
    EMAIL = "jacobn.prontomowers+75145@gmail.com"
    PASSWORD = "Pronto123#"
    CSV_FILENAME = "PO_93609_GA_20260617_061400.csv"  # Nombre del archivo CSV en la carpeta de descargas
    
    # Ejecutar automatización
    gardner_login_automation_playwright(EMAIL, PASSWORD, CSV_FILENAME)
