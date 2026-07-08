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
    'must ship via freight',
    'must ship via ltl',
    'must ship motor freight',
    'motor freight only',
    'ship via ltl',
    'ltl only',
    'ltl freight',
    'truck freight',
    'ships motor freight',
    'ships via motor freight',
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
                    # SmartShip usa <img class="ob__overview__icon">, no un <i>
                    smartship = row.locator(
                        'img.ob__overview__icon, '
                        'p.ob__overview__stats:has-text("Enhanced Shipping")'
                    )
                    # LTL / Alternate Warehouse — ícono fa-truck en la celda de
                    # disponibilidad (ej: <i class="ob__overview__icon fa fa-truck">)
                    truck_icon = row.locator(
                        'i.ob__overview__icon.fa-truck, '
                        'i.ob__overview__icon[class*="fa-truck"]'
                    )

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
                        # El ícono de camión puede coexistir con disponibilidad normal
                        if truck_icon.count() > 0:
                            availability = 'ALT_WAREHOUSE'
                            print(f"  🚛 Alt. Warehouse (LTL): {part_number}")
                    elif smartship.count() > 0:
                        # Disponible vía Enhanced Shipping/SmartShip — NO es PART_ERROR
                        in_stock = 'Y'
                        availability = 'SMARTSHIP'
                        print(f"  🚢 SmartShip disponible: {part_number}")
                    elif truck_icon.count() > 0:
                        # Envío desde almacén alternativo — requiere LTL/flete
                        in_stock = 'Y'
                        availability = 'ALT_WAREHOUSE'
                        print(f"  🚛 Alt. Warehouse (LTL): {part_number}")
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

                # ── Description, NLA, SUPERSEDED, LTL, Package Notes ─────────
                description = ''
                portal_mfrid_in_desc = ''  # mfrid que aparece en la descripción del portal
                nla = None
                superseded_from = None
                ltl = None
                item_status = 'CORRECT'
                item_error_message = None
                pack_qty = None  # se puede fijar desde Package Notes

                try:
                    desc_cell = row.locator('div[data-label="Description"]')
                    if desc_cell.count() > 0:
                        # text_content() captura TODO el texto incluyendo nodos de texto
                        # sueltos (ej: "Must ship via Motor Freight" no está en <p>).
                        # inner_text() puede omitirlos si el CSS afecta la visibilidad.
                        full_text = (desc_cell.first.text_content() or '')
                        full_text_lower = full_text.lower()

                        desc_ps = desc_cell.locator('p.ob__results__text').all()
                        if desc_ps:
                            description = desc_ps[0].inner_text().strip()
                            # Extraer portal_mfrid del encabezado de la primera <p>:
                            # "BS, 4156 GASKET (5 X 795629)"  → 'BS'
                            # "NGK, 4156 R0161-9 SPARK PLUG" → 'NGK'
                            pm_match = re.match(r'^([A-Za-z]+),\s*\S+', description)
                            if pm_match:
                                portal_mfrid_in_desc = pm_match.group(1).upper()

                        # NLA — SOLO desde el texto de descripción.
                        # NO usar availability == 'UNAVAILABLE' porque si la
                        # detección del ícono falla por timing, se producirían
                        # falsos NLA en partes que sí tienen stock y precio.
                        # Frases que el portal Briggs usa para indicar no disponible:
                        #   "This item is No Longer Available"  (Availability Notes)
                        #   "This item is not available"        (Warning)
                        #   "This item is Discontinued"         (desc + backordered)
                        _NLA_PHRASES = (
                            'this item is not available',
                            'is no longer available',
                            'this item is discontinued',
                            'no longer available',
                        )
                        if any(phrase in full_text_lower for phrase in _NLA_PHRASES):
                            nla = 'Y'
                            item_status = 'PART_ERROR'
                            item_error_message = f"No Longer Available: {part_number}"
                            print(f"  🚫 NLA: {part_number}")

                        # SUPERSEDED — dos formatos posibles del portal Briggs:
                        # 1) "Part Superseded From: OLD_PART"   (formato antiguo)
                        # 2) "Supersedes from MFRID, OLD_PART"  (Part Notes moderno)
                        #    Ej: "Supersedes from BS, 594201" → superseded_from='594201'
                        if 'part superseded from' in full_text_lower or 'supersedes from' in full_text_lower:
                            for line in full_text.splitlines():
                                line_lower = line.lower()
                                if 'part superseded from' in line_lower:
                                    after = line.split(':', 1)[-1].strip() if ':' in line else ''
                                    if after:
                                        superseded_from = after
                                    break
                                elif 'supersedes from' in line_lower:
                                    # "Supersedes from BS, 594201" → extraer solo "594201"
                                    m = re.search(
                                        r'supersedes\s+from\s+\S+,\s*(\S+)',
                                        line, re.IGNORECASE
                                    )
                                    if m:
                                        superseded_from = m.group(1).strip()
                                    else:
                                        # fallback: texto tras "supersedes from "
                                        parts = re.split(r'supersedes\s+from\s+', line, flags=re.IGNORECASE, maxsplit=1)
                                        if len(parts) > 1:
                                            raw = parts[1].strip()
                                            superseded_from = raw.split(',', 1)[-1].strip() if ',' in raw else raw
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

                        # Pack qty desde "Package Notes:" (ej: "1194(25)" → 25)
                        # El portal Briggs pone esto en un <p> dentro de la descripción.
                        # El número entre paréntesis es la cantidad del paquete.
                        for p_el in desc_ps:
                            try:
                                p_text = p_el.inner_text()
                                if 'package notes:' in p_text.lower():
                                    pm = re.search(r'\((\d+)\)', p_text)
                                    if pm:
                                        pack_qty = int(pm.group(1))
                                        print(
                                            f"  📦 Package Notes → pack_qty: "
                                            f"{part_number} = {pack_qty}"
                                        )
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass

                # Si el ícono de camión estaba presente pero el texto de descripción
                # no tenía ninguna palabra clave LTL, igualmente marcamos ltl='Y'.
                if availability == 'ALT_WAREHOUSE' and ltl is None:
                    ltl = 'Y'

                # ── List Price ───────────────────────────────────────────────
                list_price: Optional[Decimal] = None
                try:
                    lp_cell = row.locator('div[data-label="List"]')
                    if lp_cell.count() > 0:
                        list_price = parse_price(lp_cell.first.inner_text())
                except Exception:
                    pass

                # ── Cost (Your Price) ────────────────────────────────────────
                # En el HTML real el precio está dentro de un <div> hijo:
                #   <div data-label="Cost" ...><div>$7.31</div></div>
                # Intentamos primero el div interno; si no existe, usamos
                # inner_text() del contenedor (funciona igual si renderiza texto
                # directamente). El doble intento evita que un render lento
                # devuelva cadena vacía y produzca un falso PART_ERROR.
                your_price: Optional[Decimal] = None
                try:
                    cost_cell = row.locator('div[data-label="Cost"]')
                    if cost_cell.count() > 0:
                        # primer intento: div hijo directo
                        inner_div = cost_cell.first.locator('div')
                        if inner_div.count() > 0:
                            raw_cost = inner_div.first.inner_text().strip()
                        else:
                            raw_cost = cost_cell.first.inner_text().strip()
                        # si está vacío puede ser timing; reintentamos una vez
                        if not raw_cost:
                            cost_cell.first.wait_for(state='visible', timeout=3000)
                            inner_div = cost_cell.first.locator('div')
                            raw_cost = (
                                inner_div.first.inner_text().strip()
                                if inner_div.count() > 0
                                else cost_cell.first.inner_text().strip()
                            )
                        your_price = parse_price(raw_cost)
                except Exception:
                    pass

                # ── Pack qty detection ───────────────────────────────────────
                # pack_qty fue inicializado (posiblemente fijado desde Package Notes).
                # Fallback: si cart_qty difiere del solicitado (raro en Briggs).
                requested_qty = requested_qtys.get(part_number, cart_qty)
                if pack_qty is None and cart_qty > 0 and cart_qty != requested_qty and item_status not in ('PART_ERROR',):
                    pack_qty = cart_qty
                    print(f"  📦 PACK (qty diff): {part_number} | Solicitado: {requested_qty} | Mínimo: {cart_qty}")

                # Fallback: si tiene stock pero el hover no devolvió qty exacta,
                # usar requested_qty como mínimo disponible conocido
                if in_stock == 'Y' and available_qty == 0:
                    available_qty = requested_qty

                stock_icon = '✅' if in_stock == 'Y' else '❌'
                results.append({
                    'mfrid': '',
                    'portal_mfrid': portal_mfrid_in_desc,
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


def _parse_invalid_parts(page: Page) -> List[Dict]:
    """
    Lee la sección 'Invalid Parts' (div.js--invalid-parts) del portal Briggs.
    Estos ítems son rechazados por el portal al subir el CSV y se tratan como
    PART_ERROR con nla='Y'.

    Formato del texto en cada fila: "PREFIX, PARTNUMBER"  (ej: "OCS, CRL511406")
    → extrae PARTNUMBER (texto después de la primera coma).

    Retorna lista de dicts con el mismo esquema que extract_table_data.
    """
    invalid: List[Dict] = []
    try:
        container = page.locator('div.ob__container.js--invalid-parts')
        if container.count() == 0:
            return invalid

        items = container.locator(
            'li.ob__results__item div[data-label="Part"]'
        ).all()
        print(f"⚠️  Sección 'Invalid Parts' encontrada: {len(items)} ítem(s).")

        for item in items:
            raw = item.inner_text().strip()
            # Formato: "OCS, CRL511406" → mfrid='OCS', part_number='CRL511406'
            if ',' in raw:
                mfrid_prefix = raw.split(',', 1)[0].strip()
                part_number = raw.split(',', 1)[1].strip()
            else:
                mfrid_prefix = ''
                part_number = raw

            if not part_number:
                continue

            print(f"  🚫 Invalid Part (NLA): '{raw}' → mfrid={mfrid_prefix}, pn={part_number}")
            invalid.append({
                'mfrid': mfrid_prefix,
                'part_number': part_number,
                'description': '',
                'availability': 'UNAVAILABLE',
                'qty_available': 0,
                'in_stock': 'N',
                'qty': 0,           # se actualiza con requested_qty en el caller
                'requested_qty': 0,
                'list_price': None,
                'your_price': None,
                'status': 'PART_ERROR',
                'error_message': f'No Longer Available: {part_number}',
                'superseded_from': None,
                'nla': 'Y',
                'ltl': None,
                'pack_qty': None,
            })
    except Exception as e:
        print(f"⚠️  Error leyendo Invalid Parts: {e}")
    return invalid


# ── mfrid portal mapping ──────────────────────────────────────────────────────
# El portal Briggs muestra 'BS' para Briggs & Stratton, pero en la PO viene 'BRS'
_BRIGGS_MFRID_PORTAL_MAP: Dict[str, str] = {'BRS': 'BS'}


def _get_portal_mfrid(mfrid: str) -> str:
    """Convierte mfrid de la PO al código que muestra el portal Briggs (BS ≠ BRS)."""
    return _BRIGGS_MFRID_PORTAL_MAP.get((mfrid or '').upper(), mfrid or 'BS')


def _clear_table(page: Page) -> None:
    """Click en 'Clear All' para vaciar la tabla del Build An Order."""
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
        btn = page.locator('a.ob__overview__clear')
        if btn.count() > 0 and btn.first.is_visible():
            btn.first.click()
            time.sleep(1.5)
            return
        for _ in range(4):
            page.evaluate("window.scrollBy(0, 600)")
            time.sleep(0.3)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                time.sleep(1.5)
                return
    except Exception:
        pass


def _make_part_error(item: Dict, requested_qty: int, ltl: bool = False) -> Dict:
    """Crea entrada PART_ERROR cuando no se encuentra el ítem en el portal."""
    pn = item.get('part_number', '')
    return {
        'mfrid': item.get('mfrid', ''),
        'part_number': pn,
        'description': '',
        'availability': 'UNAVAILABLE',
        'qty_available': 0,
        'in_stock': 'N',
        'qty': requested_qty,
        'requested_qty': requested_qty,
        'list_price': None,
        'your_price': None,
        'status': 'PART_ERROR',
        'error_message': f'Part not found in portal: {pn}',
        'superseded_from': None,
        'nla': None,
        'ltl': 'Y' if ltl else None,
        'pack_qty': None,
    }


def _quick_ltl_scan(page: Page) -> set:
    """
    Scan rápido de la tabla de Build An Order para detectar LTL part numbers.
    Solo lee el texto de la columna Description buscando keywords LTL y el
    ícono de camión. NO hace hovers, NO extrae precios, NO extrae disponibilidad.
    Retorna set de part_numbers (UPPER) que tienen LTL.
    """
    ltl_pns: set = set()
    try:
        rows = page.locator('div.ob__results__item').all()
        print(f"  📋 Scan LTL rápido: {len(rows)} filas...")
        for row in rows:
            try:
                # Part number
                pn_elem = row.locator('div[data-label="Part #"]')
                if pn_elem.count() == 0:
                    continue
                part_number = pn_elem.first.inner_text().strip()
                if not part_number:
                    continue

                # Detectar LTL por texto de descripción
                # NOTA: usar text_content() en lugar de inner_text() porque el texto
                # LTL está en un nodo de texto directo (no dentro de <p>), y
                # inner_text() puede omitirlo según el estado de renderizado CSS.
                desc_cell = row.locator('div[data-label="Description"]')
                if desc_cell.count() > 0:
                    full_text = (desc_cell.first.text_content() or '').lower()
                    for kw in LTL_KEYWORDS:
                        if kw in full_text:
                            ltl_pns.add(part_number.upper())
                            print(f"  🚛 LTL detectado (texto): {part_number}")
                            break

                # Detectar LTL por ícono de camión
                truck = row.locator(
                    'i.ob__overview__icon.fa-truck, '
                    'i.ob__overview__icon[class*="fa-truck"]'
                )
                if truck.count() > 0:
                    ltl_pns.add(part_number.upper())
                    print(f"  🚛 LTL detectado (truck icon): {part_number}")

            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠️  Error en scan LTL: {e}")
    return ltl_pns


def _phase_1_upload_csv(
    page: Page,
    csv_filename: str,
    requested_qtys: Dict[str, int],
    po_items: Optional[List[Dict]],
) -> set:
    """
    FASE 1: Upload CSV en /portal/upload-order → esperar tabla completa →
            scan LTL ahí mismo (donde los notes LTL son visibles) →
            navegar a build-order → limpiar ítems residuales.

    IMPORTANTE: el scan LTL debe hacerse en la página upload-order porque los
    notes "Must ship via Motor Freight" sólo aparecen en esa tabla. En
    build-order esos textos no están presentes, por eso el scan retornaba vacío.

    Retorna ltl_pn_set: set[str] con los part numbers (UPPER) que tienen LTL.
    Deja la página en build-order con tabla limpia lista para Phase 2.
    """
    print("\n" + "="*60)
    print("📤 FASE 1: UPLOAD CSV → SCAN LTL EN UPLOAD-ORDER → BUILD-ORDER")
    print("="*60)

    # ── Navegar a Upload An Order ──────────────────────────────────────
    print("📤 Navegando a Upload An Order...")
    upload_link = page.locator('a.dashboard__subnav__link[href="/portal/upload-order"]')
    if upload_link.count() == 0:
        upload_link = page.locator('a[href="/portal/upload-order"]')
    upload_link.first.wait_for(state='visible', timeout=10000)
    upload_link.first.click()
    time.sleep(3)

    downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
    csv_path = os.path.join(downloads_path, csv_filename)
    print("📁 Subiendo CSV...")
    file_input = page.locator('input#order[type="file"]')
    if file_input.count() == 0:
        file_input = page.locator('input[type="file"]')
    file_input.wait_for(state='attached', timeout=15000)
    file_input.set_input_files(csv_path)
    print(f"✅ {csv_filename} adjuntado. Esperando procesamiento...")

    orbit = page.locator('div.pd-orbit.js--pd-orbit')
    try:
        orbit.wait_for(state='visible', timeout=10_000)
        print("  ⏳ Overlay visible — esperando procesamiento...")
    except Exception:
        print("  ℹ️  Overlay no detectado.")
    try:
        orbit.wait_for(state='hidden', timeout=90_000)
        print("  ✅ Overlay desaparecido — CSV procesado.")
    except Exception:
        print("  ⚠️  Timeout overlay — esperando 35s adicionales...")
        time.sleep(35)

    # ── Esperar tabla en upload-order con tiempo prudente ─────────────
    # Los notes LTL ("Must ship via Motor Freight") sólo aparecen en ESTA
    # página. En build-order no están, por eso el scan debe ocurrir aquí.
    print("\n🔍 Esperando tabla upload-order para scan LTL...")
    ltl_pn_set: set = set()
    try:
        page.wait_for_selector('div.ob__results__item', timeout=30000)
        # Tiempo prudente para que TODOS los ítems y sus notas carguen
        time.sleep(5)
        print("📋 Tabla upload-order lista — escaneando LTL...")
        ltl_pn_set = _quick_ltl_scan(page)
    except Exception as e:
        print(f"  ⚠️  No se pudo escanear tabla upload-order: {e}")

    print(f"\n🚛 LTL DETECTADOS EN UPLOAD-ORDER: {len(ltl_pn_set)}")
    for pn in ltl_pn_set:
        print(f"  • {pn}")

    # ── Navegar a Build An Order para Fase 2 ──────────────────────────
    print("\n🛒 Navegando a Build An Order para Fase 2...")
    build_sub = page.locator('a.dashboard__subnav__link[href="/portal/build-order"]')
    if build_sub.count() == 0:
        build_sub = page.locator('a[href="/portal/build-order"]').first
    try:
        orbit.wait_for(state='hidden', timeout=10_000)
    except Exception:
        pass
    build_sub.first.wait_for(state='visible', timeout=10000)
    build_sub.first.click()
    time.sleep(3)

    # ── Limpiar build-order por si quedaron ítems de sesión anterior ──
    # (el upload a veces empuja ítems automáticamente al carrito build-order)
    print("🗑️  Verificando y limpiando build-order antes de Fase 2...")
    try:
        page.wait_for_selector('div.ob__results__item', timeout=8000)
        time.sleep(1)
        _clear_table(page)
        time.sleep(1.5)
        print("  ✅ Build-order limpio.")
    except Exception:
        print("  ℹ️  No hay ítems en build-order — tabla ya limpia.")

    print("✅ Fase 1 completa. Build-order listo para Fase 2.")
    return ltl_pn_set


def _phase_2_item_by_item(
    page: Page,
    po_items: List[Dict],
    ltl_pn_set: set,
    requested_qtys: Dict[str, int],
) -> List[Dict]:
    """
    FASE 2:
      Tabla ya limpia (Phase 1 hizo Clear All).

      Para cada part_number ÚNICO:
        1. Buscar en typeahead → recolectar TODOS los mfrid disponibles en el dropdown
        2. Para cada variante (mfrid, pn) de la PO:
             - Si portal_mfrid del body está en el dropdown → válida
             - Si NO está → PART_ERROR inmediato para esa variante
        3. Añadir el pn al carrito UNA SOLA VEZ (si al menos 1 variante es válida)

      Luego scrape único y reconciliación por (mfrid_upper, pn_upper).
    """
    import copy as _copy
    from collections import defaultdict

    print("\n" + "="*60)
    print(f"🔍 FASE 2: VALIDAR {len(po_items)} COMBINACIONES → SCRAPE ÚNICO AL FINAL")
    print("="*60)
    print("  (tabla ya limpiada por Fase 1)")

    # Agrupar po_items por pn_upper preservando orden de aparición
    items_by_pn: Dict[str, List[Dict]] = defaultdict(list)
    ordered_pns: List[str] = []
    seen_pns: set = set()
    for item in po_items:
        pn = (item.get('part_number') or '').strip()
        pn_up = pn.upper()
        if pn_up not in seen_pns:
            seen_pns.add(pn_up)
            ordered_pns.append(pn_up)
        items_by_pn[pn_up].append(item)

    # (mfrid_upper, pn_upper) → dict PART_ERROR
    part_errors: Dict[tuple, Dict] = {}
    valid_pns: set = set()   # pn_upper añadidos exitosamente al carrito
    added_count = 0

    # ── Buscar cada pn único, validar variantes, añadir UNA VEZ ────────────
    for pn_upper in ordered_pns:
        variants = items_by_pn[pn_upper]
        first    = variants[0]
        pn       = (first.get('part_number') or '').strip()
        qty      = first.get('qty', 1)
        req_qty  = first.get('requested_qty') or requested_qtys.get(pn, qty)
        is_ltl   = pn_upper in ltl_pn_set

        # Portal mfrids requeridos por la PO
        required = {_get_portal_mfrid(it.get('mfrid') or 'BRS').upper() for it in variants}
        print(f"\n── {pn} | {len(variants)} variante(s): {required}")

        try:
            search_input = page.locator('#ob-search')
            search_input.wait_for(state='visible', timeout=5000)
            search_input.fill('')
            time.sleep(0.3)
            search_input.type(pn, delay=60)
            time.sleep(1.2)

            # Esperar dropdown
            dropdown = page.locator('div.ob__search__dropdown-wrapper--active')
            try:
                dropdown.wait_for(state='visible', timeout=6000)
            except Exception:
                print(f"  ❌ Dropdown no apareció → PART_ERROR todas las variantes")
                search_input.fill('')
                for it in variants:
                    mfr_up = (it.get('mfrid') or 'BRS').upper()
                    part_errors[(mfr_up, pn_upper)] = _make_part_error(it, req_qty, ltl=is_ltl)
                continue

            # Recolectar todos los mfrid disponibles en el dropdown para este pn.
            # target_mfrid_card: primera tarjeta cuyo mfrid coincide con los mfrids
            # requeridos por la PO.  Preferirla sobre first_valid_card evita añadir
            # el ítem bajo un mfrid diferente al solicitado (ej. OCS en vez de BS).
            available_portal_mfrids: set = set()
            first_valid_card = None
            target_mfrid_card = None   # tarjeta que coincide con mfrid requerido
            cards = page.locator('div.ob__searchcard')
            for card in cards.all():
                try:
                    spans = card.locator('p.ob__searchcard__title span').all()
                    if len(spans) >= 2:
                        card_mfr = spans[0].inner_text().strip().rstrip(',').strip().upper()
                        card_pn  = spans[1].inner_text().strip().upper()
                        if card_pn == pn_upper:
                            available_portal_mfrids.add(card_mfr)
                            if first_valid_card is None:
                                first_valid_card = card
                            # Preferir la tarjeta que coincide con un mfrid requerido
                            if target_mfrid_card is None and card_mfr in required:
                                target_mfrid_card = card
                except Exception:
                    continue

            print(f"  📋 Disponibles en portal: {available_portal_mfrids or '(ninguno)'}")

            # Validar cada variante (mfrid, pn) individualmente (para logging)
            # NOTA: si el pn fue encontrado bajo CUALQUIER mfrid, todas las variantes
            # recibirán ese precio en la reconciliación. part_errors solo se aplica cuando
            # el pn no fue encontrado bajo NINGÚN mfrid.
            has_valid_variant = False
            for it in variants:
                mfrid    = (it.get('mfrid') or 'BRS').strip()
                mfrid_up = mfrid.upper()
                pmfrid   = _get_portal_mfrid(mfrid).upper()

                if pmfrid in available_portal_mfrids:
                    print(f"  ✓  {mfrid},{pn} → portal:{pmfrid} disponible")
                    has_valid_variant = True
                else:
                    print(f"  ❌ {mfrid},{pn} → portal:{pmfrid} NO en portal → PART_ERROR")
                    part_errors[(mfrid_up, pn_upper)] = _make_part_error(it, req_qty, ltl=is_ltl)

            if not has_valid_variant or first_valid_card is None:
                print(f"  ❌ Sin variantes válidas para {pn} — no se añade al carrito")
                search_input.fill('')
                continue

            # Añadir al carrito UNA sola vez.
            # Preferir la tarjeta que coincide con el mfrid solicitado; si el
            # portal no tiene esa combinación exacta, usar la primera encontrada.
            card_to_click = target_mfrid_card or first_valid_card
            card_to_click.click()
            time.sleep(0.5)
            qty_input = page.locator('#qty')
            qty_input.wait_for(state='visible', timeout=3000)
            qty_input.fill(str(req_qty))
            page.locator('a.ob__search__btn').click()
            time.sleep(1.5)
            added_count += 1
            valid_pns.add(pn_upper)
            print(f"  ✓  Añadido al carrito ({added_count} pns únicos)")

        except Exception as e:
            print(f"  ❌ Excepción {pn}: {e}")
            for it in variants:
                mfr_up = (it.get('mfrid') or 'BRS').upper()
                part_errors[(mfr_up, pn_upper)] = _make_part_error(it, req_qty, ltl=is_ltl)

    print(f"\n📋 Pns únicos añadidos: {added_count} | Con PART_ERROR: {len(set(k[1] for k in part_errors))}")

    # ── Scrape tabla completa UNA VEZ ────────────────────────────────────────
    all_requested = {
        (it.get('part_number') or ''): (it.get('requested_qty') or it.get('qty', 1))
        for it in po_items if it.get('part_number')
    }
    all_requested.update(requested_qtys)

    scraped_rows: List[Dict] = []
    if added_count > 0:
        print(f"\n⏳ Scrapeando tabla ({added_count} filas esperadas)...")
        time.sleep(2)
        try:
            page.wait_for_selector('div.ob__results__item', timeout=15000)
            time.sleep(2)
            scraped_rows = extract_table_data(page, all_requested)
            print(f"✅ Scrape final: {len(scraped_rows)} filas")
        except Exception as e:
            print(f"  ⚠️  Error en scrape final: {e}")

    # Índices por pn (primera ocurrencia), por (portal_mfrid, pn) y por superseded_from
    scraped_by_pn: Dict[str, Dict] = {}
    scraped_by_mfrid_pn: Dict[tuple, Dict] = {}
    for r in scraped_rows:
        pn_up = r['part_number'].upper()
        pmfr  = (r.get('portal_mfrid') or '').upper()
        if pn_up not in scraped_by_pn:
            scraped_by_pn[pn_up] = r
        if pmfr and (pmfr, pn_up) not in scraped_by_mfrid_pn:
            scraped_by_mfrid_pn[(pmfr, pn_up)] = r

    scraped_by_superseded: Dict[str, Dict] = {}
    for r in scraped_rows:
        sf = (r.get('superseded_from') or '').upper().strip()
        if sf and sf not in scraped_by_superseded:
            scraped_by_superseded[sf] = r

    # ── Reconciliar: cada (mfrid, pn) → su propio resultado ────────────────
    # Prioridad (POR COMBINACIÓN mfrid+pn):
    #   1. err_key in part_errors       → mfrid específico NO existe en portal → PART_ERROR
    #   2. scraped_by_mfrid_pn match   → fila exacta (portal_mfrid = mfrid solicitado)
    #                                     ej: BS,4156 cuando PO pide BRS → evita NGK,4156
    #   3. scraped_by_pn fallback      → cualquier fila con ese pn
    #   4. scraped_by_superseded       → pn fue supersedido
    #   5. fallback                    → _make_part_error
    results: List[Dict] = []
    for item in po_items:
        pn       = (item.get('part_number') or '').strip()
        pn_up    = pn.upper()
        mfrid    = (item.get('mfrid') or '').strip()
        mfrid_up = mfrid.upper()
        err_key  = (mfrid_up, pn_up)
        if not pn:
            continue

        is_ltl  = pn_up in ltl_pn_set
        req_qty = item.get('requested_qty') or requested_qtys.get(pn, item.get('qty', 1))

        if err_key in part_errors:
            # Este mfrid específico NO fue encontrado en el portal → PART_ERROR
            # (OCS y SPO son PART_ERROR aunque OEP sí exista para el mismo pn)
            print(f"  ❌ {mfrid},{pn} → PART_ERROR (no encontrado en portal)")
            results.append(part_errors[err_key])

        elif pn_up in scraped_by_pn:
            # Preferir la fila cuyo portal_mfrid coincide con el mfrid solicitado.
            # BRS → BS (via _get_portal_mfrid).  Ej: tabla tiene BS,4156 y NGK,4156;
            # para una PO con BRS usamos BS,4156 e ignoramos NGK,4156.
            pmfrid = _get_portal_mfrid(mfrid).upper()
            scraped = _copy.deepcopy(
                scraped_by_mfrid_pn.get((pmfrid, pn_up)) or scraped_by_pn[pn_up]
            )
            scraped['mfrid'] = mfrid   # preservar mfrid del PO body
            if is_ltl:
                scraped['ltl'] = 'Y'
            print(
                f"  💵 {mfrid},{pn} | portal_mfrid:{pmfrid} | Cost:{scraped.get('your_price')} | "
                f"Status:{scraped.get('status')} | LTL:{scraped.get('ltl')}"
            )
            results.append(scraped)

        elif pn_up in scraped_by_superseded:
            scraped = _copy.deepcopy(scraped_by_superseded[pn_up])
            scraped['mfrid'] = mfrid
            if is_ltl:
                scraped['ltl'] = 'Y'
            print(f"  🔄 SUPERSEDED {mfrid},{pn} → {scraped.get('part_number')}")
            results.append(scraped)

        else:
            print(f"  ⚠️  {mfrid},{pn} no en tabla → PART_ERROR")
            results.append(_make_part_error(item, req_qty, ltl=is_ltl))

    # Limpiar carrito al finalizar
    print("\n🗑️  Limpiando carrito (fin de Fase 2)...")
    _clear_table(page)

    ok     = sum(1 for r in results if r.get('status') == 'CORRECT')
    errors = sum(1 for r in results if r.get('status') != 'CORRECT')
    print(f"\n✅ FASE 2: {len(results)} combinaciones | ✅ {ok} | ❌ {errors}")
    return results


def briggs_login_automation_playwright(
    username: str,
    password: str,
    csv_filename: str,
    requested_qtys: Optional[Dict[str, int]] = None,
    po_items: Optional[List[Dict]] = None,
) -> Optional[List[Dict]]:
    """
    Flujo Briggs & Stratton en dos fases:

    FASE 1 (Upload CSV):
      - Sube el CSV al portal → scrapea la tabla completa
      - Identifica qué part numbers son LTL y guarda el set en memoria

    FASE 2 (Item-by-item):
      - Para CADA ítem de la PO, busca via typeahead en Build An Order
      - Selecciona la opción que coincide (portal_mfrid BS + part#)
      - Ingresa qty → Add Part → scrapea la fila para obtener precio real
      - Aplica ltl='Y' si el part# fue detectado en Fase 1

    Retorna List[Dict] con los resultados de Fase 2 (precios reales del portal).
    """
    if requested_qtys is None:
        requested_qtys = {}

    print("🚀 Iniciando automatización Briggs & Stratton (2 fases)...")

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
        # ── Login ──────────────────────────────────────────────────
        print(f"🌐 Navegando a {BRIGGS_BASE_URL}...")
        page.goto(BRIGGS_BASE_URL, wait_until="domcontentloaded")
        time.sleep(3)

        print("🔐 Ingresando credenciales...")
        username_input = page.locator('#Username')
        username_input.wait_for(state='visible', timeout=10000)
        username_input.fill(username)
        page.locator('#Password').fill(password)

        print("🖱️ Enviando formulario de login...")
        page.locator('button.login__btn.js--login').click()
        time.sleep(4)
        print(f"✅ Login exitoso. URL: {page.url}")

        # ── Click en 'Build an Order' (menú principal) ───────────────
        print("🛒 Navegando a Build an Order (menú principal)...")
        build_main = page.locator('a.db__link__title[href="/portal/build-order"]')
        if build_main.count() == 0:
            build_main = page.locator('a[href="/portal/build-order"]').first
        build_main.wait_for(state='visible', timeout=10000)
        build_main.click()
        time.sleep(3)
        print("✅ Sección Build an Order cargada.")

        # ── FASE 1: Upload CSV → scan LTL → Clear All ───────────────
        ltl_pn_set = _phase_1_upload_csv(
            page, csv_filename, requested_qtys, po_items
        )
        print(f"\n📝 Fase 1 completa | LTL detectados: {len(ltl_pn_set)}")

        # ── FASE 2: Item-by-item para TODOS los ítems ───────────────
        items_for_phase2 = po_items if po_items else [
            {'mfrid': '', 'part_number': pn, 'qty': qty, 'requested_qty': qty}
            for pn, qty in requested_qtys.items()
        ]

        if not items_for_phase2:
            print("⚠️  Sin ítems para Fase 2 — sin datos.")
            return []

        # Phase 1 ya dejó la página en build-order con tabla limpia
        # Solo verificar que #ob-search esté disponible
        page.wait_for_selector('#ob-search', timeout=10000)
        print("✅ Build An Order listo — iniciando carga item a item...")

        final_results = _phase_2_item_by_item(
            page, items_for_phase2, ltl_pn_set, requested_qtys
        )

        ok     = sum(1 for r in final_results if r.get('status') == 'CORRECT')
        errors = sum(1 for r in final_results if r.get('status') != 'CORRECT')
        print(f"\n📊 FINAL: {len(final_results)} | ✅ {ok} | ❌ {errors}")
        return final_results

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
    CSV_FILENAME = "PO_93684_SP_20260619_125759.csv"

    result = briggs_login_automation_playwright(USERNAME, PASSWORD, CSV_FILENAME)

    if result:
        print(f"\n📊 Datos extraídos ({len(result)} ítems):")
        for idx, row in enumerate(result, 1):
            print(f"\nFila {idx}:")
            for key, value in row.items():
                print(f"  {key}: {value}")
