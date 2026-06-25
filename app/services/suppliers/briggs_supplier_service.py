"""
briggs_supplier_service.py
--------------------------
Implementación concreta de SupplierService para Briggs & Stratton (supplerID = "SP").

CSV esperado: Manufacturer | Part Number | Quantity | Part Notes  (tab-separado)
Precio a comparar: Cost (your_price scraped del portal)

Campos especiales vs Gardner/Husqvarna:
  ltl      → "Y" si la línea requiere envío LTL (Motor Freight / Ground Service)
  pack_qty → cantidad mínima de paquete cuando difiere de la solicitada
"""

import csv
import os
from typing import List, Dict, Optional
from decimal import Decimal

from models.purchase_model import (
    PurchaseOrderDataModel,
    PurchaseOrderItemModel,
    PurchaseOrderResponseProduct,
)
from services.suppliers.base_supplier_service import SupplierService
from seo_scripts.briggs_login_playwright import briggs_login_automation_playwright
from seo_scripts.insert_data_in_db import insert_po_review_details


class BriggsSupplierService(SupplierService):
    """
    Estrategia concreta para Briggs & Stratton (SP).
    """

    # ------------------------------------------------------------------ #
    #  Identificación                                                       #
    # ------------------------------------------------------------------ #

    @property
    def supplier_id(self) -> str:
        return "SP"

    @property
    def supplier_name(self) -> str:
        return "Briggs & Stratton"

    # ------------------------------------------------------------------ #
    #  Credenciales                                                         #
    # ------------------------------------------------------------------ #

    def get_credentials(self) -> Dict[str, str]:
        return {
            "email": os.getenv("BRIGGS_USERNAME", "PD204200"),
            "password": os.getenv("BRIGGS_PASSWORD", "HNb*{b*e!w"),
        }

    # ------------------------------------------------------------------ #
    #  Formato CSV  (tab-separado, 4 columnas)                             #
    # ------------------------------------------------------------------ #

    def csv_headers(self) -> List[str]:
        return ["Manufacturer", "Part Number", "Quantity", "Part Notes"]

    def csv_row(self, product: PurchaseOrderItemModel) -> List:
        return [product.mfrid, product.partNumber, product.qty, ""]

    def _create_csv(self, products: list, csv_filename: str) -> str:
        """Override: Briggs necesita CSV con TAB como delimitador."""
        temp_dir = os.path.join(os.path.expanduser("~"), "Downloads", "temp_purchase_orders")
        os.makedirs(temp_dir, exist_ok=True)
        csv_path = os.path.join(temp_dir, csv_filename)

        print(f"📝 Creando CSV (tab-separado) en: {csv_path}")
        with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(self.csv_headers())
            for product in products:
                writer.writerow(self.csv_row(product))

        print(f"✅ CSV creado: {csv_filename} ({len(products)} productos)")
        return csv_path

    # ------------------------------------------------------------------ #
    #  Automatización                                                       #
    # ------------------------------------------------------------------ #

    def run_automation(
        self,
        email: str,
        password: str,
        csv_filename: str,
        po_data: Optional[PurchaseOrderDataModel] = None,
        **kwargs,
    ) -> Optional[List[Dict]]:
        import csv as _csv_module
        requested_qtys: Dict[str, int] = {}
        po_items_list: List[Dict] = []

        # Leer el CSV ya copiado a Downloads para obtener TODOS los ítems,
        # incluyendo los pack extras añadidos por base_supplier_service.execute().
        # Así Phase 2 también busca y captura precios de packs.
        downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
        csv_path = os.path.join(downloads_path, csv_filename)
        if os.path.exists(csv_path):
            try:
                with open(csv_path, newline='', encoding='utf-8') as f:
                    reader = _csv_module.DictReader(f, delimiter='\t')
                    for row in reader:
                        pn      = (row.get('Part Number')  or '').strip()
                        mfr     = (row.get('Manufacturer') or '').strip()
                        qty_str = (row.get('Quantity')     or '1').strip()
                        qty     = int(qty_str) if qty_str.isdigit() else 1
                        if pn:
                            requested_qtys[pn] = qty
                            po_items_list.append({
                                'mfrid': mfr,
                                'part_number': pn,
                                'qty': qty,
                                'requested_qty': qty,
                            })
                print(f"  📋 CSV leído: {len(po_items_list)} ítems (inc. packs)")
            except Exception as e:
                print(f"  ⚠️  Error leyendo CSV: {e} — fallback a po_data")

        # Fallback si no se pudo leer el CSV
        if not po_items_list and po_data is not None:
            requested_qtys = {p.partNumber: p.qty for p in po_data.products}
            po_items_list = [
                {'mfrid': p.mfrid, 'part_number': p.partNumber,
                 'qty': p.qty, 'requested_qty': p.qty}
                for p in po_data.products
            ]

        return briggs_login_automation_playwright(
            username=email,
            password=password,
            csv_filename=csv_filename,
            requested_qtys=requested_qtys,
            po_items=po_items_list,
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
        Compara `your_price` (Cost del portal) con `idealCost` de la PO.
        Tolerancia: 1 % del ideal_cost.

        Statuses de salida:
          CORRECT    → precio dentro de la tolerancia
          MISMATCH   → precio fuera de la tolerancia
          PART_ERROR → NLA / not available
          SUPERSEDED → parte reemplazada
        """
        # Lookups de idealCost: primero por (mfrid, partNumber), fallback por partNumber solo
        ideal_costs_keyed: Dict[tuple, float] = {
            (p.mfrid, p.partNumber): p.idealCost for p in po_data.products
        }
        ideal_costs: Dict[str, float] = {
            p.partNumber: p.idealCost for p in po_data.products
        }
        # Mapa part_number → mfrid para enriquecer scraped_data
        mfrid_map: Dict[str, str] = {
            p.partNumber: p.mfrid for p in po_data.products
        }

        response_products: List[PurchaseOrderResponseProduct] = []

        for item in scraped_data:
            part_number: str = item.get("part_number", "")
            your_price: Optional[Decimal] = item.get("your_price")
            pre_status: str = item.get("status", "CORRECT")
            error_message: Optional[str] = item.get("error_message")
            pack_qty: Optional[int] = item.get("pack_qty")
            nla: Optional[str] = item.get("nla")
            ltl: Optional[str] = item.get("ltl")
            superseded_from: Optional[str] = item.get("superseded_from")
            cart_qty: int = item.get("qty", 0)

            # Lookup de idealCost: primero por (mfrid_item, partNumber), luego solo por partNumber
            item_mfrid_for_lookup = item.get('mfrid', '') or ''
            ideal_cost = (
                ideal_costs_keyed.get((item_mfrid_for_lookup, part_number))
                if item_mfrid_for_lookup else None
            )
            if ideal_cost is None:
                ideal_cost = ideal_costs.get(part_number, 0.0)

            # Si es SUPERSEDED, buscar ideal_cost por el part original (superseded_from)
            if ideal_cost == 0.0 and superseded_from:
                sf_parts = superseded_from.split()
                if len(sf_parts) >= 2:
                    ideal_cost = ideal_costs.get(sf_parts[-1], 0.0)
                if ideal_cost == 0.0:
                    ideal_cost = ideal_costs.get(superseded_from, 0.0)
                if ideal_cost == 0.0:
                    for prod in po_data.products:
                        if prod.partNumber in superseded_from:
                            ideal_cost = prod.idealCost
                            break

            item["ideal_cost"] = ideal_cost

            # ── Enriquecer mfrid desde la PO original ─────────────────────
            # Prioridad: 1) mfrid ya fijado en el ítem (p.ej. desde _parse_invalid_parts)
            #            2) mfrid_map de la PO (por partNumber)
            #            3) superseded_from lookup
            #            4) fallback 'BRS'
            resolved_mfrid = item.get('mfrid', '') or ''
            if not resolved_mfrid:
                resolved_mfrid = mfrid_map.get(part_number, '')
            if not resolved_mfrid and superseded_from:
                sf_parts = superseded_from.split()
                for key in [superseded_from] + ([sf_parts[-1]] if len(sf_parts) >= 2 else []):
                    resolved_mfrid = mfrid_map.get(key, '')
                    if resolved_mfrid:
                        break
            if not resolved_mfrid:
                resolved_mfrid = 'BRS'  # fallback: Briggs & Stratton siempre es BRS
            item["mfrid"] = resolved_mfrid

            price_float = float(your_price) if your_price is not None else 0.0

            # ── Statuses pre-asignados por el scraper ──────────────────────
            if pre_status == "SUPERSEDED":
                status = "SUPERSEDED"
                print(f"  🔄 SUPERSEDED [{part_number}] ← {superseded_from}")

            elif pre_status == "PART_ERROR" and price_float == 0.0:
                # Ítem verdaderamente inválido: no encontrado o sin precio
                status = "PART_ERROR"
                print(f"  ❌ PART_ERROR [{part_number}]: {error_message}")

            # ── Sin precio → PART_ERROR ──────────────────────────────────────────────────
            elif price_float == 0.0 and ideal_cost > 0:
                status = "PART_ERROR"
                error_message = f"No price available for {part_number}"
                item["status"] = "PART_ERROR"
                print(f"  ⚠️ PART_ERROR (sin precio): {part_number}")

            # ── Comparación de precios ──────────────────────────────────────
            elif price_float > 0 and ideal_cost > 0:
                difference = abs(ideal_cost - price_float)
                tolerance = ideal_cost * 0.01
                ltl_note = " [LTL]" if ltl else ""
                pack_note = f" | PACK qty:{pack_qty}" if pack_qty else ""
                print(
                    f"  💵 [{part_number}] Ideal=${ideal_cost:.2f} | "
                    f"Briggs=${price_float:.2f} | "
                    f"Diff=${difference:.2f} | Tol=${tolerance:.2f}"
                    f"{ltl_note}{pack_note}"
                )

                # Solo es MISMATCH si el precio del proveedor es MÁS ALTO que
                # el ideal. Si es igual o más bajo (below cost = buena oferta)
                # se marca como CORRECT.
                if price_float > ideal_cost and difference > tolerance:
                    status = "MISMATCH"
                    error_message = (
                        f"Price mismatch: Expected ${ideal_cost:.2f}, "
                        f"Briggs ${price_float:.2f}"
                    )
                    if pack_qty:
                        error_message += f" (Pack item — min qty: {pack_qty})"
                    print(f"  ❌ MISMATCH: {part_number}")
                else:
                    status = "CORRECT"
                    notes = []
                    if pack_qty:
                        notes.append(f"Pack item — min qty: {pack_qty}")
                    if ltl:
                        notes.append("LTL shipment required")
                    error_message = " | ".join(notes) if notes else None
                    print(f"  ✅ CORRECT: {part_number}")

            else:
                status = "CORRECT"

            item["status"] = status

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
                    ltl=ltl,
                )
            )

        # Persistir en BD
        print("💾 Guardando datos Briggs en BD...")
        inserted = insert_po_review_details(scraped_data)
        print(f"✅ {inserted} filas insertadas en po_review_details")

        return response_products

