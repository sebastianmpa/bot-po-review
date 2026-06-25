"""
cooks_power_playwright.py
--------------------------
Automatización Playwright para Cook's Power (supplerID = "CP").

Flujo:
  1. Login via modal (header link → email/password form)
  2. Navegar al Quick Order panel
  3. Por cada ítem de la PO:
     a. Escribir partNumber en #customtypeahead
     b. Leer opciones del dropdown → detectar NLA / SUPERSEDED / PACK
     c. Seleccionar la opción correcta
     d. Establecer cantidad
     e. Clic en "Add Item"
     f. Esperar a que el carrito se actualice
  4. Scrape del carrito: precio, qty, out-of-stock
  5. Devolver lista de dicts con resultados
"""

import re
import time
import os
from typing import List, Dict, Optional
from decimal import Decimal

import pyautogui

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError


# ─────────────────────────────────────────────────────────────────────────────
#  Constantes
# ─────────────────────────────────────────────────────────────────────────────

COOKS_POWER_URL = "https://www.cookspower.com/"

# Ruta de la imagen del botón ADD ITEM para PyAutoGUI
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ADD_ITEM_IMG = os.path.join(_SCRIPT_DIR, "additem.jpeg")

# Timeouts (ms)
TIMEOUT_LOGIN = 15_000
TIMEOUT_TYPEAHEAD = 10_000
TIMEOUT_CART_UPDATE = 20_000
TIMEOUT_NAVIGATION = 30_000


def parse_price(text: str) -> Optional[Decimal]:
    """
    Extrae un Decimal de un string de precio.
    Ejemplo: "$23.07" → Decimal("23.07")
    """
    try:
        clean = re.sub(r"[^\d.]", "", text.strip())
        return Decimal(clean) if clean else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  PASO 1: Login
# ─────────────────────────────────────────────────────────────────────────────

def login(page: Page, email: str, password: str) -> None:
    """Navega directo a la página de login y se autentica."""
    print("🔐 Navegando directo a la página de login...")

    # Ir directo a la URL de login en lugar de hacer clic en el header
    page.goto(
        "https://www.cookspower.com/scs/checkout.ssp?is=login&login=T&fragment=login-register#login-register",
        wait_until="domcontentloaded",
        timeout=TIMEOUT_NAVIGATION,
    )
    print(f"  ↪️  URL de login: {page.url}")

    # Esperar formulario
    page.wait_for_selector("#login-email", timeout=TIMEOUT_LOGIN)
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("button.login-register-login-submit")

    # Esperar redirección a la home tras el login
    try:
        page.wait_for_url("**/cookspower.com/", timeout=TIMEOUT_LOGIN)
        print("✅ Login exitoso — redirigido a home.")
    except PlaywrightTimeoutError:
        print(f"⚠️  Login enviado; URL actual: {page.url}")

    # Asegurar que estamos en la home antes de continuar
    if "checkout" in page.url or "login" in page.url:
        page.goto(COOKS_POWER_URL, wait_until="domcontentloaded", timeout=TIMEOUT_NAVIGATION)
        print(f"  ↪️  Redirigido a home manualmente: {page.url}")

    # Verificar que la sesión esté activa: el link de login NO debe estar visible
    try:
        page.wait_for_selector(
            "a.header-profile-login-link[data-hashtag='login-register']",
            state="hidden",
            timeout=8_000,
        )
        print("✅ Sesión activa confirmada.")
    except PlaywrightTimeoutError:
        print("⚠️  No se pudo confirmar sesión activa; continuando de todas formas.")


# ─────────────────────────────────────────────────────────────────────────────
#  PASO 2: Navegar al Quick Order
# ─────────────────────────────────────────────────────────────────────────────

def navigate_to_quick_order(page: Page) -> None:
    """Navega directo a la URL del carrito con Quick Order abierto."""
    print("🛒 Navegando directo al Quick Order...")

    page.goto(
        "https://www.cookspower.com/cart?openQuickOrder=true",
        wait_until="domcontentloaded",
        timeout=TIMEOUT_NAVIGATION,
    )

    # Esperar que el input del typeahead esté disponible
    page.wait_for_selector("#customtypeahead", timeout=TIMEOUT_NAVIGATION)
    print("✅ Panel Quick Order listo.")


# ─────────────────────────────────────────────────────────────────────────────
#  RECUPERACIÓN: CMS login redirect
# ─────────────────────────────────────────────────────────────────────────────

def recover_from_cms_redirect(page: Page) -> bool:
    """
    Detecta si la página navegó al CMS login de Cook's Power.
    Si es así:
      1. Hace clic en 'Back' (a.backlink dentro de div.backcontainer)
      2. Espera a volver a la home de cookspower.com
      3. Hace clic en 'Quick Parts Order' (a.quickorder-accesspoints-headerlink-link)
      4. Espera a que el panel Quick Order esté disponible (#customtypeahead)

    :return: True si se detectó y recuperó el redirect, False si no fue necesario.
    """
    if "cms-login" not in page.url and "cms.jsp" not in page.url:
        return False

    print("  ⚠️  Redirigido al CMS login — iniciando recuperación...")
    try:
        # Paso 1: clic en el link 'Back'
        back_link = page.locator("div.backcontainer a.backlink, a.backlink")
        if back_link.count() > 0:
            back_link.first.click()
            print("  ↩️  Clic en '⬅ Back' del CMS login.")
        else:
            page.go_back()
            print("  ↩️  go_back() ejecutado (backlink no encontrado).")

        # Paso 2: esperar a que la home / carrito de Cook's Power cargue
        try:
            page.wait_for_url("**/cookspower.com/**", timeout=TIMEOUT_NAVIGATION)
        except Exception:
            pass  # Si ya estamos en cookspower.com, continuar
        time.sleep(1)

        # Paso 3: hacer clic en 'Quick Parts Order' para reabrir el panel
        qo_link = page.locator("a.quickorder-accesspoints-headerlink-link")
        if qo_link.count() > 0:
            qo_link.first.click()
            print("  🛒  Clic en 'Quick Parts Order' para reabrir el panel.")
        else:
            # Fallback: navegar directo a la URL con el panel abierto
            print("  ⚠️  Link 'Quick Parts Order' no encontrado — navegando directo al Quick Order...")
            page.goto(
                "https://www.cookspower.com/cart?openQuickOrder=true",
                wait_until="domcontentloaded",
                timeout=TIMEOUT_NAVIGATION,
            )

        # Paso 4: esperar a que el input del typeahead esté disponible
        page.wait_for_selector("#customtypeahead", timeout=TIMEOUT_NAVIGATION)
        time.sleep(1)
        print("  ✅ Recuperado — Quick Order disponible.")
        return True

    except Exception as e:
        print(f"  ❌ No se pudo recuperar del CMS redirect: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  PASO 3: Procesar cada ítem
# ─────────────────────────────────────────────────────────────────────────────

def process_item(
    page: Page,
    part_number: str,
    requested_qty: int,
    mfrid: str = "",
    item_index: int = 0,
) -> Dict:
    """
    Escribe el part_number en #customtypeahead, detecta estado (NLA/SUPERSEDED/PACK/normal),
    selecciona la opción correcta, pone la cantidad y hace clic en Add Item.

    HTML de cada opción:
      <div class="typeahead-option" data-action="select-item" data-id="...">
        <div><img ...></div>
        <div class="details-custom">
          <div id="itemnamecustom">MOTOR TG0310</div>
          <div>SKU:603715P</div>     ← SKU sin espacio tras los dos puntos
          <div>$815.24</div>
        </div>
      </div>
    """
    result: Dict = {
        "part_number": part_number,
        "requested_sku": part_number,
        "mfrid": mfrid,
        "status": "CORRECT",
        "nla": None,
        "superseded_from": None,
        "pack_qty": None,
        "error_message": None,
        "typeahead_label": None,
    }

    print(f"\n  🔍 Procesando: {part_number} (qty={requested_qty})")

    try:
        # ── Verificar que no estamos en el CMS login ─────────────────────────
        recover_from_cms_redirect(page)

        # ── Limpiar y escribir en el typeahead ──────────────────────────────
        typeahead_input = page.locator("#customtypeahead")
        typeahead_input.click(click_count=3)   # selecciona todo el texto previo
        typeahead_input.fill("")
        time.sleep(0.3)
        typeahead_input.type(part_number, delay=80)

        # ── Esperar que aparezca al menos una opción ────────────────────────
        try:
            page.wait_for_selector(
                "div.typeahead-option[data-action='select-item']",
                timeout=TIMEOUT_TYPEAHEAD,
            )
        except PlaywrightTimeoutError:
            print(f"  ⚠️  Sin resultados en typeahead para '{part_number}'")
            result["status"] = "PART_ERROR"
            result["error_message"] = "No typeahead results found"
            return result

        # ── Leer todas las opciones ─────────────────────────────────────────
        options = page.locator("div.typeahead-option[data-action='select-item']").all()
        print(f"  📋 Opciones encontradas: {len(options)}")

        def _get_option_info(opt) -> tuple:
            """Devuelve (item_name, sku) de una opción del dropdown."""
            name_el = opt.locator("div#itemnamecustom")
            item_name = name_el.inner_text().strip() if name_el.count() > 0 else ""

            sku = ""
            for div in opt.locator("div.details-custom div").all():
                txt = div.inner_text().strip()
                if txt.upper().startswith("SKU:"):
                    sku = txt[4:].strip()   # quitar el prefijo "SKU:"
                    break
            return item_name, sku

        selected_option = None
        selected_sku = None
        exact_match = None
        pack_match = None
        pack_match_sku = None
        nla_option = None  # opción con NLA (fallback si no hay exacta)

        for opt in options:
            item_name, sku = _get_option_info(opt)
            print(f"     → name='{item_name}'  sku='{sku}'")
            result["typeahead_label"] = item_name  # guarda la primera / última vista

            # ── PRIMERO: verificar coincidencia exacta de SKU (sin NLA) ───────
            # Si escribiste "606967", seleccionar la opción con sku='606967'
            # aunque haya otra opción SUPERSEDED que apunte a 606967.
            # NO aplicar SUPERSEDED si la coincidencia exacta existe.
            if sku.upper() == part_number.upper():
                # Chequear si esta opción (exacta) tiene NLA
                has_nla = re.search(r"\bNLA\b|NO LONGER AVAIL", item_name, re.IGNORECASE)
                if not has_nla:
                    # Coincidencia exacta SIN NLA — usar directamente
                    exact_match = opt
                    print(f"  ✅ Coincidencia exacta (sin NLA): {sku}")
                    # Salir del loop inmediatamente — encontramos lo que buscábamos
                    break
                else:
                    # Coincidencia exacta pero CON NLA — guardar para luego
                    nla_option = opt
                    print(f"  ⚠️  Coincidencia exacta con NLA: {sku}")
                    continue

            # ── NLA (solo si NO hay coincidencia exacta) ─────────────────────
            # El label puede contener "NLA", "*** NLA ***", "NO LONGER AVAIL", etc.
            # Pero NO aplicar NLA si hay una coincidencia exacta del SKU escrito.
            if re.search(r"\bNLA\b|NO LONGER AVAIL", item_name, re.IGNORECASE):
                if not nla_option:
                    nla_option = opt
                print(f"  ℹ️  NLA encontrado (fallback si no hay exacta): {sku}")
                continue

            # ── SUPERSEDED (USE / SP TO / S/X TO) ───────────────────────────
            # Cuando el typeahead muestra "USE XXXXX" o "SP TO XXXXX":
            #
            # CASO A — el SKU de reemplazo ya está en la opción actual (sku != ""):
            #   · Clic directo en opt — sin Escape, sin re-búsqueda.
            #   · El Escape dispara redirección al CMS login, por eso se evita.
            #
            # CASO B — el SKU está vacío (raro):
            #   · Se intenta primero encontrar la parte en las otras opciones
            #     del dropdown ya visible (sin Escape).
            #   · Si no se encuentra, se escapa y re-busca (con recovery CMS).
            sup_match = re.match(
                r"^(?:USE|SP\s+TO|S/[A-Z]\s+TO)\s+(\S+)",
                item_name,
                re.IGNORECASE,
            )
            if sup_match:
                replacement_candidate = sup_match.group(1).strip()
                # Si el dropdown tiene un SKU propio, usarlo como reemplazo
                replacement_sku_to_find = sku if sku else replacement_candidate
                print(
                    f"  🔄 SUPERSEDED detectado: {part_number} → {replacement_sku_to_find}"
                )

                if sku:
                    # CASO A: la opción actual ya ES el reemplazo — clic directo
                    selected_option = opt
                    selected_sku = sku
                    result["status"] = "SUPERSEDED"
                    result["superseded_from"] = part_number  # parte original PO
                    result["part_number"] = sku              # nueva parte
                    print(f"  ✅ Reemplazo seleccionado directamente: {sku}")
                    break

                # CASO B: sku vacío — buscar en las demás opciones del dropdown actual
                replacement_found = False
                for r_opt in options:
                    r_name, r_sku = _get_option_info(r_opt)
                    if r_sku.upper() == replacement_sku_to_find.upper():
                        selected_option = r_opt
                        selected_sku = r_sku
                        result["status"] = "SUPERSEDED"
                        result["superseded_from"] = part_number
                        result["part_number"] = r_sku
                        replacement_found = True
                        print(f"  ✅ Reemplazo encontrado en dropdown: {r_sku}")
                        break

                if replacement_found:
                    break

                # Último recurso: Escape + nueva búsqueda (puede disparar CMS redirect)
                print(
                    f"  ⚠️  '{replacement_sku_to_find}' no en dropdown actual "
                    f"→ re-buscando..."
                )
                try:
                    typeahead_input.press("Escape")
                    time.sleep(0.5)
                    recover_from_cms_redirect(page)
                except Exception:
                    pass

                typeahead_input.click(click_count=3)
                typeahead_input.fill("")
                time.sleep(0.3)
                typeahead_input.type(replacement_sku_to_find, delay=80)

                try:
                    page.wait_for_selector(
                        "div.typeahead-option[data-action='select-item']",
                        timeout=TIMEOUT_TYPEAHEAD,
                    )
                    r_opts = page.locator(
                        "div.typeahead-option[data-action='select-item']"
                    ).all()
                    for r_opt in r_opts:
                        r_name, r_sku = _get_option_info(r_opt)
                        if r_sku.upper() == replacement_sku_to_find.upper():
                            selected_option = r_opt
                            selected_sku = r_sku
                            result["status"] = "SUPERSEDED"
                            result["superseded_from"] = part_number
                            result["part_number"] = r_sku
                            replacement_found = True
                            print(f"  ✅ Reemplazo encontrado (re-búsqueda): {r_sku}")
                            break
                except PlaywrightTimeoutError:
                    print(
                        f"  ⚠️  Timeout buscando reemplazo '{replacement_sku_to_find}'"
                    )

                if not replacement_found:
                    print(
                        f"  ⚠️  Reemplazo '{replacement_sku_to_find}' no encontrado "
                        f"→ SUPERSEDED sin reemplazar"
                    )
                    result["status"] = "SUPERSEDED"
                    try:
                        typeahead_input.press("Escape")
                        time.sleep(0.3)
                        recover_from_cms_redirect(page)
                        typeahead_input.fill("")
                    except Exception:
                        pass
                    return result

                break  # Salir del loop; selected_option apunta al reemplazo

            # ── Versión PACK (sufijo "X") ────────────────────────────────────
            if (sku.upper() == part_number.upper() + "X" or
                  (sku.upper().startswith(part_number.upper()) and sku.upper().endswith("X"))):
                pack_match = opt
                pack_match_sku = sku
                # Intentar extraer cantidad del pack del nombre
                pm = re.search(r"(?:pack\s+of|pack)\s*(\d+)", item_name, re.IGNORECASE)
                result["pack_qty"] = int(pm.group(1)) if pm else 1

        # ── Decidir qué opción usar si no fue SUPERSEDED ────────────────────
        if selected_option is None:
            if exact_match:
                selected_option = exact_match
                _, selected_sku = _get_option_info(exact_match)
                if pack_match:
                    print(f"  📦 Versión pack disponible como alternativa (pack_qty={result['pack_qty']})")
            elif nla_option:
                # Coincidencia exacta pero tiene NLA
                print(f"  ❌ NLA detectado: {part_number} — limpiando input y continuando.")
                result["status"] = "PART_ERROR"
                result["nla"] = "Y"
                result["error_message"] = f"Part {part_number} is NLA (No Longer Available)"
                try:
                    typeahead_input.press("Escape")
                    time.sleep(0.5)
                except Exception:
                    pass
                recover_from_cms_redirect(page)
                return result
            elif pack_match:
                # Solo existe versión pack
                selected_option = pack_match
                selected_sku = pack_match_sku
                print(f"  📦 PACK (única versión disponible): '{selected_sku}' pack_qty={result['pack_qty']}")
            elif options:
                # Fallback: primera opción
                selected_option = options[0]
                _, selected_sku = _get_option_info(options[0])
                print(f"  ⚠️  Sin coincidencia exacta; primera opción: '{selected_sku}'")
            else:
                result["status"] = "PART_ERROR"
                result["error_message"] = "No valid option in typeahead"
                return result

        # ── Clic en la opción ───────────────────────────────────────────────
        selected_option.click()
        print(f"  ✅ Opción seleccionada: '{selected_sku}'")
        time.sleep(0.5)  # dejar que el input se actualice

        # ── Cantidad ────────────────────────────────────────────────────────
        qty_input = page.locator("#quantity-custom")
        qty_input.wait_for(state="visible", timeout=5_000)
        qty_input.click(click_count=3)
        qty_input.fill(str(requested_qty))
        print(f"  📝 Cantidad establecida: {requested_qty}")

        # ── Contar filas del carrito antes de agregar ───────────────────────
        current_cart_rows = page.locator("div[data-type='order-item'].cart-lines-row").count()
        print(f"  🛒 Filas en carrito antes: {current_cart_rows}")

        # ── Clic en Add Item (PyAutoGUI coordenadas fijas) ─────────────────
        # Siempre las mismas coordenadas — el carrito se vacía tras cada ítem
        ADD_ITEM_X, ADD_ITEM_Y = 727, 570
        pyautogui.moveTo(ADD_ITEM_X, ADD_ITEM_Y, duration=0.3)
        time.sleep(0.2)
        pyautogui.click(ADD_ITEM_X, ADD_ITEM_Y)
        print(f"  🖱️  Clic en 'ADD ITEM' en coordenadas ({ADD_ITEM_X}, {ADD_ITEM_Y}) [item #{item_index + 1}]")

        # ── Esperar actualización del carrito ───────────────────────────────
        try:
            page.wait_for_function(
                "document.querySelectorAll(\"div[data-type='order-item'].cart-lines-row\").length"
                f" > {current_cart_rows}",
                timeout=TIMEOUT_CART_UPDATE,
            )
            print(f"  ✅ Ítem agregado correctamente al carrito.")
        except PlaywrightTimeoutError:
            print(f"  ⚠️  El carrito no aumentó de filas tras agregar '{part_number}'. "
                  "Puede que ya estuviera en el carrito o hubo un error.")

    except PlaywrightTimeoutError as e:
        print(f"  ❌ Timeout procesando {part_number}: {e}")
        result["status"] = "PART_ERROR"
        result["error_message"] = f"Timeout: {str(e)[:120]}"

    except Exception as e:
        print(f"  ❌ Error procesando {part_number} ({type(e).__name__}): {e}")
        result["status"] = "PART_ERROR"
        result["error_message"] = f"{type(e).__name__}: {str(e)[:200]}"

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  PASO 4: Scrape del carrito
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_from_detail(page: Page, product_url: str, return_url: Optional[str] = None) -> Optional[int]:
    """
    Navega al detalle del producto, extrae 'Current Stock: N' y vuelve a return_url.
    Devuelve el stock disponible como int, o None si no se puede obtener.
    """
    effective_return = return_url or page.url
    try:
        page.goto(product_url, wait_until="domcontentloaded", timeout=TIMEOUT_NAVIGATION)

        # Esperar bloque de inventario
        page.wait_for_selector("div.inventory-display", timeout=8_000)

        stock: Optional[int] = None

        # Buscar "Current Stock: N"
        qty_el = page.locator("p.inventory-display-quantity-available span")
        if qty_el.count() > 0:
            text = qty_el.first.inner_text().strip()  # "Current Stock: 38"
            match = re.search(r"(\d+)", text)
            if match:
                stock = int(match.group(1))
                print(f"    📦 Stock en detalle: {stock}")

        return stock

    except Exception as e:
        print(f"    ⚠️  Error obteniendo stock de detalle ({product_url}): {e}")
        return None

    finally:
        # Siempre volver a la URL indicada (goto es más fiable que go_back)
        try:
            page.goto(effective_return, wait_until="domcontentloaded", timeout=TIMEOUT_NAVIGATION)
            if "openQuickOrder" in effective_return:
                page.wait_for_selector("#customtypeahead", timeout=TIMEOUT_NAVIGATION)
            else:
                page.wait_for_selector("div[data-type='order-item'].cart-lines-row", timeout=10_000)
        except Exception:
            pass


def check_item_stock_from_cart(page: Page, effective_pn: str, cart_qo_url: str) -> tuple:
    """
    Busca la fila del SKU recién agregado al carrito, verifica OOS y obtiene
    la cantidad disponible navegando al detalle del producto.
    Siempre garantiza volver a cart_qo_url (Quick Order abierto) al terminar.
    Retorna (qty_available: int, in_stock: 'Y'|'N')
    """
    try:
        rows = page.locator("div[data-type='order-item'].cart-lines-row")
        target_row = None
        for i in range(rows.count()):
            row = rows.nth(i)
            sku_el = row.locator("span.product-line-sku-value")
            if sku_el.count() > 0 and sku_el.first.inner_text().strip().upper() == effective_pn.upper():
                target_row = row
                break

        if target_row is None:
            print(f"    ⚠️  Fila no encontrada en carrito: {effective_pn}")
            return 0, 'N'

        # OOS → sin stock
        oos_el = target_row.locator("p.product-line-stock-msg-out")
        if oos_el.count() > 0:
            print(f"    ❌ OOS detectado en carrito: {effective_pn}")
            return 0, 'N'

        # Qty del carrito como fallback si detail falla
        cart_qty = 0
        qty_input = target_row.locator("input[data-type='cart-item-quantity-input']")
        if qty_input.count() > 0:
            try:
                cart_qty = int(qty_input.first.input_value().strip())
            except Exception:
                pass

        # URL del detalle del producto
        link_el = target_row.locator("a.cart-lines-name-link")
        if link_el.count() == 0:
            print(f"    ✅ {effective_pn}: en stock, qty_available={cart_qty} (sin link detalle)")
            return cart_qty, 'Y'

        href = link_el.first.get_attribute("href") or ""
        detail_url = href if href.startswith("http") else f"https://www.cookspower.com{href}"

        # Ir a detalle, obtener stock y volver a Quick Order
        stock = get_stock_from_detail(page, detail_url, return_url=cart_qo_url)

        # Si detail no devuelve qty, usar cart_qty como fallback (está en carrito = hay stock)
        qty_available = stock if (stock is not None and stock > 0) else cart_qty
        print(f"    ✅ {effective_pn}: qty_available={qty_available}, in_stock=Y")
        return qty_available, 'Y'

    except Exception as e:
        print(f"    ⚠️  Error check_item_stock_from_cart({effective_pn}): {e}")
        return 0, 'N'


def remove_from_cart(page: Page, effective_pn: str) -> None:
    """
    Elimina del carrito el ítem con el SKU indicado.
    Asume que la página actual ya tiene el carrito visible.
    """
    remove_selector = (
        "a.cart-item-actions-item-list-actionable-edit-content-remove"
        "[data-action='remove-item']"
    )
    try:
        # Contar ítems antes
        before = page.locator(remove_selector).count()
        if before == 0:
            return

        btn = page.locator(remove_selector).first
        btn.click()

        # Esperar que el contador baje
        for _ in range(20):
            time.sleep(0.3)
            if page.locator(remove_selector).count() < before:
                break
        print(f"    🗑️  {effective_pn} eliminado del carrito.")
    except Exception as e:
        print(f"    ⚠️  Error eliminando {effective_pn} del carrito: {e}")


def scrape_cart(page: Page) -> List[Dict]:
    """
    Lee precio, qty y OOS de cada ítem del carrito.
    El stock disponible (qty_available/in_stock) se obtiene DURANTE el typeahead
    en check_item_stock_from_cart() y se guarda en stock_memory.
    """
    print("\n🛒 Scrapeando precios del carrito Cook's Power...")
    cart_items: List[Dict] = []

    rows = page.locator("div[data-type='order-item'].cart-lines-row").all()
    print(f"  📋 Ítems en carrito: {len(rows)}")

    for idx, row in enumerate(rows):
        try:
            # ── SKU ─────────────────────────────────────────────────────────
            sku_el = row.locator("span.product-line-sku-value")
            sku = sku_el.inner_text().strip() if sku_el.count() > 0 else ""
            if not sku:
                continue

            # ── Precio actual (your_price) ───────────────────────────────────
            price_el = row.locator("span.transaction-line-views-price-lead[data-rate]")
            your_price: Optional[Decimal] = None
            if price_el.count() > 0:
                rate_attr = price_el.first.get_attribute("data-rate")
                if rate_attr:
                    try:
                        your_price = Decimal(rate_attr.strip())
                    except Exception:
                        your_price = parse_price(price_el.first.inner_text())
                else:
                    your_price = parse_price(price_el.first.inner_text())

            # ── Precio de lista ──────────────────────────────────────────────
            old_price_el = row.locator("small.transaction-line-views-price-old")
            list_price: Optional[Decimal] = None
            if old_price_el.count() > 0:
                list_price = parse_price(old_price_el.first.inner_text())

            # ── Cantidad ─────────────────────────────────────────────────────
            qty_el = row.locator("input[data-type='cart-item-quantity-input']")
            cart_qty = 0
            if qty_el.count() > 0:
                try:
                    cart_qty = int(qty_el.first.input_value().strip())
                except Exception:
                    cart_qty = 0

            # ── Out of Stock ─────────────────────────────────────────────────
            oos_el = row.locator("p.product-line-stock-msg-out")
            out_of_stock = oos_el.count() > 0
            oos_message: Optional[str] = None
            if out_of_stock:
                try:
                    oos_message = oos_el.first.inner_text().strip()
                except Exception:
                    oos_message = "Out of Stock"
                print(f"  ❌ (OOS) {sku}: ${your_price} qty={cart_qty} | {oos_message}")
            else:
                print(f"  ✅ {sku}: ${your_price} list=${list_price} qty={cart_qty}")

            cart_items.append({
                "sku": sku,
                "your_price": your_price,
                "list_price": list_price,
                "qty": cart_qty,
                "out_of_stock": out_of_stock,
                "oos_message": oos_message,
            })

        except Exception as e:
            print(f"  ⚠️  Error leyendo fila del carrito: {e}")

    return cart_items


# ─────────────────────────────────────────────────────────────────────────────
#  PASO 5: Vaciar el carrito
# ─────────────────────────────────────────────────────────────────────────────

def clear_cart(page: Page) -> None:
    """
    Elimina todos los ítems del carrito haciendo clic en cada botón Remove.
    Selector: a.cart-item-actions-item-list-actionable-edit-content-remove[data-action='remove-item']
    """
    print("\n🗑️  Vaciando carrito...")
    removed = 0
    remove_selector = (
        "a.cart-item-actions-item-list-actionable-edit-content-remove"
        "[data-action='remove-item']"
    )

    for _ in range(100):  # límite de seguridad
        remove_btns = page.locator(remove_selector)
        count = remove_btns.count()
        if count == 0:
            break

        # Guardar el internalid del botón que vamos a eliminar
        btn = remove_btns.first
        internal_id = btn.get_attribute("data-internalid") or ""

        btn.click()
        removed += 1
        print(f"  🗑️  Ítem {removed} eliminado (id={internal_id}).")

        # Esperar a que el conteo de botones baje (desktop + mobile = 2 por ítem)
        for _ in range(20):
            time.sleep(0.3)
            if page.locator(remove_selector).count() < count:
                break

    print(f"  ✅ Carrito vaciado. {removed} ítem(s) eliminados.")


# ─────────────────────────────────────────────────────────────────────────────
#  Función principal de automatización
# ─────────────────────────────────────────────────────────────────────────────

def cooks_power_automation_playwright(
    username: str,
    password: str,
    po_items: List[Dict],  # [{"part_number": "...", "qty": N, "mfrid": "...", "idealCost": ...}]
) -> Optional[List[Dict]]:
    """
    Ejecuta la automatización completa para Cook's Power:
      - Login
      - Quick Order: item por item vía typeahead
      - Scrape del carrito final
      - Combina resultados de typeahead con datos del carrito

    :param username: Email de acceso
    :param password: Contraseña
    :param po_items:  Lista de ítems de la PO con part_number, qty, mfrid
    :return: Lista de dicts con todos los datos combinados, o None si falla
    """
    print("=" * 60)
    print("🚀 [Cook's Power] Iniciando automatización")
    print(f"📦 Ítems a procesar: {len(po_items)}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            slow_mo=200,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--block-third-party-cookies",
            ],
        )
        context = browser.new_context(
            no_viewport=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
            ),
        )
        page = context.new_page()

        try:
            # ── Ir a la página principal ────────────────────────────────────
            page.goto(COOKS_POWER_URL, wait_until="domcontentloaded", timeout=TIMEOUT_NAVIGATION)
            print(f"✅ Página cargada: {COOKS_POWER_URL}")

            # ── Cerrar banner de advertencia de cookies de terceros (si aparece) ──
            try:
                dismiss_btn = page.locator(
                    "text=dismiss, button:has-text('Dismiss'), button:has-text('Close'), "
                    ".cookie-warning button, .alert-dismiss, [data-dismiss='alert']"
                )
                if dismiss_btn.count() > 0:
                    dismiss_btn.first.click()
                    print("ℹ️  Banner de cookies cerrado.")
            except Exception:
                pass  # Si no hay banner, continuar normalmente

            # ── Login ───────────────────────────────────────────────────────
            login(page, username, password)

            # ── Navegar al Quick Order ──────────────────────────────────────
            navigate_to_quick_order(page)

            # ── Limpiar carrito antes de procesar ítems ──────────────────────
            # Elimina ítems residuales de sesiones anteriores para garantizar
            # que el carrito está vacío antes de empezar a cargar la nueva PO.
            print("🗑️ Verificando carrito Cook's Power antes de iniciar...")
            clear_cart(page)

            # ── Procesar cada ítem: añadir → capturar precio → verificar stock → eliminar ──
            CART_QO_URL = "https://www.cookspower.com/cart?openQuickOrder=true"
            typeahead_results: Dict[str, Dict] = {}
            stock_memory: Dict[str, tuple] = {}   # {sku_upper: (qty_available, in_stock)}
            price_memory: Dict[str, Dict] = {}    # {sku_upper: {your_price, list_price, qty, out_of_stock, oos_message}}

            for item_index, item in enumerate(po_items):
                original_pn = item.get("part_number", "")
                qty = item.get("qty", 1)
                mfrid = item.get("mfrid", "")

                if not original_pn:
                    continue

                item_result = process_item(page, original_pn, qty, mfrid, item_index=item_index)
                typeahead_results[original_pn] = item_result

                effective_pn_now = item_result.get("part_number", original_pn)

                if item_result.get("status") != "PART_ERROR":
                    print(f"  📊 [{item_index+1}/{len(po_items)}] Verificando stock: {effective_pn_now}...")

                    # ── Capturar precio y OOS de la fila antes de navegar ──
                    rows = page.locator("div[data-type='order-item'].cart-lines-row")
                    for i in range(rows.count()):
                        row = rows.nth(i)
                        sku_el = row.locator("span.product-line-sku-value")
                        if sku_el.count() > 0 and sku_el.first.inner_text().strip().upper() == effective_pn_now.upper():
                            price_el = row.locator("span.transaction-line-views-price-lead[data-rate]")
                            your_price_now = None
                            if price_el.count() > 0:
                                rate = price_el.first.get_attribute("data-rate")
                                try:
                                    your_price_now = Decimal(rate.strip()) if rate else parse_price(price_el.first.inner_text())
                                except Exception:
                                    your_price_now = parse_price(price_el.first.inner_text())
                            old_el = row.locator("small.transaction-line-views-price-old")
                            list_price_now = parse_price(old_el.first.inner_text()) if old_el.count() > 0 else None
                            qi = row.locator("input[data-type='cart-item-quantity-input']")
                            cart_qty_now = int(qi.first.input_value().strip()) if qi.count() > 0 else qty
                            oos_el = row.locator("p.product-line-stock-msg-out")
                            oos_now = oos_el.count() > 0
                            oos_msg_now = oos_el.first.inner_text().strip() if oos_now else None
                            price_memory[effective_pn_now.upper()] = {
                                "your_price": your_price_now,
                                "list_price": list_price_now,
                                "qty": cart_qty_now,
                                "out_of_stock": oos_now,
                                "oos_message": oos_msg_now,
                            }
                            break

                    # ── Verificar stock (navega a detalle y vuelve al QO) ──
                    qty_avail, in_stk = check_item_stock_from_cart(page, effective_pn_now, CART_QO_URL)
                    stock_memory[effective_pn_now.upper()] = (qty_avail, in_stk)

                    # ── Eliminar ítem del carrito inmediatamente ────────────
                    remove_from_cart(page, effective_pn_now)

                # Pequeña pausa entre ítems
                time.sleep(0.5)

            # El carrito ya está vacío — no se necesita scrape_cart() ni clear_cart()

            # ── Combinar resultados ────────────────────────────────────────
            results: List[Dict] = []

            for item in po_items:
                original_pn = item.get("part_number", "")
                mfrid = item.get("mfrid", "")
                mfrid_orig = item.get("mfrid_orig", "")  # ✅ Viene directamente del body
                requested_qty = item.get("qty", 1)
                ideal_cost = item.get("idealCost", 0.0)

                ta = typeahead_results.get(original_pn, {})
                effective_pn = ta.get("part_number", original_pn)
                pre_status = ta.get("status", "CORRECT")
                nla = ta.get("nla")
                superseded_from = ta.get("superseded_from")
                pack_qty = ta.get("pack_qty")
                ta_error = ta.get("error_message")

                # Datos de precio desde price_memory
                pm = price_memory.get(effective_pn.upper()) or price_memory.get(original_pn.upper())
                if pm:
                    your_price = pm.get("your_price")
                    list_price = pm.get("list_price")
                    cart_qty = pm.get("qty", requested_qty)
                    out_of_stock = pm.get("out_of_stock", False)
                    oos_message = pm.get("oos_message")
                else:
                    your_price = None
                    list_price = None
                    cart_qty = requested_qty
                    out_of_stock = False
                    oos_message = None

                # Stock desde memoria (recopilado inmediatamente tras añadir cada ítem)
                qty_available, in_stock = stock_memory.get(effective_pn.upper(), (0, 'N'))

                # Determinar status final
                if pre_status in ("PART_ERROR", "SUPERSEDED", "NLA"):
                    final_status = pre_status
                    error_message = ta_error
                    if pre_status == "PART_ERROR":
                        qty_available, in_stock = 0, 'N'
                elif out_of_stock:
                    final_status = "PART_ERROR"
                    error_message = f"{oos_message or 'Out of Stock'}: {effective_pn}"
                    # ✅ OOS NO es NLA — son cosas distintas
                    # nla queda como None (lo que ya viene del typeahead)
                    qty_available, in_stock = 0, 'N'
                else:
                    final_status = "CORRECT"  # Se refinará en process_results
                    error_message = None

                results.append({
                    "part_number": effective_pn,
                    "requested_sku": original_pn,
                    "mfrid": mfrid,
                    "mfrid_orig": mfrid_orig,  # ✅ Propagado directamente del body
                    "qty": cart_qty,
                    "qty_available": qty_available,
                    "in_stock": in_stock,
                    "your_price": your_price,
                    "list_price": list_price,
                    "ideal_cost": ideal_cost,
                    "status": final_status,
                    "nla": nla,
                    "superseded_from": superseded_from,
                    "pack_qty": pack_qty,
                    "ltl": None,
                    "error_message": error_message,
                })

            # Agregar ítems que nunca entraron al carrito (NLA, errores typeahead)
            handled_original_pns = {item.get("part_number", "") for item in po_items}
            for original_pn, ta in typeahead_results.items():
                if original_pn not in handled_original_pns:
                    continue  # ya cubierto arriba
                if ta.get("status") in ("PART_ERROR",) and ta.get("nla") == "Y":
                    # Verificar si ya está en results
                    already_in = any(r["requested_sku"] == original_pn for r in results)
                    if not already_in:
                        results.append({
                            "part_number": original_pn,
                            "requested_sku": original_pn,
                            "mfrid": "",
                            "qty": 0,
                            "your_price": None,
                            "list_price": None,
                            "ideal_cost": 0.0,
                            "status": "PART_ERROR",
                            "nla": "Y",
                            "superseded_from": None,
                            "pack_qty": None,
                            "ltl": None,
                            "error_message": ta.get("error_message"),
                        })

            print(f"\n✅ Automatización completa. {len(results)} resultados.")
            return results

        except Exception as e:
            print(f"❌ Error fatal en automatización Cook's Power: {e}")
            import traceback
            traceback.print_exc()
            return None

        finally:
            context.close()
            browser.close()


# ─────────────────────────────────────────────────────────────────────────────
#  PRUEBAS LOCALES
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pprint

    # ── Credenciales ────────────────────────────────────────────────────────
    TEST_EMAIL = "invoices.prontomowers@gmail.com"
    TEST_PASSWORD = "Hustler123"

    # ── Ítems de prueba ─────────────────────────────────────────────────────
    TEST_PO_ITEMS = [
        # SUPERSEDED: 125256 → 125255P
        {
            "part_number": "784991",
            "qty": 1,
            "mfrid": "CP",
            "idealCost": 0.0,
        },
        {
            "part_number": "784991",
            "qty": 1,
            "mfrid": "CP",
            "idealCost": 0.0,
        }
    ]

    print("=" * 60)
    print("🧪 MODO PRUEBA — Cook's Power Automation")
    print(f"📧 Usuario: {TEST_EMAIL}")
    print(f"📦 Ítems: {len(TEST_PO_ITEMS)}")
    print("=" * 60)

    results = cooks_power_automation_playwright(
        username=TEST_EMAIL,
        password=TEST_PASSWORD,
        po_items=TEST_PO_ITEMS,
    )

    print("\n" + "=" * 60)
    print("📊 RESULTADOS FINALES")
    print("=" * 60)

    if results:
        for r in results:
            status_icon = {
                "CORRECT": "✅",
                "MISMATCH": "⚠️",
                "PART_ERROR": "❌",
                "SUPERSEDED": "🔄",
            }.get(r.get("status", ""), "❓")

            print(
                f"{status_icon} [{r.get('status')}] "
                f"{r.get('part_number')} "
                f"(req: {r.get('requested_sku')}) | "
                f"qty={r.get('qty')} | "
                f"price=${r.get('your_price')} | "
                f"ideal=${r.get('ideal_cost')} | "
                f"nla={r.get('nla')} | "
                f"superseded_from={r.get('superseded_from')} | "
                f"pack_qty={r.get('pack_qty')}"
            )
            if r.get("error_message"):
                print(f"   ↳ {r['error_message']}")

        print("\n📋 Raw (pprint):")
        pprint.pprint(results)
    else:
        print("❌ No se obtuvieron resultados.")
