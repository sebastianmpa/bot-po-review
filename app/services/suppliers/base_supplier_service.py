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

            # 2. Crear CSV en carpeta temporal
            csv_path = self._create_csv(po_data.products, csv_filename)

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

            # 5. Agregar po_number y supplier_code a cada item
            # Build map part_number -> mfrid_orig from PO products (if provided)
            try:
                mfrid_orig_map = {
                    getattr(p, 'partNumber', ''): getattr(p, 'mfrid_orig', '')
                    for p in po_data.products
                }
            except Exception:
                mfrid_orig_map = {}

            for item in scraped_data:
                item["po_number"] = po_data.poNumber
                item["supplier_code"] = po_data.supplerID
                # Propagar mfrid_orig y partnumber_orig desde el body
                part = item.get('part_number') or item.get('partNumber')
                if part:
                    item['mfrid_orig'] = item.get('mfrid_orig', mfrid_orig_map.get(part, ''))
                    # SUPERSEDED: partnumber_orig = parte original (superseded_from), no el reemplazo
                    if item.get('status') == 'SUPERSEDED' and item.get('superseded_from'):
                        item['partnumber_orig'] = item['superseded_from']
                    else:
                        item['partnumber_orig'] = item.get('partnumber_orig') or part
                else:
                    item['mfrid_orig'] = item.get('mfrid_orig', '')
                    item['partnumber_orig'] = item.get('partnumber_orig', '')

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
