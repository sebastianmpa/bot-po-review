from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from pynput.keyboard import Key, Controller
import time
import os

def iniciar_driver():
    """
    Inicializa el driver de Chrome con opciones optimizadas.
    """
    chrome_options = Options()
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 60)
    return driver, wait

def ir_a_gardner_website(driver):
    """
    Navega a la página principal de Gardner Inc.
    """
    try:
        driver.get("https://www.gardnerinc.com/")
        print("✅ Se cargó la página de Gardner Inc. correctamente.")
        time.sleep(3)
    except Exception as e:
        print(f"❌ Error al cargar la página de Gardner Inc.: {e}")

def click_sign_in_button(driver, wait):
    """
    Hace clic en el botón 'Sign in' del header.
    """
    try:
        # Usar múltiples selectores para mayor confiabilidad
        sign_in_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.ms-header__signin-button[href*='signin']"))
        )
        sign_in_button.click()
        print("✅ Se hizo clic en el botón 'Sign in' correctamente.")
        time.sleep(3)
    except Exception as e:
        print(f"❌ No se pudo hacer clic en el botón 'Sign in': {e}")
        # Intentar con xpath alternativo
        try:
            sign_in_button_alt = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//a[@title='Sign in' and contains(@href, 'signin')]"))
            )
            sign_in_button_alt.click()
            print("✅ Se hizo clic en el botón 'Sign in' con selector alternativo.")
            time.sleep(3)
        except Exception as e2:
            print(f"❌ Error con selector alternativo: {e2}")

def ingresar_credenciales(driver, wait, email, password):
    """
    Ingresa el email y contraseña en los campos correspondientes.
    """
    try:
        # Ingresar email
        email_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input#email.ms-sign-in__account-item-email"))
        )
        email_input.clear()
        email_input.send_keys(email)
        print(f"✅ Se ingresó el email: {email}")
        time.sleep(1)

        # Ingresar contraseña
        password_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input#password.ms-sign-in__account-item-password"))
        )
        password_input.clear()
        password_input.send_keys(password)
        print("✅ Se ingresó la contraseña correctamente.")
        time.sleep(1)

        # Presionar Enter o buscar botón de submit
        password_input.send_keys(Keys.RETURN)
        print("✅ Se envió el formulario de login.")
        time.sleep(5)

    except Exception as e:
        print(f"❌ Error al ingresar credenciales: {e}")

def verificar_login_exitoso(driver, wait):
    """
    Verifica si el login fue exitoso esperando algún elemento característico del dashboard.
    """
    try:
        # Esperar a que la URL cambie o que aparezca algún elemento del dashboard
        wait.until(lambda d: "signin" not in d.current_url.lower())
        print("✅ Login exitoso - URL cambió correctamente.")
        print(f"🌐 URL actual: {driver.current_url}")
        return True
    except TimeoutException:
        print("⚠️ No se pudo verificar el login exitoso.")
        return False

def click_quick_order_button(driver, wait):
    """
    Hace clic en el botón 'Quick Order' del header.
    """
    try:
        # Usar selector CSS para el botón Quick Order
        quick_order_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.ms-header__quick-order-button[href='/quickorder']"))
        )
        quick_order_button.click()
        print("✅ Se hizo clic en el botón 'Quick Order' correctamente.")
        time.sleep(3)
    except Exception as e:
        print(f"❌ No se pudo hacer clic en el botón 'Quick Order': {e}")
        # Intentar con xpath alternativo
        try:
            quick_order_button_alt = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//a[@href='/quickorder']//span[contains(text(), 'Quick Order')]"))
            )
            quick_order_button_alt.click()
            print("✅ Se hizo clic en el botón 'Quick Order' con selector alternativo.")
            time.sleep(3)
        except Exception as e2:
            print(f"❌ Error con selector alternativo: {e2}")

def click_open_file_dialog(driver, wait):
    """
    Hace clic en el botón 'Open File Dialog' para abrir el cuadro de diálogo de archivo.
    Usa el mismo método que el script de Playwright para mayor compatibilidad.
    """
    try:
        # Esperar a que la página se estabilice
        time.sleep(5)
        
        # Localizar el botón
        open_file_button = wait.until(
            EC.presence_of_element_located((By.XPATH, "//button[contains(text(), 'Open File Dialog')]"))
        )
        print("✅ Botón 'Open File Dialog' encontrado.")
        
        # Hacer scroll al elemento
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", open_file_button)
        time.sleep(1)
        
        print("🎯 Haciendo clic en el botón para abrir el cuadro de diálogo...")
        
        # Hacer hover sobre el elemento (mover el mouse)
        from selenium.webdriver.common.action_chains import ActionChains
        actions = ActionChains(driver)
        actions.move_to_element(open_file_button).perform()
        time.sleep(0.5)
        
        # Hacer clic con un pequeño delay
        actions.click(open_file_button).perform()
        print("✅ Click ejecutado en el botón.")
        time.sleep(2)
        
        return True
        
    except Exception as e:
        print(f"❌ No se pudo hacer clic en el botón 'Open File Dialog': {e}")
        return False

def cargar_archivo_csv_con_pynput(csv_filename):
    """
    Carga el archivo CSV usando pynput para interactuar con el cuadro de diálogo de Windows.
    Esta función se ejecuta DESPUÉS de hacer clic en "Open File Dialog".
    """
    try:
        # Obtener la ruta de la carpeta de descargas del usuario
        downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
        csv_path = os.path.join(downloads_path, csv_filename)
        
        if not os.path.exists(csv_path):
            print(f"❌ El archivo {csv_filename} no se encuentra en la carpeta de descargas.")
            print(f"📂 Ruta buscada: {csv_path}")
            return False
        
        print(f"📂 Archivo encontrado: {csv_path}")
        
        # Esperar a que aparezca el cuadro de diálogo de Windows
        print("⏳ Esperando a que aparezca el cuadro de diálogo...")
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
        print(f"✅ Se cargó el archivo {csv_filename} correctamente usando pynput.")
        
        return True
        
    except Exception as e:
        print(f"❌ Error al cargar el archivo CSV con pynput: {e}")
        return False

def cargar_archivo_csv_directo(driver, wait, csv_filename):
    """
    Intenta cargar el archivo CSV buscando un input file en la página.
    Si no existe, usa el método alternativo con PyAutoGUI.
    """
    try:
        # Obtener la ruta de la carpeta de descargas del usuario
        downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
        csv_path = os.path.join(downloads_path, csv_filename)
        
        if not os.path.exists(csv_path):
            print(f"❌ El archivo {csv_filename} no se encuentra en la carpeta de descargas.")
            print(f"📂 Ruta buscada: {csv_path}")
            return False
        
        print(f"📂 Archivo encontrado: {csv_path}")
        
        # Esperar un poco para que la página cargue el dropzone
        time.sleep(2)
        
        # Buscar el input de tipo file (generalmente está oculto)
        try:
            # Estrategia 1: Buscar input file dentro de la sección dropzone
            file_input = driver.find_element(By.CSS_SELECTOR, "section input[type='file']")
            print("✅ Se encontró el input file con CSS Selector.")
        except:
            try:
                # Estrategia 2: Buscar cualquier input file en la página
                file_input = driver.find_element(By.CSS_SELECTOR, "input[type='file']")
                print("✅ Se encontró el input file genérico.")
            except:
                print("⚠️ No se encontró input file. Se usará el método con botón + PyAutoGUI.")
                return False
        
        # Hacer visible el input si está oculto
        driver.execute_script("arguments[0].style.display = 'block'; arguments[0].style.visibility = 'visible';", file_input)
        time.sleep(0.5)
        
        # Enviar la ruta del archivo directamente al input
        file_input.send_keys(csv_path)
        print(f"✅ Se cargó el archivo {csv_filename} directamente al input.")
        time.sleep(3)
        
        return True
        
    except Exception as e:
        print(f"⚠️ Error al cargar archivo directo: {e}")
        return False

def cerrar_navegador(driver):
    """
    Cierra el navegador después de una espera.
    """
    try:
        print("⏳ Esperando 10 segundos antes de cerrar...")
        time.sleep(10)
        driver.quit()
        print("🚪 Navegador cerrado correctamente.")
    except Exception as e:
        print(f"❌ Error al cerrar el navegador: {e}")

def gardner_login_automation(email, password, csv_filename):
    """
    Función principal que ejecuta toda la automatización de login en Gardner Inc.
    """
    print("🚀 Iniciando automatización de login en Gardner Inc...")
    
    # Inicializar driver
    driver, wait = iniciar_driver()
    
    try:
        # Ir a la página de Gardner
        ir_a_gardner_website(driver)
        
        # Hacer clic en Sign in
        click_sign_in_button(driver, wait)
        
        # Ingresar credenciales
        ingresar_credenciales(driver, wait, email, password)
        
        # Verificar login exitoso
        if verificar_login_exitoso(driver, wait):
            # Hacer clic en Quick Order
            click_quick_order_button(driver, wait)
            
            # Intentar cargar el archivo directamente (sin hacer clic en el botón)
            if not cargar_archivo_csv_directo(driver, wait, csv_filename):
                # Si el método directo falla, usar el método con botón + pynput
                print("🔄 Intentando método alternativo: botón + pynput...")
                if click_open_file_dialog(driver, wait):
                    cargar_archivo_csv_con_pynput(csv_filename)
                else:
                    print("❌ No se pudo cargar el archivo con ningún método.")
        
        print("✅ Automatización completada exitosamente.")
        
    except Exception as e:
        print(f"❌ Error durante la automatización: {e}")
    
    finally:
        # Cerrar navegador
        cerrar_navegador(driver)

if __name__ == "__main__":
    # Credenciales de acceso
    EMAIL = "jacobn.prontomowers+75145@gmail.com"
    PASSWORD = "Pronto123#"
    CSV_FILENAME = "92978 csv-1.csv"  # Nombre del archivo CSV en la carpeta de descargas
    
    # Ejecutar automatización
    gardner_login_automation(EMAIL, PASSWORD, CSV_FILENAME)
