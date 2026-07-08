"""
florida_outdoor_playwright.py
-----------------------------
Automatización Playwright para Florida Outdoor Equipment (FOE / FO).

Flujo completo:
  1.  Login (usr_name / usr_password)
  2.  Ir a Order Pad (/storefrontCommerce/orderpad.do)
  3.  Verificar radio "100 rows to display"
  4.  Subir archivo Excel y click Upload
  5.  Esperar #orderPadTable
  6.  Click "Add to Cart" (1er vez) → página recarga mostrando tr.orderPadEntryError
  7.  Scrapear #orderPadTable → capturar {part_number: error} de filas inválidas
  8.  Limpiar inputs itm_num + itm_qty de las filas con error
  9.  Click "Add to Cart" (2da vez) → navega al carrito
  10. Scrapear div.errorMessage → ítems sin stock
  11. Scrapear table.standardTable (con paginación):
      - Para cada ítem: click link → #itemDetailInfo → Quantity Available
      - Si qty_available == 0 → PART_ERROR
  12. Combinar todos los datos y retornar
"""

from playwright.sync_api import sync_playwright, Page
import time
import os
import re
from typing import List, Dict, Optional
from decimal import Decimal, InvalidOperation


FOE_BASE_URL = "https://ecommerce.floridaoutdoor.com/storefrontCommerce/?login=T&action=prepare_login"


def parse_price(text: str) -> Optional[Decimal]:
    """Convierte texto de precio a Decimal. Ej: '$23.07' → Decimal('23.07')"""
    try:
        clean = text.replace('$', '').replace(',', '').strip()
        if clean:
            return Decimal(clean)
    except (InvalidOperation, Exception):
        pass
    return None


# ── Helpers del Order Pad ──────────────────────────────────────────────────────

def _add_item_to_orderpad(page: Page, part_number: str, qty: int) -> tuple:
    """
    Añade un ítem usando el formulario lineItemAddForm del Order Pad.
    Retorna (success: bool, error_message: str|None).
    
    ✅ IMPORTANTE: Captura div.errorMessage INMEDIATAMENTE después de agregar,
    incluso si el ítem no aparece en la tabla. Los errores se mantienen en memoria.
    """
    try:
        # Contar ítems actuales antes de añadir
        before_count = page.locator(
            'table.standardTable td[name^="cust_itemlink"]'
        ).count()

        # Capturar error PREVIO antes del submit (puede quedar de operación anterior)
        pre_error_text = ''
        pre_err_el = page.locator('div.errorMessage, span.errorMessage')
        if pre_err_el.count() > 0:
            pre_error_text = pre_err_el.first.inner_text().strip()

        # Llenar campo de item number
        itm_input = page.locator('input[name="itm_num"]').first
        itm_input.wait_for(state='visible', timeout=5000)
        itm_input.click(click_count=3)
        itm_input.fill(part_number.upper())

        # Llenar cantidad
        qty_field = page.locator('input[name="qty"]').first
        qty_field.click(click_count=3)
        qty_field.fill(str(qty))

        # Submit
        add_btn = page.locator('input.addToCartButtonL')
        if add_btn.count() == 0:
            add_btn = page.locator('input[type="submit"][value*="Add to Cart"]')
        add_btn.first.click()

        # Esperar recarga de página
        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except Exception:
            try:
                page.wait_for_load_state('domcontentloaded', timeout=10000)
            except Exception:
                pass
        time.sleep(1)

        # ✅ PRIMERO: Capturar errores de validación, pero solo si son NUEVOS
        # (distintos al error que ya existía antes del submit — evita re-leer errores anteriores)
        err_el = page.locator('div.errorMessage, span.errorMessage')
        if err_el.count() > 0:
            error_text = err_el.first.inner_text().strip()
            if error_text != pre_error_text:
                # Es un error nuevo — pertenece a este ítem
                print(f"    ⚠️ Error de validación capturado: {error_text[:200]}")
                return False, error_text[:200]
            # Mismo error que antes — no es de este ítem, ignorar y verificar tabla

        # SEGUNDO: Verificar si el ítem apareció en la tabla
        after_count = page.locator(
            'table.standardTable td[name^="cust_itemlink"]'
        ).count()
        if after_count > before_count:
            print(f"    ✅ Ítem {part_number} agregado exitosamente.")
            return True, None

        # Si no hay error nuevo y no apareció = fallo silencioso
        return False, f"Item not added to cart: {part_number}"

    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:150]}"


def _show_all_cart_items(page: Page) -> None:
    """Cambia la paginación del carrito a 'All' para ver todos los ítems a la vez."""
    try:
        all_link = page.locator('div.numberItemsPerPage a:has-text("All")')
        if all_link.count() > 0 and all_link.first.is_visible():
            print("  🔢 Cambiando paginación a 'All'...")
            all_link.first.click()
            page.wait_for_load_state('domcontentloaded')
            try:
                page.wait_for_selector('table.standardTable', timeout=15000)
            except Exception:
                pass
            time.sleep(2)
            print("  ✅ Vista 'All' activa.")
        else:
            print("  ℹ️ Paginación 'All' no disponible (pocos ítems).")
    except Exception as e:
        print(f"  ⚠️ Error activando 'All': {e}")


def _clear_foe_cart(page: Page) -> None:
    """
    Limpia el carrito de FOE usando el botón Clear (clearCart).
    Se ejecuta al final, después de que todo ha sido scrapeado.
    Selector: <input type="button" value="Clear" onclick="clearCart(orderpadForm)" class="button">
    """
    print("🗑️ Limpiando carrito FOE...")
    try:
        clear_btn = page.locator('input[type="button"][value="Clear"]')
        if clear_btn.count() == 0:
            clear_btn = page.locator('input[onclick*="clearCart"]')

        if clear_btn.count() > 0:
            clear_btn.first.click()
            try:
                page.wait_for_load_state('domcontentloaded', timeout=10000)
            except Exception:
                pass
            time.sleep(2)
            remaining = page.locator('table.standardTable td[name^="cust_itemlink"]').count()
            print(f"  ✅ Carrito limpiado. Ítems restantes: {remaining}")
        else:
            print("  ⚠️ Botón 'Clear' no encontrado.")
    except Exception as e:
        print(f"  ⚠️ Error limpiando carrito: {e}")


# ── Helpers del Carrito ────────────────────────────────────────────────────────

def _parse_out_of_stock_errors(page: Page) -> Dict[str, str]:
    """
    Scrapea div.errorMessage en la página del carrito.
    Retorna {part_number: error_message} para ítems sin stock.
    """
    out_of_stock: Dict[str, str] = {}
    try:
        error_div = page.locator('div.errorMessage')
        if error_div.count() == 0:
            return out_of_stock
        full_text = error_div.first.inner_text()
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]
        for line in lines:
            m = re.search(r'for item\s+([^\s.]+)', line, re.IGNORECASE)
            if m:
                part = m.group(1).strip().rstrip('.')
                out_of_stock[part] = f"Out of stock: {line}"
                print(f"  📦 Sin stock detectado: {part}")
    except Exception as e:
        print(f"⚠️ Error parseando div.errorMessage: {e}")
    return out_of_stock


def _get_item_detail_info(page: Page) -> Dict:
    """
    Lee #itemDetailInfo y extrae Quantity Available y Dealer Price.
    """
    detail = {'qty_available': -1, 'dealer_price': None}
    try:
        detail_div = page.locator('#itemDetailInfo')
        if detail_div.count() == 0:
            return detail
        table_text = detail_div.inner_text()
        qty_match = re.search(r'Quantity Available[:\s]+(\d+)', table_text, re.IGNORECASE)
        if qty_match:
            detail['qty_available'] = int(qty_match.group(1))
        price_match = re.search(r'Dealer Price[:\s]+\$?([\d,.]+)', table_text, re.IGNORECASE)
        if price_match:
            detail['dealer_price'] = parse_price(price_match.group(1))
    except Exception as e:
        print(f"  ⚠️ Error leyendo #itemDetailInfo: {e}")
    return detail


def _collect_cart_page_items(page: Page) -> List[Dict]:
    """
    Recorre una página de table.standardTable y retorna una lista con
    {part_number, your_price, list_price, qty, description, superseded_from}.
    
    ✅ DETECTA SUPERSEDIDOS: Lee el paréntesis en la celda (ej: YH478000321 (YH478000320)).
    El superseded_from es el ítem original (alternativa antigua).
    """
    items = []
    try:
        all_rows = page.locator('table.standardTable tbody tr').all()
        i = 0
        while i < len(all_rows):
            row = all_rows[i]
            row_class = row.get_attribute('class') or ''
            if 'columnHeader' in row_class:
                i += 1
                continue
            item_td = row.locator('td[name^="cust_itemlink"]')
            if item_td.count() == 0:
                i += 1
                continue
            try:
                # ✅ Leer texto COMPLETO de la celda (puede contener paréntesis)
                item_cell_text = item_td.first.inner_text().strip()
                if not item_cell_text:
                    i += 1
                    continue

                # Parsear paréntesis para detectar supersedidos
                # Formato: "YH478000321\n(YH478000320)" o "YH478000321"
                lines = item_cell_text.split('\n')
                part_number = lines[0].strip() if lines else ''
                superseded_from = None

                # Buscar paréntesis en las líneas siguientes
                if len(lines) > 1:
                    for line in lines[1:]:
                        if line.startswith('(') and line.endswith(')'):
                            superseded_from = line.strip()[1:-1]  # Quitar paréntesis
                            print(f"    🔄 SUPERSEDIDO detectado: {part_number} ← {superseded_from}")
                            break

                if not part_number:
                    i += 1
                    continue

                # Dealer Price (your_price)
                price_td = row.locator('td[name^="calc_price"]')
                your_price = parse_price(price_td.first.inner_text()) if price_td.count() > 0 else None

                # List Price
                list_price_td = row.locator('td[name^="web_price"]')
                list_price = parse_price(list_price_td.first.inner_text()) if list_price_td.count() > 0 else None

                # Qty del carrito
                cart_qty = 0
                qty_td = row.locator('td[name^="qty_input"]')
                if qty_td.count() > 0:
                    qty_input = qty_td.first.locator('input[type="text"]')
                    if qty_input.count() > 0:
                        val = qty_input.first.get_attribute('value') or '0'
                        cart_qty = int(val) if val.strip().isdigit() else 0

                # Descripción (siguiente fila con itm_proddesc)
                description = ''
                if i + 1 < len(all_rows):
                    desc_row = all_rows[i + 1]
                    desc_td = desc_row.locator('td[name^="itm_proddesc"]')
                    if desc_td.count() > 0:
                        description = desc_td.first.inner_text().strip()
                        i += 1  # Saltar fila de descripción

                items.append({
                    'part_number': part_number,
                    'your_price': your_price,
                    'list_price': list_price,
                    'qty': cart_qty,
                    'description': description,
                    'superseded_from': superseded_from,
                })
            except Exception as e:
                print(f"  ⚠️ Error en fila {i}: {e}")
            i += 1
    except Exception as e:
        print(f"❌ Error colectando ítems de la página del carrito: {e}")
    return items


def _has_next_cart_page(page: Page) -> bool:
    """
    Navega a la siguiente página del carrito usando div.itemListNavPagination.
    Estructura: <span class="itemListNavPageCurrent">1</span> <a href="...pageClicked=2">2</a>
    """
    try:
        # Leer página actual
        current_span = page.locator('span.itemListNavPageCurrent')
        if current_span.count() == 0:
            return False

        current_page = int(current_span.first.inner_text().strip())
        next_page = current_page + 1

        # Buscar link con pageClicked=next_page dentro del bloque de paginación
        next_link = page.locator(
            f'div.itemListNavPagination a[href*="pageClicked={next_page}"]'
        )
        if next_link.count() > 0 and next_link.first.is_visible():
            print(f"    ➡️ Paginación: página {current_page} → {next_page}")
            next_link.first.click()
            page.wait_for_load_state('domcontentloaded')
            page.wait_for_selector('table.standardTable', timeout=15000)
            time.sleep(1)
            return True

    except Exception as e:
        print(f"    ⚠️ Error en paginación: {e}")
    return False


def _scrape_cart_with_details(
    page: Page,
    requested_qtys: Dict[str, int],
    out_of_stock: Dict[str, str],
    po_mfr_map: Dict[str, str],
    po_mfr_orig_map: Dict[str, str],
    on_item_ready=None,
) -> List[Dict]:
    """
    Scrapea TODAS las páginas de table.standardTable.
    Para cada ítem: re-query por índice nth(idx) → click link → #itemDetailInfo → Quantity Available.
    Usa nth(idx) en lugar de filter(has_text) para evitar referencias stale tras go_back().
    """
    all_results = []
    page_num = 1

    while True:
        print(f"  📄 Página {page_num} del carrito...")

        # Recoger datos estáticos de esta página (precio, qty, descripción)
        static_items = _collect_cart_page_items(page)
        item_count = len(static_items)
        print(f"    📋 {item_count} ítem(s) en esta página.")

        for idx in range(item_count):
            item_data = static_items[idx]
            part_number = item_data['part_number']
            cart_qty = item_data['qty']
            your_price = item_data['your_price']
            requested_qty = requested_qtys.get(part_number, cart_qty)
            pack_qty = cart_qty if cart_qty > 0 and cart_qty != requested_qty else None

            qty_available = -1
            item_status = 'CORRECT'
            item_error_message = None

            # Verificar out-of-stock del errorMessage antes de abrir detalle
            if part_number in out_of_stock:
                item_status = 'PART_ERROR'
                item_error_message = out_of_stock[part_number]
                print(f"    📦 Sin stock (errorMsg): {part_number}")
            else:
                # Re-query por índice — fresco después de cada go_back()
                try:
                    link = page.locator(
                        'table.standardTable td[name^="cust_itemlink"] a'
                    ).nth(idx)

                    print(f"    🔍 Detalle [{idx+1}/{item_count}]: {part_number}...")
                    link.click()
                    # Esperar que cargue la página de detalle (siempre navega a una nueva URL)
                    try:
                        page.wait_for_selector('#itemDetailInfo', timeout=8000)
                    except Exception:
                        pass

                    detail = _get_item_detail_info(page)
                    qty_available = detail['qty_available']

                    # Siempre volver atrás para continuar con el siguiente ítem
                    page.go_back()
                    page.wait_for_load_state('domcontentloaded')
                    try:
                        page.wait_for_selector('table.standardTable', timeout=15000)
                    except Exception:
                        pass
                    time.sleep(1.5)

                    if qty_available == 0:
                        item_status = 'PART_ERROR'
                        item_error_message = f"Out of stock (0 available): {part_number}"
                        print(f"    📦 Sin stock: {part_number}")
                    else:
                        print(
                            f"    ✓ {part_number} | Dealer: {your_price} "
                            f"| Qty: {cart_qty} | Disp: {qty_available}"
                        )

                except Exception as e:
                    print(f"    ⚠️ Error detalle {part_number}: {e}")

            # ✅ Determinar status final considerando supersedidos
            final_status = item_status
            if item_status != 'PART_ERROR' and item_data.get('superseded_from'):
                final_status = 'SUPERSEDED'
                print(f"    🔄 Status: SUPERSEDED (reemplazo: {item_data['superseded_from']})")

            all_results.append({
                'mfrid': po_mfr_map.get(part_number, ''),  # ✅ DEL BODY
                'mfrid_orig': po_mfr_orig_map.get(part_number, ''),  # ✅ DEL BODY
                'part_number': part_number,
                'description': item_data['description'],
                'qty': cart_qty,
                'requested_qty': requested_qty,
                'list_price': item_data['list_price'],
                'your_price': your_price,
                'qty_available': qty_available if qty_available >= 0 else 0,
                'in_stock': 'Y' if qty_available > 0 else 'N',
                'status': final_status,
                'error_message': item_error_message,
                'nla': None,
                'superseded_from': item_data.get('superseded_from'),  # ✅ PRESERVAR SUPERSEDIDO
                'pack_qty': pack_qty,
                'ltl': None,
            })
            if on_item_ready:
                on_item_ready(all_results[-1])

        # Intentar ir a la siguiente página
        if _has_next_cart_page(page):
            page_num += 1
            try:
                page.wait_for_selector('table.standardTable', timeout=10000)
                time.sleep(2)
                # Actualizar static_items se hace al inicio del siguiente while
            except Exception:
                print("⚠️ No se pudo cargar la siguiente página del carrito.")
                break
        else:
            print(f"  ✅ Última página ({page_num}) procesada.")
            break

    return all_results


# ── Función principal ──────────────────────────────────────────────────────────

def florida_outdoor_automation_playwright(
    username: str,
    password: str,
    po_items: List[Dict],
    on_item_ready=None,
) -> Optional[List[Dict]]:
    """
    Flujo ítem-a-ítem para Florida Outdoor Equipment.
    Cada ítem se añade individualmente via lineItemAddForm en el Order Pad.
    No requiere generación de archivos Excel.
    """
    print("🚀 Iniciando automatización Florida Outdoor Equipment (ítem-a-ítem)...")
    print(f"📦 Ítems a procesar: {len(po_items)}")

    # requested_qtys para detección de packs
    requested_qtys: Dict[str, int] = {
        item.get('part_number', ''): item.get('qty', 1)
        for item in po_items if item.get('part_number')
    }

    # ✅ Crear mapas desde el body para enriquecimiento
    po_mfr_map: Dict[str, str] = {
        item.get('part_number', ''): item.get('mfrid', '')
        for item in po_items if item.get('part_number')
    }
    po_mfr_orig_map: Dict[str, str] = {
        item.get('part_number', ''): item.get('mfrid_orig', '')
        for item in po_items if item.get('part_number')
    }

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
        # ── 1. Login ──────────────────────────────────────────────────────────
        print("🌐 Navegando a Florida Outdoor Equipment...")
        page.goto(FOE_BASE_URL, wait_until="domcontentloaded")
        time.sleep(3)

        print("🔐 Ingresando credenciales...")
        usr_input = page.locator('#usr_name')
        usr_input.wait_for(state='visible', timeout=10000)
        usr_input.fill(username)
        page.locator('#usr_password').fill(password)
        page.locator('input[type="submit"][value="Sign In"]').click()
        time.sleep(4)
        print(f"✅ Login exitoso. URL: {page.url}")

        # ── 2. Navegar a Order Pad ────────────────────────────────────────────
        print("📋 Navegando a Order Pad...")
        order_pad_link = page.locator('a[href="/storefrontCommerce/orderpad.do"]')
        if order_pad_link.count() == 0:
            order_pad_link = page.locator('a:has-text("Order Pad")')
        order_pad_link.first.wait_for(state='visible', timeout=10000)
        order_pad_link.first.click()
        time.sleep(3)
        print("✅ Order Pad cargado.")

        # ── 2b. Limpiar carrito antes de añadir ítems ────────────────────────
        # Navega al carrito, elimina ítems residuales de sesiones anteriores
        # y vuelve al Order Pad para empezar con carrito vacío.
        print("🗑️ Verificando carrito FOE antes de añadir ítems...")
        page.goto(
            "https://ecommerce.floridaoutdoor.com/storefrontCommerce/cartView.do",
            wait_until="domcontentloaded",
        )
        time.sleep(2)
        _clear_foe_cart(page)
        print("📋 Volviendo al Order Pad...")
        page.goto(
            "https://ecommerce.floridaoutdoor.com/storefrontCommerce/orderpad.do",
            wait_until="domcontentloaded",
        )
        time.sleep(3)
        print("✅ Order Pad listo para carga.")

        # ── 3. Añadir ítems uno a uno ─────────────────────────────────────────
        print("🗂️ Añadiendo ítems al carrito...")
        invalid_items: Dict[str, str] = {}

        for idx, item in enumerate(po_items):
            part_number = item.get('part_number', '')
            qty = item.get('qty', 1)
            if not part_number:
                continue

            print(f"  ➕ [{idx+1}/{len(po_items)}] {part_number} (qty={qty})")
            success, err_msg = _add_item_to_orderpad(page, part_number, qty)

            if success:
                print(f"    ✅ Añadido exitosamente.")
            else:
                error_msg = err_msg or f"No se pudo añadir: {part_number}"
                invalid_items[part_number] = error_msg
                print(f"    ❌ PART_ERROR capturado en memoria: {error_msg}")

                # ✅ Llamar on_item_ready INMEDIATAMENTE para items fallidos
                # (no esperar al final del scraping del carrito)
                if on_item_ready:
                    failed_item = {
                        'mfrid':          po_mfr_map.get(part_number, ''),
                        'mfrid_orig':     po_mfr_orig_map.get(part_number, ''),
                        'part_number':    part_number,
                        'description':    '',
                        'qty':            requested_qtys.get(part_number, qty),
                        'requested_qty':  requested_qtys.get(part_number, qty),
                        'list_price':     None,
                        'your_price':     None,
                        'qty_available':  0,
                        'in_stock':       'N',
                        'status':         'PART_ERROR',
                        'error_message':  error_msg,
                        'nla':            None,
                        'superseded_from':None,
                        'pack_qty':       None,
                        'ltl':            None,
                    }
                    on_item_ready(failed_item)

        added = len(po_items) - len(invalid_items)
        print(f"\n📊 {added}/{len(po_items)} ítems en carrito. {len(invalid_items)} fallidos.")

        # ── 4. Ir al carrito y mostrar todos (All) ────────────────────────────
        print("🛒 Navegando al carrito...")
        page.goto(
            "https://ecommerce.floridaoutdoor.com/storefrontCommerce/cartView.do",
            wait_until="domcontentloaded",
        )
        time.sleep(3)
        _show_all_cart_items(page)

        # ── 5. Detectar OOS en errorMessage ──────────────────────────────────
        out_of_stock = _parse_out_of_stock_errors(page)
        if out_of_stock:
            print(f"  📦 {len(out_of_stock)} ítem(s) sin stock en errorMessage.")

        # ── 6. Scrapear carrito con detalles de qty_available ─────────────────
        cart_items: List[Dict] = []
        if page.locator('table.standardTable').count() > 0:
            print("📊 Scrapeando carrito con detalles de stock...")
            cart_items = _scrape_cart_with_details(page, requested_qtys, out_of_stock, po_mfr_map, po_mfr_orig_map, on_item_ready)
            print(f"✅ {len(cart_items)} ítem(s) procesados del carrito.")
        else:
            print("⚠️ No se encontró table.standardTable.")

        # ── 7. Limpiar carrito ────────────────────────────────────────────────
        _clear_foe_cart(page)

        # ── 8. Combinar: válidos del carrito + fallidos al añadir ─────────────
        # ✅ Los errores se mantienen en memoria (invalid_items dict)
        cart_part_numbers = {r['part_number'] for r in cart_items}
        for part, error_msg in invalid_items.items():
            if part not in cart_part_numbers:
                cart_items.append({
                    'mfrid': po_mfr_map.get(part, ''),
                    'mfrid_orig': po_mfr_orig_map.get(part, ''),
                    'part_number': part,
                    'description': '',
                    'qty': requested_qtys.get(part, 0),
                    'requested_qty': requested_qtys.get(part, 0),
                    'list_price': None,
                    'your_price': None,
                    'qty_available': 0,
                    'in_stock': 'N',
                    'status': 'PART_ERROR',
                    'error_message': error_msg,
                    'nla': None,
                    'superseded_from': None,
                    'pack_qty': None,
                    'ltl': None,
                })
                # on_item_ready ya fue llamado inmediatamente al detectar el error
                print(f"  ℹ️ Ítem fallido {part} consolidado en resultados.")

        ok = sum(1 for r in cart_items if r['status'] == 'CORRECT')
        errors = sum(1 for r in cart_items if r['status'] == 'PART_ERROR')
        print(f"\n📊 Total: {len(cart_items)} | ✅ CORRECT: {ok} | ❌ PART_ERROR: {errors}")

        return cart_items

    except Exception as e:
        print(f"❌ Error durante la automatización Florida Outdoor: {e}")
        import traceback
        traceback.print_exc()
        return None

    finally:
        browser.close()
        print("🚪 Navegador cerrado.")


# ── Ejecución directa para pruebas ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    USERNAME = "dlrc2652"
    PASSWORD = "Elra2026$@"
    TEST_PO_ITEMS = [
        {"part_number": "10021203930", "qty": 1},
        {"part_number": "12901152730", "qty": 5},
        {"part_number": "13001744331", "qty": 4},
    ]

    result = florida_outdoor_automation_playwright(USERNAME, PASSWORD, TEST_PO_ITEMS)

    if result:
        print(f"\n📊 Datos extraídos ({len(result)} ítems):")
        for idx, row in enumerate(result, 1):
            print(f"\nFila {idx}:")
            for key, value in row.items():
                print(f"  {key}: {value}")
