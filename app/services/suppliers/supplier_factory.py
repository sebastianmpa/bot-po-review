"""
supplier_factory.py
-------------------
Factory que mapea supplerID → instancia de SupplierService.

Patrón: Factory + Registry
  - El registro es un diccionario inmutable: { supplerID: clase }
  - get_supplier_service() devuelve la instancia correcta
  - Si el supplerID no está registrado lanza SupplierNotFoundError

Para agregar un nuevo proveedor:
  1. Crear su clase en services/suppliers/
  2. Importarla aquí
  3. Agregarla al diccionario _SUPPLIER_REGISTRY
"""

from typing import Dict, Type

from services.suppliers.base_supplier_service import SupplierService
from services.suppliers.gardner_supplier_service import GardnerSupplierService
from services.suppliers.husqvarna_supplier_service import HusqvarnaSupplierService
from services.suppliers.briggs_supplier_service import BriggsSupplierService
from services.suppliers.florida_outdoor_supplier_service import FloridaOutdoorSupplierService
from services.suppliers.cooks_power_supplier_service import CooksPowerSupplierService
from services.suppliers.wesco_supplier_service import WescoSupplierService


class SupplierNotFoundError(ValueError):
    """Se lanza cuando el supplerID no tiene una implementación registrada."""

    def __init__(self, supplier_id: str):
        registered = list(_SUPPLIER_REGISTRY.keys())
        super().__init__(
            f"Proveedor '{supplier_id}' no encontrado. "
            f"Proveedores disponibles: {registered}"
        )
        self.supplier_id = supplier_id


# ------------------------------------------------------------------ #
#  Registro de proveedores: supplerID → Clase                         #
#  Para registrar uno nuevo: agregar aquí                             #
# ------------------------------------------------------------------ #
_SUPPLIER_REGISTRY: Dict[str, Type[SupplierService]] = {
    "GA": GardnerSupplierService,             # Gardner Inc
    "HU": HusqvarnaSupplierService,           # Husqvarna Group
    "SP": BriggsSupplierService,              # Briggs & Stratton
    "FOE": FloridaOutdoorSupplierService,      # Florida Outdoor Equipment
    "CP": CooksPowerSupplierService,           # Cook's Power
    "HT": WescoSupplierService,               # WescoTurf
}


def get_supplier_service(supplier_id: str) -> SupplierService:
    """
    Retorna la instancia del servicio correspondiente al supplerID.

    :param supplier_id: Identificador del proveedor ("GA", "HU", "SP", ...)
    :return: Instancia de la clase concreta de SupplierService
    :raises SupplierNotFoundError: Si el supplerID no está registrado
    """
    supplier_class = _SUPPLIER_REGISTRY.get(supplier_id)
    if supplier_class is None:
        raise SupplierNotFoundError(supplier_id)
    return supplier_class()


def get_registered_suppliers() -> Dict[str, str]:
    """
    Retorna un diccionario con los proveedores registrados.
    Útil para endpoints de diagnóstico o documentación.

    :return: { supplerID: supplier_name }
    """
    return {
        sid: cls().supplier_name
        for sid, cls in _SUPPLIER_REGISTRY.items()
    }
