import os
import pandas as pd
import re
import json
from datetime import datetime

SOURCE_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")

def extract_datetime_from_filename(filename):
    """ Extrae la fecha y hora desde el nombre del archivo """
    match = re.search(r"(\d{4}-\d{2}-\d{2}) at (\d{2}_\d{2}_\d{2})", filename)
    if match:
        date_str, time_str = match.groups()
        time_str = time_str.replace("_", ":")  # Ajustar formato de hora
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    return None

def get_latest_csv_files(directory, num_files):
    files = [f for f in os.listdir(directory) if f.startswith("Keyword Stats") and f.endswith(".csv")]
    if not files:
        print(f"⚠ No se encontraron archivos CSV en la carpeta: {directory}")
        return []

    files = sorted(files, key=lambda f: extract_datetime_from_filename(f), reverse=True)
    return [os.path.join(directory, f) for f in files[:num_files]]

def clean_csv(file_path):
    try:
        
        df = pd.read_csv(file_path, skiprows=2, encoding="utf-16", delimiter="\t")
        df.columns = df.columns.str.strip()

        # Verificar que las columnas requeridas existan
        required_columns = ['Keyword', 'Avg. monthly searches', 'Competition', 'Competition (indexed value)']
        if not set(required_columns).issubset(df.columns):
            return None

        # Renombrar columnas para consistencia
        df = df[required_columns].rename(columns={
            'Avg. monthly searches': 'Avg_monthly_searches',
            'Competition (indexed value)': 'Competition_index'
        })

        # Convertir columnas a valores numéricos y rellenar valores faltantes con 0
        df['Avg_monthly_searches'] = pd.to_numeric(df['Avg_monthly_searches'], errors='coerce').fillna(0).astype(int)
        df['Competition_index'] = pd.to_numeric(df['Competition_index'], errors='coerce').fillna(0).astype(float)

        # Filtrar filas con valores válidos en 'Avg_monthly_searches'
        df = df[df['Avg_monthly_searches'] >= 0]

        # Seleccionar y ordenar las columnas necesarias
        df = df[['Keyword', 'Competition', 'Avg_monthly_searches', 'Competition_index']]
        df = df.sort_values(by='Avg_monthly_searches', ascending=False)
        
        return df
    except Exception as e:
        print(f"❌ Error al procesar {file_path}: {e}")
        return None

def merge_csv_files(num_files, return_json=False):
    latest_files = get_latest_csv_files(SOURCE_FOLDER, num_files)
    if len(latest_files) < num_files:
        print(f"⚠ No hay suficientes archivos CSV para fusionar. Se esperaban {num_files}, pero se encontraron {len(latest_files)}")
        return None

    cleaned_dfs = []
    for file in latest_files:
        cleaned_data = clean_csv(file)
        if cleaned_data is not None:
            cleaned_dfs.append(cleaned_data)
        else:
            print(f"⚠ No se pudo limpiar el archivo: {file}")

    if not cleaned_dfs:
        return None

    # Combinar los DataFrames limpios
    merged_df = pd.concat(cleaned_dfs, ignore_index=True).drop_duplicates()

    # Rellenar valores faltantes con 0 en las columnas específicas
    merged_df['Avg_monthly_searches'] = merged_df['Avg_monthly_searches'].fillna(0).astype(int)
    merged_df['Competition_index'] = merged_df['Competition_index'].fillna(0).astype(float)

    # Seleccionar las columnas necesarias
    merged_df = merged_df[['Keyword', 'Avg_monthly_searches', 'Competition', 'Competition_index']]

    # Ordenar por 'Avg_monthly_searches' en orden descendente
    merged_df = merged_df.sort_values(by='Avg_monthly_searches', ascending=False).reset_index(drop=True)

    # Limitar a los primeros 100 registros
    merged_df = merged_df.head(100)

    # Convertir a JSON si se solicita
    merged_json = merged_df.to_json(orient="records", force_ascii=False, indent=4)
    if return_json:
        return json.loads(merged_json)  # Devuelve JSON como dict
    else:
        print("⚠ No se ha especificado return_json=True para devolver el JSON.")
        return None