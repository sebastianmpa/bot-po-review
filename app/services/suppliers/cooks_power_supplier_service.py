"""
cooks_power_supplier_service.py
--------------------------------
Implementación concreta de SupplierService para Cook's Power (supplerID = "CP").

Diferencias clave respecto a los otros proveedores:
  - NO hay CSV / Excel de upload.
  - La entrada al portal es ítem por ítem via typeahead (Quick Order).
  - El método execute() se sobreescribe completamente para omitir la generación
    y copia de archivos y pasar po_data directamente a run_automation().
  - process_results() compara your_price (carrito) vs idealCost con tolerancia 1 %.

Detección de estados durante el typeahead:
  NLA        → "*** NLA ***" en el label → status=PART_ERROR, nla='Y'
  SUPERSEDED → "USE <new_sku>" en el label → status=SUPERSEDED, superseded_from=<original>
  PACK       → versión con sufijo "X" disponible → pack_qty registrado
  OUT_OF_STOCK → indicador en el carrito → status=PART_ERROR, nla='Y'
"""

import os
from typing import List, Dict, Optional
from decimal import Decimal

from models.purchase_model import (
    PurchaseOrderDataModel,
    PurchaseOrderResponseData,
    PurchaseOrderResponseProduct,
)
from services.suppliers.base_supplier_service import SupplierService
from seo_scripts.cooks_power_playwright import cooks_power_automation_playwright
from seo_scripts.insert_data_in_db import insert_po_review_details


class CooksPowerSupplierService(SupplierService):
    """
    Estrategia concreta para Cook's Power (CP).
    No usa CSV — los ítems se ingresan uno a uno via typeahead en el portal.
    """

    # ------------------------------------------------------------------ #
    #  Identificación                                                       #
    # ------------------------------------------------------------------ #

    @property
    def supplier_id(self) -> str:
        return "CP"

    @property
    def supplier_name(self) -> str:
        return "Cook's Power"

    # ------------------------------------------------------------------ #
    #  Credenciales                                                         #
    # ------------------------------------------------------------------ #

    def get_credentials(self) -> Dict[str, str]:
        return {
            "email": os.getenv("CP_USERNAME", "invoices.prontomowers@gmail.com"),
            "password": os.getenv("CP_PASSWORD", "Hustler123"),
        }

    # ------------------------------------------------------------------ #
    #  CSV — no aplica para Cook's Power                                    #
    # ------------------------------------------------------------------ #

    def csv_headers(self) -> List[str]:
        """Cook's Power no usa CSV; retorna lista vacía."""
        return []

    def csv_row(self, product) -> List:
        """Cook's Power no usa CSV; retorna lista vacía."""
        return []

    # ------------------------------------------------------------------ #
    #  Template Method Override: omitir generación de CSV                  #
    # ------------------------------------------------------------------ #

    def execute(self, po_data: PurchaseOrderDataModel, chunk_id: str) -> PurchaseOrderResponseData:
        """
        Override del Template Method: Cook's Power no requiere generación de
        archivo CSV ni copia a ~/Downloads. Se llama directamente a run_automation()
        con los datos de la PO.
        """
        print("=" * 60)
        print(f"🚀 [{self.supplier_name}] PROCESANDO ORDEN DE COMPRA")
        print(f"📦 PO Number: {po_data.poNumber}")
        print(f"🏢 Supplier ID: {po_data.supplerID}")
        print(f"📦 Total de productos: {len(po_data.products)}")
        print("=" * 60)

        try:
            credentials = self.get_credentials()

            # Convertir productos de la PO al formato que espera run_automation()
            po_items = [
                {
                    "part_number": p.partNumber,
                    "qty": p.qty,
                    "mfrid": p.mfrid,
                    "idealCost": p.idealCost,
                }
                for p in po_data.products
            ]

            print(f"🤖 Ejecutando automatización [{self.supplier_name}]...")
            scraped_data = self.run_automation(
                email=credentials["email"],
                password=credentials["password"],
                csv_filename="",         # No aplica
                po_data=po_data,
                po_items=po_items,
            )

            if not scraped_data:
                raise RuntimeError(
                    f"[{self.supplier_name}] No se obtuvieron datos del scraping "
                    f"para PO {po_data.poNumber}"
                )

            print(f"✅ Automatización completada. {len(scraped_data)} filas extraídas.")

            # Agregar po_number, supplier_code, mfrid_orig y partnumber_orig a cada item
            mfrid_orig_map = {p.partNumber: p.mfrid_orig for p in po_data.products}
            for item in scraped_data:
                item["po_number"] = po_data.poNumber
                item["supplier_code"] = po_data.supplerID
                part = item.get("part_number", "")
                item["mfrid_orig"] = item.get("mfrid_orig") or mfrid_orig_map.get(part, "")
                # SUPERSEDED: partnumber_orig = parte original (superseded_from), no el reemplazo
                if item.get("status") == "SUPERSEDED" and item.get("superseded_from"):
                    item["partnumber_orig"] = item["superseded_from"]
                else:
                    item["partnumber_orig"] = item.get("partnumber_orig") or part

            # Procesar resultados
            response_products = self.process_results(scraped_data, po_data)

            response_data = PurchaseOrderResponseData(
                poNumber=po_data.poNumber,
                supplerID=po_data.supplerID,
                products=response_products,
            )

            self._print_summary(response_products)
            return response_data

        except Exception as e:
            print(f"❌ Error en [{self.supplier_name}] execute(): {e}")
            raise

    # ------------------------------------------------------------------ #
    #  Automatización                                                       #
    # ------------------------------------------------------------------ #

    def run_automation(
        self,
        email: str,
        password: str,
        csv_filename: str,
        po_data: Optional[PurchaseOrderDataModel] = None,
        po_items: Optional[List[Dict]] = None,
        **kwargs,
    ) -> Optional[List[Dict]]:
        """
        Delega en cooks_power_automation_playwright().
        po_items se recibe directamente desde execute() sobrescrito.
        """
        if po_items is None and po_data is not None:
            po_items = [
                {
                    "part_number": p.partNumber,
                    "qty": p.qty,
                    "mfrid": p.mfrid,
                    "idealCost": p.idealCost,
                }
                for p in po_data.products
            ]

        if not po_items:
            print("⚠️  No hay ítems para procesar.")
            return []

        return cooks_power_automation_playwright(
            username=email,
            password=password,
            po_items=po_items,
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
        Compara your_price (carrito Cook's Power) con idealCost de la PO.
        Tolerancia: 1 % del ideal_cost.

        Statuses de salida:
          CORRECT    → precio dentro de la tolerancia
          MISMATCH   → precio fuera de la tolerancia
          PART_ERROR → NLA, out of stock, o sin precio
          SUPERSEDED → el part fue supersedido (part_number apunta al nuevo)
        """
        ideal_costs: Dict[str, float] = {
            p.partNumber: p.idealCost for p in po_data.products
        }
        # También indexar por mfrID si está disponible
        mfrid_map: Dict[str, str] = {
            p.partNumber: p.mfrid for p in po_data.products if p.mfrid
        }

        response_products: List[PurchaseOrderResponseProduct] = []

        for item in scraped_data:
            part_number: str = item.get("part_number", "")
            requested_sku: str = item.get("requested_sku", part_number)
            mfrid: str = item.get("mfrid", mfrid_map.get(requested_sku, ""))
            your_price: Optional[Decimal] = item.get("your_price")
            pre_status: str = item.get("status", "CORRECT")
            error_message: Optional[str] = item.get("error_message")
            pack_qty: Optional[int] = item.get("pack_qty")
            nla: Optional[str] = item.get("nla")
            superseded_from: Optional[str] = item.get("superseded_from")
            cart_qty: int = item.get("qty", 0)

            # idealCost se busca primero por el part_number original (requested_sku)
            ideal_cost = ideal_costs.get(requested_sku) or ideal_costs.get(part_number) or 0.0
            item["ideal_cost"] = ideal_cost
            price_float = float(your_price) if your_price is not None else 0.0

            # ── Statuses pre-asignados por el scraper ──────────────────────
            if pre_status == "PART_ERROR" and price_float == 0.0:
                # Ítem verdaderamente inválido: no encontrado o sin precio
                status = "PART_ERROR"
                print(f"  ❌ PART_ERROR [{part_number}]: {error_message}")

            elif pre_status == "SUPERSEDED":
                status = "SUPERSEDED"
                print(f"  🔄 SUPERSEDED [{part_number}] ← {superseded_from}")

            # ── Comparación de precios ─────────────────────────────────────
            elif price_float > 0 and ideal_cost > 0:
                difference = abs(ideal_cost - price_float)
                tolerance = ideal_cost * 0.01
                print(
                    f"  💵 [{part_number}] Ideal=${ideal_cost:.2f} | "
                    f"CP=${price_float:.2f} | "
                    f"Diff=${difference:.2f} | Tol=${tolerance:.2f}"
                )

                if difference > tolerance:
                    status = "MISMATCH"
                    error_message = (
                        f"Price mismatch: Expected ${ideal_cost:.2f}, "
                        f"Cook's Power ${price_float:.2f}"
                    )
                    if pack_qty:
                        error_message += f" (Pack item — min qty: {pack_qty})"
                    print(f"  ⚠️  MISMATCH: {part_number}")
                else:
                    status = "CORRECT"
                    notes = []
                    if pack_qty:
                        notes.append(f"Pack item — min qty: {pack_qty}")
                    error_message = " | ".join(notes) if notes else None
                    print(f"  ✅ CORRECT: {part_number}")

            elif price_float == 0.0 and pre_status not in ("PART_ERROR", "SUPERSEDED"):
                # Sin precio pero tampoco NLA → marcar CORRECT pendiente
                status = "CORRECT"
                print(f"  ⏳ SIN PRECIO (posible error de scraping): {part_number}")

            else:
                status = "CORRECT"

            item["status"] = status
            item["mfrid"] = mfrid

            response_products.append(
                PurchaseOrderResponseProduct(
                    mfrid=mfrid,
                    partNumber=part_number,
                    qty=cart_qty,
                    idealCost=ideal_cost if ideal_cost > 0 else 0.0,
                    supplierPrice=price_float,
                    status=status,
                    nla=nla,
                    supersededFrom=superseded_from,
                    packQty=pack_qty,
                    ltl=None,
                )
            )

        # Persistir en BD
        print("💾 Guardando datos Cook's Power en BD...")
        inserted = insert_po_review_details(scraped_data)
        print(f"✅ {inserted} filas insertadas en po_review_details")

        return response_products
