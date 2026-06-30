"""
base_supplier_service.py
------------------------
Interfaz abstracta que define el contrato que debe cumplir
cada proveedor (Gardner, Husqvarna, Briggs, etc.).

Patrón: Strategy
  - SupplierService  → interfaz (esta clase)
  - GardnerSupplierService, HusqvarnaSupplierService, etc. → estrategias concretas
  - SupplierFactory  → selecciona la estrategia según supplerID
"""

import csv
import os
import shutil
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict, Optional

from models.purchase_model import (
    PurchaseOrderDataModel,
    PurchaseOrderItemModel,
    PurchaseOrderResponseData,
    PurchaseOrderResponseProduct,
)


class SupplierService(ABC):
    """
    Clase base abstracta para todos los servicios de proveedor.

    Cada proveedor concreto implementa:
      - csv_headers      → columnas del CSV que espera el portal
      - csv_row          → cómo se mapea un PurchaseOrderItemModel a una fila del CSV
      - run_automation   → ejecuta Playwright y devuelve los datos scrapeados
      - process_results  → convierte datos scrapeados a PurchaseOrderResponseProduct[]
    """

    # ------------------------------------------------------------------ #
    #  Configuración que cada proveedor debe declarar                      #
    # ------------------------------------------------------------------ #

    @property
    @abstractmethod
    def supplier_id(self) -> str:
        """Identificador del proveedor (ej: 'GA', 'HU', 'SP')."""
        ...

    @property
    @abstractmethod
    def supplier_name(self) -> str:
        """Nombre legible del proveedor (ej: 'Gardner Inc')."""
        ...

    # ------------------------------------------------------------------ #
    #  Métodos abstractos que cada proveedor debe implementar              #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def csv_headers(self) -> List[str]:
        """Devuelve los encabezados del CSV que espera el portal."""
        ...

    @abstractmethod
    def csv_row(self, product) -> List:
        """
        Convierte un PurchaseOrderItemModel en una fila del CSV.
        El orden debe coincidir con csv_headers().
        """
        ...

    @abstractmethod
    def run_automation(self, email: str, password: str, csv_filename: str, **kwargs) -> Optional[List[Dict]]:
        """
        Ejecuta la automatización de Playwright para el proveedor.

        :param email: Credencial de acceso al portal
        :param password: Contraseña de acceso al portal
        :param csv_filename: Nombre del archivo CSV (debe estar en ~/Downloads)
        :param kwargs: Parámetros opcionales específicos por proveedor (ej. po_data)
        :return: Lista de dicts con los datos scrapeados, o None si falla
        """
        ...

    @abstractmethod
    def process_results(
        self,
        scraped_data: List[Dict],
        po_data: PurchaseOrderDataModel,
    ) -> List[PurchaseOrderResponseProduct]:
        """
        Procesa y compara los datos scrapeados contra los datos de la PO.

        :param scraped_data: Datos crudos devueltos por run_automation()
        :param po_data: Datos originales de la orden de compra
        :return: Lista de PurchaseOrderResponseProduct con status calculado
        """
        ...

    # ------------------------------------------------------------------ #
    #  Credenciales — cada proveedor puede sobrescribir                    #
    # ------------------------------------------------------------------ #

    def get_credentials(self) -> Dict[str, str]:
        """
        Devuelve las credenciales del portal del proveedor.
        Sobrescribir en la clase concreta o cargar desde variables de entorno.
        """
        raise NotImplementedError(
            f"Credenciales no configuradas para proveedor '{self.supplier_id}'. "
            "Sobrescribe get_credentials() en la clase concreta."
        )

    @property
    def _upload_file_extension(self) -> str:
        """Extensión del archivo de upload. Sobrescribir para formatos distintos a CSV."""
        return ".csv"

    # ------------------------------------------------------------------ #
    #  Helpers de packs — compartidos por todos los execute()              #
    # ------------------------------------------------------------------ #

    def _pre_fetch_packs(self, po_data: PurchaseOrderDataModel) -> Dict[tuple, List[Dict]]:
        """
        Pre-consulta product_packs en PostgreSQL para todos los productos de la PO.
        Retorna {(mfrid_upper, pn_upper): [pack_entry_dicts]}.
        """
        from seo_scripts.insert_data_in_db import fetch_pack_codes_for_po
        pack_map = fetch_pack_codes_for_po(po_data.products)
        total = sum(len(v) for v in pack_map.values())
        if total:
            print(f"  📦 Pre-fetch packs: {len(pack_map)} producto(s) con {total} pack(s).")
        else:
            print("  📦 Pre-fetch packs: ningún producto tiene packs en BD.")
        return pack_map

    @staticmethod
    def _build_pack_lookup_index(
        pack_map: Dict[tuple, List[Dict]],
        original_pns_upper: set,
    ) -> Dict[str, tuple]:
        """
        Construye índice inverso: pack_pn_upper → (orig_mfrid_upper, orig_pn_upper).
        Excluye pack PNs que ya son ítems originales de la PO para evitar colisiones.
        """
        index: Dict[str, tuple] = {}
        for orig_key, packs in pack_map.items():
            for pack in packs:
                pack_pn_up = pack['partnumber'].upper()
                if pack_pn_up not in original_pns_upper:
                    index[pack_pn_up] = orig_key
        return index

    @staticmethod
    def _build_pack_extra_po_items(
        pack_map: Dict[tuple, List[Dict]],
        original_pns_upper: set,
    ) -> List[PurchaseOrderItemModel]:
        """
        Crea PurchaseOrderItemModel (qty=1, idealCost=0) para cada PN de pack
        único que no sea ya un ítem original. Se incluyen en el CSV para que
        el portal devuelva su precio.
        """
        seen: set = set()
        extra: List[PurchaseOrderItemModel] = []
        for orig_key, packs in pack_map.items():
            for pack in packs:
                pack_pn = pack['partnumber']
                pack_pn_up = pack_pn.upper()
                if pack_pn_up in original_pns_upper or pack_pn_up in seen:
                    continue
                seen.add(pack_pn_up)
                extra.append(PurchaseOrderItemModel(
                    mfrid=pack.get('mfr', orig_key[0]),
                    partNumber=pack_pn,
                    qty=1,
                    idealCost=0.0,
                ))
        if extra:
            print(f"  ➕ {len(extra)} PN(s) de pack añadidos al CSV para consulta de precio.")
        return extra

    @staticmethod
    def _build_pack_extra_dict_items(
        pack_map: Dict[tuple, List[Dict]],
        original_pns_upper: set,
    ) -> List[Dict]:
        """
        Versión dict de _build_pack_extra_po_items. Usada por proveedores
        que no usan CSV (Cook's Power, Florida Outdoor).
        """
        seen: set = set()
        extra: List[Dict] = []
        for orig_key, packs in pack_map.items():
            for pack in packs:
                pack_pn = pack['partnumber']
                pack_pn_up = pack_pn.upper()
                if pack_pn_up in original_pns_upper or pack_pn_up in seen:
                    continue
                seen.add(pack_pn_up)
                extra.append({
                    'part_number': pack_pn,
                    'mfrid':       pack.get('mfr', orig_key[0]),
                    'qty':         1,
                    'idealCost':   0.0,
                    '_pack_lookup': True,
                })
        if extra:
            print(f"  ➕ {len(extra)} PN(s) de pack añadidos a po_items para consulta de precio.")
        return extra

    @staticmethod
    def _apply_pack_costs_and_clean(
        scraped_data: List[Dict],
        pack_map: Dict[tuple, List[Dict]],
        pack_lookup_index: Dict[str, tuple],
    ) -> List[Dict]:
        """
        Después de la automatización:
          1. Identifica en scraped_data los ítems que son consultas de pack
             (usando pack_lookup_index).
          2. Extrae su precio y enriquece las entradas de pack_map con 'cost'.
          3. Adjunta pack_codes (con cost) al ítem original correspondiente.
          4. Elimina los ítems de pack de scraped_data.
        Retorna scraped_data limpio (solo ítems originales de la PO).
        """
        orig_items_by_pn: Dict[str, Dict] = {}
        pack_items: List[Dict] = []
        cleaned: List[Dict] = []

        for item in scraped_data:
            pn_up = item.get('part_number', '').upper()
            if pn_up in pack_lookup_index:
                pack_items.append(item)
            else:
                orig_items_by_pn[pn_up] = item
                cleaned.append(item)

        # 1. Extraer costo de cada pack item scrapeado
        for pack_item in pack_items:
            pn_up = pack_item.get('part_number', '').upper()
            orig_key = pack_lookup_index.get(pn_up)
            if not orig_key or orig_key not in pack_map:
                continue
            cost = float(
                pack_item.get('your_price') or
                pack_item.get('tiered_price') or
                0.0
            )
            for entry in pack_map[orig_key]:
                if entry['partnumber'].upper() == pn_up:
                    entry['cost'] = cost
                    print(
                        f"  💰 Pack cost: {pn_up} = ${cost:.2f} "
                        f"(para {orig_key[0]}/{orig_key[1]})"
                    )

        # 2. Marcar cost=None para packs que no aparecieron en el portal
        for packs in pack_map.values():
            for entry in packs:
                if 'cost' not in entry:
                    entry['cost'] = None

        # 3. Adjuntar pack_codes enriquecido al ítem original en cleaned
        #    Solo se adjunta al ítem cuyo part_number coincide directamente.
        #    Los SUPERSEDED no se tocan — su pack_codes queda como None.
        for orig_key, packs in pack_map.items():
            orig_pn_up = orig_key[1]
            item = orig_items_by_pn.get(orig_pn_up)
            if item:
                item['pack_codes'] = packs
                print(f"  📦 pack_codes adjunto a {orig_key[0]}/{orig_pn_up}: {packs}")

        if pack_items:
            print(
                f"  ✅ {len(pack_items)} pack item(s) procesados y excluidos "
                f"del resultado final."
            )
        return cleaned

    # ------------------------------------------------------------------ #
    #  Template Method: flujo completo de una PO (no se sobrescribe)       #
    # ------------------------------------------------------------------ #

    def execute(self, po_data: PurchaseOrderDataModel, chunk_id: str) -> PurchaseOrderResponseData:
        """
        Orquesta el flujo completo para una orden de compra:
          1. Genera el CSV
          2. Copia a ~/Downloads
          3. Ejecuta la automatización
          4. Procesa y compara resultados
          5. Limpia archivos temporales
          6. Retorna PurchaseOrderResponseData

        Este método es un Template Method: el esqueleto del algoritmo
        está fijo aquí, los pasos variables son los métodos abstractos.
        """
        print("=" * 60)
        print(f"🚀 [{self.supplier_name}] PROCESANDO ORDEN DE COMPRA")
        print(f"📦 PO Number: {po_data.poNumber}")
        print(f"🏢 Supplier ID: {po_data.supplerID}")
        print(f"📦 Total de productos: {len(po_data.products)}")
        print("=" * 60)

        csv_path: Optional[str] = None
        final_csv_path: Optional[str] = None

        try:
            # 1. Nombre único del archivo de upload
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = self._upload_file_extension
            csv_filename = f"PO_{po_data.poNumber}_{po_data.supplerID}_{timestamp}{ext}"

            # 1.5 Pre-fetch pack codes → construir ítems extra para consulta de precio
            pack_map = self._pre_fetch_packs(po_data)
            original_pns_upper = {p.partNumber.upper() for p in po_data.products}
            pack_lookup_index = self._build_pack_lookup_index(pack_map, original_pns_upper)
            extra_po_items = self._build_pack_extra_po_items(pack_map, original_pns_upper)

            # 2. Crear CSV en carpeta temporal (incluye PNs de pack para obtener precios)
            all_products = list(po_data.products) + extra_po_items
            csv_path = self._create_csv(all_products, csv_filename)

            # 3. Copiar a ~/Downloads (donde lo busca el playwright)
            downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
            final_csv_path = os.path.join(downloads_path, csv_filename)
            shutil.copy2(csv_path, final_csv_path)
            print(f"📋 Archivo copiado a Downloads: {final_csv_path}")

            # 4. Ejecutar automatización del proveedor
            credentials = self.get_credentials()
            print(f"🤖 Ejecutando automatización [{self.supplier_name}]...")
            scraped_data = self.run_automation(
                email=credentials["email"],
                password=credentials["password"],
                csv_filename=csv_filename,
                po_data=po_data,
            )

            if not scraped_data:
                raise RuntimeError(
                    f"[{self.supplier_name}] No se obtuvieron datos del scraping para PO {po_data.poNumber}"
                )

            print(f"✅ Automatización completada. {len(scraped_data)} filas extraídas.")

            # 4.5 Extraer costos de packs y limpiar scraped_data
            #     (los ítems extra de pack se retiran: no van al chunk ni a la BD)
            if pack_lookup_index:
                scraped_data = self._apply_pack_costs_and_clean(
                    scraped_data, pack_map, pack_lookup_index
                )

            # 5. Enriquecer items con po_number, supplier_code, mfrid_orig, partnumber_orig
            # mfrid_orig viene en el body (po_data.products) → crear mapa y asignar a cada item
            # Fallback: si mfrid_orig no viene en el body, usar mfrid (ej: 'HUS')
            mfrid_orig_map = {
                p.partNumber: (p.mfrid_orig or p.mfrid or '')
                for p in po_data.products
            }

            for item in scraped_data:
                item["po_number"] = po_data.poNumber
                item["supplier_code"] = po_data.supplerID
                part = item.get('part_number') or item.get('partNumber') or ''

                # mfrid_orig: leer del body via mapa
                # Si es SUPERSEDED, buscar por superseded_from (parte original de la PO)
                if not item.get('mfrid_orig'):
                    lookup_key = item.get('superseded_from') if item.get('status') == 'SUPERSEDED' else part
                    item['mfrid_orig'] = mfrid_orig_map.get(lookup_key, '')
                    if not item['mfrid_orig']:
                        print(f"  ⚠️  mfrid_orig VACÍO para part='{part}' (status={item.get('status')}) — no vino en el body")

                # partnumber_orig: si es SUPERSEDED, usar superseded_from (parte original)
                if item.get('status') == 'SUPERSEDED' and item.get('superseded_from'):
                    item['partnumber_orig'] = item['superseded_from']
                else:
                    item['partnumber_orig'] = item.get('partnumber_orig') or part

            # 6. Procesar resultados (lógica específica del proveedor)
            response_products = self.process_results(scraped_data, po_data)

            # 7. Construir respuesta
            response_data = PurchaseOrderResponseData(
                poNumber=po_data.poNumber,
                supplerID=po_data.supplerID,
                products=response_products,
            )

            self._print_summary(response_products)
            return response_data

        finally:
            # 8. Limpiar archivos temporales siempre3
            self._cleanup(csv_path, final_csv_path)

    # ------------------------------------------------------------------ #
    #  Helpers compartidos                                                  #
    # ------------------------------------------------------------------ #

    def _create_csv(self, products: list, csv_filename: str) -> str:
        """Genera el archivo CSV usando los headers y rows del proveedor."""
        temp_dir = os.path.join(os.path.expanduser("~"), "Downloads", "temp_purchase_orders")
        os.makedirs(temp_dir, exist_ok=True)
        csv_path = os.path.join(temp_dir, csv_filename)

        print(f"📝 Creando CSV en: {csv_path}")
        with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(self.csv_headers())
            for product in products:
                writer.writerow(self.csv_row(product))

        print(f"✅ CSV creado: {csv_filename} ({len(products)} productos)")
        return csv_path

    def _cleanup(self, csv_path: Optional[str], final_csv_path: Optional[str]) -> None:
        """Elimina archivos temporales generados durante el proceso."""
        for path in [csv_path, final_csv_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    print(f"🗑️  Eliminado: {path}")
                    # Intentar eliminar carpeta temp si quedó vacía
                    parent = os.path.dirname(path)
                    try:
                        os.rmdir(parent)
                        print(f"🗑️  Carpeta temporal eliminada: {parent}")
                    except OSError:
                        pass
                except Exception as e:
                    print(f"⚠️  No se pudo eliminar {path}: {e}")

    @staticmethod
    def _print_summary(products: List[PurchaseOrderResponseProduct]) -> None:
        total = len(products)
        correct = sum(1 for p in products if p.status == "CORRECT")
        mismatch = sum(1 for p in products if p.status == "MISMATCH")
        part_error = sum(1 for p in products if p.status == "PART_ERROR")
        superseded = sum(1 for p in products if p.status == "SUPERSEDED")
        print(f"📊 Total: {total} | ✅ CORRECT: {correct} | ⚠️ MISMATCH: {mismatch} "
              f"| ❌ PART_ERROR: {part_error} | 🔄 SUPERSEDED: {superseded}")
