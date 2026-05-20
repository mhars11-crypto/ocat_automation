from playwright.sync_api import sync_playwright
import json
import logging
from urllib.parse import urlparse
from collections import Counter, defaultdict

# ---------------- CONFIG ----------------
EMAIL = "quickadd1@albertsons.testinator.com"
PASSWORD = "testpwd1"
BASE_URL = "https://www-qa2.safeway.com/"

SEARCH_TERM = "milk"

MIN_SEARCH_ROUNDS = 2
MAX_SEARCH_ROUNDS = 4

COUPONS_TO_CLIP = 2
WAIT_AFTER_COUPONS_MS = 20000

BEVERAGES_COUPONS_TO_CLIP = 2
WAIT_AFTER_BEVERAGES_COUPONS_MS = 10000
BEVERAGES_CAROUSEL_SCAN_ROUNDS = 6

CARD_LAST4 = "4242"
CVV_VALUE = "123"
ZIP_CODE_VALUE = "94538"

# False = stop safely at final Place Order button
# True  = click final Place Order / Submit Order button
PLACE_ORDER = True

event_payloads = []
captured_apis = []
failed_apis = []

# ---------------- UNIQUE API TRACKING ----------------
# Example unique key:
# POST https://www-qa2.safeway.com/serversidetag/event
unique_api_counter = Counter()
unique_api_samples = defaultdict(list)
unique_api_methods = defaultdict(set)

COOKIES_HANDLED = False

# ---------------- LOGGING ----------------
logging.basicConfig(
    filename="test_run.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def log(msg):
    print(msg)
    logging.info(msg)

def pause(page, ms=3000):
    page.wait_for_timeout(ms)

def normalize_api_endpoint(url, method=None):
    """
    Converts full API URL into a unique endpoint key by removing query parameters.

    Example:
    https://www-qa2.safeway.com/serversidetag/event?abc=123
    becomes:
    POST https://www-qa2.safeway.com/serversidetag/event
    """
    try:
        parsed = urlparse(url)
        base_endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        if method:
            return f"{method.upper()} {base_endpoint}"

        return base_endpoint

    except Exception:
        if method:
            return f"{method.upper()} {url}"
        return url


def build_unique_api_summary():
    """
    Builds unique API summary with:
    - endpoint
    - hit count
    - methods
    - sample URLs
    """
    summary = []

    for endpoint_key, count in unique_api_counter.most_common():
        sample_urls = unique_api_samples.get(endpoint_key, [])
        methods = sorted(list(unique_api_methods.get(endpoint_key, [])))

        summary.append({
            "endpoint": endpoint_key,
            "count": count,
            "methods": methods,
            "sample_urls": sample_urls[:5]
        })

    return summary

# ---------------- SAFE HELPERS ----------------
def is_crash_error(e):
    msg = str(e).lower()
    crash_words = [
        "target crashed",
        "target closed",
        "browser has been closed",
        "context or browser has been closed",
        "page crashed",
        "target page, context or browser has been closed"
    ]
    return any(word in msg for word in crash_words)

def is_page_alive(page):
    try:
        return page is not None and not page.is_closed()
    except:
        return False

def safe_screenshot(page, path="error_screenshot.png"):
    try:
        if is_page_alive(page):
            page.screenshot(path=path, full_page=True)
            log(f"📸 Screenshot saved to {path}")
        else:
            log("⚠ Page already closed/crashed. Skipping screenshot.")
    except Exception as e:
        log(f"⚠ Screenshot failed/skipped: {e}")

def safe_evaluate(page, script, default=None):
    try:
        if not is_page_alive(page):
            return default
        return page.evaluate(script)
    except Exception as e:
        log(f"⚠ safe_evaluate failed: {e}")
        return default

def attach_network_listeners(page):
    page.on("request", capture_requests)
    page.on("response", capture_responses)

def create_fresh_page(context):
    log("🆕 Creating fresh page")
    page = context.new_page()
    page.set_default_timeout(45000)
    attach_network_listeners(page)
    return page

def recover_page_after_crash(context, old_page):
    log("🛟 Recovering from page crash / Aw Snap")

    try:
        if old_page and not old_page.is_closed():
            old_page.close()
            log("✅ Closed crashed page")
    except Exception as e:
        log(f"⚠ Could not close crashed page: {e}")

    page = create_fresh_page(context)
    open_homepage(page)
    pause(page, 7000)
    log("✅ Fresh page recovered and homepage opened")
    return page

# ---------------- NETWORK CAPTURE ----------------
def capture_requests(request):
    url = request.url
    method = request.method
    post_data = request.post_data

    if "serversidetag/event" in url or "onetag" in url or "/event/" in url:
        endpoint_key = normalize_api_endpoint(url, method)
        has_payload = bool(post_data)

        log(f"📡 REQUEST: {method} {url}")
        log(f"🔗 UNIQUE ENDPOINT KEY: {endpoint_key}")
        log(f"📦 HAS PAYLOAD: {has_payload}")

        captured_apis.append({
            "url": url,
            "method": method,
            "endpoint": endpoint_key,
            "has_payload": has_payload
        })

        unique_api_counter[endpoint_key] += 1
        unique_api_methods[endpoint_key].add(method)

        if len(unique_api_samples[endpoint_key]) < 5:
            unique_api_samples[endpoint_key].append(url)

        if has_payload:
            event_payloads.append(post_data)

def capture_responses(response):
    url = response.url
    method = response.request.method

    if "serversidetag/event" in url or "onetag" in url or "/event/" in url:
        endpoint_key = normalize_api_endpoint(url, method)

        log(f"📡 RESPONSE: {response.status} → {method} {url}")
        log(f"🔗 RESPONSE ENDPOINT: {endpoint_key}")

        if response.status != 200:
            failed_apis.append({
                "url": url,
                "method": method,
                "endpoint": endpoint_key,
                "status": response.status
            })
# ---------------- COMMON CLICK HELPERS ----------------
def click_first_visible_button(page, selectors, action_name, wait_after_ms=8000):
    log(f"👉 Looking for button/action: {action_name}")

    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            log(f"🔎 {action_name} candidates for '{sel}': {count}")

            for i in range(min(count, 40)):
                try:
                    btn = loc.nth(i)
                    if btn.is_visible() and btn.is_enabled():
                        btn.scroll_into_view_if_needed()
                        pause(page, 1000)
                        btn.click(force=True)
                        log(f"✅ Clicked {action_name} using selector: {sel}")
                        pause(page, wait_after_ms)
                        return True
                except Exception as e:
                    log(f"⚠ {action_name} candidate skipped index={i}: {e}")

        except Exception as e:
            log(f"⚠ Selector failed for {action_name}: {sel} error={e}")
            if is_crash_error(e):
                raise

    log(f"❌ Could not click action: {action_name}")
    return False

def click_enabled_continue_js(page, context_name="generic"):
    log(f"➡️ Clicking enabled Continue using JS context={context_name}")

    for attempt in range(1, 8):
        clicked = safe_evaluate(page, """
            () => {
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 &&
                           r.height > 0 &&
                           s.visibility !== 'hidden' &&
                           s.display !== 'none';
                };

                const isDisabled = (el) => {
                    return el.disabled
                        || el.getAttribute('aria-disabled') === 'true'
                        || String(el.className || '').toLowerCase().includes('disabled');
                };

                const buttons = Array.from(document.querySelectorAll("button,[role='button'],a"))
                    .filter(visible);

                const continueButtons = buttons.filter(el => {
                    const text = (el.innerText || "").trim();
                    const aria = el.getAttribute("aria-label") || "";
                    const cls = el.className || "";
                    const combined = `${text} ${aria} ${cls}`;

                    if (/next available dates|next-button/i.test(combined)) {
                        return false;
                    }

                    if (/confirm cvv|confirm/i.test(combined) && !/continue|review/i.test(combined)) {
                        return false;
                    }

                    return /^\\s*continue\\s*$/i.test(text)
                        || /continue\\s*to\\s*checkout/i.test(combined)
                        || /review order|review/i.test(combined);
                });

                const enabledContinue = continueButtons.find(el => !isDisabled(el));

                if (!enabledContinue) {
                    return {
                        clicked: false,
                        reason: "NO_ENABLED_CONTINUE",
                        found: continueButtons.map(el => ({
                            text: (el.innerText || "").trim(),
                            aria: el.getAttribute("aria-label"),
                            disabled: isDisabled(el),
                            cls: el.className
                        }))
                    };
                }

                enabledContinue.scrollIntoView({ block: "center", inline: "center" });
                enabledContinue.click();

                return {
                    clicked: true,
                    text: (enabledContinue.innerText || "").trim(),
                    aria: enabledContinue.getAttribute("aria-label"),
                    cls: enabledContinue.className
                };
            }
        """, default={"clicked": False})

        log(f"➡️ Continue JS result attempt {attempt}/7: {clicked}")

        if clicked and clicked.get("clicked"):
            pause(page, 10000)
            return True

        pause(page, 2500)

    return False

# ---------------- WAIT FOR PAGE READY ----------------
def wait_for_page_ready(page):
    log("⏳ Waiting for page/header to load")

    try:
        page.wait_for_load_state("domcontentloaded", timeout=45000)
    except:
        log("⚠ domcontentloaded timeout, continuing")

    try:
        page.wait_for_load_state("load", timeout=45000)
    except:
        log("⚠ load timeout, continuing")

    try:
        page.wait_for_selector("[data-qa='hdr-accnt-icn']", timeout=45000)
    except:
        log("⚠ Account icon not detected yet")

    try:
        page.wait_for_selector("input[data-qa='srch-inpt']", timeout=45000)
    except:
        log("⚠ Search box not detected yet")

    pause(page, 6000)
    log("✅ Page/header wait completed")

# ---------------- OPEN HOMEPAGE ----------------
def open_homepage(page):
    for attempt in range(3):
        try:
            log(f"🌐 Opening safeway homepage attempt {attempt + 1}/3")
            page.goto(BASE_URL, wait_until="commit", timeout=60000)
            wait_for_page_ready(page)

            if "/order/fall/" in page.url or "thanksgiving" in page.url:
                log("⚠ Landed on LP/holiday page. Forcing back to homepage.")
                page.goto(BASE_URL, wait_until="commit", timeout=60000)
                wait_for_page_ready(page)

            log(f"✅ Current URL: {page.url}")
            return
        except Exception as e:
            log(f"⚠ Homepage open failed: {str(e)}")
            if is_crash_error(e):
                raise
            try:
                pause(page, 5000)
            except:
                pass

    raise Exception("Unable to open homepage")

# ---------------- ACCEPT COOKIES ONLY ONCE ----------------
def accept_all_cookies(page):
    global COOKIES_HANDLED

    if COOKIES_HANDLED:
        log("🍪 Cookie already handled earlier. Skipping cookie banner check.")
        return False

    log("🍪 Checking cookie banner")

    cookie_selectors = [
        "button:has-text('Accept All')",
        "button:has-text('Continue with all')",
        "button:has-text('Accept')",
        "button:has-text('I Accept')"
    ]

    for selector in cookie_selectors:
        try:
            btn = page.locator(selector)
            if btn.count() > 0 and btn.first.is_visible():
                log(f"✅ Clicking cookie button: {selector}")
                btn.first.click(force=True)
                pause(page, 5000)
                COOKIES_HANDLED = True
                return True
        except Exception as e:
            log(f"⚠ Cookie selector skipped {selector}: {str(e)}")

    log("ℹ️ Cookie banner not found. Marking cookie check as handled.")
    COOKIES_HANDLED = True
    return False

# ---------------- LOGIN ----------------
def wait_for_visible_login_input(page, timeout_ms=20000):
    log("⏳ Waiting for visible username input")
    try:
        username = page.locator("input#enterUsername:visible")
        username.wait_for(state="visible", timeout=timeout_ms)
        log("✅ Visible username input found")
        return True
    except:
        log("⚠ Visible username input not found yet")
        return False

def open_login_modal_using_original_flow(page):
    log("👤 Clicking account icon")

    account_icon = page.locator("[data-qa='hdr-accnt-icn']")
    account_icon.wait_for(state="visible", timeout=45000)
    pause(page, 3000)
    account_icon.click(force=True)
    pause(page, 5000)

    if wait_for_visible_login_input(page, timeout_ms=5000):
        return

    log("📝 Trying visible Sign in button from account dropdown")

    try:
        visible_signin_buttons = page.locator("button[aria-label='Sign in']:visible")
        if visible_signin_buttons.count() > 0:
            log("✅ Clicking visible Sign in button")
            visible_signin_buttons.first.click(force=True)
            pause(page, 6000)
            if wait_for_visible_login_input(page, timeout_ms=8000):
                return
    except Exception as e:
        log(f"⚠ Visible Sign in click failed: {str(e)}")

    log("⚠ No visible Sign in button. Dispatching click on Sign in button.")

    try:
        sign_btn = page.locator("button[aria-label='Sign in']").first
        sign_btn.dispatch_event("click")
        pause(page, 7000)
        if wait_for_visible_login_input(page, timeout_ms=8000):
            return
    except Exception as e:
        log(f"⚠ Dispatch Sign in click failed: {str(e)}")

    log("⚠ Opening login modal using AB.COMMON fallback")

    try:
        page.evaluate("""
            () => {
                if (window.AB && AB.COMMON && AB.COMMON.openAuthenticateModal) {
                    AB.COMMON.openAuthenticateModal('signIn', 'account');
                    if (AB.COMMON.setTopNavPreviousPage) {
                        AB.COMMON.setTopNavPreviousPage();
                    }
                    return true;
                }
                return false;
            }
        """)
        pause(page, 8000)
        if wait_for_visible_login_input(page, timeout_ms=10000):
            return
    except Exception as e:
        log(f"⚠ AB.COMMON fallback failed: {str(e)}")

    raise Exception("Login modal did not open / visible username input not found")

def perform_login(page):
    log("🔐 Performing login")
    open_homepage(page)

    accept_all_cookies(page)
    pause(page, 5000)

    open_login_modal_using_original_flow(page)

    log("📧 Entering email")
    email_field = page.locator("input#enterUsername:visible")
    email_field.wait_for(state="visible", timeout=45000)
    pause(page, 3000)
    email_field.fill(EMAIL)
    pause(page, 3000)

    log("🔐 Clicking 'Sign in with password'")
    password_option = page.locator("button:has-text('Sign in with password'):visible")
    password_option.wait_for(state="visible", timeout=45000)
    pause(page, 3000)
    password_option.click(force=True)
    pause(page, 5000)

    log("🔒 Entering password")
    pwd_field = page.locator("input#password:visible")
    pwd_field.wait_for(state="visible", timeout=45000)
    pause(page, 3000)
    pwd_field.fill(PASSWORD)
    pause(page, 3000)

    log("⏳ Waiting for final Sign in button")
    final_signin_btn = page.locator("button[type='submit'][aria-label='Sign in']:visible")
    final_signin_btn.wait_for(state="visible", timeout=45000)
    pause(page, 3000)

    log("✓ Submitting login")
    final_signin_btn.click(force=True)

    log("⏳ Waiting for login to complete")
    pause(page, 18000)

    log("✅ Login submitted")

    log("🏠 Returning to homepage after login")
    open_homepage(page)
    pause(page, 7000)

# ---------------- SAFE SCROLL ----------------
def scroll_bottom_then_top(page, max_steps=12, max_scroll_y=8000):
    log("🔽 Scrolling safely downward")

    if not is_page_alive(page):
        raise Exception("Page is closed before scroll started")

    previous_y = -1
    stuck_count = 0

    for step in range(max_steps):
        total_height = safe_evaluate(page, "document.documentElement.scrollHeight", default=0)
        viewport_height = safe_evaluate(page, "window.innerHeight", default=800)
        current_y_before = safe_evaluate(page, "window.scrollY", default=0)

        if current_y_before is None:
            current_y_before = 0

        if current_y_before >= max_scroll_y:
            log(f"⚠ Reached safe max scroll limit y={current_y_before}. Stopping downward scroll.")
            break

        try:
            page.mouse.wheel(0, 800)
        except Exception as e:
            log(f"⚠ Mouse wheel failed during downward scroll: {e}")
            break

        pause(page, 900)

        current_y = safe_evaluate(page, "window.scrollY", default=current_y_before)
        new_total_height = safe_evaluate(page, "document.documentElement.scrollHeight", default=total_height)

        log(f"⬇️ Scroll down step {step + 1}: y={current_y}, pageHeight={new_total_height}")

        if current_y == previous_y or current_y + viewport_height >= new_total_height - 30:
            stuck_count += 1
        else:
            stuck_count = 0

        previous_y = current_y

        if stuck_count >= 3:
            log("✅ Scroll appears stuck/bottom reached. Stopping downward scroll.")
            break

    pause(page, 2000)

    log("🔼 Scrolling safely back to top")

    for step in range(max_steps):
        current_y = safe_evaluate(page, "window.scrollY", default=0)
        if current_y is None or current_y <= 0:
            break

        try:
            page.mouse.wheel(0, -1000)
        except Exception as e:
            log(f"⚠ Mouse wheel failed during upward scroll: {e}")
            break

        pause(page, 700)

    pause(page, 3000)
    log("✅ Top reached / safe scroll completed")

# ---------------- HOMEPAGE COUPON CLIP ----------------
def clip_coupons_on_homepage(page, count=2):
    log(f"🏷️ Trying to clip {count} coupons on Homepage only")

    pause(page, 3000)

    clip_selectors = [
        "button:has-text('Clip')",
        "button:has-text('Clip Deal')",
        "button:has-text('Clip coupon')",
        "button:has-text('Clip Coupon')",
        "button[aria-label*='Clip']:not([disabled])",
        "[data-qa*='clip']:not([disabled])",
        "[data-testid*='clip']:not([disabled])"
    ]

    clipped = 0

    def try_clip_visible_buttons():
        nonlocal clipped

        for selector in clip_selectors:
            if clipped >= count:
                break

            try:
                buttons = page.locator(selector)
                total = buttons.count()
                log(f"🔎 Homepage coupon candidates for '{selector}': {total}")

                for i in range(min(total, 40)):
                    if clipped >= count:
                        break

                    try:
                        btn = buttons.nth(i)

                        if btn.is_visible() and btn.is_enabled():
                            btn.scroll_into_view_if_needed()
                            pause(page, 800)
                            btn.click(force=True)
                            clipped += 1
                            log(f"✅ Clipped Homepage coupon #{clipped}")
                            pause(page, 2000)

                    except Exception as e:
                        log(f"⚠ Homepage coupon clip skipped index={i}: {e}")

            except Exception as e:
                log(f"⚠ Homepage coupon selector failed {selector}: {e}")

    try_clip_visible_buttons()

    if clipped >= count:
        log(f"🏁 Homepage coupon clipping done. Total clipped={clipped}")
        return clipped

    log("🔽 Small Homepage coupon scan only")

    for step in range(8):
        if clipped >= count:
            break

        current_y_before = safe_evaluate(page, "window.scrollY", default=0)
        if current_y_before is None:
            current_y_before = 0

        if current_y_before >= 5000:
            log("⚠ Reached safe Homepage coupon scan limit.")
            break

        try:
            page.mouse.wheel(0, 600)
        except Exception as e:
            log(f"⚠ Coupon scan scroll failed: {e}")
            break

        pause(page, 1200)

        current_y = safe_evaluate(page, "window.scrollY", default=current_y_before)
        log(f"⬇️ Homepage coupon scan scroll step {step + 1}: y={current_y}")

        try_clip_visible_buttons()

    log("🔼 Returning to top after Homepage coupon scan")

    for step in range(15):
        current_y = safe_evaluate(page, "window.scrollY", default=0)
        if current_y is None or current_y <= 0:
            break
        try:
            page.mouse.wheel(0, -900)
        except:
            break
        pause(page, 600)

    pause(page, 2500)

    log(f"🏁 Homepage coupon clipping done. Total clipped={clipped}")
    return clipped

# ---------------- ADD TO CART FROM HOMEPAGE ----------------
def add_to_cart_from_homepage(page):
    log("🛒 Searching Homepage for Add button")

    pause(page, 2000)

    add_span_selector = "span.btn-stppr-text.pl-2:has-text('Add')"

    add_button_fallbacks = [
        add_span_selector,
        "button:has-text('Add'):visible",
        "button[aria-label*='Add']:visible",
        "[data-qa*='add']:visible",
        "[aria-label*='Add to Cart']:visible"
    ]

    for sel in add_button_fallbacks:
        try:
            loc = page.locator(sel)
            count = loc.count()
            log(f"🔎 Homepage Add candidates for '{sel}': {count}")

            for i in range(min(count, 50)):
                try:
                    item = loc.nth(i)

                    if item.is_visible():
                        item.scroll_into_view_if_needed()
                        pause(page, 1000)

                        if sel == add_span_selector:
                            parent_button = item.locator("xpath=ancestor::button[1]")
                            if parent_button.count() > 0 and parent_button.first.is_visible():
                                parent_button.first.click(force=True)
                            else:
                                item.click(force=True)
                        else:
                            item.click(force=True)

                        log("✅ Clicked Add to Cart on Homepage")
                        pause(page, 8000)
                        return True

                except Exception as e:
                    log(f"⚠ Homepage Add click skipped index={i}: {e}")

        except Exception as e:
            log(f"⚠ Homepage Add selector failed '{sel}': {e}")

    log("❌ Could not find any Add button on Homepage")
    return False

# ---------------- SEARCH PRODUCT ----------------
def search_product(page, search_term=SEARCH_TERM):
    log(f"🔍 Searching product using search input: {search_term}")

    pause(page, 2000)

    search_selector = "input[data-qa='srch-inpt']"

    try:
        search_box = page.locator(search_selector)
        search_box.wait_for(state="visible", timeout=45000)

        pause(page, 1000)
        search_box.click(force=True)
        pause(page, 1000)

        search_box.fill("")
        pause(page, 500)
        search_box.fill(search_term)
        pause(page, 1500)

        page.keyboard.press("Enter")
        log("✅ Search submitted")

        pause(page, 10000)
        return True

    except Exception as e:
        log(f"❌ Search product failed: {e}")
        if is_crash_error(e):
            raise
        return False

def search_has_products(page):
    candidates = [
        "[data-qa='product-card']",
        "[data-qa='prd-itm']",
        "article:has(button:has-text('Add'))",
        "span.btn-stppr-text.pl-2:has-text('Add')",
        "button:has-text('Add')",
        "a[href*='/shop/product-details']",
        "a[href*='product-details']"
    ]

    for sel in candidates:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                log(f"✅ Products detected using selector: {sel} count={loc.count()}")
                return True
        except Exception as e:
            if is_crash_error(e):
                raise

    log("⚠ Could not confirm products; treating as NO products.")
    return False

def search_product_with_retries(page, search_term=SEARCH_TERM, min_rounds=MIN_SEARCH_ROUNDS, max_rounds=MAX_SEARCH_ROUNDS):
    for attempt in range(1, max_rounds + 1):
        log(f"🔁 Search attempt {attempt}/{max_rounds} for term='{search_term}'")

        submitted = search_product(page, search_term)
        if not submitted:
            log("⚠ Search was not submitted. Retrying.")
            continue

        pause(page, 3000)

        ok = search_has_products(page)

        if ok:
            log("✅ Search returned products.")
            return True

        if attempt < max_rounds:
            log("⚠ No products found. Returning homepage and retrying search...")
            pause(page, 5000)
            open_homepage(page)
            pause(page, 5000)

    log(f"❌ Search failed after {max_rounds} attempts.")
    return False

# ---------------- ADD TO CART FROM SEARCH RESULTS ----------------
def add_to_cart_from_search_results(page):
    log("🛒 Searching search-results page for Add button")

    pause(page, 2000)

    add_span_selector = "span.btn-stppr-text.pl-2:has-text('Add')"

    add_button_fallbacks = [
        add_span_selector,
        "button:has-text('Add'):visible",
        "button[aria-label*='Add']:visible",
        "[data-qa*='add']:visible",
        "[aria-label*='Add to Cart']:visible"
    ]

    for sel in add_button_fallbacks:
        try:
            loc = page.locator(sel)
            count = loc.count()
            log(f"🔎 Search results Add candidates for '{sel}': {count}")

            for i in range(min(count, 60)):
                try:
                    item = loc.nth(i)

                    if item.is_visible():
                        item.scroll_into_view_if_needed()
                        pause(page, 1200)

                        if sel == add_span_selector:
                            parent_button = item.locator("xpath=ancestor::button[1]")
                            if parent_button.count() > 0 and parent_button.first.is_visible():
                                parent_button.first.click(force=True)
                            else:
                                item.click(force=True)
                        else:
                            item.click(force=True)

                        log("✅ Clicked Add to Cart on search results")
                        pause(page, 8000)
                        return True

                except Exception as e:
                    log(f"⚠ Search Add click skipped index={i}: {e}")

        except Exception as e:
            log(f"⚠ Search Add selector failed '{sel}': {e}")
            if is_crash_error(e):
                raise

    log("❌ Could not find any Add button on search results")
    return False

# ---------------- PRODUCT CARD -> PDP ----------------
def open_product_details_from_search_results(page):
    log("🧾 Opening product details page from search results product card")

    pause(page, 4000)

    result = safe_evaluate(page, """
        () => {
            const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 &&
                       r.height > 0 &&
                       s.visibility !== 'hidden' &&
                       s.display !== 'none';
            };

            const blocked = (el) => {
                const text = (el.innerText || "").trim();
                const aria = el.getAttribute("aria-label") || "";
                const combined = `${text} ${aria}`.toLowerCase();
                return /add|cart|clip|coupon|quantity|increment|decrement|plus|minus|signin|sign in/.test(combined);
            };

            const links = Array.from(document.querySelectorAll("a[href*='product-details'], a[href*='/shop/product-details']"))
                .filter(el => visible(el) && !blocked(el));

            if (links.length > 0) {
                const target = links[0];
                target.scrollIntoView({ block: "center", inline: "center" });
                target.click();
                return {
                    clicked: true,
                    method: "product-details-link",
                    href: target.href,
                    text: (target.innerText || "").trim().slice(0, 200)
                };
            }

            const cards = Array.from(document.querySelectorAll(
                "[data-qa='product-card'], [data-qa='prd-itm'], article, div[class*='product-card'], div[class*='productCard']"
            )).filter(el => visible(el));

            for (const card of cards) {
                if (blocked(card)) continue;

                const link = card.querySelector("a[href]");
                if (link && visible(link) && !blocked(link)) {
                    link.scrollIntoView({ block: "center", inline: "center" });
                    link.click();
                    return {
                        clicked: true,
                        method: "card-link",
                        href: link.href,
                        text: (link.innerText || card.innerText || "").trim().slice(0, 200)
                    };
                }

                card.scrollIntoView({ block: "center", inline: "center" });
                card.click();
                return {
                    clicked: true,
                    method: "card-click",
                    text: (card.innerText || "").trim().slice(0, 200)
                };
            }

            return {
                clicked: false,
                reason: "NO_PRODUCT_CARD_OR_LINK_FOUND"
            };
        }
    """, default={"clicked": False})

    log(f"🧾 Product card/PDP click result: {result}")

    if result and result.get("clicked"):
        pause(page, 12000)

        try:
            page.wait_for_load_state("domcontentloaded", timeout=45000)
        except:
            log("⚠ PDP domcontentloaded timeout, continuing")

        current_url = page.url
        log(f"🔗 URL after product card click: {current_url}")

        pdp_detected = safe_evaluate(page, """
            () => {
                const text = document.body.innerText || "";
                return {
                    urlLooksPdp: location.href.includes("product-details"),
                    hasPdpBreadcrumb: /Categories|Dairy|Milk|Cream|Beverages/i.test(text),
                    hasProductTitle: !!document.querySelector("h1") || /Bestseller|Sponsored|Terms apply/i.test(text),
                    detected: location.href.includes("product-details") || !!document.querySelector("h1")
                };
            }
        """, default={"detected": False})

        log(f"🧾 PDP detection result: {pdp_detected}")

        if pdp_detected and pdp_detected.get("detected"):
            log("✅ Landed on Product Details Page")
            return True

    safe_screenshot(page, "pdp_not_opened.png")
    log("❌ Could not open Product Details Page from search results")
    return False

# ---------------- FIXED PDP ADD / INCREASE QUANTITY ----------------
def add_to_cart_from_pdp(page):
    log("🛒 Adding product to cart from PDP OR increasing quantity if already added")

    pause(page, 4000)

    scroll_positions = [
        {"name": "current/top area", "scroll": 0},
        {"name": "middle area", "scroll": 700},
        {"name": "lower area", "scroll": 1200},
        {"name": "back near top", "scroll": -1200},
    ]

    for scan_round, pos in enumerate(scroll_positions, start=1):
        log(f"🔎 PDP add/increase scan round {scan_round}: {pos['name']}")

        try:
            if pos["scroll"] != 0:
                page.mouse.wheel(0, pos["scroll"])
                pause(page, 2500)
        except Exception as e:
            log(f"⚠ PDP scan scroll skipped/failed: {e}")

        result = safe_evaluate(page, """
            () => {
                const normalize = (txt) => {
                    return (txt || "")
                        .replace(/\\u00a0/g, " ")
                        .replace(/\\s+/g, " ")
                        .trim();
                };

                const visible = (el) => {
                    if (!el || !el.getBoundingClientRect) return false;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 &&
                           r.height > 0 &&
                           s.visibility !== "hidden" &&
                           s.display !== "none" &&
                           Number(s.opacity || "1") > 0;
                };

                const isDisabled = (el) => {
                    if (!el) return true;
                    return el.disabled
                        || el.getAttribute("disabled") !== null
                        || el.getAttribute("aria-disabled") === "true"
                        || String(el.className || "").toLowerCase().includes("disabled");
                };

                const getInfo = (el) => {
                    const text = normalize(el.innerText || el.textContent || "");
                    const aria = normalize(el.getAttribute("aria-label") || "");
                    const title = normalize(el.getAttribute("title") || "");
                    const id = normalize(el.id || "");
                    const cls = normalize(String(el.className || ""));
                    const qa = normalize(el.getAttribute("data-qa") || "");
                    const testid = normalize(el.getAttribute("data-testid") || "");
                    const name = normalize(el.getAttribute("name") || "");
                    const value = normalize(el.getAttribute("value") || "");

                    const combined = normalize([
                        text,
                        aria,
                        title,
                        id,
                        cls,
                        qa,
                        testid,
                        name,
                        value
                    ].join(" "));

                    return {
                        text,
                        aria,
                        title,
                        id,
                        cls,
                        qa,
                        testid,
                        name,
                        value,
                        combined
                    };
                };

                const blocked = (combined) => {
                    const c = combined.toLowerCase();

                    return /share feedback|contact us|privacy|terms|search products|search|account|sign in|categories|dairy, eggs|beverages|bread|bakery|baby care|view all|filter|sort|sponsored|coupon|clip|promo|address|delivery address|pickup address|checkout|continue|place order|cart icon|shopping cart/.test(c);
                };

                const fireClick = (el) => {
                    el.scrollIntoView({ block: "center", inline: "center" });
                    el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, cancelable: true, view: window }));
                    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
                    el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
                    el.click();
                };

                const clickables = Array.from(document.querySelectorAll(
                    "button, [role='button'], a, span, div"
                )).filter(el => visible(el) && !isDisabled(el));

                const debugVisible = clickables.map(el => {
                    const info = getInfo(el);
                    return {
                        tag: el.tagName,
                        text: info.text.slice(0, 120),
                        aria: info.aria || null,
                        id: info.id || null,
                        cls: info.cls.slice(0, 160),
                        qa: info.qa || null,
                        testid: info.testid || null
                    };
                }).slice(0, 80);

                // 1. Try normal Add / Add to Cart.
                const addCandidates = [];

                for (const el of clickables) {
                    const info = getInfo(el);
                    const combined = info.combined;

                    if (!combined) continue;
                    if (blocked(combined)) continue;

                    const looksLikeAdd =
                        /\\badd\\b/i.test(combined)
                        || /add\\s*to\\s*cart/i.test(combined)
                        || /add\\s*to\\s*basket/i.test(combined);

                    if (!looksLikeAdd) continue;

                    let clickable =
                        el.closest("button") ||
                        el.closest("[role='button']") ||
                        el;

                    if (!clickable || !visible(clickable) || isDisabled(clickable)) continue;

                    const parentInfo = getInfo(clickable);

                    if (blocked(parentInfo.combined)) continue;

                    addCandidates.push({
                        el: clickable,
                        score: parentInfo.combined.toLowerCase().includes("add to cart") ? 1 : 2
                    });
                }

                addCandidates.sort((a, b) => a.score - b.score);

                if (addCandidates.length > 0) {
                    const target = addCandidates[0].el;
                    const info = getInfo(target);
                    fireClick(target);

                    return {
                        clicked: true,
                        action: "ADD_TO_CART",
                        text: info.text,
                        aria: info.aria,
                        cls: info.cls,
                        qa: info.qa,
                        testid: info.testid
                    };
                }

                // 2. Product may already be added. Try plus / increment / increase quantity buttons.
                const incrementCandidates = [];

                const allButtons = Array.from(document.querySelectorAll(
                    "button, [role='button'], span, div, svg"
                )).filter(el => visible(el));

                for (const el of allButtons) {
                    const info = getInfo(el);
                    const combined = info.combined;

                    if (blocked(combined)) continue;

                    const textIsPlus =
                        info.text === "+"
                        || info.text === "＋"
                        || info.aria === "+"
                        || info.title === "+";

                    const looksLikeIncrement =
                        /increase/i.test(combined)
                        || /increment/i.test(combined)
                        || /add\\s*one/i.test(combined)
                        || /add\\s*1/i.test(combined)
                        || /quantity\\s*(increase|increment|plus|add)/i.test(combined)
                        || /(increase|increment|plus).*quantity/i.test(combined)
                        || /stepper.*(plus|increase|increment)/i.test(combined)
                        || /(plus|increment|increase)/i.test(combined)
                        || textIsPlus;

                    const classLooksPlus =
                        /plus|increment|increase|add/i.test(info.cls)
                        || /plus|increment|increase|add/i.test(info.qa)
                        || /plus|increment|increase|add/i.test(info.testid)
                        || /plus|increment|increase|add/i.test(info.id);

                    if (!looksLikeIncrement && !classLooksPlus) continue;

                    let clickable =
                        el.closest("button") ||
                        el.closest("[role='button']") ||
                        el.closest("[data-qa*='increment']") ||
                        el.closest("[data-testid*='increment']") ||
                        el.closest("[data-qa*='plus']") ||
                        el.closest("[data-testid*='plus']") ||
                        el;

                    if (!clickable || !visible(clickable) || isDisabled(clickable)) continue;

                    const clickableInfo = getInfo(clickable);
                    const clickableCombined = clickableInfo.combined;

                    if (blocked(clickableCombined)) continue;

                    const rect = clickable.getBoundingClientRect();
                    if (rect.width > 350 || rect.height > 200) continue;

                    incrementCandidates.push({
                        el: clickable,
                        score:
                            /increase|increment/i.test(clickableCombined) ? 1 :
                            /plus/i.test(clickableCombined) ? 2 :
                            textIsPlus ? 3 :
                            4
                    });
                }

                incrementCandidates.sort((a, b) => a.score - b.score);

                if (incrementCandidates.length > 0) {
                    const target = incrementCandidates[0].el;
                    const info = getInfo(target);
                    fireClick(target);

                    return {
                        clicked: true,
                        action: "INCREASE_QUANTITY_ALREADY_ADDED",
                        text: info.text,
                        aria: info.aria,
                        cls: info.cls,
                        qa: info.qa,
                        testid: info.testid
                    };
                }

                const bodyText = normalize(document.body.innerText || "");
                const hasQtyState =
                    /quantity/i.test(bodyText)
                    || /in cart/i.test(bodyText)
                    || /added/i.test(bodyText);

                return {
                    clicked: false,
                    reason: hasQtyState ? "QTY_STATE_DETECTED_BUT_INCREMENT_NOT_FOUND" : "NO_ADD_OR_INCREMENT_FOUND",
                    visibleButtons: debugVisible
                };
            }
        """, default={"clicked": False, "reason": "JS_EVALUATE_FAILED"})

        log(f"🛒 PDP add/increase result round {scan_round}: {result}")

        if result and result.get("clicked"):
            action = result.get("action")
            pause(page, 10000)

            if action == "ADD_TO_CART":
                log("✅ Added product to cart from PDP")
            elif action == "INCREASE_QUANTITY_ALREADY_ADDED":
                log("✅ Product was already added. Increased quantity from PDP")
            else:
                log("✅ PDP cart action completed")

            return True

    log("🔁 Trying Playwright fallback selectors for PDP Add/Increase")

    fallback_selectors = [
        "button:has-text('Add'):visible",
        "button:has-text('Add to Cart'):visible",
        "button:has-text('Add to cart'):visible",
        "button[aria-label*='Add']:visible",
        "button[aria-label*='Add to Cart']:visible",
        "button[aria-label*='Add to cart']:visible",
        "span.btn-stppr-text.pl-2:has-text('Add')",

        "button[aria-label*='Increase']:visible",
        "button[aria-label*='increase']:visible",
        "button[aria-label*='Increment']:visible",
        "button[aria-label*='increment']:visible",
        "button[aria-label*='plus']:visible",
        "button[aria-label*='Plus']:visible",
        "button[aria-label*='Add one']:visible",
        "button[aria-label*='add one']:visible",
        "button:has-text('+'):visible",
        "[data-qa*='increment']:visible",
        "[data-qa*='increase']:visible",
        "[data-qa*='plus']:visible",
        "[data-testid*='increment']:visible",
        "[data-testid*='increase']:visible",
        "[data-testid*='plus']:visible",
        "[class*='increment']:visible",
        "[class*='increase']:visible",
        "[class*='plus']:visible"
    ]

    clicked = click_first_visible_button(
        page,
        fallback_selectors,
        "PDP Add to Cart / Increase Quantity",
        wait_after_ms=10000
    )

    if clicked:
        log("✅ PDP Add/Increase completed using fallback selector")
        return True

    safe_screenshot(page, "pdp_add_or_increase_failed.png")
    log("❌ Could not add product or increase quantity from PDP")
    return False

# ---------------- PDP CATEGORIES -> AISLES -> BEVERAGES ----------------
def go_to_categories_from_pdp(page):
    log("🧭 Clicking Categories breadcrumb/link from PDP")

    pause(page, 3000)

    category_selectors = [
        "a[href*='/shop/aisles.html']:has-text('Categories')",
        "a:has-text('Categories')",
        "nav[aria-label='Breadcrumb'] a:has-text('Categories')",
        "[data-testid='breadcrumbs'] a:has-text('Categories')",
        "[class*='breadcrumbs'] a:has-text('Categories')"
    ]

    for sel in category_selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            log(f"🔎 Categories breadcrumb candidates for '{sel}': {count}")

            for i in range(min(count, 5)):
                item = loc.nth(i)
                if item.is_visible():
                    item.scroll_into_view_if_needed()
                    pause(page, 1000)
                    item.click(force=True)
                    pause(page, 12000)

                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=45000)
                    except:
                        log("⚠ Categories page domcontentloaded timeout, continuing")

                    log(f"🔗 URL after Categories click: {page.url}")

                    detected = safe_evaluate(page, """
                        () => {
                            const text = document.body.innerText || "";
                            return {
                                urlLooksAisles: location.href.includes("/shop/aisles"),
                                hasCategoriesHeading: /Categories/i.test(text),
                                hasBeverages: /BEVERAGES|Beverages/i.test(text),
                                detected: location.href.includes("/shop/aisles") || (/Categories/i.test(text) && /Beverages/i.test(text))
                            };
                        }
                    """, default={"detected": False})

                    log(f"🧭 Aisles/Categories detection result: {detected}")

                    if detected and detected.get("detected"):
                        log("✅ Landed on Aisles/Categories page")
                        return True

        except Exception as e:
            log(f"⚠ Categories selector failed {sel}: {e}")
            if is_crash_error(e):
                raise

    try:
        fallback_url = f"{BASE_URL}shop/aisles.html"
        log(f"🧭 Categories click failed. Forcing aisles URL: {fallback_url}")
        page.goto(fallback_url, wait_until="commit", timeout=60000)
        pause(page, 12000)
        log(f"🔗 URL after forced aisles URL: {page.url}")
        return True
    except Exception as e:
        log(f"❌ Forced aisles URL failed: {e}")
        safe_screenshot(page, "categories_page_not_opened.png")
        return False

def click_beverages_on_aisles_page(page):
    log("🥤 Clicking Beverages tile on Aisles/Categories page")

    pause(page, 5000)

    beverage_selectors = [
        "a[href*='/shop/aisles/beverages']:visible",
        "a[aria-label*='Beverages']:visible",
        "a:has-text('BEVERAGES'):visible",
        "a:has-text('Beverages'):visible",
        "h3:has-text('BEVERAGES')",
        "h3:has-text('Beverages')"
    ]

    for sel in beverage_selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            log(f"🔎 Beverages candidates for '{sel}': {count}")

            for i in range(min(count, 10)):
                item = loc.nth(i)

                if item.is_visible():
                    item.scroll_into_view_if_needed()
                    pause(page, 1000)

                    if sel.startswith("h3"):
                        parent_link = item.locator("xpath=ancestor::a[1]")
                        if parent_link.count() > 0 and parent_link.first.is_visible():
                            parent_link.first.click(force=True)
                        else:
                            item.click(force=True)
                    else:
                        item.click(force=True)

                    pause(page, 12000)

                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=45000)
                    except:
                        log("⚠ Beverages page domcontentloaded timeout, continuing")

                    log(f"🔗 URL after Beverages click: {page.url}")

                    detected = safe_evaluate(page, """
                        () => {
                            const text = document.body.innerText || "";
                            return {
                                urlLooksBeverages: location.href.toLowerCase().includes("beverages"),
                                hasBeveragesText: /BEVERAGES|Beverages/i.test(text),
                                detected: location.href.toLowerCase().includes("beverages") || /BEVERAGES|Beverages/i.test(text)
                            };
                        }
                    """, default={"detected": False})

                    log(f"🥤 Beverages page detection result: {detected}")

                    if detected and detected.get("detected"):
                        log("✅ Landed on Beverages page")
                        return True

        except Exception as e:
            log(f"⚠ Beverages selector failed {sel}: {e}")
            if is_crash_error(e):
                raise

    result = safe_evaluate(page, """
        () => {
            const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 &&
                       r.height > 0 &&
                       s.visibility !== 'hidden' &&
                       s.display !== 'none';
            };

            const links = Array.from(document.querySelectorAll("a[href]"))
                .filter(el => visible(el))
                .filter(el => {
                    const href = el.href || "";
                    const text = el.innerText || "";
                    const aria = el.getAttribute("aria-label") || "";
                    return /beverages/i.test(`${href} ${text} ${aria}`);
                });

            if (links.length === 0) {
                return { clicked: false, reason: "NO_BEVERAGES_LINK" };
            }

            const target = links[0];
            target.scrollIntoView({ block: "center", inline: "center" });
            target.click();

            return {
                clicked: true,
                href: target.href,
                text: (target.innerText || "").trim(),
                aria: target.getAttribute("aria-label")
            };
        }
    """, default={"clicked": False})

    log(f"🥤 Beverages JS click result: {result}")

    if result and result.get("clicked"):
        pause(page, 12000)
        log("✅ Landed on Beverages page using JS fallback")
        return True

    safe_screenshot(page, "beverages_not_clicked.png")
    log("❌ Could not click Beverages tile")
    return False

    # ---------------- BEVERAGES DEALS CAROUSEL COUPON CLIP ----------------
def clip_coupons_from_beverages_deals_carousel(page, count=2, max_rounds=6):
    log(f"🏷️ Scanning Beverages page deals carousel and clipping {count} coupons")

    pause(page, 5000)

    clipped = 0

    def try_clip_visible_coupons(context_name="visible-scan"):
        nonlocal clipped

        result = safe_evaluate(page, f"""
            () => {{
                const normalize = (txt) => {{
                    return (txt || "")
                        .replace(/\\u00a0/g, " ")
                        .replace(/\\s+/g, " ")
                        .trim();
                }};

                const visible = (el) => {{
                    if (!el || !el.getBoundingClientRect) return false;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 &&
                           r.height > 0 &&
                           s.visibility !== "hidden" &&
                           s.display !== "none" &&
                           Number(s.opacity || "1") > 0;
                }};

                const isDisabled = (el) => {{
                    if (!el) return true;
                    return el.disabled ||
                           el.getAttribute("disabled") !== null ||
                           el.getAttribute("aria-disabled") === "true" ||
                           String(el.className || "").toLowerCase().includes("disabled");
                }};

                const blocked = (txt) => {{
                    const t = normalize(txt).toLowerCase();
                    return /unclip|clipped|added|remove|checkout|cart|continue|sign in|account|privacy|terms/.test(t);
                }};

                const getCombined = (el) => {{
                    return normalize([
                        el.innerText || "",
                        el.textContent || "",
                        el.getAttribute("aria-label") || "",
                        el.getAttribute("title") || "",
                        el.getAttribute("data-qa") || "",
                        el.getAttribute("data-testid") || "",
                        el.id || "",
                        String(el.className || "")
                    ].join(" "));
                }};

                const fireClick = (el) => {{
                    el.scrollIntoView({{ block: "center", inline: "center" }});
                    el.dispatchEvent(new MouseEvent("mouseover", {{ bubbles: true, cancelable: true, view: window }}));
                    el.dispatchEvent(new MouseEvent("mousedown", {{ bubbles: true, cancelable: true, view: window }}));
                    el.dispatchEvent(new MouseEvent("mouseup", {{ bubbles: true, cancelable: true, view: window }}));
                    el.click();
                }};

                const clickables = Array.from(document.querySelectorAll(
                    "button, [role='button'], a, span, div"
                )).filter(el => visible(el) && !isDisabled(el));

                const candidates = [];

                for (const el of clickables) {{
                    const combined = getCombined(el);
                    if (!combined) continue;

                    const looksLikeClip =
                        /\\bclip\\b/i.test(combined) ||
                        /clip deal/i.test(combined) ||
                        /clip coupon/i.test(combined) ||
                        /clip offer/i.test(combined) ||
                        /load to card/i.test(combined) ||
                        /add coupon/i.test(combined);

                    if (!looksLikeClip) continue;
                    if (blocked(combined)) continue;

                    let clickable =
                        el.closest("button") ||
                        el.closest("[role='button']") ||
                        el.closest("a") ||
                        el;

                    if (!clickable || !visible(clickable) || isDisabled(clickable)) continue;

                    const clickableCombined = getCombined(clickable);
                    if (blocked(clickableCombined)) continue;

                    const rect = clickable.getBoundingClientRect();

                    if (rect.width > 500 || rect.height > 300) continue;

                    candidates.push({{
                        el: clickable,
                        text: normalize(clickable.innerText || el.innerText || ""),
                        aria: clickable.getAttribute("aria-label"),
                        cls: String(clickable.className || "").slice(0, 160),
                        qa: clickable.getAttribute("data-qa"),
                        testid: clickable.getAttribute("data-testid"),
                        combined: clickableCombined.slice(0, 220)
                    }});
                }}

                if (candidates.length === 0) {{
                    return {{
                        clicked: false,
                        reason: "NO_VISIBLE_CLIP_BUTTONS",
                        context: "{context_name}",
                        visibleClipLike: clickables
                            .map(el => getCombined(el))
                            .filter(t => /clip|coupon|deal|offer|save/i.test(t))
                            .slice(0, 20)
                    }};
                }}

                const targetInfo = candidates[0];
                fireClick(targetInfo.el);

                return {{
                    clicked: true,
                    context: "{context_name}",
                    text: targetInfo.text,
                    aria: targetInfo.aria,
                    cls: targetInfo.cls,
                    qa: targetInfo.qa,
                    testid: targetInfo.testid,
                    combined: targetInfo.combined,
                    totalCandidates: candidates.length
                }};
            }}
        """, default={"clicked": False, "reason": "JS_EVALUATE_FAILED"})

        log(f"🏷️ Beverages coupon clip attempt result [{context_name}]: {result}")

        if result and result.get("clicked"):
            clipped += 1
            pause(page, 4000)
            log(f"✅ Clipped Beverages deals carousel coupon #{clipped}/{count}")
            return True

        return False

    def scroll_to_deals_or_coupon_area():
        log("🔎 Scrolling Beverages page to locate deals/coupon carousel")

        result = safe_evaluate(page, """
            () => {
                const normalize = (txt) => {
                    return (txt || "")
                        .replace(/\\u00a0/g, " ")
                        .replace(/\\s+/g, " ")
                        .trim();
                };

                const visible = (el) => {
                    if (!el || !el.getBoundingClientRect) return false;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 &&
                           r.height > 0 &&
                           s.visibility !== "hidden" &&
                           s.display !== "none";
                };

                const candidates = Array.from(document.querySelectorAll(
                    "h1, h2, h3, h4, section, div"
                )).filter(el => {
                    if (!visible(el)) return false;

                    const text = normalize(el.innerText || "");
                    if (!text) return false;
                    if (text.length > 900) return false;

                    return /deals|coupon|coupons|for you|save|weekly ad|offers/i.test(text);
                });

                if (candidates.length > 0) {
                    candidates[0].scrollIntoView({ block: "center", inline: "center" });

                    return {
                        found: true,
                        text: normalize(candidates[0].innerText || "").slice(0, 250),
                        tag: candidates[0].tagName,
                        cls: String(candidates[0].className || "").slice(0, 160)
                    };
                }

                return {
                    found: false,
                    reason: "NO_DEALS_AREA_FOUND"
                };
            }
        """, default={"found": False})

        log(f"🔎 Scroll to Beverages deals/coupon area result: {result}")

        if result and result.get("found"):
            pause(page, 3000)
            return True

        return False

    def click_carousel_next_if_present(round_num):
        log(f"➡️ Trying Beverages deals carousel next/right button round {round_num}")

        result = safe_evaluate(page, """
            () => {
                const normalize = (txt) => {
                    return (txt || "")
                        .replace(/\\u00a0/g, " ")
                        .replace(/\\s+/g, " ")
                        .trim();
                };

                const visible = (el) => {
                    if (!el || !el.getBoundingClientRect) return false;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 &&
                           r.height > 0 &&
                           s.visibility !== "hidden" &&
                           s.display !== "none";
                };

                const isDisabled = (el) => {
                    if (!el) return true;
                    return el.disabled ||
                           el.getAttribute("disabled") !== null ||
                           el.getAttribute("aria-disabled") === "true" ||
                           String(el.className || "").toLowerCase().includes("disabled");
                };

                const getCombined = (el) => {
                    return normalize([
                        el.innerText || "",
                        el.getAttribute("aria-label") || "",
                        el.getAttribute("title") || "",
                        el.getAttribute("data-qa") || "",
                        el.getAttribute("data-testid") || "",
                        el.id || "",
                        String(el.className || "")
                    ].join(" "));
                };

                const buttons = Array.from(document.querySelectorAll(
                    "button, [role='button'], a"
                )).filter(el => visible(el) && !isDisabled(el));

                const nextButtons = buttons.filter(el => {
                    const c = getCombined(el).toLowerCase();

                    const looksNext =
                        /next|right|carousel-next|slick-next|swiper-button-next|arrow-right|see more|show more/.test(c);

                    const blocked =
                        /next available dates|continue|checkout|cart|account|sign in|privacy|terms/.test(c);

                    return looksNext && !blocked;
                });

                if (nextButtons.length === 0) {
                    return {
                        clicked: false,
                        reason: "NO_CAROUSEL_NEXT_BUTTON"
                    };
                }

                const target = nextButtons[0];
                target.scrollIntoView({ block: "center", inline: "center" });
                target.click();

                return {
                    clicked: true,
                    text: normalize(target.innerText || ""),
                    aria: target.getAttribute("aria-label"),
                    cls: String(target.className || "").slice(0, 160),
                    qa: target.getAttribute("data-qa"),
                    testid: target.getAttribute("data-testid")
                };
            }
        """, default={"clicked": False})

        log(f"➡️ Beverages carousel next result: {result}")

        if result and result.get("clicked"):
            pause(page, 3000)
            return True

        return False

    while clipped < count:
        clicked = try_clip_visible_coupons("initial-visible")
        if not clicked:
            break

    if clipped >= count:
        log(f"🏁 Beverages deals coupon clipping completed. clipped={clipped}")
        return clipped

    scroll_to_deals_or_coupon_area()

    while clipped < count:
        clicked = try_clip_visible_coupons("after-scroll-to-deals")
        if not clicked:
            break

    if clipped >= count:
        log(f"🏁 Beverages deals coupon clipping completed. clipped={clipped}")
        return clipped

    for round_num in range(1, max_rounds + 1):
        if clipped >= count:
            break

        log(f"🔁 Beverages deals carousel scan round {round_num}/{max_rounds}")

        while clipped < count:
            clicked = try_clip_visible_coupons(f"carousel-round-{round_num}")
            if not clicked:
                break

        if clipped >= count:
            break

        next_clicked = click_carousel_next_if_present(round_num)

        if next_clicked:
            continue

        try:
            page.mouse.wheel(0, 700)
            pause(page, 2500)
            log("⬇️ Scrolled Beverages page while scanning for deals carousel coupons")
        except Exception as e:
            log(f"⚠ Beverages coupon scan scroll failed: {e}")
            break

    log("🔼 Returning toward top after Beverages deals coupon scan")

    for _ in range(12):
        current_y = safe_evaluate(page, "window.scrollY", default=0)

        if current_y is None or current_y <= 0:
            break

        try:
            page.mouse.wheel(0, -900)
        except:
            break

        pause(page, 500)

    pause(page, 2500)

    log(f"🏁 Beverages deals coupon clipping completed. Total clipped={clipped}")
    return clipped

# ---------------- CART ICON ----------------
def click_cart_icon(page):
    log("🛒 Clicking cart icon")

    cart_selectors = [
        "[data-qa='hdr-cart-icn']",
        "a[aria-label*='Cart']",
        "button[aria-label*='Cart']",
        "a[aria-label*='cart']",
        "button[aria-label*='cart']",
        "a[href*='/cart']",
        "button:has-text('Cart')"
    ]

    for sel in cart_selectors:
        try:
            cart = page.locator(sel)
            count = cart.count()
            log(f"🔎 Cart icon candidates for '{sel}': {count}")

            if count > 0:
                for i in range(min(count, 5)):
                    try:
                        item = cart.nth(i)
                        if item.is_visible():
                            item.scroll_into_view_if_needed()
                            pause(page, 1000)

                            old_url = page.url
                            item.click(force=True)
                            pause(page, 12000)

                            log(f"✅ Cart icon clicked using {sel}")
                            log(f"🔗 URL before cart click: {old_url}")
                            log(f"🔗 URL after cart click: {page.url}")

                            return True
                    except Exception as e:
                        log(f"⚠ Cart candidate skipped index={i}: {e}")

        except Exception as e:
            log(f"⚠ Cart selector failed {sel}: {e}")
            if is_crash_error(e):
                raise

    log("❌ Cart icon not found")
    return False

# ---------------- CART / CHECKOUT ----------------
def wait_for_cart_page(page):
    log("⏳ Waiting for cart page/cart drawer/content")

    pause(page, 5000)

    cart_ready_selectors = [
        "button:has-text('Continue to Checkout'):visible",
        "button:has-text('Continue to checkout'):visible",
        "a:has-text('Continue to Checkout'):visible",
        "a:has-text('Continue to checkout'):visible",
        "button:has-text('Checkout'):visible",
        "a:has-text('Checkout'):visible",
        "button:has-text('View cart'):visible",
        "a:has-text('View cart'):visible",
        "button:has-text('View Cart'):visible",
        "a:has-text('View Cart'):visible",
        "[data-qa*='checkout']:visible",
        "[data-testid*='checkout']:visible",
        "[data-qa*='cart']:visible",
        "[data-testid*='cart']:visible",
        "text=Shopping Cart",
        "text=Cart"
    ]

    for sel in cart_ready_selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            log(f"🔎 Cart readiness check '{sel}' count={count}")

            if count > 0:
                for i in range(min(count, 5)):
                    try:
                        item = loc.nth(i)
                        if item.is_visible():
                            log(f"✅ Cart/cart drawer detected using: {sel}")
                            pause(page, 3000)
                            return True
                    except:
                        pass
        except Exception as e:
            log(f"⚠ Cart readiness selector failed {sel}: {e}")

    log("⚠ Cart content not detected strongly")
    return False

def force_open_cart_page(page):
    cart_urls = [
        f"{BASE_URL}cart",
        f"{BASE_URL}cart/",
        f"{BASE_URL}checkout/cart"
    ]

    for url in cart_urls:
        try:
            log(f"🛒 Forcing cart page open: {url}")
            page.goto(url, wait_until="commit", timeout=60000)
            pause(page, 10000)

            if wait_for_cart_page(page):
                log(f"✅ Cart page opened using forced URL: {url}")
                return True

        except Exception as e:
            log(f"⚠ Forced cart URL failed {url}: {e}")
            if is_crash_error(e):
                raise

    log("❌ Could not force open cart page")
    return False

def click_view_cart_if_present(page):
    log("🛒 Checking if mini-cart has View Cart button")

    view_cart_selectors = [
        "button:has-text('View cart'):visible",
        "a:has-text('View cart'):visible",
        "button:has-text('View Cart'):visible",
        "a:has-text('View Cart'):visible",
        "button[aria-label*='View cart']:visible",
        "a[aria-label*='View cart']:visible",
        "[data-qa*='view-cart']:visible",
        "[data-testid*='view-cart']:visible",
        "a[href*='/cart']:visible"
    ]

    clicked = click_first_visible_button(
        page,
        view_cart_selectors,
        "View Cart",
        wait_after_ms=10000
    )

    if clicked:
        log("✅ View Cart clicked")
        return True

    log("ℹ️ View Cart button not found")
    return False

def click_checkout_button(page):
    log("🧾 Trying to click Checkout / Continue to Checkout button")

    pause(page, 5000)

    js_clicked = safe_evaluate(page, """
        () => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 &&
                       r.height > 0 &&
                       s.visibility !== 'hidden' &&
                       s.display !== 'none';
            };

            const isDisabled = (el) => {
                return el.disabled
                    || el.getAttribute('aria-disabled') === 'true'
                    || String(el.className || '').toLowerCase().includes('disabled');
            };

            const clickables = Array.from(document.querySelectorAll("button, a, [role='button']"))
                .filter(el => visible(el) && !isDisabled(el));

            const candidates = clickables.filter(el => {
                const text = (el.innerText || "").trim();
                const aria = el.getAttribute("aria-label") || "";
                const combined = `${text} ${aria}`;

                return /continue\\s*to\\s*checkout/i.test(combined)
                    || /^\\s*checkout\\s*$/i.test(text)
                    || /start\\s*checkout/i.test(combined);
            });

            if (candidates.length === 0) {
                return {
                    clicked: false,
                    reason: "NO_CHECKOUT_OR_CONTINUE_TO_CHECKOUT"
                };
            }

            const preferred =
                candidates.find(el => /continue\\s*to\\s*checkout/i.test((el.innerText || "") + " " + (el.getAttribute("aria-label") || "")))
                || candidates.find(el => /^\\s*checkout\\s*$/i.test((el.innerText || "").trim()))
                || candidates[0];

            preferred.scrollIntoView({ block: "center", inline: "center" });
            preferred.click();

            return {
                clicked: true,
                text: (preferred.innerText || "").trim(),
                aria: preferred.getAttribute("aria-label"),
                cls: preferred.className
            };
        }
    """, default={"clicked": False})

    log(f"🧾 JS Checkout click result: {js_clicked}")

    if js_clicked and js_clicked.get("clicked"):
        pause(page, 12000)
        return True

    checkout_selectors = [
        "button:has-text('Continue to Checkout'):visible",
        "button:has-text('Continue to checkout'):visible",
        "a:has-text('Continue to Checkout'):visible",
        "a:has-text('Continue to checkout'):visible",
        "button:has-text('Checkout'):visible",
        "a:has-text('Checkout'):visible",
        "button:has-text('Continue Checkout'):visible",
        "a:has-text('Continue Checkout'):visible",
        "button:has-text('Start checkout'):visible",
        "a:has-text('Start checkout'):visible",
        "button[aria-label*='Continue to Checkout']:visible",
        "button[aria-label*='Continue to checkout']:visible",
        "a[aria-label*='Continue to Checkout']:visible",
        "a[aria-label*='Continue to checkout']:visible",
        "button[aria-label*='Checkout']:visible",
        "a[aria-label*='Checkout']:visible",
        "[data-qa*='checkout']:visible",
        "[data-testid*='checkout']:visible",
        "[class*='checkout']:visible"
    ]

    clicked = click_first_visible_button(
        page,
        checkout_selectors,
        "Checkout / Continue to Checkout",
        wait_after_ms=12000
    )

    if clicked:
        return True

    log("⚠ Checkout not found directly. Trying View Cart first.")
    view_cart_clicked = click_view_cart_if_present(page)

    if view_cart_clicked:
        clicked = click_first_visible_button(
            page,
            checkout_selectors,
            "Checkout after View Cart",
            wait_after_ms=12000
        )
        if clicked:
            return True

    log("⚠ Checkout still not found. Forcing cart page URL.")
    forced = force_open_cart_page(page)

    if forced:
        clicked = click_first_visible_button(
            page,
            checkout_selectors,
            "Checkout after forced cart page",
            wait_after_ms=12000
        )
        if clicked:
            return True

    safe_screenshot(page, "checkout_button_not_found.png")
    raise Exception("Checkout / Continue to Checkout button not found/clickable")

# ---------------- TIMESLOT ----------------
def click_next_available_dates(page):
    log("📅 Checking Next Available Dates")

    result = safe_evaluate(page, """
        () => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            };

            const isDisabled = (el) => {
                return el.disabled
                    || el.getAttribute('aria-disabled') === 'true'
                    || String(el.className || '').toLowerCase().includes('disabled');
            };

            const buttons = Array.from(document.querySelectorAll("button,[role='button']"))
                .filter(el => visible(el) && !isDisabled(el));

            const next = buttons.find(el => {
                const text = (el.innerText || "").trim();
                const aria = el.getAttribute("aria-label") || "";
                const cls = el.className || "";
                const combined = `${text} ${aria} ${cls}`;
                return /next available dates|next-button/i.test(combined);
            });

            if (!next) {
                return { clicked: false, reason: "NO_NEXT_AVAILABLE_DATES" };
            }

            next.scrollIntoView({ block: "center", inline: "center" });
            next.click();

            return {
                clicked: true,
                text: (next.innerText || "").trim(),
                aria: next.getAttribute("aria-label"),
                cls: next.className
            };
        }
    """, default={"clicked": False})

    log(f"📅 Next Available Dates result: {result}")

    if result and result.get("clicked"):
        pause(page, 5000)
        return True

    return False

def click_more_times_if_present(page, max_clicks=5):
    log("🔽 Checking/expanding More times")

    clicked_any = False

    for attempt in range(1, max_clicks + 1):
        try:
            more_times = page.locator("button:has-text('More times'):visible")

            if more_times.count() > 0 and more_times.first.is_visible() and more_times.first.is_enabled():
                log(f"🔽 Clicking More times attempt {attempt}/{max_clicks}")
                more_times.first.scroll_into_view_if_needed()
                pause(page, 800)
                more_times.first.click(force=True)
                clicked_any = True
                pause(page, 2500)
            else:
                log("ℹ️ More times not visible/enabled anymore")
                break

        except Exception as e:
            log(f"ℹ️ More times not clicked/not needed: {e}")
            break

    return clicked_any

def select_date_by_index(page, index):
    result = safe_evaluate(page, f"""
        () => {{
            const visible = (el) => {{
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            }};

            const isDisabled = (el) => {{
                return el.getAttribute('aria-disabled') === 'true'
                    || String(el.className || '').toLowerCase().includes('disabled')
                    || el.getAttribute('disabled') !== null;
            }};

            let tiles = Array.from(document.querySelectorAll(
                "div.date-tile[role='button'], div[class*='date-tile'][role='button'], [class*='date-tile'][role='button']"
            )).filter(el => visible(el) && !isDisabled(el));

            if (tiles.length === 0) {{
                tiles = Array.from(document.querySelectorAll("[role='button']"))
                    .filter(el => visible(el) && !isDisabled(el))
                    .filter(el => /Today|Mon|Tue|Wed|Thu|Fri|Sat|Sun|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec/i.test(el.innerText || ''));
            }}

            const texts = tiles.map(el => (el.innerText || '').trim());

            if (tiles.length === 0) {{
                return {{ clicked: false, reason: "NO_DATE_TILES", texts }};
            }}

            const idx = Math.min({index}, tiles.length - 1);
            const target = tiles[idx];

            target.scrollIntoView({{ block: "center", inline: "center" }});
            target.click();

            return {{
                clicked: true,
                index: idx,
                text: (target.innerText || '').trim(),
                total: tiles.length,
                allTexts: texts
            }};
        }}
    """, default={"clicked": False})

    log(f"📅 Date selection result index={index}: {result}")

    if result and result.get("clicked"):
        pause(page, 5000)
        return True

    return False

def select_next_available_time_slot(page):
    log("🕒 Selecting preferred available timeslot: 1 PM, else next")

    click_more_times_if_present(page, max_clicks=5)

    result = safe_evaluate(page, """
        () => {
            const normalize = (txt) => {
                return (txt || "")
                    .replace(/\\u00a0/g, " ")
                    .replace(/\\s+/g, " ")
                    .trim();
            };

            const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 &&
                       r.height > 0 &&
                       s.visibility !== 'hidden' &&
                       s.display !== 'none';
            };

            const isDisabled = (el) => {
                if (!el) return true;
                const text = normalize(el.innerText || "").toLowerCase();
                return el.disabled
                    || el.getAttribute('aria-disabled') === 'true'
                    || String(el.className || "").toLowerCase().includes('disabled')
                    || /unavailable|sold out|not available/.test(text);
            };

            const fireClick = (el) => {
                el.scrollIntoView({ block: "center", inline: "center" });
                el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, cancelable: true, view: window }));
                el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
                el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
                el.click();
            };

            const pref = [
                "1 PM", "2 PM", "3 PM", "4 PM", "5 PM", "6 PM", "7 PM", "8 PM",
                "12 PM", "11 AM", "10 AM", "9 AM", "8 AM"
            ];

            const blocked = (text) => {
                const t = normalize(text).toLowerCase();
                return /checkout page|order info|payment|pay with|visa|place order|order summary|estimated total|promo|donate|round up|gift|bag|cart|privacy policy|terms of use/.test(t);
            };

            const slotRegexFor = (slot) => {
                const escaped = slot.replace(" ", "\\\\s*");
                return new RegExp(`(^|\\\\b)${escaped}($|\\\\b|\\\\s|-|price)`, "i");
            };

            const candidates = [];

            const elements = Array.from(document.querySelectorAll("button, [role='button'], input, label, li, div, span, p"))
                .filter(el => visible(el) && !isDisabled(el));

            for (let i = 0; i < elements.length; i++) {
                const el = elements[i];
                const text = normalize(
                    el.innerText ||
                    el.getAttribute("aria-label") ||
                    el.getAttribute("value") ||
                    el.parentElement?.innerText ||
                    ""
                );

                if (!text || blocked(text)) continue;

                for (let p = 0; p < pref.length; p++) {
                    const slot = pref[p];
                    const regex = slotRegexFor(slot);

                    if (!regex.test(text)) continue;

                    let clickable = el;

                    for (let depth = 0; depth < 8; depth++) {
                        if (!clickable.parentElement) break;

                        const parent = clickable.parentElement;
                        const parentText = normalize(parent.innerText || "");

                        if (
                            parentText &&
                            parentText.length < 300 &&
                            regex.test(parentText) &&
                            !blocked(parentText) &&
                            visible(parent) &&
                            !isDisabled(parent)
                        ) {
                            clickable = parent;
                        } else {
                            break;
                        }
                    }

                    clickable =
                        clickable.closest("label") ||
                        clickable.closest("button") ||
                        clickable.closest("[role='button']") ||
                        clickable.closest("li") ||
                        clickable;

                    if (!clickable || !visible(clickable) || isDisabled(clickable)) continue;

                    candidates.push({
                        priority: p,
                        slot,
                        text,
                        clickedText: normalize(clickable.innerText || clickable.getAttribute("aria-label") || ""),
                        element: clickable
                    });

                    break;
                }
            }

            candidates.sort((a, b) => a.priority - b.priority);

            if (candidates.length === 0) {
                return {
                    selected: false,
                    reason: "NO_SLOT_CANDIDATES_FOUND",
                    bodyTextSnippet: normalize(document.body.innerText || "").slice(0, 1800)
                };
            }

            const chosen = candidates[0];

            fireClick(chosen.element);

            const radio =
                chosen.element.querySelector?.("input[type='radio']") ||
                chosen.element.closest("label")?.querySelector?.("input[type='radio']") ||
                chosen.element.parentElement?.querySelector?.("input[type='radio']");

            if (radio && !radio.disabled) {
                radio.checked = true;
                radio.dispatchEvent(new Event("input", { bubbles: true }));
                radio.dispatchEvent(new Event("change", { bubbles: true }));
            }

            return {
                selected: true,
                slot: chosen.slot,
                text: chosen.text,
                clickedText: chosen.clickedText,
                priority: chosen.priority
            };
        }
    """, default={"selected": False})

    log(f"🕒 Timeslot select result: {result}")

    if result and result.get("selected"):
        pause(page, 5000)
        return True

    return False

def handle_schedule_pickup_time_exact(page):
    log("🕒 Handling Schedule pickup time exact flow - selecting 1 PM else next slot")

    pause(page, 4000)

    detected = safe_evaluate(page, """
        () => {
            const text = document.body.innerText || "";
            const hasScheduleText =
                /Schedule pickup time|Schedule delivery time|Pickup date|Pickup date & time|Order will be ready|More times/i.test(text);

            const hasDateTiles =
                document.querySelectorAll("div.date-tile[role='button'], [class*='date-tile'][role='button']").length > 0;

            const hasSlotText =
                /\\b(8|9|10|11|12|1|2|3|4|5|6|7)\\s*(AM|PM)\\b/i.test(text);

            return {
                hasScheduleText,
                hasDateTiles,
                hasSlotText,
                detected: hasScheduleText || hasDateTiles || hasSlotText
            };
        }
    """, default={"detected": False})

    log(f"🕒 Schedule detection result: {detected}")

    if not detected or not detected.get("detected"):
        log("ℹ️ Schedule pickup time screen not detected")
        return False

    if detected.get("hasSlotText") and not detected.get("hasDateTiles"):
        if select_next_available_time_slot(page):
            if click_enabled_continue_js(page, "timeslot-selected-no-date-tiles"):
                log("✅ Pickup slot selected and Continue clicked")
                return True

    for date_page_round in range(1, 4):
        log(f"📅 Date page round {date_page_round}/3 for preferred slot")

        for idx in [3, 4, 2, 1, 0]:
            date_ok = select_date_by_index(page, idx)

            if not date_ok:
                slot_text_visible = safe_evaluate(page, """
                    () => /\\b(8|9|10|11|12|1|2|3|4|5|6|7)\\s*(AM|PM)\\b/i.test(document.body.innerText || "")
                """, default=False)

                if slot_text_visible:
                    if select_next_available_time_slot(page):
                        if click_enabled_continue_js(page, "timeslot-selected-date-less"):
                            log("✅ Pickup slot selected and Continue clicked")
                            return True

                continue

            click_more_times_if_present(page, max_clicks=5)

            if select_next_available_time_slot(page):
                if click_enabled_continue_js(page, "timeslot-selected"):
                    log("✅ Pickup slot selected and Continue clicked")
                    return True

                log("⚠ Slot selected but Continue did not click. Retrying Continue.")
                pause(page, 3000)

                if click_enabled_continue_js(page, "timeslot-selected-retry"):
                    log("✅ Pickup slot selected and Continue clicked on retry")
                    return True

            log(f"⚠ No selectable slot for date index {idx}; trying next date")

        next_clicked = click_next_available_dates(page)
        if not next_clicked:
            log("ℹ️ No more Next Available Dates button. Ending slot search.")
            break

    safe_screenshot(page, "timeslot_not_selected.png")
    log("❌ Could not select any preferred/next time slot after date retries")
    return False

# ---------------- PAYMENT 4242 / CVV + ZIP ----------------
def handle_existing_card_4242_cvv(page):
    log(f"💳 Checking payment section for existing card ending {CARD_LAST4}")

    pause(page, 4000)

    detected = safe_evaluate(page, f"""
        () => {{
            const body = document.body.innerText || "";
            return {{
                hasPayWith: /Pay with|Payment|payment method/i.test(body),
                hasCard: body.includes("{CARD_LAST4}"),
                hasConfirmCvv: /Confirm CVV|CVV|Security code|Security Code|Update VISA|Billing ZIP Code/i.test(body),
                detected: (/Pay with|Payment|payment method/i.test(body) && body.includes("{CARD_LAST4}")) || /Confirm CVV|Update VISA|Billing ZIP Code/i.test(body)
            }};
        }}
    """, default={"detected": False})

    log(f"💳 Payment detection result: {detected}")

    if not detected or not detected.get("detected"):
        log("ℹ️ Payment 4242/CVV section not detected")
        return False

    clicked_confirm_cvv = safe_evaluate(page, f"""
        () => {{
            const visible = (el) => {{
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            }};

            const isDisabled = (el) => {{
                return el.disabled
                    || el.getAttribute('aria-disabled') === 'true'
                    || String(el.className || '').toLowerCase().includes('disabled');
            }};

            const body = document.body.innerText || "";

            if (/Billing ZIP Code|Please enter CVV|Update VISA|CVV/i.test(body) && document.querySelector("input#cvvNumber, input[name='CVV']")) {{
                return {{ clicked: true, reason: "CVV_FORM_ALREADY_OPEN" }};
            }}

            const clickables = Array.from(document.querySelectorAll("button, [role='button'], a"))
                .filter(el => visible(el) && !isDisabled(el));

            let btn = clickables.find(el => {{
                const text = (el.innerText || "").trim();
                const aria = el.getAttribute("aria-label") || "";
                const combined = `${{text}} ${{aria}}`;
                return /Confirm\\s*CVV/i.test(combined) && combined.includes("{CARD_LAST4}");
            }});

            if (!btn) {{
                btn = clickables.find(el => {{
                    const text = (el.innerText || "").trim();
                    const aria = el.getAttribute("aria-label") || "";
                    return /Confirm\\s*CVV/i.test(`${{text}} ${{aria}}`);
                }});
            }}

            if (!btn) {{
                return {{ clicked: true, reason: "CONFIRM_CVV_NOT_FOUND_BUT_CONTINUING" }};
            }}

            btn.scrollIntoView({{ block: "center", inline: "center" }});
            btn.click();

            return {{
                clicked: true,
                text: (btn.innerText || "").trim(),
                aria: btn.getAttribute("aria-label"),
                cls: btn.className
            }};
        }}
    """, default={"clicked": False})

    log(f"💳 Confirm CVV click result: {clicked_confirm_cvv}")

    if not clicked_confirm_cvv or not clicked_confirm_cvv.get("clicked"):
        safe_screenshot(page, "confirm_cvv_not_clicked.png")
        return False

    pause(page, 5000)

    fill_result = safe_evaluate(page, f"""
        () => {{
            const nativeSet = (input, value) => {{
                const prototype = Object.getPrototypeOf(input);
                const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");

                if (descriptor && descriptor.set) {{
                    descriptor.set.call(input, value);
                }} else {{
                    input.value = value;
                }}

                input.dispatchEvent(new Event("input", {{ bubbles: true }}));
                input.dispatchEvent(new Event("change", {{ bubbles: true }}));
                input.dispatchEvent(new KeyboardEvent("keyup", {{ bubbles: true }}));
                input.dispatchEvent(new KeyboardEvent("blur", {{ bubbles: true }}));
            }};

            const cvvInput =
                document.querySelector("input#cvvNumber") ||
                document.querySelector("input[name='CVV']") ||
                Array.from(document.querySelectorAll("input")).find(el => {{
                    const combined = [
                        el.id || "",
                        el.name || "",
                        el.placeholder || "",
                        el.getAttribute("aria-label") || "",
                        el.getAttribute("maxlength") || ""
                    ].join(" ").toLowerCase();

                    return /cvv|cvc|security/.test(combined) || el.getAttribute("maxlength") === "3";
                }});

            const zipInput =
                document.querySelector("input#zip") ||
                document.querySelector("input[name='ZipCode']") ||
                Array.from(document.querySelectorAll("input")).find(el => {{
                    const combined = [
                        el.id || "",
                        el.name || "",
                        el.placeholder || "",
                        el.getAttribute("aria-label") || "",
                        el.getAttribute("maxlength") || ""
                    ].join(" ").toLowerCase();

                    return /zip|postal/.test(combined) || el.getAttribute("maxlength") === "5";
                }});

            if (!cvvInput) {{
                return {{
                    filled: false,
                    reason: "CVV_INPUT_NOT_FOUND"
                }};
            }}

            cvvInput.scrollIntoView({{ block: "center", inline: "center" }});
            cvvInput.focus();
            nativeSet(cvvInput, "{CVV_VALUE}");

            let zipStatus = "ZIP_NOT_FOUND";

            if (zipInput) {{
                zipInput.scrollIntoView({{ block: "center", inline: "center" }});

                if (!zipInput.disabled && !zipInput.readOnly) {{
                    zipInput.focus();
                    nativeSet(zipInput, "{ZIP_CODE_VALUE}");
                    zipStatus = "ZIP_FILLED_ENABLED";
                }} else {{
                    nativeSet(zipInput, "{ZIP_CODE_VALUE}");
                    zipStatus = "ZIP_WAS_DISABLED_JS_VALUE_SET";
                }}
            }}

            return {{
                filled: true,
                cvv: {{
                    id: cvvInput.id,
                    name: cvvInput.name,
                    aria: cvvInput.getAttribute("aria-label"),
                    valueLength: cvvInput.value.length
                }},
                zip: zipInput ? {{
                    id: zipInput.id,
                    name: zipInput.name,
                    aria: zipInput.getAttribute("aria-label"),
                    disabled: zipInput.disabled,
                    value: zipInput.value,
                    status: zipStatus
                }} : null
            }};
        }}
    """, default={"filled": False})

    log(f"💳 CVV + ZIP fill result: {fill_result}")

    if not fill_result or not fill_result.get("filled"):
        safe_screenshot(page, "cvv_zip_not_filled.png")
        return False

    pause(page, 3000)

    confirm_clicked = False

    for attempt in range(1, 12):
        log(f"💳 Confirm button click attempt {attempt}/11")

        confirm_result = safe_evaluate(page, """
            () => {
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };

                const isDisabled = (el) => {
                    return el.disabled
                        || el.getAttribute('aria-disabled') === 'true'
                        || String(el.className || '').toLowerCase().includes('disabled');
                };

                const buttons = Array.from(document.querySelectorAll("button, [role='button']"))
                    .filter(visible);

                const confirmButtons = buttons.filter(el => {
                    const text = (el.innerText || "").trim();
                    const aria = el.getAttribute("aria-label") || "";
                    const combined = `${text} ${aria}`;
                    return /^\\s*confirm\\s*$/i.test(text) || /confirm/i.test(combined);
                });

                const enabledConfirm = confirmButtons.find(el => !isDisabled(el));

                if (!enabledConfirm) {
                    return {
                        clicked: false,
                        reason: "NO_ENABLED_CONFIRM"
                    };
                }

                enabledConfirm.scrollIntoView({ block: "center", inline: "center" });
                enabledConfirm.click();

                return {
                    clicked: true,
                    text: (enabledConfirm.innerText || "").trim(),
                    aria: enabledConfirm.getAttribute("aria-label"),
                    cls: enabledConfirm.className
                };
            }
        """, default={"clicked": False})

        log(f"💳 Confirm click result: {confirm_result}")

        if confirm_result and confirm_result.get("clicked"):
            confirm_clicked = True
            break

        pause(page, 2500)

    if not confirm_clicked:
        safe_screenshot(page, "cvv_zip_confirm_not_clicked.png")
        return False

    pause(page, 10000)

    log("✅ Existing card 4242 CVV + ZIP confirmed")
    return True

# ---------------- CHECKOUT FLOW ----------------
def handle_existing_card_4242_cvv(page):
    log(f"💳 Checking payment section for existing card ending {CARD_LAST4}")

    pause(page, 4000)

    # ---------------- Detect payment section ----------------
    detected = safe_evaluate(page, f"""
        () => {{
            const body = document.body.innerText || "";
            return {{
                hasPayWith: /Pay with|Payment|payment method/i.test(body),
                hasCard: body.includes("{CARD_LAST4}"),
                hasConfirmCvv: /Confirm CVV|CVV|Security code|Billing ZIP Code/i.test(body),
                detected: (/Pay with|Payment/i.test(body) && body.includes("{CARD_LAST4}")) 
                          || /CVV|Security code|Billing ZIP Code/i.test(body)
            }};
        }}
    """, default={"detected": False})

    log(f"💳 Payment detection result: {detected}")

    if not detected or not detected.get("detected"):
        log("ℹ️ Payment section not detected")
        return False

    # ---------------- Open CVV form if needed ----------------
    try:
        confirm_btn = page.locator("text=Confirm CVV").first
        if confirm_btn.count() > 0 and confirm_btn.is_visible():
            confirm_btn.click(force=True)
            pause(page, 4000)
            log("✅ Clicked Confirm CVV")
    except Exception as e:
        log(f"🔁 Confirm CVV click skipped: {e}")

    # ---------------- ✅ PLAYWRIGHT CVV FIX ----------------
    try:
        cvv = page.locator("input#cvvNumber, input[name='CVV']").first
        cvv.wait_for(state="visible", timeout=15000)

        # Retry typing CVV (important for React inputs)
        success = False
        for attempt in range(3):
            cvv.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")

            cvv.type(CVV_VALUE, delay=120)  # 🔥 real user typing

            # trigger blur
            cvv.press("Tab")
            pause(page, 2000)

            val = cvv.input_value()
            log(f"🔍 CVV attempt {attempt+1}: value='{val}' len={len(val)}")

            if len(val) >= 3:
                success = True
                break

        if not success:
            log("❌ CVV not filled correctly after retries")
            safe_screenshot(page, "cvv_failed.png")
            return False

        log("✅ CVV filled successfully via typing")

    except Exception as e:
        log(f"❌ CVV handling failed: {e}")
        safe_screenshot(page, "cvv_exception.png")
        return False

    # ---------------- ZIP (optional reinforce) ----------------
    try:
        zip_field = page.locator("input#zip, input[name='ZipCode']").first
        if zip_field.count() > 0:
            val = zip_field.input_value()
            log(f"🔍 ZIP current value: {val}")

            if not val or len(val) < 5:
                zip_field.click()
                zip_field.fill(ZIP_CODE_VALUE)
                zip_field.press("Tab")
                log("✅ ZIP filled using Playwright")
    except Exception as e:
        log(f"ℹ️ ZIP handling skipped: {e}")

    pause(page, 3000)

    # ---------------- ✅ Click Confirm (fixed) ----------------
    for attempt in range(1, 10):
        log(f"💳 Confirm button click attempt {attempt}/9")

        try:
            btn = page.locator(
                "button:has-text('Confirm'), button[aria-label*='Confirm']"
            ).filter(has_not_text="Continue").first

            if btn.count() > 0 and btn.is_visible():
                if btn.is_enabled():
                    btn.click(force=True)
                    pause(page, 8000)
                    log("✅ CVV Confirm clicked successfully")
                    return True
                else:
                    log("⚠ Confirm button disabled, waiting...")
        except Exception as e:
            log(f"⚠ Confirm click attempt failed: {e}")

        pause(page, 2000)

    # JS fallback (last resort)
    result = safe_evaluate(page, """
        () => {
            const btn = [...document.querySelectorAll("button")]
                .find(b => /confirm/i.test(b.innerText || "") && !b.disabled);

            if (!btn) return { clicked: false };

            btn.click();
            return { clicked: true };
        }
    """)

    log(f"💳 JS confirm fallback result: {result}")

    if result and result.get("clicked"):
        pause(page, 8000)
        return True

    safe_screenshot(page, "confirm_failed.png")
    return False


def handle_checkout_interstitials(page):
    log("🧩 Handling checkout interstitials/modals if any")

    interstitial_selectors = [
        "button:has-text('Skip'):visible",
        "button:has-text('Not now'):visible",
        "button:has-text('No thanks'):visible",
        "button:has-text('Maybe later'):visible",
        "button:has-text('Close'):visible",
        "button[aria-label*='Close']:visible"
    ]

    for round_num in range(1, 3):
        log(f"🔁 Interstitial handling round {round_num}/2")
        clicked_any = False

        for sel in interstitial_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(force=True)
                    log(f"✅ Clicked interstitial button: {sel}")
                    pause(page, 5000)
                    clicked_any = True
                    break
            except Exception as e:
                log(f"⚠ Interstitial selector failed {sel}: {e}")

        if not clicked_any:
            log("ℹ️ No interstitial found in this round")
            break

    return True

def checkout_continue_until_place_order(page):
    log("🧾 Continuing checkout flow until final order step")

    final_place_order_selectors = [
        "button:has-text('Place Order'):visible",
        "button:has-text('Place order'):visible",
        "button:has-text('Submit Order'):visible",
        "button:has-text('Submit order'):visible",
        "button:has-text('Place My Order'):visible",
        "button[aria-label*='Place Order']:visible",
        "button[aria-label*='Submit Order']:visible",
        "[data-qa*='place-order']:visible",
        "[data-testid*='place-order']:visible"
    ]

    optional_skip_selectors = [
        "button:has-text('Skip'):visible",
        "button:has-text('Not now'):visible",
        "button:has-text('No thanks'):visible",
        "button:has-text('Maybe later'):visible",
        "button[aria-label*='Close']:visible",
        "button:has-text('Close'):visible"
    ]

    schedule_done = False
    payment_done = False

    for step in range(1, 18):
        log(f"🔁 Checkout continue step {step}/17")
        pause(page, 4000)

        for sel in final_place_order_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible() and btn.is_enabled():
                    btn.scroll_into_view_if_needed()
                    pause(page, 1000)

                    if PLACE_ORDER:
                        log("🚨 PLACE_ORDER=True. Clicking final Place Order button.")
                        btn.click(force=True)
                        pause(page, 15000)
                        log("✅ Final order placement clicked")
                        return "ORDER_PLACED"
                    else:
                        log("🛑 Reached final Place Order step. PLACE_ORDER=False, stopping safely before placing order.")
                        safe_screenshot(page, "checkout_ready_to_place_order.png")
                        return "READY_TO_PLACE_ORDER"
            except Exception as e:
                log(f"⚠ Final place-order check failed for selector {sel}: {e}")

        if not schedule_done:
            schedule_result = handle_schedule_pickup_time_exact(page)
            if schedule_result:
                schedule_done = True
                continue

        if not payment_done:
            payment_result = handle_existing_card_4242_cvv(page)
            if payment_result:
                payment_done = True
                continue

        skipped = click_first_visible_button(
            page,
            optional_skip_selectors,
            "Optional Skip/Close popup",
            wait_after_ms=5000
        )

        if skipped:
            continue

        continued = click_enabled_continue_js(page, "checkout-loop")

        if continued:
            continue

        log("ℹ️ No enabled continue found. Scrolling a bit and rechecking.")

        try:
            page.mouse.wheel(0, 700)
            pause(page, 3000)
        except:
            pass

        if not schedule_done:
            schedule_result_after_scroll = handle_schedule_pickup_time_exact(page)
            if schedule_result_after_scroll:
                schedule_done = True
                continue

        if not payment_done:
            payment_result_after_scroll = handle_existing_card_4242_cvv(page)
            if payment_result_after_scroll:
                payment_done = True
                continue

        log("⚠ Checkout flow could not continue further. Stopping.")
        safe_screenshot(page, "checkout_stopped.png")
        return "CHECKOUT_STOPPED"

    log("⚠ Max checkout steps reached. Stopping.")
    safe_screenshot(page, "checkout_max_steps_reached.png")
    return "MAX_STEPS_REACHED"

def proceed_cart_to_checkout_and_order(page):
    log("🛒➡️🧾 Proceeding from cart to checkout/order flow")

    wait_for_cart_page(page)

    click_checkout_button(page)

    handle_checkout_interstitials(page)

    handle_schedule_pickup_time_exact(page)
    handle_existing_card_4242_cvv(page)

    result = checkout_continue_until_place_order(page)

    log(f"🏁 Checkout/order flow result: {result}")
    return result

# ---------------- MAIN ----------------
def run(playwright):
    log("🚀 RUNNING UPDATED SCRIPT WITH PDP ADD/INCREASE FIX + AISLES + BEVERAGES FLOW")

    browser = playwright.chromium.launch(
        headless=False,
        slow_mo=300,
        args=[
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding"
        ]
    )

    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = create_fresh_page(context)

    checkout_result = None
    clipped = 0
    beverages_clipped = 0

    try:
        perform_login(page)

        log("🏠 Ensuring Homepage before Homepage-only coupon clipping")
        open_homepage(page)
        pause(page, 5000)

        clipped = clip_coupons_on_homepage(page, COUPONS_TO_CLIP)

        log(f"⏳ Waiting {WAIT_AFTER_COUPONS_MS / 1000:.0f}s after Homepage coupon clipping. clipped={clipped}")
        pause(page, WAIT_AFTER_COUPONS_MS)

        log("🏠 Returning to homepage before homepage scroll and add-to-cart")
        open_homepage(page)
        pause(page, 7000)

        scroll_bottom_then_top(page, max_steps=12, max_scroll_y=8000)

        hp_added = add_to_cart_from_homepage(page)
        if not hp_added:
            raise Exception("Add to cart failed on Homepage")

        log("🔍 Starting search flow after Homepage add-to-cart")

        try:
            search_ok = search_product_with_retries(
                page,
                SEARCH_TERM,
                MIN_SEARCH_ROUNDS,
                MAX_SEARCH_ROUNDS
            )
        except Exception as e:
            if is_crash_error(e):
                log("🛟 Page crashed during search. Recovering and retrying search flow.")
                page = recover_page_after_crash(context, page)

                search_ok = search_product_with_retries(
                    page,
                    SEARCH_TERM,
                    MIN_SEARCH_ROUNDS,
                    MAX_SEARCH_ROUNDS
                )
            else:
                raise

        if not search_ok:
            raise Exception("Search did not return products after retries")

        scroll_bottom_then_top(page, max_steps=18, max_scroll_y=10000)

        search_added = add_to_cart_from_search_results(page)
        if not search_added:
            raise Exception("Add to cart failed on search results")

        # ------------------------------------------------------------------
        # NEW FLOW:
        # 1. Click product card from search results
        # 2. Land on PDP
        # 3. Add to cart OR increase quantity if already added
        # 4. Scroll PDP bottom/top
        # 5. Click Categories breadcrumb
        # 6. Click Beverages
        # 7. Scroll Beverages bottom/top
        # ------------------------------------------------------------------

        log("🧾 Opening product card from search results to PDP")

        pdp_opened = open_product_details_from_search_results(page)
        if not pdp_opened:
            raise Exception("Product Details Page did not open from search results")

        pdp_added = add_to_cart_from_pdp(page)
        if not pdp_added:
            raise Exception("Add to cart / increase quantity failed on Product Details Page")

        log("🔽🔼 Scrolling PDP bottom then top")
        scroll_bottom_then_top(page, max_steps=18, max_scroll_y=12000)

        categories_opened = go_to_categories_from_pdp(page)
        if not categories_opened:
            raise Exception("Categories/Aisles page did not open from PDP")

        beverages_opened = click_beverages_on_aisles_page(page)
        if not beverages_opened:
            raise Exception("Beverages page did not open from Categories/Aisles page")

        log("🏷️ Scanning and clipping coupons from Beverages deals carousel")
        beverages_clipped = clip_coupons_from_beverages_deals_carousel(
            page,
            count=BEVERAGES_COUPONS_TO_CLIP,
            max_rounds=BEVERAGES_CAROUSEL_SCAN_ROUNDS
        )

        log(
            f"⏳ Waiting {WAIT_AFTER_BEVERAGES_COUPONS_MS / 1000:.0f}s after Beverages deals carousel coupon clipping. "
            f"beverages_clipped={beverages_clipped}"
        )
        pause(page, WAIT_AFTER_BEVERAGES_COUPONS_MS)

        log("🔽🔼 Scrolling Beverages page bottom then top")
        scroll_bottom_then_top(page, max_steps=18, max_scroll_y=12000)

        # Continue old next steps: cart -> checkout -> final place order safe stop
        cart_clicked = click_cart_icon(page)
        if not cart_clicked:
            raise Exception("Cart icon click failed")

        checkout_result = proceed_cart_to_checkout_and_order(page)

        unique_api_summary = build_unique_api_summary()

        apis_with_payload = sum(1 for api in captured_apis if api.get("has_payload"))
        apis_without_payload = sum(1 for api in captured_apis if not api.get("has_payload"))

        log("\n📊 SUMMARY")
        log(f"Captured APIs: {len(captured_apis)}")
        log(f"Unique APIs: {len(unique_api_summary)}")
        log(f"Captured Payloads: {len(event_payloads)}")
        log(f"APIs With Payload: {apis_with_payload}")
        log(f"APIs Without Payload: {apis_without_payload}")
        log(f"Failed APIs: {len(failed_apis)}")
        log(f"Homepage Coupons Clipped: {clipped}")
        log(f"Beverages Deals Coupons Clipped: {beverages_clipped}")
        log(f"Checkout Result: {checkout_result}") 
        log(f"PLACE_ORDER flag: {PLACE_ORDER}")

        log("\n🔗 UNIQUE API ENDPOINTS")
        if unique_api_summary:
            for idx, api in enumerate(unique_api_summary, start=1):
                log(f"{idx}. {api['endpoint']} | hits={api['count']}")
        else:
            log("No unique API endpoints captured.")

        with open("captured_apis.json", "w") as f:
            json.dump(captured_apis, f, indent=2)

        with open("unique_captured_apis.json", "w") as f:
            json.dump(unique_api_summary, f, indent=2)

        with open("event_payloads.json", "w") as f:
            json.dump(event_payloads, f, indent=2)

        with open("failed_apis.json", "w") as f:
            json.dump(failed_apis, f, indent=2)

        result = "PASSED" if not failed_apis else "FAILED"
        log(f"✅ RESULT: {result}")

    except Exception as e:
        log(f"❌ ERROR: {str(e)}")

        if is_crash_error(e):
            log("⚠ Error was caused by crashed/closed page. Screenshot may not be possible.")

        safe_screenshot(page, "error_screenshot.png")

    finally:
        log("✅ Process completed")
        input("Press Enter to close browser...")

        try:
            if context:
                context.close()
        except Exception as e:
            log(f"⚠ Context close skipped/failed: {e}")

        try:
            if browser and browser.is_connected():
                browser.close()
        except Exception as e:
            log(f"⚠ Browser close skipped/failed: {e}")

# ---------------- ENTRY ----------------
if __name__ == "__main__":
    with sync_playwright() as p:
        run(p)
