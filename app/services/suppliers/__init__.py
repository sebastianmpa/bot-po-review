"""
Paquete suppliers — implementaciones del patrón Strategy por proveedor.

Exports públicos:
  - SupplierService           → clase base abstracta
  - GardnerSupplierService    → estrategia GA
  - HusqvarnaSupplierService  → estrategia HU
  - BriggsSupplierService     → estrategia SP (pendiente)
  - get_supplier_service()    → factory principal
  - get_registered_suppliers()→ utilidad de diagnóstico
  - SupplierNotFoundError     → excepción de proveedor no registrado
"""

from services.suppliers.base_supplier_service import SupplierService
from services.suppliers.gardner_supplier_service import GardnerSupplierService
from services.suppliers.husqvarna_supplier_service import HusqvarnaSupplierService
from services.suppliers.briggs_supplier_service import BriggsSupplierService
from services.suppliers.supplier_factory import (
    get_supplier_service,
    get_registered_suppliers,
    SupplierNotFoundError,
)

__all__ = [
    "SupplierService",
    "GardnerSupplierService",
    "HusqvarnaSupplierService",
    "BriggsSupplierService",
    "get_supplier_service",
    "get_registered_suppliers",
    "SupplierNotFoundError",
]
