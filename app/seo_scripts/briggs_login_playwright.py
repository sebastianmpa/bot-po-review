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
                nla = None
                superseded_from = None
                ltl = None
                item_status = 'CORRECT'
                item_error_message = None
                pack_qty = None  # se puede fijar desde Package Notes

                try:
                    desc_cell = row.locator('div[data-label="Description"]')
                    if desc_cell.count() > 0:
                        full_text = desc_cell.first.inner_text()
                        full_text_lower = full_text.lower()

                        desc_ps = desc_cell.locator('p.ob__results__text').all()
                        if desc_ps:
                            description = desc_ps[0].inner_text().strip()

                        # NLA — SOLO desde el texto de descripción.
                        # NO usar availability == 'UNAVAILABLE' porque si la
                        # detección del ícono falla por timing, se producirían
                        # falsos NLA en partes que sí tienen stock y precio.
                        if 'this item is not available' in full_text_lower:
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


def briggs_login_automation_playwright(
    username: str,
    password: str,
    csv_filename: str,
    requested_qtys: Optional[Dict[str, int]] = None,
    po_items: Optional[List[Dict]] = None,
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
        print(f"✅ Archivo {csv_filename} adjuntado. Esperando procesamiento del portal...")

        # El portal Briggs muestra un overlay div.pd-orbit.js--pd-orbit mientras
        # procesa el CSV (~30 s). Hay que esperar a que desaparezca antes de
        # intentar cualquier clic — de lo contrario intercepta los pointer events
        # y Playwright lanza TimeoutError aunque el enlace sea visible y estable.
        orbit = page.locator('div.pd-orbit.js--pd-orbit')
        try:
            # Esperar a que el overlay aparezca (confirma que el portal arrancó)
            orbit.wait_for(state='visible', timeout=10_000)
            print("  ⏳ Overlay de procesamiento visible — esperando que termine...")
        except Exception:
            print("  ℹ️  Overlay no detectado al inicio — puede que el portal ya procesó.")

        try:
            # Esperar hasta 90 s a que el overlay desaparezca completamente
            orbit.wait_for(state='hidden', timeout=90_000)
            print("  ✅ Overlay desaparecido — CSV procesado.")
        except Exception:
            print("  ⚠️  Timeout esperando overlay — esperando 35 s adicionales...")
            time.sleep(35)

        # Pausa extra para que el DOM se estabilice tras el procesamiento
        time.sleep(2)
        print(f"✅ Archivo {csv_filename} cargado y procesado.")

        # ── 6. Click en 'Build An Order' (subnav) — aquí aparece la tabla ────
        print("🛒 Navegando a Build An Order (subnav) para ver resultados...")
        build_sub = page.locator('a.dashboard__subnav__link[href="/portal/build-order"]')
        if build_sub.count() == 0:
            build_sub = page.locator('a[href="/portal/build-order"]').first

        # Segunda verificación: asegurar que el overlay no esté bloqueando el clic
        try:
            orbit.wait_for(state='hidden', timeout=15_000)
        except Exception:
            pass  # Si ya está oculto o no existe, continuar

        build_sub.first.wait_for(state='visible', timeout=10000)
        build_sub.first.click()
        time.sleep(3)
        print("✅ Build An Order (subnav) cargado.")

        # ── 7. Esperar tabla de resultados ────────────────────────────────────
        print("⏳ Esperando tabla de resultados (div.ob__results__item)...")
        page.wait_for_selector('div.ob__results__item', timeout=60000)
        time.sleep(5)
        print("▶️  Tabla lista. Iniciando scraping...")

        # ── 8. Scrapear tabla válida ──────────────────────────────────────
        cart_data = extract_table_data(page, requested_qtys)
        print(f"✅ Scraping completado. {len(cart_data)} ítems extraídos.")

        # ── 8b. Capturar Invalid Parts (NLA rechazados por el portal) ──────
        invalid_parts = _parse_invalid_parts(page)
        # Lookup de qty por (mfrid_upper, pn_upper) → fallback a requested_qtys
        po_qty_by_mfrid_pn: Dict[tuple, int] = {}
        if po_items:
            for it in po_items:
                mfr = (it.get('mfrid') or '').upper()
                _pn = (it.get('part_number') or it.get('partNumber', '')).upper()
                po_qty_by_mfrid_pn[(mfr, _pn)] = it.get('qty', 0)
        for inv in invalid_parts:
            mfr_up = (inv.get('mfrid') or '').upper()
            pn_up = inv['part_number'].upper()
            req_qty = (
                po_qty_by_mfrid_pn.get((mfr_up, pn_up))
                or requested_qtys.get(inv['part_number'], 0)
            )
            inv['requested_qty'] = req_qty
            inv['qty'] = req_qty
        if invalid_parts:
            print(f"⚠️  {len(invalid_parts)} ítem(s) en 'Invalid Parts' → NLA.")
            cart_data.extend(invalid_parts)

        # ── 9. Limpiar tabla con 'Clear All' (scroll al fondo para revelarlo) ─
        print("🗑️  Limpiando tabla con 'Clear All'...")
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            clear_all_btn = page.locator('a.ob__overview__clear')
            if clear_all_btn.count() == 0 or not clear_all_btn.first.is_visible():
                for _ in range(4):
                    page.evaluate("window.scrollBy(0, 600)")
                    time.sleep(0.4)
                    if clear_all_btn.count() > 0 and clear_all_btn.first.is_visible():
                        break
            if clear_all_btn.count() > 0 and clear_all_btn.first.is_visible():
                clear_all_btn.first.click()
                time.sleep(2)
                print("  ✅ Tabla limpiada con 'Clear All'.")
            else:
                print("  ⚠️ Botón 'Clear All' no encontrado — tabla puede quedar con ítems.")
        except Exception as _e:
            print(f"  ⚠️ Error limpiando con 'Clear All': {_e}")

        # ── 9b. Reconciliar: garantizar un resultado por (mfrid, part) ───────
        # covered_no_mfrid  → pn cubierto por entrada con mfrid='' (cubre todas las variantes)
        # covered_with_mfrid → (MFRID_UPPER, PN_UPPER) con mfrid explícito
        covered_no_mfrid: set = set()
        covered_with_mfrid: set = set()
        for r in cart_data:
            pn_up = r['part_number'].upper()
            mfr_up = (r.get('mfrid') or '').upper()
            if mfr_up:
                covered_with_mfrid.add((mfr_up, pn_up))
            else:
                covered_no_mfrid.add(pn_up)
            if r.get('superseded_from'):
                covered_no_mfrid.add(r['superseded_from'].upper())

        items_to_check = (
            po_items if po_items else
            [{'mfrid': '', 'part_number': pn, 'qty': qty}
             for pn, qty in requested_qtys.items()]
        )
        for itm in items_to_check:
            mfrid_orig = (itm.get('mfrid') or '')
            pn_orig = itm.get('part_number') or itm.get('partNumber', '')
            req_qty = itm.get('qty', 0)
            if not pn_orig:
                continue
            mfr_up = mfrid_orig.upper()
            pn_up = pn_orig.upper()
            # Cubierto si: a) hay entrada sin mfrid para este pn (cubre todas las variantes)
            #             b) hay coincidencia exacta (mfrid, pn)
            #             c) el input no tiene mfrid y existe cualquier entrada para este pn
            if pn_up in covered_no_mfrid:
                continue
            if mfr_up and (mfr_up, pn_up) in covered_with_mfrid:
                continue
            if not mfr_up and any(p == pn_up for _, p in covered_with_mfrid):
                continue
            print(f"  ⚠️ '{mfrid_orig}/{pn_orig}' sin resultado en el portal → PART_ERROR.")
            cart_data.append({
                'mfrid': mfrid_orig,
                'part_number': pn_orig,
                'description': '',
                'availability': 'UNAVAILABLE',
                'qty_available': 0,
                'in_stock': 'N',
                'qty': req_qty,
                'requested_qty': req_qty,
                'list_price': None,
                'your_price': None,
                'status': 'PART_ERROR',
                'error_message': f'Part not found in portal results: {pn_orig}',
                'superseded_from': None,
                'nla': None,
                'ltl': None,
                'pack_qty': None,
            })

        ok = sum(1 for r in cart_data if r['status'] == 'CORRECT')
        errors = sum(1 for r in cart_data if r['status'] != 'CORRECT')
        print(f"\n📊 Total: {len(cart_data)} | ✅ CORRECT: {ok} | ❌ Errores: {errors}")

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
    CSV_FILENAME = "PO_93684_SP_20260619_125759.csv"

    result = briggs_login_automation_playwright(USERNAME, PASSWORD, CSV_FILENAME)

    if result:
        print(f"\n📊 Datos extraídos ({len(result)} ítems):")
        for idx, row in enumerate(result, 1):
            print(f"\nFila {idx}:")
            for key, value in row.items():
                print(f"  {key}: {value}")
