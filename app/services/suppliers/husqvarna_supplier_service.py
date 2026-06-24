"""
husqvarna_supplier_service.py
-----------------------------
Implementación concreta de SupplierService para Husqvarna (supplerID = "HU").

CSV esperado por Husqvarna: SKU | QUANTITY | COMMENT | DATE

Precio a comparar: your_price (scraped del carrito #cart-delivery-cart-table)

Lógica de kit/paquete:
  Si cart_qty > requested_qty → el producto es un kit.
  Se usa cart_qty como qty de respuesta y se añade nota en error_message.
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
from seo_scripts.husqvarna_login_playwright import husqvarna_login_automation_playwright
from seo_scripts.insert_data_in_db import insert_po_review_details


class HusqvarnaSupplierService(SupplierService):
    """
    Estrategia concreta para Husqvarna Group.

    CSV de importación: SKU | QUANTITY | COMMENT | DATE
      - COMMENT y DATE se envían vacíos.
    Precio a comparar: msrp (columna scrapeada del portal).
    Ejecución: secuencial, un archivo CSV por PO.
    """

    # ------------------------------------------------------------------ #
    #  Identificación                                                       #
    # ------------------------------------------------------------------ #

    @property
    def supplier_id(self) -> str:
        return "HU"

    @property
    def supplier_name(self) -> str:
        return "Husqvarna Group"

    # ------------------------------------------------------------------ #
    #  Credenciales                                                         #
    # ------------------------------------------------------------------ #

    def get_credentials(self) -> Dict[str, str]:
        return {
            "email": os.getenv("HUSQVARNA_EMAIL", "danielam.prontomowers@gmail.com"),
            "password": os.getenv("HUSQVARNA_PASSWORD", "Chainsaw01"),
        }

    # ------------------------------------------------------------------ #
    #  Formato CSV                                                          #
    # TODO: Confirmar columnas exactas que espera el portal Husqvarna      #
    # ------------------------------------------------------------------ #

    def csv_headers(self) -> List[str]:
        """Husqvarna espera: SKU | QUANTITY | COMMENT | DATE"""
        return ["SKU", "QUANTITY", "COMMENT", "DATE"]

    def csv_row(self, product: PurchaseOrderItemModel) -> List:
        """
        Fila del CSV para Husqvarna.
        - SKU      → partNumber
        - QUANTITY → qty
        - COMMENT  → vacío (opcional)
        - DATE     → vacío (opcional)
        """
        return [product.partNumber, product.qty, "", ""]

    # ------------------------------------------------------------------ #
    #  Automatización                                                       #
    # ------------------------------------------------------------------ #

    def _cleanup(self, csv_path, final_csv_path) -> None:
        """
        Limpieza desactivada temporalmente para Husqvarna.
        Los archivos CSV generados se conservan en disco para inspección.
        """
        print("⏸️  [Husqvarna] Limpieza de archivos desactivada temporalmente.")
        if csv_path:
            print(f"📁 CSV temp conservado: {csv_path}")
        if final_csv_path:
            print(f"📁 CSV Downloads conservado: {final_csv_path}")

    def run_automation(
        self,
        email: str,
        password: str,
        csv_filename: str,
        po_data: Optional[PurchaseOrderDataModel] = None,
        **kwargs,
    ) -> Optional[List[Dict]]:
        """
        Delega en el script Playwright de Husqvarna.
        Pasa requested_qtys para detección de kits.
        """
        requested_qtys: Dict[str, int] = {}
        if po_data is not None:
            requested_qtys = {p.partNumber: p.qty for p in po_data.products}

        return husqvarna_login_automation_playwright(
            email, password, csv_filename, requested_qtys
        )

    # ------------------------------------------------------------------ #
    #  Procesamiento de resultados                                          #
    # ------------------------------------------------------------------ #

    def process_results(
        self,
        scraped_data: List[Dict],
        po_data: PurchaseOrderDataModel,
    ) -> List[PurchaseOrderResponseProduct]:
        """
        Compara `your_price` (del carrito) con `idealCost` de la PO.
        Tolerancia: 1 % del ideal_cost.

        Campos scrapeados por husqvarna_login_playwright:
          part_number, description, warehouse_qty, est_ship_date,
          qty (carrito), requested_qty, is_kit,
          tiered_price, your_price,
          status (CORRECT | PART_ERROR pre-asignado), error_message

        Statuses de salida:
          CORRECT    → precio dentro de la tolerancia
          MISMATCH   → precio fuera de la tolerancia
          PART_ERROR → sin your_price o marcado como error en scraper
                       (ej. "not found by cross-referencing")
          KIT        → is_kit=True + precio correcto (nota incluida)
        """
        ideal_costs: Dict[str, float] = {
            p.partNumber: p.idealCost for p in po_data.products
        }
        # Tomar mfrid directamente del PO (el scraper siempre lo deja vacío)
        mfrid_map: Dict[str, str] = {
            p.partNumber: p.mfrid for p in po_data.products
        }

        response_products: List[PurchaseOrderResponseProduct] = []

        for item in scraped_data:
            part_number: str = item.get("part_number", "")

            # Enriquecer mfrid desde el PO — prioridad: PO > scraper
            item["mfrid"] = mfrid_map.get(part_number, item.get("mfrid", ""))
            your_price: Optional[Decimal] = item.get("your_price")
            pre_status: str = item.get("status", "CORRECT")
            error_message: Optional[str] = item.get("error_message")
            pack_qty: Optional[int] = item.get("pack_qty")
            nla: Optional[str] = item.get("nla")
            superseded_from: Optional[str] = item.get("superseded_from")
            cart_qty: int = item.get("qty", 0)

            ideal_cost = ideal_costs.get(part_number, 0.0)
            price_float = float(your_price) if your_price is not None else 0.0

            # ── 1. Statuses pre-asignados por el scraper ──────────────────
            if pre_status == "SUPERSEDED":
                status = "SUPERSEDED"
                print(f"  🔄 SUPERSEDED [{part_number}] ← {superseded_from}")

            elif pre_status == "NLA":
                status = "NLA"
                print(f"  🚫 NLA [{part_number}]")

            elif pre_status == "PART_ERROR" and price_float == 0.0:
                # Ítem verdaderamente inválido: no encontrado o sin precio
                status = "PART_ERROR"
                print(f"  ⚠️ PART_ERROR [{part_number}]: {error_message}")

            # ── 2. Sin precio → PART_ERROR ────────────────────────────────────────────────
            elif price_float == 0.0 and ideal_cost > 0:
                status = "PART_ERROR"
                error_message = f"No 'your_price' available for {part_number}"
                print(f"  ⚠️ PART_ERROR (sin precio): {part_number}")

            # ── 3. Comparación de precios ─────────────────────────────────
            elif price_float > 0 and ideal_cost > 0:
                difference = abs(ideal_cost - price_float)
                tolerance = ideal_cost * 0.01

                pack_note = f" | PACK qty: {pack_qty}" if pack_qty else ""
                print(
                    f"  💵 [{part_number}] Ideal=${ideal_cost:.2f} | "
                    f"Your Price=${price_float:.2f} | "
                    f"Diff=${difference:.2f} | Tol=${tolerance:.2f}{pack_note}"
                )

                if difference > tolerance:
                    status = "MISMATCH"
                    error_message = (
                        f"Price mismatch: Expected ${ideal_cost:.2f}, "
                        f"Husqvarna Your Price ${price_float:.2f}"
                    )
                    if pack_qty:
                        error_message += f" (Pack item — min qty: {pack_qty})"
                    print(f"  ❌ MISMATCH: {part_number}")
                else:
                    status = "CORRECT"
                    if pack_qty:
                        error_message = f"Pack item — minimum qty: {pack_qty}"
                    print(f"  ✅ CORRECT: {part_number}")

            else:
                status = "CORRECT"

            # ── Escribir resultados finales al item (para el INSERT en BD) ────
            item['status'] = status
            item['ideal_cost'] = ideal_cost
            item['error_message'] = error_message

            response_products.append(
                PurchaseOrderResponseProduct(
                    mfrid=item.get("mfrid", ""),
                    partNumber=part_number,
                    qty=cart_qty,
                    idealCost=ideal_cost if ideal_cost > 0 else 0.0,
                    supplierPrice=price_float,
                    status=status,
                    nla=nla,
                    supersededFrom=superseded_from,
                    packQty=pack_qty,
                )
            )

        # Persistir en BD
        print("💾 Guardando datos Husqvarna en BD...")
        inserted = insert_po_review_details(scraped_data)
        print(f"✅ {inserted} filas insertadas en po_review_details")

        return response_products
