"""
wesco_supplier_service.py
--------------------------------
Implementación concreta de SupplierService para Wesco / WescoTurf.

Flujo:
  - Usa el Template Method de `SupplierService` para crear CSV y copiar a Downloads.
  - Ejecuta `wesco_automation_playwright()` pasando `po_items` para propagar `mfrid`.
  - Procesa resultados comparando `your_price` con `idealCost` (tolerancia 1%).
  - Persiste los datos crudos en `po_review_details`.
"""

import os
from typing import List, Dict, Optional
from decimal import Decimal

from models.purchase_model import (
    PurchaseOrderDataModel,
    PurchaseOrderItemModel,
    PurchaseOrderResponseProduct,
    PurchaseOrderResponseData,
)
from services.suppliers.base_supplier_service import SupplierService
from seo_scripts.wesco_playwright import wesco_automation_playwright
from seo_scripts.insert_data_in_db import insert_po_review_details


class WescoSupplierService(SupplierService):
    """
    Estrategia concreta para WescoTurf (supplerID = "WE").
    """

    @property
    def supplier_id(self) -> str:
        return "WE"

    @property
    def supplier_name(self) -> str:
        return "WescoTurf"

    def get_credentials(self) -> Dict[str, str]:
        return {
            "email": os.getenv("WESCO_USERNAME", "admin@prontomowers.com"),
            "password": os.getenv("WESCO_PASSWORD", "pRONTO2023!"),
        }

    def csv_headers(self) -> List[str]:
        # Minimal CSV: Item, Quantity
        return ["Item", "Quantity"]

    def csv_row(self, product: PurchaseOrderItemModel) -> List:
        return [product.partNumber, product.qty]

    def run_automation(
        self,
        email: str,
        password: str,
        csv_filename: str,
        po_data: Optional[PurchaseOrderDataModel] = None,
        po_items: Optional[List[Dict]] = None,
        **kwargs,
    ) -> Optional[List[Dict]]:
        # Build po_items mapping to pass manufacturer ids to the scraper
        items = po_items or []
        if not items and po_data is not None:
            items = [
                {"part_number": p.partNumber, "qty": p.qty, "mfrid": p.mfrid, "mfrid_orig": p.mfrid_orig}
                for p in po_data.products
            ]

        return wesco_automation_playwright(
            username=email,
            password=password,
            csv_filename=csv_filename,
            po_items=items,
            po_data=po_data,
        )

    def process_results(
        self,
        scraped_data: List[Dict],
        po_data: PurchaseOrderDataModel,
    ) -> List[PurchaseOrderResponseProduct]:
        ideal_costs: Dict[str, float] = {p.partNumber: p.idealCost for p in po_data.products}
        mfrid_map: Dict[str, str] = {p.partNumber: p.mfrid for p in po_data.products}

        response_products: List[PurchaseOrderResponseProduct] = []

        for item in scraped_data:
            part_number: str = item.get("part_number", "")
            scraped_mfrid = item.get("mfrid", "")
            item["mfrid"] = scraped_mfrid if scraped_mfrid else mfrid_map.get(part_number, "")
            your_price = item.get("your_price")
            pre_status: str = item.get("status", "CORRECT")
            error_message = item.get("error_message")
            cart_qty = int(item.get("qty", 0) or 0)
            in_stock = item.get("in_stock")

            ideal_cost = ideal_costs.get(part_number, 0.0)
            item["ideal_cost"] = ideal_cost
            price_float = float(your_price) if your_price is not None else 0.0

            if pre_status == "PART_ERROR" and price_float == 0.0:
                # Ítem verdaderamente inválido: no encontrado o sin precio
                status = "PART_ERROR"
            elif pre_status == "SUPERSEDED":
                status = "SUPERSEDED"
            elif price_float == 0.0:
                # No price parsed — keep as CORRECT for now (cart may update later)
                status = "CORRECT"
            elif price_float > 0 and ideal_cost > 0:
                difference = abs(ideal_cost - price_float)
                tolerance = ideal_cost * 0.01
                if difference > tolerance:
                    status = "MISMATCH"
                    error_message = f"Price mismatch: Expected ${ideal_cost:.2f}, Wesco ${price_float:.2f}"
                else:
                    status = "CORRECT"
            else:
                status = "CORRECT"

            item["status"] = status

            response_products.append(
                PurchaseOrderResponseProduct(
                    mfrid=item.get("mfrid", ""),
                    partNumber=part_number,
                    qty=cart_qty,
                    idealCost=ideal_cost if ideal_cost > 0 else 0.0,
                    supplierPrice=float(price_float),
                    status=status,
                    nla=None,
                    supersededFrom=item.get("superseded_from"),
                    packQty=item.get("pack_qty"),
                    ltl=None,
                )
            )

        # Persist raw scraped rows in DB
        print("💾 Guardando datos Wesco en BD...")
        try:
            inserted = insert_po_review_details(scraped_data)
            print(f"✅ {inserted} filas insertadas en po_review_details")
        except Exception as e:
            print(f"⚠️ Error al insertar en BD: {e}")

        return response_products
