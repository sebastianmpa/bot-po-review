import os
import requests
import logging
import time
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

API_HOST = os.getenv("API_TASK_CHUNK_HOST", "10.1.10.65")
API_PORT = os.getenv("API_TASK_CHUNK_PORT", "3000")
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/api/task-chunk/v0"

# Configuración de logging
LOGS_DIR = os.path.join(os.path.dirname(__file__), "../../../logs")
os.makedirs(LOGS_DIR, exist_ok=True)
log_file = os.path.join(LOGS_DIR, "chunk_api.log")

logger = logging.getLogger("chunk_api_logger")
logger.setLevel(logging.INFO)
handler = TimedRotatingFileHandler(log_file, when="midnight", backupCount=7, encoding="utf-8")
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

def register_chunk_item(data: dict):
    """
    Envía un PATCH a la ruta /chunk-progress con el JSON proporcionado y guarda la respuesta en logs.
    Realiza hasta 3 reintentos en caso de error, con los siguientes intervalos:
    1er reintento: 15 segundos
    2do reintento: 2 minutos
    3er reintento: 15 minutos
    :param data: Diccionario que contiene los datos a enviar en el cuerpo de la solicitud.
    :return: Respuesta de la API o el último error.
    """
    url = f"{API_BASE_URL}/chunk-progress"
    headers = {"Content-Type": "application/json"}
    retries = [
        15,      # 1er reintento: 15 segundos
        120,     # 2do reintento: 2 minutos
        900      # 3er reintento: 15 minutos
    ]

    attempt = 0
    while attempt <= len(retries):
        try:
            response = requests.patch(url, json=data, headers=headers, timeout=15)

            # Capturar errores HTTP con detalle del body de respuesta
            if not response.ok:
                try:
                    error_body = response.json()
                except Exception:
                    error_body = response.text

                error_msg = (
                    f"HTTP {response.status_code} {response.reason} | "
                    f"URL: {url} | "
                    f"Response body: {error_body}"
                )
                logger.error(f"ERROR HTTP: {error_msg} | REQUEST: {data}")
                print(f"❌ Error HTTP al enviar chunk: {error_msg}")

                if attempt < len(retries):
                    wait_time = retries[attempt]
                    print(f"🔄 Reintentando en {wait_time} segundos... (Intento {attempt+1} de {len(retries)})")
                    time.sleep(wait_time)
                    attempt += 1
                    continue
                else:
                    return {"error": error_msg, "status_code": response.status_code, "body": error_body}

            resp_json = response.json()
            logger.info(f"REQUEST: {data}")
            logger.info(f"RESPONSE: {resp_json}")
            print(f"✅ Chunk API respondió correctamente: {resp_json}")
            return resp_json

        except requests.exceptions.ConnectionError as e:
            error_msg = f"No se pudo conectar a {url} | Verifica que el servidor esté activo | Detalle: {str(e)}"
            logger.error(f"CONNECTION ERROR: {error_msg} | REQUEST: {data}")
            print(f"❌ Error de conexión: {error_msg}")
        except requests.exceptions.Timeout as e:
            error_msg = f"Timeout al conectar con {url} (límite: 15s) | Detalle: {str(e)}"
            logger.error(f"TIMEOUT ERROR: {error_msg} | REQUEST: {data}")
            print(f"❌ Timeout: {error_msg}")
        except requests.exceptions.RequestException as e:
            error_msg = f"Error inesperado en la solicitud PATCH a {url} | Detalle: {str(e)}"
            logger.error(f"REQUEST ERROR: {error_msg} | REQUEST: {data}")
            print(f"❌ Error en la solicitud: {error_msg}")

        if attempt < len(retries):
            wait_time = retries[attempt]
            print(f"🔄 Reintentando en {wait_time} segundos... (Intento {attempt+1} de {len(retries)})")
            time.sleep(wait_time)
            attempt += 1
        else:
            final_error = f"Se agotaron los {len(retries)} reintentos al enviar chunk a {url}"
            logger.error(f"MAX RETRIES: {final_error} | REQUEST: {data}")
            print(f"❌ {final_error}")
            return {"error": final_error}

if __name__ == "__main__":
    # Ejemplo de uso para probar la función register_chunk_item
    test_data = {
        "chunkId": "test-chunk-123",
        "item": {
            "product_video": {
                "url": "https://ejemplo.com/producto",
                "video_url": "https://ejemplo.com/video.mp4",
                "video_id": "abc123",
                "note": "Prueba de registro de chunk"
            }
        },
        "status": "Success"
    }
    print("Enviando datos de prueba a /chunk-progress ...")
    result = register_chunk_item(test_data)
    print("Respuesta de la API:")
    print(result)