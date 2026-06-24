from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import time
import re


def _parse_price(text: str):
    if not text:
        return None
    # remove currency symbols and commas
    num = re.sub(r"[^0-9.\\-]", "", text)
    try:
        return float(num) if num != "" else None
    except ValueError:
        return None


def wesco_automation_playwright(
    username: str,
    password: str,
    csv_filename: str,
    downloads_dir: str = None,
    headless: bool = False,
    debug: bool = False,
    po_items: list = None,
    po_data: object = None,
):
    """
    Automate Wesco/WescoTurf bulk order flow using Playwright.
    This version uses the Edge channel (msedge) when available and matches
    the browser launch pattern used by the Husqvarna script.
    
    Args:
        debug: if True, captures screenshot and HTML for inspection and prints selector info
    """
    results = []

    # Determine csv full path
    if os.path.isabs(csv_filename) and os.path.exists(csv_filename):
        csv_path = csv_filename
    else:
        if downloads_dir is None:
            downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        csv_path = os.path.join(downloads_dir, csv_filename)

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=headless,
        args=['--start-maximized', '--disable-blink-features=AutomationControlled'],
        channel='msedge',
    )
    context = browser.new_context(
        no_viewport=True,
        ignore_https_errors=True,
        user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0'
        ),
    )
    page = context.new_page()

    try:
        # Open login page directly so the login form is present
        page.goto("https://www.wescoturf.com/ccrz__CCSiteLogin?store=DefaultStore&cclcl=en_US", timeout=30000)
        try:
            page.fill("#emailField", username)
            page.fill("#passwordField", password)
            page.click("#send2Dsk")
            # Wait for navigation after login click to avoid execution-context-destroyed errors
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                time.sleep(2)
        except PlaywrightTimeoutError:
            # fallback login flow
            try:
                page.wait_for_selector("input[name=Email]", timeout=5000)
                page.fill("input[name=Email]", username)
                page.fill("input[name=Password]", password)
                page.click("button[type=submit]")
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    time.sleep(2)
            except Exception:
                raise

        # Navigate to bulk order / load dealer order
        # Try to navigate to bulk order / load dealer order safely
        try:
            page.wait_for_selector('a[href*="pageKey=bulkOrder"]', timeout=5000)
            link = page.query_selector('a[href*="pageKey=bulkOrder"]')
        except Exception:
            link = None

        if link:
            link.click()
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                time.sleep(1)
        else:
            # try dropdown path
            try:
                page.wait_for_selector("text=Order Parts", timeout=5000)
                menu = page.query_selector("text=Order Parts")
            except Exception:
                menu = None

            if menu:
                menu.click()
                try:
                    page.wait_for_selector('a:has-text("Load Dealer Order")', timeout=5000)
                    maybe = page.query_selector('a:has-text("Load Dealer Order")')
                except Exception:
                    maybe = None

                if maybe:
                    maybe.click()
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        time.sleep(1)
                else:
                    page.goto("https://www.wescoturf.com/ccrz__CCPage?pageKey=bulkOrder")
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        time.sleep(1)
            else:
                page.goto("https://www.wescoturf.com/ccrz__CCPage?pageKey=bulkOrder")
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    time.sleep(1)

        # Upload CSV
        # Wait for the file input to appear before querying
        file_input = None
        try:
            page.wait_for_selector("#myfile, input[type=\"file\"]", timeout=10000)
            file_input = page.query_selector("#myfile") or page.query_selector('input[type="file"]')
        except Exception:
            file_input = page.query_selector("#myfile") or page.query_selector('input[type="file"]')

        if not file_input:
            raise RuntimeError("Could not find file input '#myfile' on bulk order page")
        file_input.set_input_files(csv_path)
        time.sleep(0.5)

        # Wait for upload button then click
        try:
            page.wait_for_selector("#uploadCsv, input[type=submit]", timeout=10000)
            upload_btn = page.query_selector("#uploadCsv") or page.query_selector('input[type="submit"]')
        except Exception:
            upload_btn = page.query_selector("#uploadCsv") or page.query_selector('input[type="submit"]')

        if not upload_btn:
            raise RuntimeError("Could not find upload button '#uploadCsv'")

        print(f"[DEBUG] Clicking upload button: {upload_btn.get_attribute('id') or upload_btn.get_attribute('name') or 'submit'}")
        upload_btn.click()
        
        # Wait for cart to appear - the form submit triggers JS/AJAX to load cart items
        print("[DEBUG] Waiting for cart items to appear after upload...")
        try:
            # Wait for any of these cart container elements to appear
            page.wait_for_selector(
                '#cart_items_container, .cc_cart_item_list, .cart_item_list, .cart_item, [class*="cart_item"]',
                timeout=20000
            )
            print("[DEBUG] Cart container appeared")
        except Exception as e:
            print(f"[DEBUG] wait_for_selector for cart failed: {e}")
            print("[DEBUG] Trying to wait for specific cart indicators...")
            try:
                # Wait for the Shopping Cart title or similar
                page.wait_for_selector('h2:has-text("Shopping Cart"), .cc_title:has-text("Shopping Cart")', timeout=10000)
                print("[DEBUG] Found Shopping Cart title")
            except Exception as e2:
                print(f"[DEBUG] Cart title search also failed: {e2}")
                print("[DEBUG] Sleeping 3 seconds and proceeding...")
                time.sleep(3)
        
        # Check if we're still on the upload page or if we navigated
        current_url = page.url
        print(f"[DEBUG] Current URL after upload: {current_url}")
        
        # Small extra wait for JavaScript to process
        time.sleep(1)

        # Debug mode: capture screenshot and HTML
        if debug:
            print("\n[DEBUG] Capturing screenshot and HTML for inspection...")
            try:
                page.screenshot(path="wesco_cart_debug.png")
                print("✓ Screenshot saved: wesco_cart_debug.png")
            except Exception as e:
                print(f"⚠️ Screenshot failed: {e}")
            
            try:
                html_content = page.content()
                with open("wesco_cart_debug.html", "w", encoding="utf-8") as f:
                    f.write(html_content)
                print("✓ HTML saved: wesco_cart_debug.html")
                print("\n[DEBUG] HTML content sample (first 5000 chars):")
                print(html_content[:5000])
                print("\n[DEBUG] Checking if we're on cart page...")
                if "dealerorderreference" in html_content or "Load Dealer Order" in html_content:
                    print("⚠️ Still on Load Dealer Order page! Upload may not have worked.")
                if "Shopping Cart" in html_content or "cart_items" in html_content:
                    print("✓ Found Shopping Cart indicators in HTML")
                    # Find all divs/elements with cart in the class name
                    print("\n[DEBUG] Searching for cart-related elements in HTML...")
                    import re as regex
                    # Find all class attributes with 'cart' in them
                    cart_classes = set(regex.findall(r'class="([^"]*cart[^"]*)"', html_content, regex.IGNORECASE))
                    if cart_classes:
                        print("[DEBUG] Found classes with 'cart':")
                        for cls in list(cart_classes)[:15]:
                            print(f"  - {cls}")
                    # Look for Shopping Cart heading/title
                    shopping_cart_lines = [line for line in html_content.split('\n') if 'shopping cart' in line.lower()]
                    if shopping_cart_lines:
                        print("\n[DEBUG] Lines containing 'shopping cart':")
                        for line in shopping_cart_lines[:5]:
                            print(f"  {line.strip()[:100]}")
            except Exception as e:
                print(f"⚠️ HTML capture failed: {e}")
        
        # Scrape cart items
        print(f"\n[DEBUG] Looking for cart item selectors...")
        item_nodes = page.query_selector_all('.cart_item_list .cart_item, #cart_items_container .cart_item')
        print(f"[DEBUG] Found {len(item_nodes) if item_nodes else 0} items with primary selectors")
        
        if not item_nodes:
            item_nodes = page.query_selector_all('.cart_item')
            print(f"[DEBUG] Fallback 1: found {len(item_nodes) if item_nodes else 0} items with .cart_item")

        print(f"[DEBUG] Empezando a scrapear {len(item_nodes)} items...")
        items_success = 0
        items_failed = 0
        
        for idx, node in enumerate(item_nodes):
            try:
                # Debug: mostrar contenido del item
                if idx == 0:  # Solo para el primero
                    node_html = node.evaluate('el => el.outerHTML')
                    print(f"\n[DEBUG] HTML del primer item:\n{node_html[:2000]}\n")
                
                sku_node = node.query_selector('.cc_value_sku, .sku, .item_sku')
                sku = sku_node.inner_text().strip() if sku_node else (node.get_attribute('data-sku') or node.get_attribute('data-part') or '')

                desc_node = node.query_selector('.item_title a, .item_title, .description')
                desc = desc_node.inner_text().strip() if desc_node else ''

                qty = 0
                qty_node = node.query_selector('input[name^="qty"], input.qty, .qty')
                if qty_node:
                    qty_val = qty_node.get_attribute('value') or qty_node.input_value()
                    qty = int(re.sub(r'[^0-9]', '', qty_val)) if qty_val and re.search(r'\d', qty_val) else 0
                else:
                    qty_span = node.query_selector('.quantity, .qty_text')
                    if qty_span:
                        qtxt = qty_span.inner_text()
                        qty = int(re.sub(r'[^0-9]', '', qtxt)) if qtxt and re.search(r'\d', qtxt) else 0

                # Extract INDIVIDUAL unit price (NOT the total)
                # Strategy: Find .price_block container, then get all .cc_value elements
                # and filter out the one that's in a "Total" section
                your_price = None
                price_text = None
                
                # Get the price block container
                price_block = node.query_selector('.price_block')
                if price_block:
                    # Get all .cc_value elements within price_block
                    cc_values = price_block.query_selector_all('.cc_value')
                    
                    for cc_val in cc_values:
                        try:
                            # Get the immediate parent .price div
                            price_parent = cc_val.evaluate('el => el.closest(".price")')
                            if price_parent:
                                parent_text = price_parent.inner_text()
                                # Skip if this price element is in a "Total" line
                                if "Total" in parent_text:
                                    continue
                            
                            txt = cc_val.inner_text().strip()
                            if txt and re.search(r'\$.*[0-9]', txt):
                                price_text = txt
                                break
                        except:
                            continue
                
                # Fallback 1: if price_block not found, try querying from node directly
                if not price_text:
                    cc_values = node.query_selector_all('.cc_value')
                    for cc_val in cc_values:
                        try:
                            txt = cc_val.inner_text().strip()
                            # Only take if it looks like a price and appears before any "Total" text
                            if txt and re.search(r'\$.*[0-9]', txt):
                                # Check if "Total" appears later in the node
                                node_text = node.inner_text()
                                price_pos = node_text.find(txt)
                                total_pos = node_text.find("Total")
                                if total_pos == -1 or price_pos < total_pos:
                                    price_text = txt
                                    break
                        except:
                            continue
                
                # Fallback 2: try specific selectors for individual price
                if not price_text:
                    price_selectors = [
                        '.b2b_Your_Price', '.sale_price', '.your_price',
                        '.b2b_Price', '.price .cc_value'
                    ]
                    for sel in price_selectors:
                        pn = node.query_selector(sel)
                        if pn:
                            txt = pn.inner_text().strip()
                            if txt and re.search(r'[0-9]', txt):
                                price_text = txt
                                break
                
                # Last resort: search for first $ amount in node text
                if not price_text:
                    node_txt = node.inner_text()
                    m = re.search(r'\$\s*[0-9,]+(?:\.[0-9]{1,2})?', node_txt)
                    if m:
                        price_text = m.group(0)

                if price_text:
                    your_price = _parse_price(price_text)
                    print(f"  💰 Precio individual extraído: {price_text} → {your_price}")

                list_price = None
                list_selectors = ['.list_price .cc_value', '.list_price', '.was_price', '.b2b_Suggested_Price']
                for sel in list_selectors:
                    ln = node.query_selector(sel)
                    if ln:
                        lp = ln.inner_text().strip()
                        if lp and re.search(r'[0-9]', lp):
                            list_price = _parse_price(lp)
                            break

                avail_text = None
                in_stock = None
                avail_selectors = ['.availability', '.qty_available', '.b2b_availability', '.availability_text']
                for sel in avail_selectors:
                    an = node.query_selector(sel)
                    if an:
                        text = an.inner_text().strip()
                        if text:
                            avail_text = text
                            if re.search(r'available', text, re.IGNORECASE):
                                in_stock = True
                            elif re.search(r'not in stock|out of stock|unavailable|nla', text, re.IGNORECASE):
                                in_stock = False
                            break

                # If no explicit availability node found, try to infer from the item node text
                if in_stock is None:
                    node_txt = node.inner_text()
                    if re.search(r'available', node_txt, re.IGNORECASE):
                        in_stock = True
                    elif re.search(r'not in stock|out of stock|unavailable|nla', node_txt, re.IGNORECASE):
                        in_stock = False

                # Normalizar a int y 'Y'/'N'
                qty_avail_int = qty if in_stock is True else 0
                in_stock_yn = 'Y' if in_stock is True else 'N'

                # Error message: sin stock → texto raw; parcial/B/O → texto raw; disponible puro → None
                if in_stock is not True:
                    error_message = avail_text
                elif avail_text and re.search(r'partial|b/o:|not in stock|back.?order', avail_text, re.IGNORECASE):
                    error_message = avail_text
                else:
                    error_message = None

                results.append({
                    'part_number': sku,
                    'description': desc,
                    'qty': qty,
                    'your_price': your_price,
                    'list_price': list_price,
                    'mfrid': '',
                    'qty_available': qty_avail_int,
                    'in_stock': in_stock_yn,
                    'error_message': error_message,
                })
                items_success += 1
            except Exception as e:
                items_failed += 1
                print(f"[DEBUG] Item {idx} falló: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        print(f"\n[DEBUG] ✅ {items_success} items procesados exitosamente")
        print(f"[DEBUG] ❌ {items_failed} items fallaron")
        print(f"[DEBUG] Total en results: {len(results)}")

        # ── Limpiar carrito después del scraping ─────────────────────────────
        print("🗑️ Limpiando carrito Wesco...")
        try:
            clear_btn = page.locator('button.clearCart')
            if clear_btn.count() > 0:
                clear_btn.first.click()
                # Esperar a que aparezca el modal #clearAllMod
                page.wait_for_selector('#clearAllMod', state='visible', timeout=8000)
                confirm_btn = page.locator('#clearAllMod input.clearCartItems')
                if confirm_btn.count() == 0:
                    confirm_btn = page.locator('#clearAllMod input[type="button"][value*="Clear"]')
                confirm_btn.first.click()
                try:
                    page.wait_for_load_state('domcontentloaded', timeout=10000)
                except Exception:
                    pass
                time.sleep(2)
                print("  ✅ Carrito limpiado.")
            else:
                print("  ⚠️ Botón 'Clear Cart' no encontrado.")
        except Exception as e:
            print(f"  ⚠️ Error limpiando carrito: {e}")

        # Propagate mfrid from provided po_items or po_data (prefer po_items)
        mfr_map = {}
        try:
            if po_items:
                for p in po_items:
                    key = p.get('part_number') or p.get('partNumber')
                    if key:
                        mfr_map[key] = p.get('mfrid') or p.get('mfrid_orig') or ''
            elif po_data is not None:
                # po_data expected to be a PurchaseOrderDataModel-like object
                for p in getattr(po_data, 'products', []):
                    key = getattr(p, 'partNumber', None)
                    if key:
                        mfr_map[key] = getattr(p, 'mfrid', '') or getattr(p, 'mfrid_orig', '') or ''
        except Exception:
            mfr_map = {}

        if mfr_map:
            for item in results:
                pn = item.get('part_number')
                if pn and not item.get('mfrid'):
                    item['mfrid'] = mfr_map.get(pn, '')

    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    return results


if __name__ == '__main__':
    # Quick manual test runner (edit credentials and csv path as needed)
    TEST_USERNAME = 'admin@prontomowers.com'
    TEST_PASSWORD = 'pRONTO2023!'
    TEST_CSV = 'Wesco.csv'  # put this file into your Downloads folder or set absolute path

    try:
        items = wesco_automation_playwright(TEST_USERNAME, TEST_PASSWORD, TEST_CSV, headless=False, debug=True)
        print('Scraped', len(items), 'items')
        for it in items:
            print(it)
    except Exception as e:
        print('Error during automation:', e)
