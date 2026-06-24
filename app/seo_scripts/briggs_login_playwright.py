"""
briggs_login_playwright.py
--------------------------
Automatización Playwright para el portal Briggs & Stratton (BSP Power Portal).

Flujo:
  1. Login con Username / Password
  2. Build an Order → Upload An Order
  3. Subir CSV (Manufacturer | Part Number | Quantity | Part Notes)
  4. Esperar tabla de resultados (.ob__results__items)
  5. Scrapear cada fila:
     - Part #, Qty, Availability, Description, List Price, Cost
     - Detectar NLA  → "This item is not available" en description
     - Detectar SUPERSEDED → "Part Superseded From: " en description
     - Detectar LTL  → "Must Ship via" en description  → ltl='Y'
     - Detectar pack → qty en carrito != qty solicitada → pack_qty=cart_qty
  6. Retorna List[Dict] con los datos de cada línea

Status de salida posibles:
  CORRECT    → ítem normal (disponible o backorder)
  PART_ERROR → NLA / not available
  SUPERSEDED → parte reemplazada
"""

from playwright.sync_api import sync_playwright, Page
from pynput.keyboard import Key, Controller
import re
import time
import os
from typing import List, Dict, Optional
from decimal import Decimal, InvalidOperation


BRIGGS_BASE_URL = "https://www.powerdistributors.com/portal/dashboard"

LTL_KEYWORDS = [
    'must ship via motor freight',
    'must ship via ground service',
    'must ship via freight',
    'must ship via ltl',
]


def parse_price(text: str) -> Optional[Decimal]:
    """Convierte texto de precio a Decimal. Ej: '$1,197.89' → Decimal('1197.89')"""
    try:
        clean = text.replace('$', '').replace(',', '').strip()
        if clean:
            return Decimal(clean)
    except (InvalidOperation, Exception):
        pass
    return None


def extract_table_data(page: Page, requested_qtys: Dict[str, int]) -> List[Dict]:
    """
    Scrapea la tabla de resultados del portal Briggs (Build An Order).

    Estructura HTML:
      div.ob__results__item
        div[data-label="Part #"]          → part_number
        div[data-label="Availability"]
          div.order-avail.order_sh        → in_stock='Y', "X available"
          div.order-avail.order_ooo       → backordered
          Solo alert sin order_sh         → in_stock='N', available_qty=0
        div[data-label="Description"]     → description + NLA/SUPERSEDED/LTL
        div[data-label="List"]            → list_price
        div[data-label="Cost"]            → your_price
    """
    results = []

    try:
        page.wait_for_selector('div.ob__results__item', timeout=30000)
        print("📊 Tabla de resultados Briggs (Build An Order) encontrada.")

        rows = page.locator('div.ob__results__item').all()
        print(f"📋 Total de filas encontradas: {len(rows)}")

        for idx, row in enumerate(rows):
            try:
                # ── Part # ──────────────────────────────────────────────────
                part_number = ''
                try:
                    pn_elem = row.locator('div[data-label="Part #"]')
                    if pn_elem.count() > 0:
                        part_number = pn_elem.first.inner_text().strip()
                except Exception:
                    pass

                if not part_number:
                    continue

                # ── Availability + available_qty + in_stock ──────────────────
                available_qty = 0
                in_stock = 'N'
                availability = 'UNAVAILABLE'

                try:
                    check_icon = row.locator('i.ob__overview__icon.success')
                    alert_icon = row.locator('i.ob__overview__icon.alert')

                    if check_icon.count() > 0:
                        # Tiene stock — hacer hover para que el portal cargue la cantidad
                        in_stock = 'Y'
                        availability = 'AVAILABLE'
                        try:
                            check_icon.first.hover()
                            time.sleep(0.4)
                        except Exception:
                            pass
                        memo_els = row.locator(
                            'div.order-avail.order_sh div.availability-memo span'
                        )
                        if memo_els.count() > 0:
                            text = memo_els.first.inner_text().strip()  # "184 available"
                            m = re.search(r'([\d,]+)', text)
                            if m:
                                available_qty = int(m.group(1).replace(',', ''))
                        if alert_icon.count() > 0:
                            availability = 'PARTIAL'  # algo disponible + algo en backorder
                    elif alert_icon.count() > 0:
                        # Solo alerta = sin stock
                        in_stock = 'N'
                        available_qty = 0
                        availability = 'BACKORDERED'
                except Exception:
                    pass

                # ── Qty (input) ──────────────────────────────────────────────
                cart_qty = 0
                try:
                    qty_input = row.locator('div[data-label="Qty."] input')
                    if qty_input.count() > 0:
                        val = qty_input.first.input_value().strip()
                        cart_qty = int(val) if val and val.isdigit() else 0
                except Exception:
                    pass
                # Fallback: usar requested_qty si no se pudo leer
                if cart_qty == 0:
                    cart_qty = requested_qtys.get(part_number, 0)

                # ── Description, NLA, SUPERSEDED, LTL ───────────────────────
                description = ''
                nla = None
                superseded_from = None
                ltl = None
                item_status = 'CORRECT'
                item_error_message = None

                try:
                    desc_cell = row.locator('div[data-label="Description"]')
                    if desc_cell.count() > 0:
                        full_text = desc_cell.first.inner_text()
                        full_text_lower = full_text.lower()

                        desc_ps = desc_cell.locator('p.ob__results__text').all()
                        if desc_ps:
                            description = desc_ps[0].inner_text().strip()

                        # NLA
                        if 'this item is not available' in full_text_lower or availability == 'UNAVAILABLE':
                            nla = 'Y'
                            item_status = 'PART_ERROR'
                            item_error_message = f"No Longer Available: {part_number}"
                            print(f"  🚫 NLA: {part_number}")

                        # SUPERSEDED
                        if 'part superseded from' in full_text_lower:
                            for line in full_text.splitlines():
                                if 'part superseded from' in line.lower():
                                    after = line.split(':', 1)[-1].strip() if ':' in line else ''
                                    if after:
                                        superseded_from = after
                                    break
                            if superseded_from:
                                item_status = 'SUPERSEDED'
                                item_error_message = f"Superseded: {superseded_from} → {part_number}"
                                print(f"  🔄 SUPERSEDED: {superseded_from} → {part_number}")

                        # LTL
                        for kw in LTL_KEYWORDS:
                            if kw in full_text_lower:
                                ltl = 'Y'
                                print(f"  🚛 LTL: {part_number}")
                                break
                except Exception:
                    pass

                # ── List Price ───────────────────────────────────────────────
                list_price: Optional[Decimal] = None
                try:
                    lp_cell = row.locator('div[data-label="List"]')
                    if lp_cell.count() > 0:
                        list_price = parse_price(lp_cell.first.inner_text())
                except Exception:
                    pass

                # ── Cost (Your Price) ────────────────────────────────────────
                your_price: Optional[Decimal] = None
                try:
                    cost_cell = row.locator('div[data-label="Cost"]')
                    if cost_cell.count() > 0:
                        your_price = parse_price(cost_cell.first.inner_text())
                except Exception:
                    pass

                # ── Pack qty detection ───────────────────────────────────────
                requested_qty = requested_qtys.get(part_number, cart_qty)
                pack_qty = None
                if cart_qty > 0 and cart_qty != requested_qty and item_status not in ('PART_ERROR',):
                    pack_qty = cart_qty
                    print(f"  📦 PACK: {part_number} | Solicitado: {requested_qty} | Mínimo: {cart_qty}")

                # Fallback: si tiene stock pero el hover no devolvió qty exacta,
                # usar requested_qty como mínimo disponible conocido
                if in_stock == 'Y' and available_qty == 0:
                    available_qty = requested_qty

                stock_icon = '✅' if in_stock == 'Y' else '❌'
                results.append({
                    'mfrid': '',
                    'part_number': part_number,
                    'description': description,
                    'availability': availability,
                    'qty_available': available_qty,
                    'in_stock': in_stock,
                    'qty': cart_qty,
                    'requested_qty': requested_qty,
                    'list_price': list_price,
                    'your_price': your_price,
                    'status': item_status,
                    'error_message': item_error_message,
                    'superseded_from': superseded_from,
                    'nla': nla,
                    'ltl': ltl,
                    'pack_qty': pack_qty,
                })
                print(
                    f"  ✓ [{idx}] {part_number} | {stock_icon} Stock: {available_qty} | "
                    f"Cost: {your_price} | Status: {item_status}"
                )

            except Exception as e:
                print(f"  ⚠️ Error procesando fila {idx}: {e}")
                continue

    except Exception as e:
        print(f"❌ Error extrayendo tabla Briggs: {e}")

    return results
def briggs_login_automation_playwright(
    username: str,
    password: str,
    csv_filename: str,
    requested_qtys: Optional[Dict[str, int]] = None,
) -> Optional[List[Dict]]:
    """
    Flujo completo de automatización Briggs & Stratton.

    Pasos:
      1.  Login con username / password
      2.  Esperar dashboard
      3.  Click en 'Build an Order'
      4.  Click en 'Upload An Order'
      5.  Subir CSV via file input
      6.  Esperar resultados
      7.  Scrapear tabla
      8.  Retornar datos
    """
    if requested_qtys is None:
        requested_qtys = {}

    print("🚀 Iniciando automatización Briggs & Stratton con Playwright...")

    downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
    csv_path = os.path.join(downloads_path, csv_filename)

    if not os.path.exists(csv_path):
        print(f"❌ Archivo CSV no encontrado: {csv_path}")
        return None

    print(f"📂 Archivo encontrado: {csv_path}")

    p = sync_playwright().start()

    browser = p.chromium.launch(
        headless=False,
        args=['--start-maximized', '--disable-blink-features=AutomationControlled'],
        channel='msedge',
    )
    context = browser.new_context(
        no_viewport=True,
        user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0'
        ),
    )
    page = context.new_page()
    print("✅ Edge iniciado.")

    try:
        # ── 1. Ir al portal ───────────────────────────────────────────────
        print(f"🌐 Navegando a {BRIGGS_BASE_URL}...")
        page.goto(BRIGGS_BASE_URL, wait_until="domcontentloaded")
        time.sleep(3)

        # ── 2. Login ──────────────────────────────────────────────────────
        print("🔐 Ingresando credenciales...")
        username_input = page.locator('#Username')
        username_input.wait_for(state='visible', timeout=10000)
        username_input.fill(username)

        page.locator('#Password').fill(password)

        print("🖱️ Enviando formulario de login...")
        page.locator('button.login__btn.js--login').click()
        time.sleep(4)
        print(f"✅ Login exitoso. URL: {page.url}")

        # ── 3. Click en 'Build an Order' (menú principal) ───────────────────
        # Esto carga el subnav con Upload An Order y Build An Order
        print("🛒 Navegando a Build an Order (menú principal)...")
        build_main = page.locator('a.db__link__title[href="/portal/build-order"]')
        if build_main.count() == 0:
            build_main = page.locator('a[href="/portal/build-order"]').first
        build_main.wait_for(state='visible', timeout=10000)
        build_main.click()
        time.sleep(3)
        print("✅ Sección Build an Order cargada.")

        # ── 4. Click en 'Upload An Order' (subnav) ───────────────────────────
        print("📤 Navegando a Upload An Order (subnav)...")
        upload_link = page.locator('a.dashboard__subnav__link[href="/portal/upload-order"]')
        if upload_link.count() == 0:
            upload_link = page.locator('a[href="/portal/upload-order"]')
        upload_link.first.wait_for(state='visible', timeout=10000)
        upload_link.first.click()
        time.sleep(3)
        print("✅ Upload An Order cargado.")

        # ── 5. Subir CSV ─────────────────────────────────────────────────────
        print("📁 Subiendo archivo CSV...")
        file_input = page.locator('input#order[type="file"]')
        if file_input.count() == 0:
            file_input = page.locator('input[type="file"]')
        file_input.wait_for(state='attached', timeout=15000)
        file_input.set_input_files(csv_path)
        time.sleep(2)
        print(f"✅ Archivo {csv_filename} cargado.")

        # ── 6. Click en 'Build An Order' (subnav) — aquí aparece la tabla ────
        print("🛒 Navegando a Build An Order (subnav) para ver resultados...")
        build_sub = page.locator('a.dashboard__subnav__link[href="/portal/build-order"]')
        if build_sub.count() == 0:
            build_sub = page.locator('a[href="/portal/build-order"]').first
        build_sub.first.wait_for(state='visible', timeout=10000)
        build_sub.first.click()
        time.sleep(3)
        print("✅ Build An Order (subnav) cargado.")

        # ── 7. Esperar tabla de resultados ────────────────────────────────────
        print("⏳ Esperando tabla de resultados (div.ob__results__item)...")
        page.wait_for_selector('div.ob__results__item', timeout=60000)
        time.sleep(5)
        print("▶️  Tabla lista. Iniciando scraping...")

        # ── 8. Scrapear ───────────────────────────────────────────────────
        cart_data = extract_table_data(page, requested_qtys)
        print(f"✅ Scraping completado. {len(cart_data)} ítems extraídos.")

        # ── 9. Eliminar todos los ítems del resultado ─────────────────────
        print("🗑️  Eliminando ítems de la tabla de resultados...")
        removed = 0
        for _ in range(len(cart_data) + 5):  # +5 margen de seguridad
            remove_btns = page.locator('button.ob__results__remove-btn')
            if remove_btns.count() == 0:
                break
            remove_btns.first.click()
            removed += 1
            time.sleep(0.3)
        print(f"✅ {removed} ítem(s) eliminados de la tabla.")

        return cart_data

    except Exception as e:
        print(f"❌ Error durante la automatización Briggs: {e}")
        import traceback
        traceback.print_exc()
        return None

    finally:
        browser.close()
        print("🚪 Navegador cerrado.")


# ── Ejecución directa para pruebas ────────────────────────────────────────────
if __name__ == "__main__":
    USERNAME = "PD204200"
    PASSWORD = "HNb*{b*e!w"
    CSV_FILENAME = "briggs.csv"

    result = briggs_login_automation_playwright(USERNAME, PASSWORD, CSV_FILENAME)

    if result:
        print(f"\n📊 Datos extraídos ({len(result)} ítems):")
        for idx, row in enumerate(result, 1):
            print(f"\nFila {idx}:")
            for key, value in row.items():
                print(f"  {key}: {value}")
