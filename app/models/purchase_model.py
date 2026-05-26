from pydantic import BaseModel
from typing import List, Optional, Union

class PurchaseOrderItemModel(BaseModel):
    mfrid: str  # Mapea a MANUFACTURER en el CSV
    partNumber: str  # Mapea a PART NUMBER en el CSV
    qty: int  # Mapea a QUANTITY en el CSV
    idealCost: float  # Costo ideal del producto
    definition: Optional[str] = None  # Descripción del producto

class PurchaseOrderDataModel(BaseModel):
    poNumber: str
    supplerID: str  # ID del proveedor (ej: "GA" para Gardner)
    products: List[PurchaseOrderItemModel]

class ProductToReviewDataModel(BaseModel):
    productToReview: Optional[List[PurchaseOrderDataModel]] = None  # Array de órdenes de compra (nuevo formato)

class SeoCategoryRequestModel(BaseModel):
    name: Optional[str] = None  # Nombre de la tarea
    description: Optional[str] = None  # Descripción de la tarea
    userId: Optional[str] = None  # ID del usuario que crea la tarea
    taskTypeId: Optional[str] = None  # ID del tipo de tarea
    chunkId: str
    # data puede ser formato nuevo (ProductToReviewDataModel con array) o antiguo (PurchaseOrderDataModel directo)
    data: Optional[Union[ProductToReviewDataModel, PurchaseOrderDataModel]] = None
    # Campos para compatibilidad con formato antiguo (deprecados)
    poNumber: Optional[str] = None
    supplerID: Optional[str] = None
    products: Optional[List[PurchaseOrderItemModel]] = None



class PurchaseOrderResponseProduct(BaseModel):
    mfrid: str
    partNumber: str
    qty: int
    idealCost: float
    supplierPrice: Optional[float] = None
    status: str  # "CORRECT" o "MISMATCH"

class PurchaseOrderResponseData(BaseModel):
    poNumber: str
    supplerID: str
    products: List[PurchaseOrderResponseProduct]

class ResponseBlogModel(BaseModel):
    chunkId: str
    item: List[PurchaseOrderResponseData]  # Array de respuestas para múltiples POs
    status: str  # "success" o "failed"
    
    def dict(self, *args, **kwargs):
        """Override dict() para serializar correctamente el modelo al chunk API.
        - chunkId: tal cual llega
        - item: siempre objeto (nunca array)
        - status: 'Success' o 'Failed' según requiere la API
        """
        # Serializar cada PO usando model_dump para evitar doble escape
        items_list = [po.model_dump() for po in self.item]
        # La API espera item como objeto, no array
        if len(items_list) == 0:
            item_obj = {"products": []}
        elif len(items_list) == 1:
            item_obj = items_list[0]
        else:
            item_obj = {"orders": items_list}
        # Capitalizar status: 'success' -> 'Success', 'failed' -> 'Failed'
        status_value = self.status.capitalize()
        return {
            "chunkId": self.chunkId,
            "item": item_obj,
            "status": status_value
        }