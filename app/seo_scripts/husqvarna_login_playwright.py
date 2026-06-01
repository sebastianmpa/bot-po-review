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
    Extrae todos los datos de la tabla de compras de Husqvarna después de cargar el CSV.
    Tabla: id="purchase-history-table"
    Columnas: Status, Item #, Product Description, Dealer Comments, Requested Ship Date, Qty, MSRP
    """
    results = []
    
    try:
        # Esperar a que la tabla esté presente
        page.wait_for_selector('#purchase-history-table', timeout=10000)
        print("📊 Tabla de compras de Husqvarna encontrada")
        
        # Obtener todas las filas de la tabla (excluyendo header)
        rows = page.locator('#purchase-history-table tbody tr').all()
        print(f"📋 Total de filas encontradas: {len(rows)}")
        
        for idx, row in enumerate(rows):
            try:
                # Extraer Status (columna 2: errorDesc)
                status = ''
                try:
                    status_elem = row.locator('td:nth-child(2)')
                    if status_elem.count() > 0:
                        status = status_elem.inner_text().strip()
                except:
                    pass
                
                # Extraer Item # (Part Number) (columna 3: partNumber)
                part_number = ''
                try:
                    part_elem = row.locator('td:nth-child(3)')
                    if part_elem.count() > 0:
                        part_number = part_elem.inner_text().strip()
                except:
                    pass
                
                # Extraer Product Description (columna 4: itemDesc)
                product_description = ''
                try:
                    desc_elem = row.locator('td:nth-child(4)')
                    if desc_elem.count() > 0:
                        product_description = desc_elem.inner_text().strip()
                except:
                    pass
                
                # Extraer Dealer Comments (columna 5: comments)
                dealer_comments = ''
                try:
                    comments_elem = row.locator('td:nth-child(5)')
                    if comments_elem.count() > 0:
                        dealer_comments = comments_elem.inner_text().strip()
                except:
                    pass
                
                # Extraer Requested Ship Date (columna 6: requestedShipDate)
                ship_date = ''
                try:
                    ship_date_elem = row.locator('td:nth-child(6)')
                    if ship_date_elem.count() > 0:
                        ship_date = ship_date_elem.inner_text().strip()
                except:
                    pass
                
                # Extraer Quantity (columna 7: quantity)
                qty = 0
                try:
                    qty_elem = row.locator('td:nth-child(7)')
                    if qty_elem.count() > 0:
                        qty_str = qty_elem.inner_text().strip()
                        qty = int(qty_str) if qty_str.isdigit() else 0
                except:
                    pass
                
                # Extraer MSRP (columna 8: msrp)
                msrp = None
                try:
                    msrp_elem = row.locator('td:nth-child(8)')
                    if msrp_elem.count() > 0:
                        msrp_text = msrp_elem.inner_text().strip()
                        msrp = parse_price(msrp_text)
                except:
                    pass
                
                # Solo agregar filas que tienen part_number
                if part_number:
                    row_data = {
                        'status': status,
                        'part_number': part_number,
                        'product_description': product_description,
                        'dealer_comments': dealer_comments,
                        'requested_ship_date': ship_date,
                        'qty': qty,
                        'msrp': msrp
                    }
                    
                    results.append(row_data)
                    print(f"  ✓ Fila {idx}: {part_number} - Status: {status}")
                
            except Exception as e:
                print(f"  ⚠️ Error procesando fila {idx}: {e}")
                continue
        
        return results
        
    except Exception as e:
        print(f"❌ Error extrayendo datos de la tabla: {e}")
        return []


def husqvarna_login_automation_playwright(email, password, csv_filename):
    """
    Función principal que ejecuta toda la automatización de login en Husqvarna usando Playwright.
    Proceso:
    1. Ir a la URL principal
    2. Encontrar y hacer clic en el enlace de login
    3. Ingresar credenciales
    4. Navegar a Import Order
    5. Cargar archivo CSV
    6. Hacer scraping de la tabla de resultados
    """
    print("🚀 Iniciando automatización de login en Husqvarna con Playwright...")
    
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
    
    # Crear contexto sin viewport fijo
    context = browser.new_context(
        no_viewport=True,
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0'
    )
    
    page = context.new_page()
    print("✅ Edge iniciado correctamente.")
    
    try:
        # 1. Ir a la página principal de Husqvarna
        print("📍 Navegando a Husqvarna Group...")
        page.goto("https://power.husqvarnagroup.com/webapp/wcs/stores/servlet/HomePageView?catalogId=10051&langId=-1&storeId=10201", wait_until="domcontentloaded")
        time.sleep(3)
        print("✅ Se cargó la página de Husqvarna.")
        
        # 2. Hacer clic en el enlace de login dentro del div.porlet_content
        print("🔐 Buscando y haciendo clic en 'Go to Login'...")
        login_link = page.locator('.porlet_content a[href*="LogonForm"]')
        if login_link.count() > 0:
            login_link.click()
            time.sleep(3)
            print("✅ Se hizo clic en 'Go to Login'.")
        else:
            print("⚠️ No se encontró el enlace de login en el div.porlet_content")
            return None
        
        # 3. Ingresar email (username)
        print(f"📧 Ingresando email: {email}")
        email_input = page.locator('input[name="username"].form-control')
        email_input.fill(email)
        time.sleep(1)
        print("✅ Email ingresado.")
        
        # 4. Ingresar contraseña
        print("🔑 Ingresando contraseña...")
        password_input = page.locator('input[name="password"].form-control')
        password_input.fill(password)
        time.sleep(1)
        print("✅ Contraseña ingresada.")
        
        # 5. Hacer clic en el botón de envío "Acceder"
        print("📤 Haciendo clic en 'Acceder'...")
        submit_button = page.locator('button[type="submit"][name="operation"][value="verify"]')
        submit_button.click()
        time.sleep(5)
        print("✅ Formulario enviado.")
        
        # 6. Verificar login exitoso
        print("🔍 Verificando login exitoso...")
        page.wait_for_load_state("networkidle")
        current_url = page.url
        print(f"🌐 URL actual: {current_url}")
        
        if "logon" not in current_url.lower():
            print("✅ Login exitoso.")
        else:
            print("⚠️ El login puede no haber sido exitoso.")
        
        # 7. Navegar a Import Order
        print("📥 Navegando a 'Import Order'...")
        import_order_link = page.locator('a.MuiTypography-h4[href*="import-order"]')
        if import_order_link.count() > 0:
            import_order_link.click()
            time.sleep(3)
            print("✅ Se navegó a 'Import Order'.")
        else:
            print("⚠️ No se encontró el enlace 'Import Order'")
            return None
        
        # 8. Cargar el archivo CSV
        print("📁 Preparando para cargar el archivo...")
        time.sleep(5)  # Esperar a que la página se estabilice completamente
        
        # Localizar el botón Browse
        browse_button = page.locator('label[data-testid="requisition-list-file-browsing"]')
        
        if browse_button.count() > 0:
            # Hacer scroll suave al elemento
            browse_button.scroll_into_view_if_needed()
            time.sleep(1)
            print("✅ Botón 'Browse' encontrado.")
            
            print("🎯 Haciendo clic en el botón para abrir el cuadro de diálogo...")
            
            # Hacer hover y click para abrir el diálogo
            browse_button.hover()
            time.sleep(0.5)
            browse_button.click(delay=100)
            print("✅ Click ejecutado en el botón Browse.")
            
            # Esperar a que el cuadro de diálogo de Windows se abra
            print("⏳ Esperando a que se abra el cuadro de diálogo de Windows...")
            time.sleep(3)
            
            # Usar pynput para escribir la ruta del archivo
            print(f"⌨️ Escribiendo la ruta del archivo: {csv_path}")
            keyboard = Controller()
            keyboard.type(csv_path)
            time.sleep(1)
            
            # Presionar Enter para confirmar
            print("⏎ Presionando Enter para confirmar...")
            keyboard.press(Key.enter)
            keyboard.release(Key.enter)
            time.sleep(2)
            print(f"✅ Se cargó el archivo {csv_filename} correctamente.")
        else:
            print("⚠️ No se encontró el botón Browse para cargar el archivo")
            return None
        
        # 9. Hacer clic en el botón "Upload Order"
        print("📤 Haciendo clic en 'Upload Order'...")
        upload_button = page.locator('button[type="submit"][data-testid="requisition-list-upload"]')
        if upload_button.count() > 0:
            upload_button.click()
            time.sleep(5)
            print("✅ Se hizo clic en 'Upload Order'.")
        else:
            print("⚠️ No se encontró el botón 'Upload Order'")
            return None
        
        # 10. Esperar a que se cargue la tabla de resultados
        print("⏳ Esperando a que se cargue la tabla de resultados...")
        time.sleep(7)
        
        # 11. Hacer scraping de la tabla de resultados
        print("🔍 Iniciando scraping de los resultados...")
        scraped_data = extract_table_data(page)
        
        print(f"✅ Scraping completado. {len(scraped_data)} filas extraídas.")
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
    # Credenciales de acceso (aquí irían las credenciales reales)
    EMAIL = "your_email@example.com"
    PASSWORD = "your_password"
    CSV_FILENAME = "husq.csv"  # Nombre del archivo CSV en la carpeta de descargas
    
    # Ejecutar automatización
    result = husqvarna_login_automation_playwright(EMAIL, PASSWORD, CSV_FILENAME)
    
    if result:
        print("\n📊 Datos extraídos:")
        for idx, row in enumerate(result, 1):
            print(f"\nFila {idx}:")
            for key, value in row.items():
                print(f"  {key}: {value}")
