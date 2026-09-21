"""
WAFID - Accreditation Application Status Tracker
Page : https://wafid.com/wafid-ui/#/accreditation-request  (wafid.com/en/new/accreditation-request/ ke andar ka iframe)

Kya karta hai:
  - Har Accreditation ID ke liye page kholta hai, ID daalta hai, "Check" dabata hai
    (bilkul waise jaise aap browser mein karte hain) aur result card padhta hai.
  - reCAPTCHA ke saath koi chhed-chhaad / evasion NAHI karta. Agar site request rok de,
    to us ID ko "BLOCKED/ERROR" report karta hai aur pichhla status hi maanta hai.
  - Pichhle run se compare karke Telegram pe hourly summary + change pe ALERT bhejta hai.

Privacy: IDs secret WAFID_IDS mein rehti hain, state GitHub Actions cache mein
(public repo mein commit nahi hoti). Poora result sirf aapke private Telegram chat mein jaata hai.

WAFID_IDS format:  id|Name|City;id|Name|City;...
"""
import hashlib, json, os, re, sys, time, uuid, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

URL = os.getenv("WAFID_STATUS_URL", "https://wafid.com/wafid-ui/#/accreditation-request")
STATE_DIR = Path(os.getenv("STATUS_STATE_DIR", Path(__file__).parent / "status_state"))
STATE = STATE_DIR / "latest.json"
IST = timezone(timedelta(hours=5, minutes=30))
GAP_SECONDS = float(os.getenv("GAP_SECONDS", "6"))   # har ID ke beech thoda ruko


def log(*a):
    print(*a, flush=True)


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_ids():
    raw = os.getenv("WAFID_IDS", "").strip()
    apps = []
    for part in [p for p in re.split(r"[;\n]", raw) if p.strip()]:
        bits = [b.strip() for b in part.split("|")]
        apps.append({"id": bits[0], "name": bits[1] if len(bits) > 1 else bits[0],
                     "city": bits[2] if len(bits) > 2 else ""})
    return apps


# ---------------- Telegram ----------------
def _tg():
    return os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"), os.getenv("TG_API", "https://api.telegram.org")


def send_telegram(text):
    tok, chat, api = _tg()
    if not (tok and chat):
        log("Telegram secrets missing - skipped"); return False
    for i in range(0, max(len(text), 1), 3900):
        data = urllib.parse.urlencode({"chat_id": chat, "text": text[i:i + 3900], "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(f"{api}/bot{tok}/sendMessage", data=data, timeout=30)
    log("Telegram sent"); return True


def send_telegram_photo(path, caption):
    tok, chat, api = _tg()
    if not (tok and chat) or not Path(path).exists():
        return
    b = uuid.uuid4().hex
    body = b"".join([
        f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}\r\n".encode(),
        f"--{b}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1000]}\r\n".encode(),
        f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"s.png\"\r\nContent-Type: image/png\r\n\r\n".encode(),
        Path(path).read_bytes(), f"\r\n--{b}--\r\n".encode()])
    req = urllib.request.Request(f"{api}/bot{tok}/sendPhoto", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        urllib.request.urlopen(req, timeout=60); log("Telegram photo sent")
    except Exception as e:
        log("Telegram photo error:", e)


# ---------------- Scrape ----------------
READ_FIELDS_JS = """(id) => {
  const card = document.querySelector('[class*="_resultsCard"]');
  if (!card) return null;
  const items = [...card.querySelectorAll('[class*="_fieldItem"]')];
  const out = items.map(it => ({
    label: ((it.querySelector('[class*="_fieldLabel"]') || {}).innerText || '').trim(),
    value: ((it.querySelector('[class*="_fieldValue"]') || {}).innerText || '').trim()
  })).filter(f => f.label || f.value);
  if (!out.length) { const t = card.innerText.trim(); if (t) out.push({label: 'Result', value: t}); }
  return out;
}"""

READ_MSGS_JS = """() => [...document.querySelectorAll(
  '.ant-message-notice, .ant-notification-notice, .ant-alert, .ant-result, .ant-form-item-explain-error, .ant-empty')]
  .map(e => e.innerText.trim()).filter(Boolean)"""


def find_status_in_json(obj, depth=0):
    if depth > 4 or obj is None:
        return ""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "status" in str(k).lower() and isinstance(v, (str, int, float)) and str(v).strip():
                return str(v)
        for v in obj.values():
            s = find_status_in_json(v, depth + 1)
            if s:
                return s
    if isinstance(obj, list):
        for v in obj[:5]:
            s = find_status_in_json(v, depth + 1)
            if s:
                return s
    return ""


def check_one(page, app):
    aid = app["id"]
    res = {"id": aid, "name": app["name"], "city": app["city"], "ok": False,
           "http": None, "status": "", "fields": [], "error": ""}
    inp = page.locator('input[placeholder="Search"]').first
    inp.wait_for(state="visible", timeout=45000)
    inp.fill("")
    inp.fill(aid)
    pat = re.compile(rf"/{re.escape(aid)}(\?|$)", re.I)
    try:
        with page.expect_response(lambda r: bool(pat.search(r.url)), timeout=45000) as ri:
            page.get_by_role("button", name=re.compile(r"^\s*Check\s*$", re.I)).first.click()
        resp = ri.value
        res["http"] = resp.status
        try:
            data = resp.json()
        except Exception:
            data = None
    except Exception as e:
        res["error"] = f"Site se response nahi aaya ({type(e).__name__})"
        return res

    time.sleep(2.5)
    fields = page.evaluate(READ_FIELDS_JS, aid) or []
    msgs = page.evaluate(READ_MSGS_JS) or []
    id_shown = any(f["value"].strip().lower() == aid.lower() for f in fields)

    if res["http"] and res["http"] >= 400:
        blob = " ".join(msgs) + " " + json.dumps(data)[:300] if data is not None else " ".join(msgs)
        kind = "BLOCKED (reCAPTCHA/permission)" if res["http"] in (401, 403) or "captcha" in blob.lower() else "ERROR"
        res["error"] = f"{kind}: HTTP {res['http']} {blob.strip()[:250]}"
        return res
    if not id_shown:
        res["error"] = "Result card mein ye ID nahi dikhi. " + (" | ".join(msgs)[:250] if msgs else "")
        return res

    res["ok"] = True
    res["fields"] = fields
    st = next((f["value"] for f in fields if re.search(r"status", f["label"], re.I)), "")
    res["status"] = st or find_status_in_json(data) or "(status field nahi mila)"
    return res


def scrape(apps):
    from playwright.sync_api import sync_playwright
    results, shot = [], None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)          # koi stealth / UA spoofing nahi
        page = browser.new_page(viewport={"width": 1280, "height": 1600})
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_selector('input[placeholder="Search"]', timeout=60000)
            time.sleep(3)
            for i, app in enumerate(apps):
                if i:
                    time.sleep(GAP_SECONDS)
                try:
                    r = check_one(page, app)
                except Exception as e:
                    r = {"id": app["id"], "name": app["name"], "city": app["city"], "ok": False,
                         "http": None, "status": "", "fields": [], "error": f"{type(e).__name__}: {str(e)[:200]}"}
                log(f"  {app['id']}: ok={r['ok']} status={r['status']!r} err={r['error'][:120]}")
                results.append(r)
            if not any(r["ok"] for r in results):
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                shot = str(STATE_DIR / "error.png")
                page.screenshot(path=shot, full_page=True)
        except Exception as e:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            shot = str(STATE_DIR / "error.png")
            try: page.screenshot(path=shot, full_page=True)
            except Exception: shot = None
            raise RuntimeError(f"Page load fail: {type(e).__name__}: {str(e)[:200]}") from None
        finally:
            browser.close()
    return results, shot


def fingerprint(r):
    return hashlib.sha256(json.dumps(r["fields"], sort_keys=True).encode()).hexdigest()[:16]


# ---------------- Report ----------------
def block(r, old, changed):
    head = f"<b>{esc(r['name'])}</b> ({esc(r['id'])})" + (f" – {esc(r['city'])}" if r["city"] else "")
    lines = [("🔴 CHANGED  " if changed else "") + head]
    if not r["ok"]:
        lines.append(f"⚠️ Check nahi ho paya: {esc(r['error'])}")
        if old:
            lines.append(f"Last known status: <b>{esc(old.get('status', ''))}</b> ({esc(old.get('checked_at', ''))})")
        return "\n".join(lines)
    if changed and old:
        lines.append(f"Status: {esc(old.get('status', ''))} → <b>{esc(r['status'])}</b>")
    else:
        lines.append(f"Status: <b>{esc(r['status'])}</b>")
    for f in r["fields"]:
        if re.search(r"status", f["label"], re.I) or f["value"].strip().lower() == r["id"].lower():
            continue
        lines.append(f"• {esc(f['label'])}: {esc(f['value'][:200])}")
    return "\n".join(lines)


def main():
    mode = os.getenv("RUN_MODE", "normal").strip().lower()
    now = datetime.now(IST).strftime("%d-%m-%Y %H:%M IST")

    if mode == "test-telegram":
        ok = send_telegram(f"<b>[TEST] WAFID Application Status tracker - Telegram OK</b>\n{esc(now)}")
        sys.exit(0 if ok else 1)

    apps = parse_ids()
    if not apps:
        send_telegram("<b>WAFID status tracker</b>\nWAFID_IDS secret khaali hai. Settings → Secrets mein add kijiye.")
        sys.exit(1)

    old_state = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        results, shot = scrape(apps)
    except Exception as e:
        log("FAILED:", e)
        send_telegram(f"<b>{'[TEST] ' if mode == 'test-full' else ''}WAFID status tracker FAILED</b>\n{esc(now)}\n\n{esc(e)}")
        send_telegram_photo(str(STATE_DIR / "error.png"), "WAFID status page - error screenshot")
        sys.exit(1)

    new_state, blocks, n_changed = dict(old_state), [], 0
    for r in results:
        old = old_state.get(r["id"])
        changed = False
        if r["ok"]:
            fp = fingerprint(r)
            changed = bool(old) and (old.get("fp") != fp)
            new_state[r["id"]] = {"status": r["status"], "fp": fp, "checked_at": now}
        n_changed += changed
        blocks.append(block(r, old, changed))

    n_ok = sum(r["ok"] for r in results)
    first = not old_state
    if mode == "test-full":
        title = "[TEST] WAFID application status"
    elif first:
        title = "WAFID application status - tracker started"
    elif n_changed:
        title = f"🔴 ALERT: WAFID application status CHANGED ({n_changed})"
    else:
        title = "WAFID application status - hourly update"
    summary = f"Checked {n_ok}/{len(results)}" + ("" if n_ok == len(results) else " (baaki ke liye last known status dikhaya hai)")
    if not n_changed and not first and mode != "test-full":
        summary += " | No change"
    msg = f"<b>{esc(title)}</b>\n{esc(now)}\n{esc(summary)}\n\n" + "\n\n".join(blocks)

    if mode == "test-full" or first or n_changed or os.getenv("HOURLY_TELEGRAM", "true").lower() == "true":
        send_telegram(msg)
    if shot:
        send_telegram_photo(shot, "Koi bhi ID check nahi ho payi - page screenshot")

    if mode == "normal":
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new_state, indent=2))
    log(title)
    sys.exit(0 if n_ok else 1)


if __name__ == "__main__":
    main()
