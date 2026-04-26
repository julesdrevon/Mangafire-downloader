import os
import time
import requests
import zipfile
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import WebDriverException, InvalidSessionIdException


class RateLimitedError(Exception):
    pass


class HomepageRedirectError(Exception):
    pass


def find_existing_path(candidates):
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None




def build_webdriver(user_agent=None):
    from selenium.webdriver.firefox.options import Options as FirefoxOptions

    profile_dir = os.path.join(os.getcwd(), ".browser-profile-firefox")
    if not os.path.exists(profile_dir):
        os.makedirs(profile_dir)

    zen_binary = find_existing_path([
        os.getenv("ZEN_BINARY"),
        os.path.expandvars(r"%LOCALAPPDATA%\Zen Browser\zen.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Zen\zen.exe"),
        os.path.expandvars(r"%APPDATA%\Zen Browser\zen.exe"),
        r"C:\Program Files\Zen Browser\zen.exe",
        r"C:\Program Files (x86)\Zen Browser\zen.exe",
    ])

    firefox_binary = find_existing_path([
        zen_binary,
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe"),
    ])

    if not firefox_binary:
        raise RuntimeError(
            "Zen Browser introuvable. Installe-le ou définis ZEN_BINARY=<chemin vers zen.exe>."
        )

    browser_label = "Zen Browser" if zen_binary and firefox_binary == zen_binary else "Firefox"

    ff_options = FirefoxOptions()
    ff_options.binary_location = firefox_binary
    ff_options.add_argument("-profile")
    ff_options.add_argument(profile_dir)
    ff_options.set_preference("dom.webdriver.enabled", False)
    ff_options.set_preference("useAutomationExtension", False)
    if user_agent:
        ff_options.set_preference("general.useragent.override", user_agent)

    from selenium.webdriver.firefox.service import Service as FirefoxService
    try:
        from webdriver_manager.firefox import GeckoDriverManager
        service = FirefoxService(GeckoDriverManager().install())
    except ImportError:
        print("⚠️ webdriver-manager non installé. Lance: pip install webdriver-manager")
        raise
    driver = webdriver.Firefox(service=service, options=ff_options)
    return driver, browser_label

def create_zip(folder_path, zip_name):
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder_path):
            for file in sorted(files):
                zipf.write(os.path.join(root, file), file)
    print(f"📦 Archive créée avec succès : {zip_name}")


def normalize_flaresolverr_url(raw_url):
    url = (raw_url or "http://192.168.1.49:8191/v1").strip().rstrip("/")
    if not url:
        url = "http://192.168.1.49:8191/v1"
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


def get_flaresolverr_solution(target_url):
    if os.getenv("USE_FLARESOLVERR", "1") != "1":
        return None, None

    flaresolverr_url = normalize_flaresolverr_url(os.getenv("FLARESOLVERR_URL", "http://192.168.1.49:8191/v1"))
    print(f"🛡️ FlareSolverr endpoint: {flaresolverr_url}")
    payload = {
        "cmd": "request.get",
        "url": target_url,
        "maxTimeout": 120000,
    }

    session_name = os.getenv("FLARESOLVERR_SESSION", "")
    if session_name:
        payload["session"] = session_name
        payload["session_ttl_minutes"] = 10

    try:
        response = requests.post(flaresolverr_url, json=payload, timeout=130)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        print(f"⚠️ FlareSolverr indisponible: {exc}")
        return None, None

    if data.get("status") != "ok":
        print(f"⚠️ FlareSolverr n'a pas resolu le challenge: {data.get('message', 'erreur inconnue')}")
        return None, None

    solution = data.get("solution", {})
    response_html = solution.get("response") or ""
    if page_has_rate_limit(response_html):
        print("⛔ FlareSolverr indique un blocage Cloudflare 1015 (rate limit).")
        return None, None

    cookies = solution.get("cookies") or []
    user_agent = solution.get("userAgent")
    if cookies:
        print(f"✅ FlareSolverr: {len(cookies)} cookies recuperes")
    return cookies, user_agent


def wait_for_cf_clearance(driver, timeout=90):
    print("⏳ Attente de la résolution Cloudflare (cf_clearance)...")
    for elapsed in range(timeout):
        cookies = {c['name'] for c in driver.get_cookies()}
        if 'cf_clearance' in cookies:
            print(f"✅ Cloudflare résolu (cf_clearance obtenu en {elapsed}s).")
            return True
        if elapsed == 12:
            print("👉 Si un CAPTCHA/challenge Cloudflare s'affiche, résous-le dans la fenêtre du navigateur.")
        time.sleep(1)
    print("⚠️ cf_clearance non obtenu après le délai. Tentative de continuer quand même...")
    return False


def apply_cookies_via_cdp(driver, cookies):
    """Injecte les cookies via CDP AVANT toute navigation.
    cf_clearance est ainsi présent sur la première requête → Cloudflare ne challenge pas."""
    if not cookies:
        return 0

    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception as exc:
        print(f"⚠️ CDP Network.enable échoué ({exc}), fallback add_cookie.")
        return _apply_cookies_fallback(driver, cookies)

    added = 0
    for raw_cookie in cookies:
        name = raw_cookie.get("name")
        value = raw_cookie.get("value")
        if not name or value is None:
            continue
        cdp_cookie = {
            "name": name,
            "value": value,
            "domain": raw_cookie.get("domain") or ".mangafire.to",
            "path": raw_cookie.get("path") or "/",
        }
        if isinstance(raw_cookie.get("secure"), bool):
            cdp_cookie["secure"] = raw_cookie["secure"]
        if isinstance(raw_cookie.get("httpOnly"), bool):
            cdp_cookie["httpOnly"] = raw_cookie["httpOnly"]
        expires = raw_cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cdp_cookie["expires"] = int(expires)
        try:
            driver.execute_cdp_cmd("Network.setCookie", cdp_cookie)
            added += 1
        except Exception:
            continue

    print(f"✅ Cookies injectés via CDP (avant navigation): {added}")
    return added


def _apply_cookies_fallback(driver, cookies):
    """Fallback: navigue homepage puis add_cookie (moins fiable vs Cloudflare)."""
    driver.get("https://mangafire.to/")
    added = 0
    for raw_cookie in cookies:
        name = raw_cookie.get("name")
        value = raw_cookie.get("value")
        if not name or value is None:
            continue
        cookie = {"name": name, "value": value}
        if raw_cookie.get("domain"):
            cookie["domain"] = raw_cookie["domain"]
        if raw_cookie.get("path"):
            cookie["path"] = raw_cookie["path"]
        if isinstance(raw_cookie.get("secure"), bool):
            cookie["secure"] = raw_cookie["secure"]
        if isinstance(raw_cookie.get("httpOnly"), bool):
            cookie["httpOnly"] = raw_cookie["httpOnly"]
        expires = raw_cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expiry"] = int(expires)
        try:
            driver.add_cookie(cookie)
            added += 1
        except Exception:
            continue
    print(f"✅ Cookies injectés (fallback homepage): {added}")
    return added


def debug_dom_state(driver, tag="state"):
    debug_enabled = os.getenv("DEBUG_DOM", "1") == "1"
    if not debug_enabled:
        return

    print(f"🧪 DEBUG [{tag}] URL: {driver.current_url}")
    print(f"🧪 DEBUG [{tag}] Title: {driver.title}")
    selectors = [
        "div.page img",
        "div.page picture img",
        "#reader img",
        ".reader img",
        "img[data-src*='http']",
        "img[src*='http']",
    ]
    for selector in selectors:
        count = len(driver.find_elements(By.CSS_SELECTOR, selector))
        print(f"🧪 DEBUG [{tag}] {selector}: {count}")

    source_lower = driver.page_source.lower()
    print(f"🧪 DEBUG [{tag}] contains cloudflare: {'cloudflare' in source_lower}")
    print(f"🧪 DEBUG [{tag}] contains /read/: {'/read/' in driver.current_url}")


def save_debug_html(driver, reason):
    debug_enabled = os.getenv("DEBUG_DOM", "1") == "1"
    if not debug_enabled:
        return
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]", "_", reason)[:40]
    filename = f"debug_{safe_reason}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    print(f"🧪 DEBUG HTML sauvegardé: {filename}")


def page_has_rate_limit(content, title=""):
    raw = (content or "").lower()
    title_l = (title or "").lower()
    return (
        "error 1015" in raw
        or "you are being rate limited" in raw
        or "cloudflare" in raw and "rate limited" in raw
        or "error 1015" in title_l
    )


def page_is_mangafire_homepage(driver):
    current_url = (driver.current_url or "").rstrip("/")
    title = (driver.title or "").lower()
    source_lower = (driver.page_source or "").lower()
    return (
        current_url == "https://mangafire.to"
        or ("mangafire" in title and "read online" in title and "/read/" not in current_url)
        or ("mangafire" in source_lower and "/read/" not in current_url and "home" in source_lower)
    )


def assert_not_rate_limited(driver, stage):
    if page_has_rate_limit(driver.page_source, driver.title):
        save_debug_html(driver, f"rate_limited_{stage}")
        raise RateLimitedError(
            "Cloudflare Error 1015 détecté (rate limit). "
            "Attends 30-60 minutes avant de réessayer et évite les relances en boucle."
        )


def assert_not_homepage_redirect(driver, stage):
    if page_is_mangafire_homepage(driver):
        save_debug_html(driver, f"homepage_redirect_{stage}")
        raise HomepageRedirectError(
            "MangaFire t'a renvoyé sur l'accueil au lieu du chapitre. "
            "Je n'insiste pas davantage pour éviter de boucler sur une session bloquée."
        )


def normalize_mangafire_url(raw_url):
    url = (raw_url or "").strip().strip('"').strip("'")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def ensure_reader_page(driver, expected_url):
    expected_path = expected_url.split("?", 1)[0]
    WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(3)
    assert_not_rate_limited(driver, "initial_open")
    assert_not_homepage_redirect(driver, "initial_open")

    if "/read/" in driver.current_url and driver.current_url.startswith("https://mangafire.to/read/"):
        debug_dom_state(driver, "reader_ok_initial")
        return

    debug_dom_state(driver, "redirect_detected_initial")
    print("⚠️ MangaFire a redirigé vers une autre page (Cloudflare probable).")
    print("👉 Dans la fenêtre du navigateur:")
    print("   1) Résous le check Cloudflare")
    print(f"   2) Ouvre manuellement cette URL: {expected_path}")

    for attempt in range(1, 4):
        user_action = input(
            f"Appuie sur Entrée pour re-tester (tentative {attempt}/3), ou tape q pour annuler: "
        ).strip().lower()
        if user_action == "q":
            raise RuntimeError("Opération annulée par l'utilisateur pendant la vérification Cloudflare.")

        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(2)
        assert_not_rate_limited(driver, f"attempt_{attempt}_after_user")

        if driver.current_url.startswith("https://mangafire.to/read/"):
            debug_dom_state(driver, "reader_ok_after_user")
            return

        # Vérifie que cf_clearance est présent avant de retenter le chapitre.
        cf_cookies = {c['name'] for c in driver.get_cookies()}
        if 'cf_clearance' not in cf_cookies:
            print("⚠️ cf_clearance absent — résous d'abord le challenge Cloudflare dans le navigateur.")
            wait_for_cf_clearance(driver, timeout=60)

        # Retente l'URL chapitre via JS pour minimiser la re-détection.
        driver.execute_script(f"window.location.href = '{expected_path}';")
        WebDriverWait(driver, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(3)
        assert_not_rate_limited(driver, f"attempt_{attempt}_after_retry_get")
        assert_not_homepage_redirect(driver, f"attempt_{attempt}_after_retry_get")
        if driver.current_url.startswith("https://mangafire.to/read/"):
            debug_dom_state(driver, "reader_ok_after_retry_get")
            return

        print(f"ℹ️ Toujours redirigé: {driver.current_url}")

    save_debug_html(driver, "reader_page_not_opened")
    raise RuntimeError(
        f"La page chapitre ne s'ouvre pas après 3 tentatives. URL actuelle: {driver.current_url}. "
        f"URL demandée: {expected_path}"
    )


def extract_page_image_elements(driver):
    selectors = [
        "div.page img",
        "div.page picture img",
        "#reader img",
        ".reader img",
        "img[data-src*='http']",
        "img[src*='http']",
    ]

    seen = set()
    unique_elements = []
    for selector in selectors:
        for img in driver.find_elements(By.CSS_SELECTOR, selector):
            src = img.get_attribute("src") or img.get_attribute("data-src") or img.get_attribute("data-original")
            if not src or src in seen:
                continue
            if "logo" in src or "favicon" in src or "sharethis" in src:
                continue

            # Ignore les petites images d'UI (icones, boutons, etc.).
            width = driver.execute_script("return arguments[0].naturalWidth || 0", img)
            if width and int(width) < 300:
                continue

            seen.add(src)
            unique_elements.append(img)
    return unique_elements

def download_manga():
    # --- INPUT UTILISATEUR ---
    url = normalize_mangafire_url(input("🔗 Entrez l'URL de la page MangaFire : "))
    if not url:
        print("❌ URL vide. Exemple attendu : https://mangafire.to/read/xxx/fr/volume-1")
        return
    if "mangafire.to/read/" not in url:
        print("❌ URL invalide. Colle une URL de chapitre MangaFire contenant /read/.")
        return

    # Extraction automatique du nom pour le dossier
    # Exemple URL: mangafire.to/read/mad.8020/fr/chapter-1
    download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
    os.makedirs(download_dir, exist_ok=True)

    slug_match = re.search(r'/read/([^/]+)', url)
    folder_name = slug_match.group(1).replace('.', '_') if slug_match else "manga_download"
    suffix = url.split('/')[-1]
    folder_name = os.path.join(download_dir, f"{folder_name}_{suffix}")

    use_flare = input("🛡️  Utiliser FlareSolverr ? (y/n) : ").strip().lower() == "y"
    if use_flare:
        flare_cookies, flare_user_agent = get_flaresolverr_solution(url)
    else:
        flare_cookies, flare_user_agent = None, None

    # --- CONFIGURATION SELENIUM ---
    driver, browser_name = build_webdriver(user_agent=flare_user_agent)

    try:
        print(f"🌐 Navigateur utilisé : {browser_name}")
        if flare_cookies:
            apply_cookies_via_cdp(driver, flare_cookies)
        else:
            # Warmup homepage pour laisser Cloudflare établir cf_clearance avant d'ouvrir le chapitre.
            print("🌐 Chargement de la page d'accueil MangaFire (warmup Cloudflare)...")
            driver.get("https://mangafire.to/")
            wait_for_cf_clearance(driver)
        try:
            driver.get(url)
        except InvalidSessionIdException:
            print("❌ La session Brave a été fermée ou coupée pendant la navigation.")
            return
        except WebDriverException:
            print(f"❌ URL non acceptée par le navigateur : {url}")
            return
        assert_not_rate_limited(driver, "after_get_url")
        assert_not_homepage_redirect(driver, "after_get_url")
        ensure_reader_page(driver, url)
        print(f"⏳ Chargement de la page... (Vérification Cloudflare en cours)")
        time.sleep(7) 
        assert_not_rate_limited(driver, "after_wait")
        assert_not_homepage_redirect(driver, "after_wait")

        # Lire le total de pages attendu depuis la div total-page.
        def get_total_pages(driver):
            try:
                el = driver.find_element(By.CSS_SELECTOR, ".total-page")
                return int(el.text.strip())
            except Exception:
                return None

        while True:
            input("📜 Charge toutes les images dans le navigateur, puis appuie sur Entrée pour continuer...")
            assert_not_rate_limited(driver, "after_scroll")
            assert_not_homepage_redirect(driver, "after_scroll")

            if not driver.current_url.startswith("https://mangafire.to/read/"):
                debug_dom_state(driver, "blocked_after_scroll")
                save_debug_html(driver, "blocked_after_scroll")
                raise RuntimeError(
                    f"Navigation bloquée par Cloudflare (URL actuelle: {driver.current_url}). "
                    "Relance en laissant le navigateur ouvert, complete le check puis reessaie."
                )

            images = extract_page_image_elements(driver)
            debug_dom_state(driver, "after_extract")

            if not images:
                save_debug_html(driver, "no_images_found")
                print("❌ Aucune image trouvée. Vérifiez l'URL ou si le site a changé de structure.")
                return

            total_pages = get_total_pages(driver)
            if total_pages and len(images) < total_pages:
                print(f"⚠️  {len(images)}/{total_pages} images trouvées — {total_pages - len(images)} manquante(s).")
                print("👉 Charge les images manquantes dans le navigateur et rappuie sur Entrée.")
                continue
            if total_pages:
                print(f"✅ {len(images)}/{total_pages} images trouvées.")
            break

        if not os.path.exists(folder_name): os.makedirs(folder_name)

        print(f"🖼️  {len(images)} pages détectées. Téléchargement...")

        # Cookies + headers du navigateur pour débloquer le CDN (cookies tiers requis).
        session_cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        user_agent = driver.execute_script("return navigator.userAgent")
        dl_headers = {
            "Referer": "https://mangafire.to/",
            "User-Agent": user_agent,
        }

        failed = []
        for i, img in enumerate(images):
            img_url = img.get_attribute("src") or img.get_attribute("data-src") or img.get_attribute("data-original")
            if not img_url:
                failed.append(i + 1)
                continue
            file_path = os.path.join(folder_name, f"{i+1:03d}.jpg")
            saved = False
            for attempt in range(1, 6):
                try:
                    res = requests.get(img_url, stream=True, timeout=20, headers=dl_headers, cookies=session_cookies)
                    if res.status_code == 200:
                        with open(file_path, 'wb') as f:
                            for chunk in res.iter_content(1024):
                                f.write(chunk)
                        saved = True
                        break
                    elif res.status_code in (520, 521, 522, 523, 524):
                        wait = attempt * 3
                        print(f"  ⚠️  Page {i+1} — erreur CDN {res.status_code}, retry {attempt}/5 dans {wait}s...")
                        time.sleep(wait)
                    else:
                        print(f"  ⚠️  Page {i+1} — HTTP {res.status_code}, abandon.")
                        break
                except requests.exceptions.RequestException as exc:
                    print(f"  ⚠️  Page {i+1} — réseau: {exc}, retry {attempt}/5...")
                    time.sleep(attempt * 2)
            if not saved:
                failed.append(i + 1)
                placeholder = os.path.join(folder_name, f"{i+1:03d}")
                open(placeholder, 'wb').close()
                print(f"  ❌ Page {i+1} échouée — fichier vide créé.")
            else:
                print(f"  > Page {i+1}/{len(images)}", end="\r")

        if failed:
            print(f"\n⚠️  {len(failed)} page(s) manquante(s) (fichiers vides) : {failed}")

        # --- ARCHIVAGE ---
        print("\nZipper les fichiers...")
        zip_filename = f"{folder_name}.zip" # Tu peux changer en .cbz ici si tu veux
        create_zip(folder_name, zip_filename)

        # Nettoyage (supprime le dossier d'images brutes)
        for root, _, files in os.walk(folder_name):
            for f in files: os.remove(os.path.join(root, f))
        os.rmdir(folder_name)
        print("🧹 Nettoyage terminé. Dossier temporaire supprimé.")

    except RateLimitedError as e:
        print(f"⛔ {e}")
        print("💡 Conseils: ralentis les essais, garde une seule session, et réessaie plus tard.")
    except HomepageRedirectError as e:
        print(f"⛔ {e}")
        print("💡 Conseils: attends que la session expire, puis relance une seule fois avec FlareSolverr.")
    except Exception as e:
        print(f"💥 Erreur : {e}")
    finally:
        driver.quit()
    input("\nAppuie sur Entrée pour fermer...")

if __name__ == "__main__":
    download_manga()