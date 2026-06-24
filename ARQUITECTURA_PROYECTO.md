# 🏗️ Arquitectura del Proyecto de Scraping de Órdenes de Compra

## 📊 Flujo General del Sistema

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. REQUEST LLEGA A FASTAPI                                      │
│    POST /start-purchase-order-automation                        │
│    {                                                            │
│      "chunkId": "abc123",                                       │
│      "data": {                                                  │
│        "productToReview": [                                     │
│          {                                                      │
│            "poNumber": "PO-001",                                │
│            "supplerID": "GA|HU|SP|FO",                          │
│            "products": [                                        │
│              {                                                  │
│                "mfrid": "MTD",        ✅ VIENE EN EL BODY       │
│                "partNumber": "123",                             │
│                "qty": 5,                                        │
│                "idealCost": 25.50                               │
│              }                                                  │
│            ]                                                    │
│          }                                                      │
│        ]                                                        │
│      }                                                          │
│    }                                                            │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. CONTROLLER: purchase_order_controller.py                     │
│    ✓ Parsea SeoCategoryRequestModel                             │
│    ✓ Detecta formato (antiguo vs nuevo)                         │
│    ✓ Arranca en background thread                               │
│    ✓ Retorna OK inmediatamente                                  │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. SERVICE ORQUESTADOR: purchase_order_service.py               │
│    ✓ _resolve_purchase_orders() → detecta formato               │
│    ✓ Para cada PO:                                              │
│      - Obtiene SupplierService via Factory                      │
│      - Llama supplier_service.execute(po_data)                  │
│    ✓ Construye respuesta final                                  │
│    ✓ Envía al TaskHub (Chunk API)                               │
└────────────────────┬────────────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
         ▼                       ▼
    ┌─────────────┐        ┌─────────────┐
    │ Gardner(GA) │        │ Husqvarna   │
    │ Briggs(SP)  │   OR   │ (HU)        │
    │ Florida(FO) │        │ etc         │
    └──────┬──────┘        └──────┬──────┘
           │                      │
           └──────────┬───────────┘
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. BASE SUPPLIER SERVICE (Template Method)                      │
│    execute(po_data) → ORQUESTA EL FLUJO COMPLETO:              │
│                                                                 │
│    a) _create_csv()       → Genera CSV con headers             │
│    b) Copia a ~/Downloads → Playwright accede aquí             │
│    c) run_automation()    → PLAYWRIGHT ESPECÍFICO               │
│       - Login al portal                                         │
│       - Upload CSV                                              │
│       - Scraping tabla resultados                               │
│       - Extrae: part#, qty, price, stock, etc.                  │
│       - Retorna: List[Dict] con datos CRUDOS                    │
│    d) process_results()   → LÓGICA ESPECÍFICA DEL PROVEEDOR    │
│       - Enriquece con mfrid del request body                    │
│       - Compara precios vs idealCost                            │
│       - Detecta SUPERSEDED / NLA / PACKS / LTL                  │
│       - Calcula status final (CORRECT|MISMATCH|PART_ERROR|...)  │
│       - Retorna: List[PurchaseOrderResponseProduct]             │
│    e) insert_po_review_details() → Persiste en BD              │
│    f) _cleanup()         → Elimina archivos temporales          │
│                                                                 │
│    Retorna: PurchaseOrderResponseData con toda la info          │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. RESPONSE FINAL → CHUNK API (TaskHub)                         │
│    {                                                            │
│      "chunkId": "abc123",                                       │
│      "item": {                                                  │
│        "poNumber": "PO-001",                                    │
│        "supplerID": "GA",                                       │
│        "products": [                                            │
│          {                                                      │
│            "mfrid": "MTD",        ✅ PROPAGADO DESDE BODY       │
│            "partNumber": "123",                                 │
│            "qty": 5,                                            │
│            "idealCost": 25.50,                                  │
│            "supplierPrice": 24.99,                              │
│            "status": "CORRECT|MISMATCH|PART_ERROR|SUPERSEDED"   │
│            ...                                                  │
│          }                                                      │
│        ]                                                        │
│      },                                                         │
│      "status": "Success"                                        │
│    }                                                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Ciclo de Vida del MFRID (Manufacturer ID)

### **ENTRADA (Body)**
```json
{
  "products": [
    {
      "mfrid": "MTD",      👈 VIENE AQUÍ DESDE EL USUARIO
      "partNumber": "123"
    }
  ]
}
```

### **VIAJE POR EL SISTEMA**
```
1. Controller recibe el mfrid en PurchaseOrderItemModel.mfrid
   ├─ Se almacena en po_data.products[].mfrid
   └─ Se passa al execute() en SupplierService

2. Base service: execute()
   └─ Pasa po_data con mfrid a run_automation()

3. Playwright scraper (ej: briggs_login_playwright.py)
   ├─ Ejecuta automation
   ├─ Devuelve List[Dict] con mfrid: '' (VACÍO - no viene del portal)
   └─ Retorna: scraped_data

4. Base service: process_results()
   ├─ Enriquece mfrid desde po_mfr_map (creado a partir de po_data.products)
   ├─ Prioridad:
   │  1️⃣  po_mfr_map[part_number] → VIENE DEL REQUEST BODY ✅
   │  2️⃣  SUPERSEDED lookup       → Si fue reemplazada
   │  3️⃣  Fallback                → Ej: 'BRS' para Briggs
   └─ Resultado: mfrid con valor

5. Response: PurchaseOrderResponseProduct.mfrid ✅ PROPAGADO
```

### **PERSISTENCIA (BD)**
```
insert_po_review_details()
  └─ item["mfrid"] = ... ya enriquecido
     └─ INSERT INTO po_review_details (mfrid, ...) 
```

---

## 📦 Estructura de Servicios (Patrón Strategy)

```
SupplierService (ABC - interfaz)
├── Métodos abstractos (cada proveedor implementa):
│   ├── supplier_id: str                    # "GA", "HU", "SP", "FO"
│   ├── supplier_name: str                  # "Gardner Inc", etc.
│   ├── csv_headers() → List[str]           # Columnas esperadas
│   ├── csv_row(product) → List             # Mapeo a fila CSV
│   ├── get_credentials() → Dict            # Usuario/contraseña
│   ├── run_automation(...) → List[Dict]    # PLAYWRIGHT
│   └── process_results(...) → List[Response] # LÓGICA DEL PROVEEDOR
│
├── execute(po_data) → PurchaseOrderResponseData
│   └─ Template Method: orquesta TODOS los pasos
│
└── Implementaciones concretas:
    ├── GardnerSupplierService     (SP)
    ├── HusqvarnaSupplierService   (HU)
    ├── BriggsSupplierService      (SP)
    └── FloridaOutdoorSupplierService (FO)
```

---

## 🎯 Dónde se Calcula el STATUS

### **BriggsSupplierService.process_results() - Ejemplo:**

```python
def process_results(self, scraped_data, po_data):
    """
    Recibe:
      - scraped_data: [
          {
            "part_number": "123",
            "your_price": 24.99,
            "status": "CORRECT",      ← Pre-calculado por scraper
            "mfrid": ""               ← VACÍO del scraper
          }
        ]
      - po_data.products: [
          {
            "mfrid": "MTD",           ← VIENE DEL BODY ✅
            "partNumber": "123",
            "idealCost": 25.50
          }
        ]
    """
    
    for item in scraped_data:
        part_number = item["part_number"]
        
        # 1️⃣ ENRIQUECER MFRID desde el body
        item["mfrid"] = mfrid_map.get(part_number, 'BRS')  # ✅ DEL REQUEST
        
        # 2️⃣ COMPARAR PRECIOS
        ideal_cost = ideal_costs[part_number]
        your_price = item["your_price"]
        
        if abs(ideal_cost - your_price) > tolerance:
            item["status"] = "MISMATCH"  ← CALCULADO
        
        # 3️⃣ DETECTAR NLA / SUPERSEDED (del scraper)
        if item.get("nla") == "Y":
            item["status"] = "PART_ERROR"
        
        # 4️⃣ CREAR RESPONSE CON TODO ENRIQUECIDO
        response.append(
            PurchaseOrderResponseProduct(
                mfrid=item["mfrid"],     ✅ YA PROPAGADO
                status=item["status"]    ✅ YA CALCULADO
            )
        )
    
    return response
```

---

## 📝 Flujo de Datos MFRID en Florida Outdoor (NEW)

### **Cambio Reciente:**
En lugar de **scraping del mfrid** (que no estaba disponible en el portal FOE), 
ahora:

1. **Recibes `mfrid` en el body** ✅
2. **Lo propagas sin tratar de scrapearlo** ✅
3. **Lo insertas en resultados** ✅

```python
# florida_outdoor_playwright.py

def florida_outdoor_automation_playwright(username, password, po_items):
    # po_items contiene: [{"part_number": "...", "mfrid": "...", "qty": ...}]
    
    # 1. Crear mapa: part_number → mfrid (del body)
    po_mfr_map = {
        item.get('part_number'): item.get('mfrid', '')
        for item in po_items
    }
    
    # 2. Scraping normal (no intentamos extraer mfrid del portal)
    cart_items = _scrape_cart_with_details(page, requested_qtys, po_mfr_map)
    
    # 3. Ya viene enriquecido en cart_items
    return cart_items  # [{"mfrid": "MTD", "part_number": "123", ...}]
```

---

## 🗂️ Estructura de Directorios

```
app/
├── controllers/
│   └── purchase_order_controller.py    ← FastAPI endpoint
├── services/
│   ├── purchase_order_service.py       ← Orquestador
│   └── suppliers/
│       ├── __init__.py                 ← SupplierFactory
│       ├── base_supplier_service.py    ← Template Method
│       ├── gardner_supplier_service.py
│       ├── husqvarna_supplier_service.py
│       ├── briggs_supplier_service.py
│       └── florida_outdoor_supplier_service.py
├── seo_scripts/
│   ├── gardner_login_playwright.py     ← Scrapers específicos
│   ├── husqvarna_login_playwright.py
│   ├── briggs_login_playwright.py
│   ├── florida_outdoor_playwright.py
│   ├── insert_data_in_db.py            ← Persistencia
│   └── ...
├── models/
│   └── purchase_model.py               ← Pydantic models
└── utils/
    └── bd_mysql.py                     ← Conexiones BD
```

---

## ✨ Patrones de Diseño Utilizados

| Patrón | Dónde | Propósito |
|--------|-------|----------|
| **Strategy** | SupplierService + Factory | Intercambiar comportamiento por proveedor |
| **Template Method** | BaseSupplierService.execute() | Pasos fijos, detalles variables |
| **Factory** | SupplierFactory.get_supplier_service() | Crear el servicio correcto por ID |
| **Adapter** | csv_row() | Convertir modelo a formato esperado por portal |

---

## 🔧 Cómo Extender para un Nuevo Proveedor

1. **Crear clase concreta** que herede de `SupplierService`
2. **Implementar métodos abstractos:**
   - `supplier_id` / `supplier_name`
   - `csv_headers()` / `csv_row()`
   - `run_automation()` → Crea script Playwright
   - `process_results()` → Lógica específica de comparación
3. **Registrar en SupplierFactory** (`suppliers/__init__.py`)
4. **Listo!** El sistema automáticamente delegará a tu servicio cuando `supplerID` coincida

---

## 📊 Campos en PurchaseOrderResponseProduct

```python
class PurchaseOrderResponseProduct(BaseModel):
    mfrid: str                    ✅ ENRIQUECIDO desde body
    partNumber: str               ← Del scraping
    qty: int                      ← Del scraping + carrito
    idealCost: float              ← Del request body
    supplierPrice: Optional[float] ← Del scraping
    status: str                   ← CALCULADO
    nla: Optional[str]            ← Detectado (Y/N)
    supersededFrom: Optional[str] ← Detectado en descripción
    packQty: Optional[int]        ← Detectado (qty mínimo)
    ltl: Optional[str]            ← Detectado (Y/N)
```

