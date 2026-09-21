"""
WAFID - Medical Center Demand Tracker (India)
Page : https://wafid.com/en/accreditation/terms-conditions/
Kaam : India select karke har city ka demand (High/Moderate/Low + numeric rate) padhta hai,
       pichhle run se compare karta hai, aur change hone par Email/Telegram alert bhejta hai.
"""
import csv, json, os, re, smtplib, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path

URL = os.getenv("WAFID_URL", "https://wafid.com/en/accreditation/terms-conditions/")
COUNTRY = os.getenv("WAFID_COUNTRY", "India")
WATCH_CITIES = [c.strip().lower() for c in os.getenv("WATCH_CITIES", "Gorakhpur").split(",") if c.strip()]
BASE = Path(__file__).parent
STATE = BASE / "state" / "latest.json"
HISTORY = BASE / "state" / "history.csv"
DEBUG = BASE / "debug"
IST = timezone(timedelta(hours=5, minutes=30))


def log(*a):
    print(*a, flush=True)


def scrape():
    from playwright.sync_api import sync_playwright  # yahi import, taaki Telegram test bina Playwright chale
    rates = {}  # city_id -> numeric rate, from site's own console log
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1800},
                                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                           "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

        def on_console(msg):
            m = re.search(r"Demand rate for city\s+(\S+)\s*:\s*([\d.]+)", msg.text)
            if m:
                rates[m.group(1)] = m.group(2)
        page.on("console", on_console)

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_selector("#id_city", state="attached", timeout=60000)
            time.sleep(2)

            # --- Method 1 (primary): page ke andar ke data variables seedha padho ---
            # Site ki JS: CITIES[countryCode] = [[id, name], ...] ; cityDemandMap[cityId] = rate
            # Label rule (site ki updateDemandRate): >=80 High, >=30 Moderate, warna Low
            direct = page.evaluate("""(country) => {
                try {
                    if (typeof CITIES === 'undefined' || typeof cityDemandMap === 'undefined') return null;
                    const opt = [...document.querySelectorAll('#id_country option')]
                                .find(o => o.textContent.trim().toLowerCase() === country.toLowerCase());
                    if (!opt) return null;
                    const list = CITIES[opt.value] || [];
                    return list.map(c => ({id: String(c[0]), name: String(c[1]).trim(),
                                           rate: cityDemandMap[c[0]] ?? cityDemandMap[String(c[0])] ?? null}));
                } catch (e) { return null; }
            }""", COUNTRY)
            if direct:
                log(f"{COUNTRY}: {len(direct)} cities (direct data)")
                out = []
                for c in direct:
                    r = c["rate"]
                    lvl = "NO RATE" if r is None else ("High" if r >= 80 else "Moderate" if r >= 30 else "Low")
                    out.append({"city": c["name"], "city_id": c["id"], "level": lvl,
                                "rate": "" if r is None else f"{float(r):.2f}", "error": ""})
                    log(f'  {c["name"]:<18} {lvl:<10} rate={out[-1]["rate"]}')
                return out
            log("Direct data nahi mila - UI click method use kar rahe hain")

            # --- Country select (label "Country" wala Semantic UI dropdown) ---
            country_dd = page.locator(".field").filter(
                has=page.locator("label", has_text=re.compile(r"^\s*Country\s*$"))).locator(".ui.dropdown").first
            country_dd.click()
            search = country_dd.locator("input.search")
            if search.count():
                search.fill(COUNTRY)
            country_dd.locator(".menu .item").filter(
                has_text=re.compile(rf"^\s*{re.escape(COUNTRY)}\s*$")).first.click()

            # --- City list ---
            city_dd = page.locator(".ui.dropdown:has(#id_city)").first
            page.wait_for_function(
                "() => document.querySelectorAll('.ui.dropdown:has(#id_city) .menu .item').length > 0",
                timeout=30000)
            time.sleep(1)
            items = city_dd.locator(".menu .item")
            cities = [{"id": items.nth(i).get_attribute("data-value"),
                       "name": items.nth(i).inner_text().strip()} for i in range(items.count())]
            log(f"{COUNTRY}: {len(cities)} cities found")

            # --- Har city ka demand ---
            results = []
            for c in cities:
                prev_text = page.locator("#demand-text").inner_text() if page.locator("#demand-text").count() else ""
                city_dd.click()
                city_dd.locator(f'.menu .item[data-value="{c["id"]}"]').click()
                level, err = "", ""
                for _ in range(30):  # max ~15 sec
                    time.sleep(0.5)
                    txt = page.locator("#demand-text").inner_text() if page.locator("#demand-text").count() else ""
                    if c["name"].lower() in txt.lower() and page.locator("#demand-rate").is_visible():
                        level = page.locator("#demand-rate-value").inner_text().strip()
                        if level:
                            break
                    if page.locator("#city-error").count() and page.locator("#city-error").is_visible():
                        err = page.locator("#city-error").inner_text().strip()
                        break
                rv = rates.get(c["id"], "")
                try: rv = f"{float(rv):.2f}" if rv != "" else ""
                except ValueError: pass
                results.append({"city": c["name"], "city_id": c["id"], "level": level or "UNKNOWN",
                                "rate": rv, "error": err})
                log(f'  {c["name"]:<18} {level or "UNKNOWN":<10} rate={rates.get(c["id"], "")} {err}')
            return results
        except Exception:
            DEBUG.mkdir(exist_ok=True)
            page.screenshot(path=str(DEBUG / "error.png"), full_page=True)
            (DEBUG / "page.html").write_text(page.content(), encoding="utf-8")
            raise
        finally:
            browser.close()


def compare(old, new):
    o = {r["city"]: r for r in old}
    n = {r["city"]: r for r in new}
    changes = []
    for city in n:
        if city not in o:
            changes.append(f"NEW CITY ADDED: {city} -> {n[city]['level']}")
        elif o[city]["level"] != n[city]["level"]:
            changes.append(f"DEMAND CHANGED: {city}: {o[city]['level']} -> {n[city]['level']}")
    for city in o:
        if city not in n:
            changes.append(f"CITY REMOVED: {city} (was {o[city]['level']})")
    return changes


def table(rows):
    return "\n".join(f"{r['city']:<18} {r['level']:<10} {('rate ' + r['rate']) if r['rate'] else ''}" for r in rows)


def send_email(subject, body):
    user, pwd = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    to = os.getenv("ALERT_TO") or user
    if not (user and pwd):
        log("Email secrets missing - email skipped"); return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pwd)
        s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
    log("Email sent to", to)


def send_telegram(text):
    """Telegram pe message bhejta hai. Secrets na hon to skip. Fail ho to exception deta hai."""
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        log("Telegram secrets missing - Telegram skipped"); return False
    for part in [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]:
        data = urllib.parse.urlencode({"chat_id": chat, "text": part, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(os.getenv("TG_API", "https://api.telegram.org") + f"/bot{tok}/sendMessage", data=data, timeout=30)
    log("Telegram sent"); return True


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tg_report(title, now, rows, changes=None, watch_hits=None, note=""):
    counts = {k: sum(1 for r in rows if r["level"] == k) for k in ("High", "Moderate", "Low")}
    lines = [f"<b>{esc(title)}</b>", f"{esc(now)}", ""]
    if watch_hits:
        lines.append("<b>WATCHED CITY LISTED: " + esc(", ".join(f"{r['city']} ({r['level']})" for r in watch_hits)) + "</b>")
    else:
        lines.append(f"Gorakhpur listed: NO" if "gorakhpur" in WATCH_CITIES else "Watched cities listed: NO")
    if changes is None:
        pass
    elif changes:
        lines += ["", "<b>Changes since last check:</b>"] + [f"- {esc(c)}" for c in changes]
    else:
        lines += ["", "No change since last check."]
    lines += ["", f"{COUNTRY}: {len(rows)} cities | High {counts['High']} | Moderate {counts['Moderate']} | Low {counts['Low']}",
              "<pre>" + esc(f"{'City':<16}{'Level':<10}Rate\n" + "\n".join(
                  f"{r['city'][:15]:<16}{r['level']:<10}{r['rate']}" for r in rows)) + "</pre>",
              "Rule: rate &gt;=80 High, &gt;=30 Moderate, else Low"]
    if note:
        lines += ["", esc(note)]
    return "\n".join(lines)


def main():
    mode = os.getenv("RUN_MODE", "normal").strip().lower()   # normal | test-telegram | test-full
    now = datetime.now(IST).strftime("%d-%m-%Y %H:%M IST")

    # --- Test 1: sirf Telegram connection check (wafid nahi kholta) ---
    if mode == "test-telegram":
        ok = send_telegram(f"<b>[TEST] WAFID tracker - Telegram connection OK</b>\n{esc(now)}\n\n"
                           "Ye test message hai. Hourly updates isi chat pe aayenge.")
        sys.exit(0 if ok else 1)

    try:
        new = scrape()
        if not new:
            raise RuntimeError("0 cities mile - page structure badla ho sakta hai")
    except Exception as e:
        log("SCRAPE FAILED:", e)
        msg = f"<b>{'[TEST] ' if mode == 'test-full' else ''}WAFID tracker FAILED</b>\n{esc(now)}\n\n{esc(e)}\n\nGitHub Actions run ka 'debug' artifact dekhein."
        try: send_telegram(msg)
        except Exception as te: log("Telegram error:", te)
        if os.getenv("ALERT_ON_FAILURE", "false").lower() == "true":
            try: send_email("WAFID tracker FAILED", f"{now}\n\n{e}")
            except Exception as me: log("Email error:", me)
        sys.exit(1)

    watch_hits = [r for r in new if r["city"].lower() in WATCH_CITIES]
    old = json.loads(STATE.read_text())["cities"] if STATE.exists() else None
    changes = compare(old, new) if old is not None else None

    # --- Test 2: poora check chalao, Telegram pe bhejo, par state/history save mat karo ---
    if mode == "test-full":
        send_telegram(tg_report("[TEST] WAFID demand check - full run", now, new, changes, watch_hits,
                                note="Test run: state/history save nahi hui."))
        return

    # --- Normal hourly run ---
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"checked_at": now, "country": COUNTRY, "cities": new}, indent=2))
    first = not HISTORY.exists()
    with HISTORY.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if first:
            w.writerow(["checked_at", "country", "city", "city_id", "level", "rate", "error"])
        for r in new:
            w.writerow([now, COUNTRY, r["city"], r["city_id"], r["level"], r["rate"], r["error"]])

    hit = bool(watch_hits) and (changes is None or any(w in c.lower() for c in changes for w in WATCH_CITIES))
    if old is None:
        title = "WAFID tracker started - baseline"
    elif changes:
        title = ("URGENT: " if hit else "") + f"WAFID demand CHANGE ({len(changes)})"
    else:
        title = "WAFID hourly update"

    # Telegram: har ghante update (HOURLY_TELEGRAM=true), warna sirf change pe
    if os.getenv("HOURLY_TELEGRAM", "true").lower() == "true" or old is None or changes:
        send_telegram(tg_report(title, now, new, changes, watch_hits))

    # Email: sirf baseline ya change pe (optional)
    if old is None or changes:
        body = f"{now}\n\n" + ("Changes:\n- " + "\n- ".join(changes) + "\n\n" if changes else "") + table(new)
        try: send_email(title, body)
        except Exception as me: log("Email error:", me)
    log(title)


if __name__ == "__main__":
    main()
