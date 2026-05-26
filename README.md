# API-Bots: Automatizaciones con FastAPI

## 📌 Descripción

Esta API está desarrollada con FastAPI y sigue una arquitectura basada en MVC (Model-View-Controller). Su propósito es manejar múltiples automatizaciones a través de endpoints organizados en cuatro bots.

Cada bot tiene su propio conjunto de controladores, modelos y servicios para garantizar la modularidad y escalabilidad del código.

## 📂 Estructura del Proyecto

📦 API-Bots/
├── 📂 app/
│   ├── 📂 controllers/       # Controladores (manejan la lógica de los endpoints)
│   │   ├── 📄 bot1_controller.py
│   │   ├── 📄 bot2_controller.py
│   │   ├── 📄 bot3_controller.py
│   │   ├── 📄 bot4_controller.py
│   │   ├── 📄 common_controller.py  # Para endpoints generales (opcional)
│   │   ├── 📄 __init__.py
│   │
│   ├── 📂 models/            # Modelos Pydantic (esquemas de datos)
│   │   ├── 📄 bot1_model.py
│   │   ├── 📄 bot2_model.py
│   │   ├── 📄 bot3_model.py
│   │   ├── 📄 bot4_model.py
│   │   ├── 📄 __init__.py
│   │
│   ├── 📂 routes/            # Definición de rutas (organización modular)
│   │   ├── 📄 bot1_routes.py
│   │   ├── 📄 bot2_routes.py
│   │   ├── 📄 bot3_routes.py
│   │   ├── 📄 bot4_routes.py
│   │   ├── 📄 __init__.py
│   │
│   ├── 📂 services/          # Lógica de negocio / automatizaciones
│   │   ├── 📄 bot1_service.py
│   │   ├── 📄 bot2_service.py
│   │   ├── 📄 bot3_service.py
│   │   ├── 📄 bot4_service.py
│   │   ├── 📄 __init__.py
│   │
│   ├── 📂 config/            # Configuraciones (base de datos, env, etc.)
│   │   ├── 📄 settings.py
│   │   ├── 📄 database.py
│   │   ├── 📄 __init__.py
│   │
│   ├── 📂 utils/             # Funciones auxiliares o comunes
│   │   ├── 📄 helpers.py
│   │   ├── 📄 logger.py
│   │   ├── 📄 __init__.py
│   │
│   ├── 📄 main.py            # Punto de entrada de la API
│
├── 📄 .env                   # Variables de entorno
├── 📄 requirements.txt        # Dependencias de Python
├── 📄 .gitignore              # Archivos a ignorar en Git
└── 📄 README.md               # Documentación del proyecto



## 🚀 Instalación y Configuración

1️⃣ Clonar el repositorio
```sh
git clone https://github.com/sebastianmpa/api-bots.git
cd api-bots

## 🚀 Instalación y Configuración
python -m venv venv
# Activar el entorno:
# En Windows:
venv\Scripts\activate
# En Mac/Linux:
source venv/bin/activate


pip install -r requirements.txt


uvicorn app.main:app --reload