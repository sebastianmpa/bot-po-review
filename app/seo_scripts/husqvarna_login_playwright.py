from playwright.sync_api import sync_playwright, Page
from pynput.keyboard import Key, Controller
import time
import os
from typing import List, Dict, Optional, Tuple
from decimal import Decimal


def parse_price(price_text: str) -> Optional[Decimal]:
    """
    Convierte texto de precio a Decimal.
    Ejemplo: "$23.07" -> Decimal("23.07")
    """
    try:
        clean_price = price_text.replace('$', '').replace(',', '').strip()
        if clean_price:
            return Decimal(clean_price)
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  PASO 1: Detectar errores en la tabla de resultados del import
# ─────────────────────────────────────────────────────────────────────────────

def detect_import_errors(page: Page) -> Tuple[List[str], bool]:
    """
    Analiza la página de resultados del import y devuelve:
      - Lista de dicts de error enriquecidos:
          {part_number, requested_part, status, nla, superseded_from, error_message}
        Statuses posibles: "PART_ERROR" | "SUPERSEDED" | "NLA"
      - Bool: True si hay algún error (y por tanto hay que pulsar Remove Items)

    Detección por tipo:
      SUPERSEDED → columna ITEM# tiene dos part numbers (el último = superseded_from)
      NLA        → el texto de error contiene "no longer available"
      PART_ERROR → "not found by cross-referencing" u otros errores
    """
    error_parts: List[Dict] = []

    try:
        page.wait_for_selector(
            '#purchase-history-table, .MuiAlert-colorError',
            timeout=15000
        )
    except Exception:
        print("⚠️ Timeout esperando resultados del import")
        return error_parts, False

    # ── Verificar banner de resumen de errores ──────────────────────────────
    error_banner = page.locator('.MuiAlert-colorError p:has-text("found with error")')
    has_errors = error_banner.count() > 0

    if not has_errors:
        print("✅ Sin errores en el import.")
        return error_parts, False

    error_text = error_banner.first.inner_text().strip()
    print(f"⚠️ Banner de error detectado: '{error_text}'")

    # ── Analizar cada fila con error ────────────────────────────────────────
    try:
        rows = page.locator('#purchase-history-table tbody tr').all()
        for idx, row in enumerate(rows):
            try:
                error_alert = row.locator('.MuiAlert-colorError')
                if error_alert.count() == 0:
                    continue

                # ── Texto del error ─────────────────────────────────────────
                error_msg_text = error_alert.first.inner_text().strip().lower()

                # ── Columna ITEM# (td:nth-child(3)) ────────────────────────
                item_col_text = ''
                part_elem = row.locator('td:nth-child(3)')
                if part_elem.count() > 0:
                    item_col_text = part_elem.inner_text().strip()

                if not item_col_text:
                    continue

                # ── Detectar SUPERSEDED: dos part numbers en ITEM# ──────────
                # El portal puede mostrar: "OLD_PART\nNEW_PART" o "OLD_PART NEW_PART"
                item_tokens = item_col_text.replace('\n', ' ').split()
                item_tokens = [t.strip() for t in item_tokens if t.strip()]

                if len(item_tokens) >= 2:
                    # Último token = superseded_from (parte original que ordenamos)
                    requested_part = item_tokens[-1]
                    replacement_part = item_tokens[0]
                    error_parts.append({
                        'part_number': replacement_part,   # nuevo part (reemplazo)
                        'requested_part': requested_part,  # lo que pedimos originalmente
                        'status': 'SUPERSEDED',
                        'nla': None,
                        'superseded_from': requested_part,
                        'error_message': (
                            f"Superseded: {requested_part} → {replacement_part}"
                        ),
                    })
                    print(f"  🔄 SUPERSEDED: {requested_part} → {replacement_part}")

                # ── Detectar NLA: "no longer available" en el texto de error ─
                elif 'no longer available' in error_msg_text:
                    error_parts.append({
                        'part_number': item_col_text,
                        'requested_part': item_col_text,
                        'status': 'PART_ERROR',
                        'nla': 'Y',
                        'superseded_from': None,
                        'error_message': f"No Longer Available: {item_col_text}",
                    })
                    print(f"  \U0001f6ab NLA: {item_col_text}")

                # ── PART_ERROR genérico ─────────────────────────────────────
                else:
                    error_parts.append({
                        'part_number': item_col_text,
                        'requested_part': item_col_text,
                        'status': 'PART_ERROR',
                        'nla': None,
                        'superseded_from': None,
                        'error_message': 'The item is not found by cross-referencing',
                    })
                    print(f"  ❌ PART_ERROR: {item_col_text}")

            except Exception:
                continue
    except Exception as e:
        print(f"⚠️ No se pudo extraer partes con error: {e}")

    print(f"📋 Total partes con error: {len(error_parts)}")
    return error_parts, True


# ─────────────────────────────────────────────────────────────────────────────
#  PASO 2: Scraping de la tabla del carrito (#cart-delivery-cart-table)
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_cart_page(page: Page, requested_qtys: Dict[str, int], results: List[Dict]) -> None:
    """
    Scraping de una sola página de la tabla #cart-delivery-cart-table.
    Agrega los items encontrados a `results` (lista mutable compartida).
    """
    rows = page.locator(
        '#cart-delivery-cart-table tbody tr[data-testid^="undefined-row-"]'
    ).all()
    print(f"  📋 Filas en esta página: {len(rows)}")
    base_idx = len(results)

    for idx, row in enumerate(rows):
        try:
            row_id = row.get_attribute('id') or ''
            row_key = row_id.replace('undefined-row-', '')

            # ── Item # (part_number) + detección SUPERSEDED ────────────────
            part_number = ''
            superseded_from = None
            item_status = 'CORRECT'
            item_error_message = None
            nla = None
            try:
                pn_cell = row.locator(f'[data-testid="undefined-cell-{row_key}_itemNumber"]')
                if pn_cell.count() > 0:
                    # Todos los p.MuiTypography-body3 dentro de la celda
                    all_pn = pn_cell.locator('p.MuiTypography-body3').all()
                    if len(all_pn) >= 2:
                        # SUPERSEDED: primer p = reemplazo (nuevo), segundo p = original (lo que pedimos)
                        part_number = all_pn[0].inner_text().strip()
                        superseded_from = all_pn[1].inner_text().strip()
                        item_status = 'SUPERSEDED'
                        item_error_message = (
                            f"Superseded: {superseded_from} \u2192 {part_number}"
                        )
                        print(
                            f"    \U0001f504 SUPERSEDED: {superseded_from} \u2192 {part_number}"
                        )
                    elif len(all_pn) == 1:
                        part_number = all_pn[0].inner_text().strip()
            except Exception:
                pass

            if not part_number:
                continue

            # ── Description + detección NLA ─────────────────────────────────
            description = ''
            try:
                desc_cell = row.locator(
                    f'[data-testid="undefined-cell-{row_key}_Description"]'
                )
                if desc_cell.count() > 0:
                    desc_p = desc_cell.locator('p.MuiTypography-body3').first
                    if desc_p.count() > 0:
                        description = desc_p.inner_text().strip()
                    # Detectar NLA: MuiAlert-colorError en la celda Description
                    nla_alert = desc_cell.locator('.MuiAlert-colorError')
                    if nla_alert.count() > 0:
                        alert_text = nla_alert.first.inner_text().strip().lower()
                        if 'no longer available' in alert_text:
                            nla = 'Y'
                            item_status = 'PART_ERROR'
                            item_error_message = f"No Longer Available: {part_number}"
                            print(f"    \U0001f6ab NLA: {part_number}")
            except Exception:
                pass

            # ── Warehouse (Qty) ─────────────────────────────────────────
            warehouse_qty = ''
            try:
                wh_cell = row.locator('[data-testid*="_Warehouse"]')
                if wh_cell.count() > 0:
                    wh_elem = wh_cell.locator('p').first
                    if wh_elem.count() > 0:
                        warehouse_qty = wh_elem.inner_text().strip()
            except Exception:
                pass

            # ── Est. Ship Date ──────────────────────────────────────────
            est_ship_date = ''
            try:
                date_cell = row.locator('[data-testid*="_Est. Ship Date"]')
                if date_cell.count() > 0:
                    date_elem = date_cell.locator('p').first
                    if date_elem.count() > 0:
                        est_ship_date = date_elem.inner_text().strip()
            except Exception:
                pass

            # ── Quantity ────────────────────────────────────────────────
            cart_qty = 0
            try:
                qty_input = row.locator(f'[data-testid="{row_key}-quantity"] input')
                if qty_input.count() > 0:
                    qty_val = qty_input.get_attribute('value') or '0'
                    cart_qty = int(qty_val) if qty_val.isdigit() else 0
                else:
                    qty_input_fb = row.locator('input[aria-label^="Quantity:"]')
                    if qty_input_fb.count() > 0:
                        aria = qty_input_fb.first.get_attribute('aria-label') or ''
                        parts = aria.split(':')
                        if len(parts) == 2:
                            cart_qty = int(parts[1].strip())
            except Exception:
                pass

            # ── Tiered Price ────────────────────────────────────────────
            tiered_price: Optional[Decimal] = None
            try:
                tp_cell = row.locator('[data-testid*="_Tiered Price"]')
                if tp_cell.count() > 0:
                    tp_elem = tp_cell.locator('[data-testid="offer-price"]').first
                    if tp_elem.count() > 0:
                        tiered_price = parse_price(tp_elem.inner_text())
            except Exception:
                pass

            # ── Your Price ──────────────────────────────────────────────
            your_price: Optional[Decimal] = None
            try:
                price_cell = row.locator(f'[data-testid="undefined-cell-{row_key}_price"]')
                if price_cell.count() > 0:
                    price_elem = price_cell.locator('[data-testid="offer-price"]').first
                    if price_elem.count() > 0:
                        your_price = parse_price(price_elem.inner_text())
            except Exception:
                pass

            # ── Kit / paquete ───────────────────────────────────────────
            requested_qty = requested_qtys.get(part_number, cart_qty)
            is_kit = cart_qty > requested_qty
            # pack_qty: cantidad mínima de paquete impuesta por el portal
            # cuando dice "quantity has been changed" al mínimo de pack
            pack_qty = cart_qty if is_kit else None

            if is_kit:
                print(
                    f"    📦 KIT/PACK: {part_number} | "
                    f"Solicitado: {requested_qty} | Mínimo pack: {cart_qty}"
                )

            # qty_available: si el ítem tiene error (NLA) → 0; si está en carrito → usar cart_qty
            # Husqvarna no expone cantidad exacta en stock, pero si llegó al carrito hay stock
            _has_stock = item_status not in ('PART_ERROR',)
            results.append({
                'mfrid': '',
                'part_number': part_number,
                'description': description,
                'warehouse_qty': warehouse_qty,
                'est_ship_date': est_ship_date,
                'qty': cart_qty,
                'requested_qty': requested_qty,
                'qty_available': cart_qty if _has_stock else 0,
                'in_stock': 'Y' if _has_stock else 'N',
                'is_kit': is_kit,
                'pack_qty': pack_qty,
                'tiered_price': tiered_price,
                'your_price': your_price,
                'status': item_status,
                'error_message': item_error_message,
                'nla': nla,
                'superseded_from': superseded_from,
                'ltl': None,
            })
            print(
                f"    ✓ [{base_idx + idx}] {part_number} | "
                f"Qty: {cart_qty} | Tiered Price: {tiered_price} | Kit: {is_kit}"
            )

        except Exception as e:
            print(f"    ⚠️ Error procesando fila {base_idx + idx}: {e}")
            continue


def _get_pagination_info(page: Page):
    """
    Devuelve (current_page, total_pages) leyendo el componente MuiPagination
    dentro del carrito. Si no hay paginación visible retorna (1, 1).
    """
    try:
        nav = page.locator('nav[aria-label="pagination navigation"]')
        if nav.count() == 0:
            return 1, 1

        # Botones de página numerados (aria-label="page N" o "Go to page N")
        page_btns = nav.locator('button[aria-label^="page"], button[aria-label^="Go to page"]').all()
        total_pages = len(page_btns) + 1  # +1 por la página activa que puede no tener "Go to"

        # Página actual: botón con aria-current="true"
        current_btn = nav.locator('button[aria-current="true"]')
        current_page = 1
        if current_btn.count() > 0:
            label = current_btn.first.get_attribute('aria-label') or 'page 1'
            # "page 1" → 1
            current_page = int(label.replace('page', '').strip())

        # Total real: último botón de página numerado
        all_numbered = nav.locator('button[aria-label^="page"], button[aria-label^="Go to page"]').all()
        max_page = current_page
        for btn in all_numbered:
            lbl = btn.get_attribute('aria-label') or ''
            num_str = lbl.replace('Go to page', '').replace('page', '').strip()
            try:
                max_page = max(max_page, int(num_str))
            except ValueError:
                pass

        return current_page, max_page

    except Exception as e:
        print(f"  ⚠️ No se pudo leer paginación: {e}")
        return 1, 1


def extract_cart_table_data(page: Page, requested_qtys: Dict[str, int]) -> List[Dict]:
    """
    Extrae los datos de la tabla Cart Details (#cart-delivery-cart-table)
    iterando todas las páginas disponibles (max 100 items/página).

    Lógica de kit/paquete:
      Si cart_qty > requested_qty → is_kit = True.
    """
    results = []

    try:
        print("⏳ Esperando tabla Cart Details...")
        page.wait_for_selector('#cart-delivery-cart-table', timeout=15000)
        print("📊 Tabla Cart Details encontrada — esperando carga completa...")
        time.sleep(15)
        print("▶️  Iniciando scraping...")

        page_num = 1
        while True:
            current, total = _get_pagination_info(page)
            print(f"📄 Scraping página {current} / {total}...")

            _scrape_cart_page(page, requested_qtys, results)

            if current >= total:
                print(f"✅ Última página ({current}/{total}) procesada.")
                break

            # Ir a la siguiente página
            next_btn = page.locator('button[aria-label="Go to next page"]:not([disabled])')
            if next_btn.count() == 0:
                print("ℹ️ Botón 'siguiente' no disponible, fin de paginación.")
                break

            print(f"➡️  Navegando a página {current + 1}...")
            next_btn.click()
            time.sleep(2)
            page.wait_for_selector('#cart-delivery-cart-table', timeout=10000)
            time.sleep(1)
            page_num += 1

        print(f"✅ Scraping completo: {len(results)} items en {page_num} página(s).")
        return results

    except Exception as e:
        print(f"❌ Error extrayendo datos del carrito: {e}")
        return results


# ─────────────────────────────────────────────────────────────────────────────
#  PASO 3: Limpiar el carrito (Select All → Delete Items)
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_cart(page: Page) -> None:
    """
    Elimina todos los items del carrito:
      1. Click en el checkbox 'Select all items' del header del carrito
      2. Click en el botón 'Delete Items'
    """
    print("🧹 Iniciando limpieza del carrito...")

    try:
        # Checkbox "Select all items" en el header de la tabla del carrito
        select_all = page.locator(
            'th[data-testid="cart-delivery-header-row-head-cell-checkbox"] '
            'input[aria-label="Select all items"]'
        )
        if select_all.count() == 0:
            print("  ⚠️ Checkbox 'Select all items' del carrito no encontrado.")
            return

        select_all.click()
        time.sleep(1)
        print("  ☑️ Todos los items del carrito seleccionados.")

        # Botón "Delete Items"
        delete_btn = page.locator('button:has-text("Delete Items")')
        if delete_btn.count() == 0:
            print("  ⚠️ Botón 'Delete Items' no encontrado.")
            return

        delete_btn.first.click()
        time.sleep(3)
        print("  🗑️ Carrito limpiado correctamente.")

    except Exception as e:
        print(f"  ❌ Error limpiando el carrito: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  FUNCIÓN PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def _clear_husqvarna_cart(page: Page) -> None:
    """
    Intenta navegar al carrito Husqvarna y eliminar todos los ítems previos.
    Se llama ANTES del import para evitar contaminación de sesiones anteriores.
    Usa el enlace de carrito del header; si no lo encuentra, lo intenta
    directamente por URL. Falla silenciosamente si el carrito no es accesible.
    """
    print("🔍 Verificando carrito Husqvarna antes del import...")
    try:
        # Intentar enlace de carrito en el header de navegación
        cart_link = page.locator(
            'a[href*="/cart"], a[href*="OrderCalculate"], '
            '[aria-label*="cart" i], [data-testid*="cart"]'
        )
        if cart_link.count() > 0 and cart_link.first.is_visible():
            cart_link.first.click()
        else:
            # Fallback: URL directa del carrito Husqvarna Pro
            page.goto(
                "https://power.husqvarnagroup.com/cart",
                wait_until="domcontentloaded",
                timeout=15000,
            )
        try:
            page.wait_for_selector('#cart-delivery-cart-table', timeout=8000)
            rows = page.locator(
                '#cart-delivery-cart-table tbody tr[data-testid^="undefined-row-"]'
            )
            if rows.count() > 0:
                print(f"  ⚠️ Carrito tiene {rows.count()} ítem(s). Limpiando...")
                cleanup_cart(page)
                print("  ✅ Carrito Husqvarna limpiado.")
            else:
                print("  ✅ Carrito Husqvarna vacío, procediendo.")
        except Exception:
            print("  ✅ No se encontró tabla de carrito (probablemente vacío).")
    except Exception as e:
        print(f"  ⚠️ Error verificando carrito Husqvarna: {e}")


def _husqvarna_checkout_ltl_scan(page: Page, po_number: str) -> set:
    """
    Desde la página del carrito:
      1. Selecciona todos los ítems
      2. Llena PO# y Order Reference en el formulario
      3. Click 'Checkout' → espera #order-review-cart-cart-table
      4. Escanea cada fila buscando 'Freight Category: X' (X no vacío) → ltl='Y'
      5. Click 'Back' para volver al carrito

    La columna Description en la tabla de revisión muestra:
      <p>Freight Category:  </p>   → vacío = NO LTL
      <p>Freight Category: P1</p>  → valor presente = LTL

    Retorna set de part_numbers (str) con LTL detectado.
    """
    ltl_pns: set = set()
    try:
        # 1. Seleccionar todos los ítems del carrito
        print("  ☑️ Seleccionando todos para checkout LTL scan...")
        select_all = page.locator(
            'th[data-testid="cart-delivery-header-row-head-cell-checkbox"] '
            'input[aria-label="Select all items"]'
        )
        if select_all.count() > 0:
            select_all.first.click()
            time.sleep(1)
        else:
            print("  ⚠️ Checkbox 'Select all' no encontrado — continuando")

        # 2. Llenar formulario Order Reference Details
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.5)
        po_input = page.locator('input[name="poNumber"]')
        if po_input.count() > 0:
            po_input.first.fill(str(po_number))
            time.sleep(0.3)
        ref_input = page.locator('input[name="orderReferenceNumber"]')
        if ref_input.count() > 0:
            ref_input.first.fill("test")
            time.sleep(0.3)

        # 3. Click Checkout (scroll al fondo para encontrar el botón)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
        checkout_btn = page.locator('button[data-testid="cart-checkout"]')
        if checkout_btn.count() == 0:
            print("  ⚠️ Botón Checkout no encontrado — saltando scan LTL")
            return ltl_pns
        checkout_btn.first.scroll_into_view_if_needed()
        checkout_btn.first.click()
        print("  ✅ Click Checkout — esperando página de revisión...")
        time.sleep(5)

        # 4. Esperar tabla de revisión
        try:
            page.wait_for_selector('#order-review-cart-cart-table', timeout=15000)
            time.sleep(2)
        except Exception:
            print("  ⚠️ Tabla de revisión no cargó — saltando scan LTL")
            return ltl_pns

        # Escanear todas las páginas de la tabla de revisión
        page_num = 1
        while True:
            current, total = _get_pagination_info(page)
            print(f"  📄 Scan LTL checkout — página {current}/{total}...")

            rows = page.locator(
                '#order-review-cart-cart-table tbody tr[data-testid^="undefined-row-"]'
            ).all()

            for row in rows:
                try:
                    row_key = (
                        row.get_attribute('data-testid') or ''
                    ).replace('undefined-row-', '')
                    if not row_key:
                        continue

                    # Part number
                    pn_cell = row.locator(
                        f'[data-testid="undefined-cell-{row_key}_itemNumber"]'
                    )
                    part_number = ''
                    if pn_cell.count() > 0:
                        pn_ps = pn_cell.locator('p.MuiTypography-body3').all()
                        if pn_ps:
                            part_number = pn_ps[0].inner_text().strip()

                    if not part_number:
                        continue

                    # Freight Category en columna Description
                    desc_cell = row.locator(
                        f'[data-testid="undefined-cell-{row_key}_Description"]'
                    )
                    if desc_cell.count() > 0:
                        for p_el in desc_cell.locator('p').all():
                            try:
                                p_text = p_el.inner_text().strip()
                                if 'freight category' in p_text.lower() and ':' in p_text:
                                    after = p_text.split(':', 1)[1].strip()
                                    if after:  # Valor no vacío → LTL
                                        ltl_pns.add(part_number)
                                        print(f"  🚛 LTL: {part_number} | {p_text}")
                                    break
                            except Exception:
                                continue

                except Exception:
                    continue

            if current >= total:
                break

            next_btn = page.locator(
                'button[aria-label="Go to next page"]:not([disabled])'
            )
            if next_btn.count() == 0:
                break
            next_btn.click()
            time.sleep(2)
            page.wait_for_selector('#order-review-cart-cart-table', timeout=10000)
            time.sleep(1)
            page_num += 1

        print(f"  ✅ Scan LTL checkout completo: {len(ltl_pns)} LTL detectado(s)")

    except Exception as e:
        print(f"  ⚠️ Error en _husqvarna_checkout_ltl_scan: {e}")

    finally:
        # 5. Volver al carrito con el botón Back
        print("  ⬅️ Volviendo al carrito (Back)...")
        try:
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.5)
            back_btn = page.locator('a[href="/en-us/cart"]')
            if back_btn.count() == 0:
                back_btn = page.locator('a:has-text("Back")')
            if back_btn.count() > 0:
                back_btn.first.scroll_into_view_if_needed()
                back_btn.first.click()
                time.sleep(3)
                try:
                    page.wait_for_selector(
                        '#cart-delivery-cart-table', timeout=10000
                    )
                    print("  ✅ De vuelta en el carrito.")
                except Exception:
                    print("  ⚠️ No se pudo confirmar regreso al carrito")
            else:
                print("  ⚠️ Botón Back no encontrado")
        except Exception as back_e:
            print(f"  ⚠️ Error volviendo al carrito: {back_e}")

    return ltl_pns


def husqvarna_login_automation_playwright(
    email: str,
    password: str,
    csv_filename: str,
    requested_qtys: Optional[Dict[str, int]] = None,
    po_number: str = "TEST001",
) -> Optional[List[Dict]]:
    """
    Automatización completa de Husqvarna. Flujo secuencial:

      1.  Login
      2.  Navegar a Import Order
      3.  Subir CSV
      4.  Click Upload Order
      5.  Detectar errores en la tabla de import
      6.  Si hay errores → capturar partes con error → click "Remove Items"
      7.  Click checkbox "Select All" (header)
      8.  Click "Add to cart"
      9.  Click "Go to cart"
      10. Scraping de #cart-delivery-cart-table
      11. Retornar datos del carrito + partes con error marcadas como PART_ERROR

    :param requested_qtys: dict {partNumber: qty_solicitada} para detectar kits.
                           Si no se pasa, no se detectan kits.
    """
    print("🚀 Iniciando automatización Husqvarna con Playwright...")

    if requested_qtys is None:
        requested_qtys = {}

    downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
    csv_path = os.path.join(downloads_path, csv_filename)

    if not os.path.exists(csv_path):
        print(f"❌ Archivo no encontrado: {csv_path}")
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
        # ── 1. Ir a la página principal ──────────────────────────────────────
        print("📍 Navegando a Husqvarna Group...")
        page.goto(
            "https://power.husqvarnagroup.com/webapp/wcs/stores/servlet/"
            "HomePageView?catalogId=10051&langId=-1&storeId=10201",
            wait_until="domcontentloaded",
        )
        time.sleep(3)

        # ── 2. Click en 'Go to Login' ────────────────────────────────────────
        print("🔐 Buscando enlace de login...")
        login_link = page.locator('.porlet_content a[href*="LogonForm"]')
        if login_link.count() == 0:
            print("❌ Enlace de login no encontrado")
            return None
        login_link.click()
        time.sleep(3)

        # ── 3. Ingresar credenciales ─────────────────────────────────────────
        print(f"📧 Ingresando email: {email}")
        page.locator('input[name="username"].form-control').fill(email)
        time.sleep(0.5)
        print("🔑 Ingresando contraseña...")
        page.locator('input[name="password"].form-control').fill(password)
        time.sleep(0.5)

        print("📤 Enviando formulario de login...")
        page.locator('button[type="submit"][name="operation"][value="verify"]').click()
        time.sleep(5)
        page.wait_for_load_state("networkidle")

        current_url = page.url
        if "logon" in current_url.lower():
            print("⚠️ Login puede no haber sido exitoso.")
        else:
            print(f"✅ Login exitoso. URL: {current_url}")

        # ── 3b. Limpiar carrito antes del import ──────────────────────────────
        _clear_husqvarna_cart(page)

        # ── 4. Navegar a Import Order ────────────────────────────────────────
        print("📥 Navegando a 'Import Order'...")
        import_link = page.locator('a.MuiTypography-h4[href*="import-order"]')
        if import_link.count() == 0:
            print("❌ Enlace 'Import Order' no encontrado")
            return None
        import_link.click()
        time.sleep(3)

        # ── 5. Subir CSV ─────────────────────────────────────────────────────
        print("📁 Buscando botón Browse...")
        time.sleep(5)
        browse_button = page.locator('label[data-testid="requisition-list-file-browsing"]')
        if browse_button.count() == 0:
            print("❌ Botón Browse no encontrado")
            return None

        browse_button.scroll_into_view_if_needed()
        time.sleep(0.5)
        browse_button.hover()
        time.sleep(0.3)
        browse_button.click(delay=100)
        print("✅ Click en Browse.")

        print("⏳ Esperando diálogo de Windows...")
        time.sleep(3)
        keyboard = Controller()
        print(f"⌨️ Escribiendo ruta: {csv_path}")
        keyboard.type(csv_path)
        time.sleep(1)
        keyboard.press(Key.enter)
        keyboard.release(Key.enter)
        time.sleep(2)
        print(f"✅ Archivo {csv_filename} cargado.")

        # ── 6. Click 'Upload Order' ──────────────────────────────────────────
        print("📤 Haciendo clic en 'Upload Order'...")
        upload_btn = page.locator('button[type="submit"][data-testid="requisition-list-upload"]')
        if upload_btn.count() == 0:
            print("❌ Botón 'Upload Order' no encontrado")
            return None
        upload_btn.click()
        time.sleep(7)
        print("✅ Upload Order ejecutado.")

        # ── 7. Detectar errores en la tabla de import ────────────────────────
        print("🔍 Detectando errores en resultados del import...")
        error_parts, has_errors = detect_import_errors(page)

        # ── 8. Si hay errores → click 'Remove Items' ─────────────────────────
        if has_errors:
            print(f"🗑️ Eliminando {len(error_parts)} item(s) con error...")
            remove_btn = page.locator('button:has-text("Remove Items")')
            if remove_btn.count() > 0:
                remove_btn.first.click()
                time.sleep(3)
                print("✅ Items con error eliminados.")
            else:
                print("⚠️ Botón 'Remove Items' no encontrado, continuando...")

        # ── 9. Seleccionar todos (checkbox header) ───────────────────────────
        print("☑️ Seleccionando todos los items...")
        # El checkbox de selección global está en el header de la tabla de import
        select_all = page.locator(
            'input.PrivateSwitchBase-input[type="checkbox"][data-indeterminate="false"]'
        ).first
        if select_all.count() > 0:
            select_all.click()
            time.sleep(1)
            print("✅ Todos los items seleccionados.")
        else:
            print("⚠️ Checkbox 'Select All' no encontrado, intentando continuar...")

        # ── 10. Click 'Add to cart' ───────────────────────────────────────────
        print("🛒 Haciendo clic en 'Add to cart'...")
        print("⏳ Esperando 10s antes de buscar 'Add to cart'...")
        time.sleep(10)
        add_to_cart_btn = page.locator('button:has-text("Add to cart")')
        # Scroll hasta encontrar el botón
        if add_to_cart_btn.count() == 0 or not add_to_cart_btn.first.is_visible():
            print("⬇️ Scroll hasta el final para encontrar 'Add to cart'...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            for _ in range(5):
                if add_to_cart_btn.count() > 0 and add_to_cart_btn.first.is_visible():
                    break
                page.evaluate("window.scrollBy(0, 600)")
                time.sleep(0.5)
        if add_to_cart_btn.count() == 0:
            print("❌ Botón 'Add to cart' no encontrado")
            return None
        add_to_cart_btn.first.scroll_into_view_if_needed()
        add_to_cart_btn.first.click()
        time.sleep(5)
        print("✅ Items agregados al carrito.")

        # ── 11. Click 'Go to cart' ────────────────────────────────────────────
        print("🛒 Haciendo clic en 'Go to cart'...")
        go_to_cart_btn = page.locator('button:has-text("Go to cart")')
        # Si no está visible, hacer scroll hasta el fondo para que aparezca
        if go_to_cart_btn.count() == 0 or not go_to_cart_btn.first.is_visible():
            print("⬇️ Scroll hasta el final para encontrar 'Go to cart'...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)
            # Reintentar hasta 5 veces scrolleando de a tramos
            for _ in range(5):
                if go_to_cart_btn.count() > 0 and go_to_cart_btn.first.is_visible():
                    break
                page.evaluate("window.scrollBy(0, 600)")
                time.sleep(0.5)
        if go_to_cart_btn.count() == 0:
            print("❌ Botón 'Go to cart' no encontrado")
            return None
        go_to_cart_btn.first.scroll_into_view_if_needed()
        go_to_cart_btn.first.click()
        time.sleep(7)
        print("✅ Navegando al carrito.")

        # ── 12. Scraping del carrito ──────────────────────────────────────────
        print("🔍 Iniciando scraping del carrito...")
        cart_data = extract_cart_table_data(page, requested_qtys)
        print(f"✅ Scraping completado. {len(cart_data)} items en carrito.")

        # ── 12b. Checkout LTL scan ────────────────────────────────────────────
        # Navega al checkout para leer 'Freight Category' en la tabla de revisión.
        # Un valor no vacío después de ':' (ej: 'Freight Category: P1') indica LTL.
        print("\n🔍 Scan LTL via checkout (Freight Category)...")
        ltl_pns = _husqvarna_checkout_ltl_scan(page, po_number)
        if ltl_pns:
            for item in cart_data:
                if item.get('part_number') in ltl_pns:
                    item['ltl'] = 'Y'
            print(f"  🚛 {len(ltl_pns)} ítem(s) marcados con ltl='Y'.")
        else:
            print("  ℹ️ Sin LTL detectados en checkout.")

        # ── 13. Limpiar el carrito ────────────────────────────────────────────
        cleanup_cart(page)

        # ── 14. Agregar partes con error al resultado ─────────────────────────
        for err in error_parts:
            err_part = err['part_number']
            cart_data.append({
                'mfrid': '',
                'part_number': err_part,
                'description': '',
                'warehouse_qty': '',
                'est_ship_date': '',
                'qty': 0,
                'requested_qty': requested_qtys.get(
                    err.get('requested_part', err_part), 0
                ),
                'qty_available': 0,
                'in_stock': 'N',
                'pack_qty': None,
                'nla': err.get('nla'),
                'superseded_from': err.get('superseded_from'),
                'tiered_price': None,
                'your_price': None,
                'status': err['status'],
                'error_message': err.get('error_message'),
            })
            status_icon = {
                'SUPERSEDED': '🔄',
                'NLA': '🚫',
                'PART_ERROR': '❌',
            }.get(err['status'], '⚠️')
            print(f"  {status_icon} {err['status']} agregado: {err_part}")

        print(f"\n📊 Resumen: {len(cart_data)} total | "
              f"{len(cart_data) - len(error_parts)} en carrito | "
              f"{len(error_parts)} con error")

        browser.close()
        print("🚪 Navegador cerrado.")
        return cart_data

    except Exception as e:
        print(f"❌ Error durante la automatización: {e}")
        import traceback
        traceback.print_exc()
        browser.close()
        return None


if __name__ == "__main__":
    EMAIL = "danielam.prontomowers@gmail.com"
    PASSWORD = "Chainsaw01"
    CSV_FILENAME = "husq-test.csv"

    result = husqvarna_login_automation_playwright(EMAIL, PASSWORD, CSV_FILENAME)

    if result:
        print("\n📊 Datos extraídos:")
        for idx, row in enumerate(result, 1):
            print(f"\nFila {idx}:")
            for key, value in row.items():
                print(f"  {key}: {value}")
