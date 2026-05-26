from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import pyautogui
import time
import pickle

def iniciar_driver():
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

# ✅ INICIO DE SESIÓN EN GOOGLE ADS
def iniciar_sesion(driver, wait, email, password):
    try:
        driver.get("https://accounts.google.com/v3/signin/identifier?service=adwords&flowName=GlifWebSignIn&flowEntry=ServiceLogin")
        
        email_field = wait.until(EC.presence_of_element_located((By.ID, "identifierId")))
        email_field.send_keys(email)
        email_field.send_keys(Keys.RETURN)
        time.sleep(7)  # Espera breve para cargar el siguiente paso
        
        password_field = wait.until(EC.presence_of_element_located((By.NAME, "Passwd")))
        password_field.send_keys(password)
        password_field.send_keys(Keys.RETURN)

        print("✅ Inicio de sesión exitoso.")
        time.sleep(3)
    except Exception as e:
        print(f"❌ Error al iniciar sesión: {e}")

# ✅ SELECCIONAR CUENTA EN GOOGLE ADS
def seleccionar_cuenta(driver, wait, account_number):
    try:
        # Abrir Google Ads en una nueva pestaña
        driver.execute_script("window.open('https://ads.google.com/aw/campaigns', '_blank');")
        driver.switch_to.window(driver.window_handles[1])
        print("✅ Se abrió Google Ads en una nueva pestaña correctamente.")

        # Cerrar la pestaña anterior
        driver.switch_to.window(driver.window_handles[0])
        driver.close()
        print("❎ Se cerró la pestaña anterior.")

        driver.switch_to.window(driver.window_handles[0])

        account_span = wait.until(EC.element_to_be_clickable((By.XPATH, f"//span[normalize-space()='{account_number}']")))
        account_span.click()
        print(f"✅ Se seleccionó la cuenta {account_number} correctamente.")
    except Exception as e:
        print(f"❌ No se pudo seleccionar la cuenta {account_number}: {e}")

def ir_a_keyword_planner(driver, wait):
    try:
        tools_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Tools']")))
        tools_button.click()
        print("✅ Se hizo clic en 'Tools'.")
    except Exception as e:
        print(f"❌ No se pudo hacer clic en 'Tools': {e}")

    try:    
        discover_keywords_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Discover new keywords']")))
        discover_keywords_button.click()
        print("✅ Se hizo clic en 'Discover new keywords'.")
    except Exception as e:
        print(f"❌ No se pudo hacer clic en 'Discover new keywords': {e}")
    

# ✅ AGREGAR PALABRAS CLAVE EN EL INPUT DEL KEYWORD PLANNER
def agregar_keywords(driver, wait, keywords):
    try:
        keyword_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Try \"meal delivery\" or \"leather boots\"']")))

        for keyword in keywords:
            keyword_input.send_keys(keyword)
            keyword_input.send_keys(Keys.RETURN)
            time.sleep(0.5)  # Pausa breve entre entradas para estabilidad

        print(f"✅ Se ingresaron las palabras clave: {keywords}")
    except Exception as e:
        print(f"❌ No se pudieron ingresar las palabras clave: {e}")

# ✅ AGREGAR URL OPCIONAL PARA FILTRAR PALABRAS CLAVE
def agregar_url_filtro(driver, wait, site_url=None):
    if site_url:
        try:
            url_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@aria-label='Enter a site to filter unrelated keywords']")))
            url_input.send_keys(site_url)
            url_input.send_keys(Keys.RETURN)
            print(f"✅ Se ingresó la URL de filtrado: {site_url}")
        except Exception as e:
            print(f"❌ No se pudo ingresar la URL de filtrado: {e}")
    else:
        print("ℹ️ No se ingresó ninguna URL de filtrado.")

# ✅ HACER CLIC EN "GET RESULTS"
def obtener_resultados(driver, wait):
    try:
        driver.execute_script("document.body.style.zoom='80%'")
        print("🔍 Zoom ajustado al 80%.")
        get_results_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//material-button[contains(@class, 'submit-button')]//material-ripple")))
        get_results_button.click()
        print("✅ Se hizo clic en 'Get Results' correctamente.")
    except Exception as e:
        print(f"❌ No se pudo hacer clic en 'Get Results': {e}")


def descargar_keywords_pygui():
    try:
        print("🖱️ Enfocando la ventana del navegador...")

        # 🖥️ Hacer clic en la barra de título para enfocar la ventana
        pyautogui.click(100, 10)  # Ajusta si es necesario

        # 🕒 Pequeña espera para estabilizar
        time.sleep(1)

        print("🖱️ Moviendo el cursor y haciendo clic en el botón de descarga...")

        # 📍 Definir coordenadas del botón en la pantalla
        x, y = 1331, 280  # Ajusta estas coordenadas según tu pantalla

        # 🔍 Mover el cursor a la posición
        pyautogui.moveTo(x, y, duration=1)

        # 🖱️ Simular un clic real con presionar y soltar el mouse
        pyautogui.mouseDown()  # Presionar clic
        time.sleep(0.01)  # Esperar un poco
        pyautogui.mouseUp()  # Soltar clic
        print(f"✅ Clic simulado con mouseDown/mouseUp en las coordenadas ({x}, {y})")

        
        # ⏳ Esperar para ver si ocurre el cambio
        time.sleep(2)

        # ⌨️ Enviar Enter para confirmar la acción
        pyautogui.press("enter")
        print("✅ Enter presionado después del clic.")

        
        
    except Exception as e:
        print(f"❌ No se pudo completar la descarga de keywords con PyAutoGUI: {e}")

# ✅ CERRAR EL NAVEGADOR
def cerrar_navegador(driver):
    try:
        time.sleep(5)
        driver.quit()
        print("🚪 Navegador cerrado correctamente.")
    except Exception as e:
        print(f"❌ Error al cerrar el navegador: {e}")

def click_skip_for_now(driver, wait):
    """
    Hace clic en el botón "Skip for now" para omitir la verificación de dos pasos.
    """
    try:
        skip_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Skip for now']")))
        skip_button.click()
        print("✅ Se hizo clic en 'Skip for now' correctamente.")
    except Exception as e:
        print(f"❌ No se pudo hacer clic en 'Skip for now': {e}")

def cerrar_alerta_superpuesta(driver, wait):
    """
    Hace clic en el botón de cerrar alerta superpuesta si está presente.
    """
    try:
        # Esperar a que el botón esté presente y sea clickeable
        close_alert_button = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//material-button[@aria-label='Hide']//material-ripple[@class='_ngcontent-awn-AWSM-15']"))
        )
        # Hacer clic en el botón
        close_alert_button.click()
        print("✅ Se hizo clic en el botón para cerrar la alerta superpuesta.")
    except TimeoutException:
        print("ℹ️ No se encontró ninguna alerta superpuesta para cerrar.")
    except Exception as e:
        print(f"❌ Error al intentar cerrar la alerta superpuesta: {e}")

def login_automation_with_cookies(cookie_file='new_account_cookies.pkl'):
    driver, wait = iniciar_driver()
    driver.get("https://ads.google.com")  # Abre el dominio raíz primero

    # Cargar cookies
    with open(cookie_file, 'rb') as file:
        cookies = pickle.load(file)
        for cookie in cookies:
            cookie.pop('sameSite', None)
            try:
                driver.add_cookie(cookie)
            except Exception as e:
                print(f"⚠️ No se pudo agregar la cookie: {cookie.get('name')} - {e}")

    driver.get("https://ads.google.com/aw/campaigns?ocid=6878459735&euid=1390636465&__u=7102183785&uscid=6878459735&__c=5101490015&authuser=1&workspaceId=0&subid=us-en-awhp-g-aw-c-home-signin-bgc!o2-ahpm-0000000188-0000000001%7C-ahpm-0000000179-0000000001%7C-ahpm-0000000182-0000000001")  # Ahora sí, ve a la página deseada
    time.sleep(5)

    print("✅ Login con cookies realizado. Google Ads debería estar autenticado.")
    return driver, wait



def login_automation(cookie_file='new_account_cookies.pkl'):
    """
    Login automático usando cookies generados previamente.
    """
    return login_automation_with_cookies(cookie_file)

# ✅ KEYWORD PLANNER AUTOMATION
def keyword_planner_automation(driver, wait, keywords, url=None):
    time.sleep(5)
    ir_a_keyword_planner(driver, wait)
    agregar_keywords(driver, wait, keywords)
    time.sleep(10)
    obtener_resultados(driver, wait)
    descargar_keywords_pygui()
    print(f"✅ Automatización completada para: {keywords}")

if __name__ == "__main__":
    driver, wait = login_automation()
    ir_a_keyword_planner(driver, wait)
    cerrar_navegador(driver)