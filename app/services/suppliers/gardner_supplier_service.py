"""
gardner_supplier_service.py
---------------------------
Implementación concreta de SupplierService para Gardner Inc (supplerID = "GA").

CSV esperado por Gardner:  MANUFACTURER | PART NUMBER | QUANTITY
Precio a comparar:         your_price  (columna scrapeada)
"""

import os
from typing import List, Dict, Optional
from decimal import Decimal

from models.purchase_model import (
    PurchaseOrderDataModel,
    PurchaseOrderItemModel,
    PurchaseOrderResponseProduct,
)
from services.suppliers.base_supplier_service import SupplierService
from seo_scripts.gardner_login_playwright import gardner_login_automation_playwright
from seo_scripts.insert_data_in_db import insert_po_review_details


# Mapping: código de 3 letras del portal Gardner → código Ideal (sistema interno)
# El portal acortó algunos mfrid a 3 dígitos; hay que restaurarlos antes de guardar.
_GARDNER_MFRID_NORMALIZE: Dict[str, str] = {
    'AGR': 'AGRI',
    'HYG': 'HG',
    'HOM': 'HOME',
    'TIL': 'TILL',
    'WLB': 'WALB',
    'TUF': 'TUFF',
}


class GardnerSupplierService(SupplierService):
    """
    Estrategia concreta para Gardner Inc.
    Credenciales cargadas desde variables de entorno o valores por defecto.
    """

    # ------------------------------------------------------------------ #
    #  Identificación                                                       #
    # ------------------------------------------------------------------ #

    @property
    def supplier_id(self) -> str:
        return "GA"

    @property
    def supplier_name(self) -> str:
        return "Gardner Inc"

    # ------------------------------------------------------------------ #
    #  Credenciales                                                         #
    # ------------------------------------------------------------------ #

    def get_credentials(self) -> Dict[str, str]:
        return {
            "email": os.getenv("GARDNER_EMAIL", "jacobn.prontomowers+75145@gmail.com"),
            "password": os.getenv("GARDNER_PASSWORD", "Pronto123#"),
        }

    # ------------------------------------------------------------------ #
    #  Formato CSV                                                          #
    # ------------------------------------------------------------------ #

    def csv_headers(self) -> List[str]:
        """Gardner espera: MANUFACTURER | PART NUMBER | QUANTITY"""
        return ["MANUFACTURER", "PART NUMBER", "QUANTITY"]

    def csv_row(self, product: PurchaseOrderItemModel) -> List:
        return [product.mfrid, product.partNumber, product.qty]

    # ------------------------------------------------------------------ #
    #  Automatización                                                       #
    # ------------------------------------------------------------------ #

    def run_automation(self, email: str, password: str, csv_filename: str, **kwargs) -> Optional[List[Dict]]:
        """Delega en el script Playwright de Gardner."""
        return gardner_login_automation_playwright(email, password, csv_filename)

    # ------------------------------------------------------------------ #
    #  Procesamiento de resultados                                          #
    # ------------------------------------------------------------------ #

    def process_results(
        self,
        scraped_data: List[Dict],
        po_data: PurchaseOrderDataModel,
    ) -> List[PurchaseOrderResponseProduct]:
        """
        Compara `your_price` (precio Gardner) con `idealCost` de la PO.
        Tolerancia: 1 % del ideal_cost.

        Statuses posibles:
          CORRECT    → diferencia dentro de la tolerancia
          MISMATCH   → diferencia supera la tolerancia
          PART_ERROR → sin precio del supplier o parte no encontrada
          SUPERSEDED → parte reemplazada (el scraper ya lo marcó)
        """
        # Índice rápido por partNumber
        ideal_costs: Dict[str, float] = {
            p.partNumber: p.idealCost for p in po_data.products
        }
        # Índice por mfrid+partNumber concatenado (para partes con MFRID vacío)
        products_by_concat: Dict[str, PurchaseOrderItemModel] = {
            f"{p.mfrid}{p.partNumber}": p for p in po_data.products
        }

        response_products: List[PurchaseOrderResponseProduct] = []

        for item in scraped_data:
            part_number: str = item["part_number"]
            supplier_price: Optional[Decimal] = item.get("your_price")

            # Normalizar mfrid Gardner → Ideal antes de cualquier lookup o salida.
            # El portal Gardner recorta algunos códigos a 3 letras (AGR, HYG, …);
            # los convertimos al código del sistema Ideal (AGRI, HG, …).
            raw_mfrid = (item.get("mfrid") or "").strip()
            item["mfrid"] = _GARDNER_MFRID_NORMALIZE.get(raw_mfrid.upper(), raw_mfrid)

            # --- Resolver ideal_cost y mfrid ---
            ideal_cost = ideal_costs.get(part_number, 0.0)

            if not item.get("mfrid") and ideal_cost == 0.0:
                matched = products_by_concat.get(part_number)
                if matched:
                    item["mfrid"] = matched.mfrid
                    ideal_cost = matched.idealCost

            if ideal_cost == 0.0:
                for product in po_data.products:
                    if product.partNumber == part_number:
                        if not item.get("mfrid"):
                            item["mfrid"] = product.mfrid
                        ideal_cost = product.idealCost
                        break

            item["ideal_cost"] = ideal_cost if ideal_cost > 0 else 0.0

            if supplier_price is None:
                supplier_price_float = 0.0
            else:
                supplier_price_float = float(supplier_price)

            # --- Calcular status ---
            # Respetar SUPERSEDED marcado por el scraper
            if item.get('status') == 'SUPERSEDED':
                status = 'SUPERSEDED'
                item['status'] = 'SUPERSEDED'

            elif item.get('status') == 'NLA':
                status = 'PART_ERROR'
                item['status'] = 'PART_ERROR'

            elif item.get('status') == 'PART_ERROR' and supplier_price_float == 0.0:
                # Ítem verdaderamente inválido: no encontrado o sin precio
                status = "PART_ERROR"
                item["status"] = "PART_ERROR"

            elif supplier_price_float > 0 and ideal_cost > 0:
                difference = abs(ideal_cost - supplier_price_float)
                tolerance = ideal_cost * 0.01

                print(
                    f"💵 [{part_number}] Ideal=${ideal_cost:.2f} | "
                    f"Gardner=${supplier_price_float:.2f} | "
                    f"Diff=${difference:.2f} | Tol=${tolerance:.2f}"
                )

                if difference > tolerance:
                    status = "MISMATCH"
                    item["status"] = "PRICE_MISMATCH"
                    item["error_message"] = (
                        f"Price mismatch: Expected ${ideal_cost:.2f}, "
                        f"Gardner ${supplier_price_float:.2f}"
                    )
                    print(f"❌ MISMATCH en {part_number}")
                else:
                    status = "CORRECT"
                    item["status"] = "CORRECT"
                    print(f"✅ CORRECT: {part_number}")

            elif supplier_price_float == 0 and ideal_cost > 0:
                status = "PART_ERROR"
                item["status"] = "PART_ERROR"
                if not item.get("error_message"):
                    item["error_message"] = f"No supplier price available for {part_number}"
                print(f"⚠️ PART_ERROR (sin precio): {part_number}")

            else:
                # Ni precio del supplier ni ideal_cost: marcar como CORRECT por defecto
                status = "CORRECT"
                item["status"] = "CORRECT"

            response_products.append(
                PurchaseOrderResponseProduct(
                    mfrid=item.get('mfrid', ''),
                    partNumber=part_number,
                    qty=item.get('qty', 0),
                    idealCost=ideal_cost if ideal_cost > 0 else 0.0,
                    supplierPrice=supplier_price_float,
                    status=status,
                    nla=item.get('nla'),
                    supersededFrom=item.get('superseded_from'),
                    packQty=item.get('pack_qty'),
                )
            )

        # Persistir en BD
        print("💾 Guardando datos Gardner en BD...")
        inserted = insert_po_review_details(scraped_data)
        print(f"✅ {inserted} filas insertadas en po_review_details")

        return response_products
