import os
import time
import pickle
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from dotenv import load_dotenv
from webdriver_manager.chrome import ChromeDriverManager

# Cargar las variables de entorno desde el archivo .env
load_dotenv('.env')

def generate_cookies(email, password, cookie_file):
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.maximize_window()

    try:
        # 1. Login en Google
        driver.get('https://accounts.google.com/InteractiveLogin/signinchooser?continue=https%3A%2F%2Fads.google.com%2Faw%2Fcampaigns')
        email_input = driver.find_element(By.ID, 'identifierId')
        email_input.clear()
        email_input.send_keys(email)
        email_input.send_keys(Keys.ENTER)

        password_input = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.NAME, "Passwd"))
        )
        password_input.clear()
        password_input.send_keys(password)
        password_input.send_keys(Keys.ENTER)

        # 2. Esperar a que completes el MFA manualmente
        input("🔐 Completa la autenticación multifactor (MFA) en el navegador y presiona Enter aquí para continuar...")

        # 3. Ir a Google Ads (esto asegura que las cookies de ads.google.com se generen)
        driver.get('https://ads.google.com/aw/campaigns')
        input("🔎 Cuando veas que Google Ads está completamente cargado, presiona Enter aquí para guardar las cookies...")

        # 4. Guardar cookies en un archivo
        cookies = driver.get_cookies()
        with open(cookie_file, 'wb') as file:
            pickle.dump(cookies, file)
        print(f"✅ Cookies saved to {cookie_file}")

    except Exception as e:
        print(f"❌ Error during login: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    # Obtener las variables de entorno
    email = os.getenv('EMAIL')
    password = os.getenv('PASSWORD')
    cookie_file = 'new_account_cookies.pkl'

    # Generar cookies para la cuenta
    generate_cookies(email, password, cookie_file)