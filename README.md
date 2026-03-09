# 🏥 DMED Documents Bot

Telegram bot orqali mehnatga layoqatsizlik ma'lumotnomasi yaratish tizimi.

---

## 📁 Fayl tuzilmasi

```
dmed_bot/
├── main.py          ← Asosiy ishga tushiruvchi
├── bot.py           ← Telegram bot logikasi
├── web.py           ← Flask web server
├── requirements.txt ← Python kutubxonalar
├── railway.toml     ← Railway konfiguratsiya
├── .env.example     ← Muhit o'zgaruvchilari namunasi
├── templates/
│   └── pin.html     ← Hujjat ko'rish sahifasi
└── data/            ← JSON ma'lumotlar (avtomatik yaratiladi)
    ├── users.json
    └── documents.json
```

---

## 🚀 Railway.app ga deploy qilish

### 1. Bot yaratish
1. Telegram'da [@BotFather](https://t.me/BotFather) ga yozing
2. `/newbot` buyrug'ini yuboring
3. Bot nomini kiriting (misol: `DMED Documents`)
4. Bot username kiriting (misol: `dmed_documents_bot`)
5. **Token** ni nusxalab oling

### 2. Telegram Stars to'lovini yoqish
1. @BotFather ga `/mybots` yozing
2. Botingizni tanlang
3. `Bot Settings` → `Payments` → `Telegram Stars` ni tanlang

### 3. GitHub'ga yuklash
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/SIZNING_USERNAME/dmed-bot.git
git push -u origin main
```

### 4. Railway'da deploy
1. [railway.app](https://railway.app) ga kiring
2. `New Project` → `Deploy from GitHub repo`
3. Repositoriyangizni tanlang
4. `Variables` bo'limiga o'ting va quyidagilarni kiriting:

```
BOT_TOKEN      = (BotFather'dan olgan token)
ADMIN_ID       = (Sizning Telegram ID: @userinfobot dan oling)
WEB_URL        = (Keyinroq, deploy bo'lgach URL ni ko'ring)
STARS_PRICE    = 50
```

5. Deploy bo'lishini kuting
6. `Settings` → `Networking` → `Generate Domain` bosing
7. Ko'rsatilgan URL ni `WEB_URL` ga yozing
8. Botni qayta restart qiling

---

## 💡 Ishlatish

### Foydalanuvchi uchun:
1. Botga `/start` yuboring
2. `⭐ Obuna sotib olish` bosing → 50 Stars to'lang
3. `📋 Hujjat yaratish` bosing
4. Barcha ma'lumotlarni kiriting (12 ta savol)
5. **Havola + PIN** olasiz
6. Havolani oching → PIN kiriting → Hujjat chiqadi
7. 🖨️ Chop etish tugmasi bilan print qiling

### Admin uchun:
- `/stats` — statistika ko'rish

---

## 🔧 Local test uchun

```bash
pip install -r requirements.txt

# .env fayl yarating
cp .env.example .env
# .env faylni tahrirlang

# Ishga tushiring
python main.py
```

---

## ⚠️ Muhim eslatmalar

- `data/` papkasi Railway'da har restart'da tozalanadi
- Doimiy saqlash uchun **Railway Volume** yoki **PostgreSQL** ishlatish tavsiya etiladi
- Hozirgi kod oddiy JSON faylda saqlaydi (test uchun yetarli)

---

## 📞 Yordam

Muammo bo'lsa, admin bilan bog'laning.
