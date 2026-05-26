import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv('.env')

def get_db_connection():
    """Establece una conexión a la base de datos MySQL utilizando variables de entorno."""
    return mysql.connector.connect(
        host=os.getenv('DB_HOSTNAME'),
        user=os.getenv('DB_USERNAME'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_DATABASE')
    )

def get_store_descriptions():
    """Obtiene las descripciones de las tiendas y sus tipos desde la base de datos."""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DESCRIPTION, store_type FROM prontoweb.stores;")
        stores = cursor.fetchall()
        cursor.close()
        connection.close()
        return stores
    except mysql.connector.Error as err:
        print(f"Error: {err}")
        return []
    
def get_store_url_by_id(store_id):
    """
    Obtiene el campo URLSTORE de la tabla prontoweb.stores usando el id de la tienda.
    :param store_id: ID de la tienda.
    :return: URLSTORE si existe, None si no se encuentra o hay error.
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT URLSTORE FROM prontoweb.stores WHERE id = %s;", (store_id,))
        result = cursor.fetchone()
        cursor.close()
        connection.close()
        return result[0] if result else None
    except mysql.connector.Error as err:
        print(f"Error: {err}")
        return None