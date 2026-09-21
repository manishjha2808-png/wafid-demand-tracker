# WAFID Medical Center Demand Tracker (India) – Telegram hourly updates

Ye tracker har ghante https://wafid.com/en/accreditation/terms-conditions/ se India ki saari cities ka demand (rate + High/Moderate/Low) padhta hai, aur **har ghante Telegram pe update bhejta hai**. Har update mein ye cheezein hoti hain:
- Gorakhpur listed hai ya nahi
- pichhle ghante se kya badla (nayi city, demand change, city hati)
- saari cities ki table, rate ke saath

Gorakhpur list mein aaye to message ka title **URGENT** se shuru hoga.
Label ka rule WAFID ki apni script se liya gaya hai: rate >=80 High, >=30 Moderate, baaki Low.

---

## Setup (ek baar, ~15 minute)

### Step 1 – Telegram bot banayein
1. Telegram mein **@BotFather** search karke kholiye aur `/newbot` bhejiye.
2. Bot ka naam dijiye (jaise `WAFID Tracker`), phir username dijiye jo `bot` pe khatam ho (jaise `manish_wafid_bot`).
3. BotFather ek **token** dega, jaise `7123456789:AAH...xyz`. Ise copy kar lijiye, ye `TELEGRAM_BOT_TOKEN` hai.
4. Apne naye bot ki chat kholiye, **Start** dabaiye aur koi bhi message bhejiye (jaise `hi`).
5. Browser mein ye link kholiye, `<TOKEN>` ki jagah apna token daal ke:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Wahan `"chat":{"id":123456789` jaisa number dikhega. Ye `TELEGRAM_CHAT_ID` hai.
   *(Agar result khaali `[]` aaye, to bot ko ek aur message bhej ke page refresh kijiye.)*

### Step 2 – GitHub repository
1. https://github.com pe login karke **New repository** pe click kijiye. Naam rakhiye `wafid-demand-tracker` aur **Public** chuniye. Public mein Actions ke minutes unlimited free hain, aur token Secrets mein encrypted rehta hai.
2. **Add file → Upload files** se is zip ki saari files upload kijiye, `.github/workflows/wafid-demand.yml` samet.
   Agar folder upload na ho, to **Add file → Create new file** mein naam `.github/workflows/wafid-demand.yml` likh ke content paste kar dijiye.

### Step 3 – Secrets daalein
Repo → **Settings → Secrets and variables → Actions → New repository secret** pe jaake ye secrets daalein:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Step 1.3 ka token |
| `TELEGRAM_CHAT_ID` | Step 1.5 ka number |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_TO` | *(optional)* email bhi chahiye to. Email sirf change pe aata hai |

### Step 4 – Permission
Repo → **Settings → Actions → General → Workflow permissions** mein **Read and write permissions** select karke Save kijiye.

---

## Testing option (Actions tab → WAFID Demand Tracker → **Run workflow**)

Dropdown mein 3 option hain:

| Mode | Kya karta hai | Kab use karein |
|---|---|---|
| `test-telegram` | Sirf ek test message bhejta hai, wafid nahi kholta (~20 sec) | Token/Chat ID sahi hai ya nahi, ye check karne ke liye |
| `test-full` | Wafid se poora data padh ke Telegram pe bhejta hai, par history save nahi karta | Kabhi bhi current status turant dekhne ke liye |
| `normal` | Bilkul hourly run jaisa (history save hoti hai) | Baseline turant set karni ho |

**Pehle `test-telegram` chalaiye, phir `test-full`.** Dono ke message Telegram pe aa jayein to setup poora hai. Iske baad har ghante (IST :47 ke aas-paas) update apne aap aayega.

---

## Settings (`.github/workflows/wafid-demand.yml`)
- `HOURLY_TELEGRAM: "true"` → har ghante message. `"false"` karne pe sirf change hone pe aayega.
- `WATCH_CITIES: "Gorakhpur"` → aur cities comma se jod sakte hain (`"Gorakhpur,Lucknow"`).
- `cron: "17 * * * *"` → schedule UTC mein hai.

## Dhyan dein
- GitHub ka hourly schedule kabhi-kabhi 5–30 minute late chalta hai, aur bahut busy time pe koi run skip bhi ho sakta hai. Ye GitHub ki limitation hai.
- Koi run fail ho to Telegram pe **FAILED** message aayega. Tab Actions mein us run ka **debug** artifact download karke bhejiye.
- Pura record `state/history.csv` mein save hota hai, jise Excel mein khol sakte hain.
