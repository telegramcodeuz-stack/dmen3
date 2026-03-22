"""
AI Quiz Bot
- Groq AI orqali avtomatik test tuzadi
- @QuizBot ga yuboradi va havola beradi
- Knopkali interfeys
- Ko'p akkaunt pool
"""

import asyncio
import json
import logging
import os
import random
import re
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from groq import Groq
from telethon import TelegramClient, events, Button
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, TextWithEntities

# ============================================================
#  SOZLAMALAR — Railway environment variables dan o'qiladi
# ============================================================
import os as _os

BOT_TOKEN    = _os.environ["BOT_TOKEN"]
API_ID       = int(_os.environ["API_ID"])
API_HASH     = _os.environ["API_HASH"]
GROQ_API_KEY = _os.environ["GROQ_API_KEY"]
ADMIN_IDS    = [int(x) for x in _os.environ.get("ADMIN_IDS", "0").split(",") if x.strip()]
NOTIFY_PHONE = _os.environ.get("NOTIFY_PHONE", "")

# Telefon raqamlar: PHONE_NUMBERS env da vergul bilan yoziladi
# Misol: +998901234567,+998901234568
PHONE_NUMBERS = [
    p.strip() for p in _os.environ.get("PHONE_NUMBERS", "").split(",")
    if p.strip()
]

# Humo kartalar: HUMO_CARDS env da vergul bilan
# Misol: 9860 1234 5678 9001,9860 1234 5678 9002
HUMO_CARDS = [
    c.strip() for c in _os.environ.get("HUMO_CARDS", "").split(",")
    if c.strip()
]

AD_EVERY   = int(_os.environ.get("AD_EVERY", "6"))
AD_TEXT    = _os.environ.get("AD_TEXT", "📢 @quiz_import_bot — AI yordamida @QuizBot testi yarating!")
GROQ_MODEL = _os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

ACCOUNTS_FILE = _os.environ.get("ACCOUNTS_FILE", "/data/accounts.json")
DB_FILE       = _os.environ.get("DB_FILE",       "/data/bot.db")

# /data papkasi bo'lmasa — local papkada saqlaymiz
if not _os.path.exists("/data"):
    _os.makedirs("data", exist_ok=True)
    ACCOUNTS_FILE = "data/accounts.json"
    DB_FILE       = "data/bot.db"


# ============================================================
#  TO'LOV TIZIMI
# ============================================================

# Humo kartalar ro'yxati — navbat bilan beriladi
# ⬇️ O'z karta raqamlaringizni shu yerga yozing!
HUMO_CARDS = [
    "9860 XXXX XXXX 0001",   # karta 1 — o'zgartiring
    "9860 XXXX XXXX 0002",   # karta 2 — o'zgartiring
    "9860 XXXX XXXX 0003",   # karta 3 — o'zgartiring
    "9860 XXXX XXXX 0004",   # karta 4 — o'zgartiring
    "9860 XXXX XXXX 0005",   # karta 5 — o'zgartiring
]
AI_PRICE           = 2000    # 1 ta AI test narxi (so'm)
FILE_PRICE_PER_25  = 2000    # har 25 savol uchun narx (fayl orqali)
PAYMENT_TIMEOUT    = 180     # sekund (3 daqiqa)
HUMOCARD_BOT    = "@humocardbot"
NOTIFY_PHONE    = "+998934897111"  # @humocardbot xabar keladigan raqam

# Kartalar band/bo'sh holati: card_num -> user_id yoki None
card_assignments: dict = {card: None for card in HUMO_CARDS}


def get_free_card(user_id: int) -> Optional[str]:
    """Bo'sh karta berish — navbat bilan"""
    busy = set(card_assignments.values())
    for card in HUMO_CARDS:
        if card_assignments.get(card) is None:
            card_assignments[card] = user_id
            return card
    return None   # hammasi band


def calc_file_price(q_count: int) -> int:
    """Fayl uchun narx: har 25 savolga 2000 so'm, qisman bo'lsa ham to'liq hisoblanadi"""
    import math
    blocks = math.ceil(q_count / 25)
    return blocks * FILE_PRICE_PER_25


def release_card(card_num: str):
    """Kartani bo'shatish"""
    card_assignments[card_num] = None


def db_save_user(user_id: int, first_name: str = "",
                 last_name: str = "", username: str = ""):
    """Foydalanuvchini saqlash yoki yangilash"""
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        INSERT INTO users (user_id, first_name, last_name, username, created_at, last_seen)
        VALUES (?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET
            first_name = excluded.first_name,
            last_name  = excluded.last_name,
            username   = excluded.username,
            last_seen  = datetime('now','localtime')
    """, (user_id, first_name or "", last_name or "", username or ""))
    con.commit()
    con.close()


def db_count_users() -> int:
    """Jami foydalanuvchilar soni"""
    con = sqlite3.connect(DB_FILE)
    n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    con.close()
    return n


def db_get_users(limit: int = 20, offset: int = 0) -> list:
    """Foydalanuvchilar ro'yxati"""
    con = sqlite3.connect(DB_FILE)
    rows = con.execute("""
        SELECT user_id, first_name, last_name, username, created_at, last_seen
        FROM users ORDER BY last_seen DESC LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    con.close()
    return rows


def db_get_user(user_id: int):
    """Bitta foydalanuvchi"""
    con = sqlite3.connect(DB_FILE)
    row = con.execute(
        "SELECT * FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    con.close()
    return row


def db_get_balance(user_id: int) -> int:
    con = sqlite3.connect(DB_FILE)
    row = con.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    con.close()
    return row[0] if row else 0



def db_add_balance(user_id: int, amount: int, reason: str = ""):
    con = sqlite3.connect(DB_FILE)
    con.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (amount, user_id)
    )
    con.execute(
        """INSERT INTO balance_log (user_id, amount, reason, created_at)
           VALUES (?, ?, ?, datetime('now','localtime'))""",
        (user_id, amount, reason)
    )
    con.commit()
    con.close()


def db_deduct_balance(user_id: int, amount: int, reason: str = "") -> bool:
    con = sqlite3.connect(DB_FILE)
    row = con.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row or row[0] < amount:
        con.close()
        return False
    con.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id=?",
        (amount, user_id)
    )
    con.execute(
        """INSERT INTO balance_log (user_id, amount, reason, created_at)
           VALUES (?, ?, ?, datetime('now','localtime'))""",
        (user_id, -amount, reason)
    )
    con.commit()
    con.close()
    return True


def db_create_payment(user_id: int, card_num: str, amount: int) -> int:
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(
        """INSERT INTO payments
           (user_id, card_num, amount, status, created_at, expires_at)
           VALUES (?, ?, ?, 'pending',
                   datetime('now','localtime'),
                   datetime('now','localtime','+3 minutes'))""",
        (user_id, card_num, amount)
    )
    pay_id = cur.lastrowid
    con.commit()
    con.close()
    return pay_id


def db_get_pending(user_id: int) -> Optional[tuple]:
    """Foydalanuvchining kutilayotgan to'lovi (id, card, amount, expires)"""
    con = sqlite3.connect(DB_FILE)
    row = con.execute(
        """SELECT id, card_num, amount, expires_at FROM payments
           WHERE user_id=? AND status='pending'
           AND expires_at > datetime('now','localtime')
           ORDER BY id DESC LIMIT 1""",
        (user_id,)
    ).fetchone()
    con.close()
    return row


def db_confirm_payment(pay_id: int) -> Optional[tuple]:
    """To'lovni tasdiqlash → (user_id, amount, card_num)"""
    con = sqlite3.connect(DB_FILE)
    row = con.execute(
        "SELECT user_id, amount, card_num FROM payments WHERE id=? AND status='pending'",
        (pay_id,)
    ).fetchone()
    if row:
        con.execute(
            """UPDATE payments SET status='confirmed',
               paid_at=datetime('now','localtime') WHERE id=?""",
            (pay_id,)
        )
        con.commit()
    con.close()
    return row


def db_expire_old():
    """Muddati o'tgan to'lovlarni bekor qilish"""
    con = sqlite3.connect(DB_FILE)
    expired = con.execute(
        """SELECT id, card_num FROM payments WHERE status='pending'
           AND expires_at < datetime('now','localtime')"""
    ).fetchall()
    for pay_id, card_num in expired:
        con.execute(
            "UPDATE payments SET status='expired' WHERE id=?", (pay_id,)
        )
        release_card(card_num)
    con.commit()
    con.close()
    return len(expired)


def db_payment_stats() -> dict:
    con = sqlite3.connect(DB_FILE)
    total = con.execute(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='confirmed'"
    ).fetchone()[0]
    today = con.execute(
        """SELECT COALESCE(SUM(amount),0) FROM payments
           WHERE status='confirmed' AND DATE(paid_at)=DATE('now','localtime')"""
    ).fetchone()[0]
    pending = con.execute(
        "SELECT COUNT(*) FROM payments WHERE status='pending'"
    ).fetchone()[0]
    con.close()
    return {"total": total, "today": today, "pending": pending}


def db_init():
    """Barcha jadvallarni yaratish"""
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            first_name  TEXT DEFAULT '',
            last_name   TEXT DEFAULT '',
            username    TEXT DEFAULT '',
            balance     INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT '',
            last_seen   TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS payments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            card_num    TEXT NOT NULL,
            amount      INTEGER NOT NULL,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT DEFAULT '',
            paid_at     TEXT DEFAULT '',
            expires_at  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS balance_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount      INTEGER NOT NULL,
            reason      TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS quizzes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            fan_name    TEXT DEFAULT '',
            q_count     INTEGER DEFAULT 0,
            variant_num INTEGER DEFAULT 1,
            url         TEXT DEFAULT '',
            time_choice TEXT DEFAULT '30',
            order_type  TEXT DEFAULT 'order',
            source      TEXT DEFAULT 'ai',
            created_at  TEXT DEFAULT ''
        );
    """)
    # Eski jadvalga ustun qo'shish (mavjud DB uchun)
    for col_sql in [
        "ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0",
    ]:
        try:
            cur.execute(col_sql)
            con.commit()
        except Exception:
            pass
    con.commit()
    con.close()


def db_save_quiz(user_id: int, fan_name: str, q_count: int,
                 variant_num: int, url: str, time_choice: str,
                 order_type: str, source: str = "ai") -> int:
    """Quiz ma'lumotlarini saqlash, id qaytaradi"""
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO quizzes
        (user_id, fan_name, q_count, variant_num, url, time_choice, order_type, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
    """, (user_id, fan_name, q_count, variant_num, url, time_choice, order_type, source))
    quiz_id = cur.lastrowid
    con.commit()
    con.close()
    return quiz_id


def db_get_user_quizzes(user_id: int, limit: int = 20) -> list:
    """Foydalanuvchining quizlari"""
    con = sqlite3.connect(DB_FILE)
    rows = con.execute("""
        SELECT id, fan_name, q_count, variant_num, url, source, created_at
        FROM quizzes
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    con.close()
    return rows


def db_count_user_quizzes(user_id: int) -> int:
    con = sqlite3.connect(DB_FILE)
    n = con.execute(
        "SELECT COUNT(*) FROM quizzes WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    con.close()
    return n


# ============================================================
#  LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("AIQuizBot")

# ============================================================
#  GROQ AI — TEST TUZISH
# ============================================================
groq_client = Groq(api_key=GROQ_API_KEY)

LANGUAGES = {
    "uz": "O'zbek tilida",
    "ru": "Rus tilida",
    "en": "Ingliz tilida",
}

def build_prompt(fan: str, count: int, lang: str = "uz",
                 difficulty: str = "o'rta", topic: str = "") -> str:
    lang_text = LANGUAGES.get(lang, "O'zbek tilida")
    mavzu_text = f'Mavzu: "{topic}"' if topic else ""
    return f"""Siz {lang_text} test tuzuvchi mutaxassissiz.
"{fan}" fanidan {count} ta test savoli tuz.
{mavzu_text}
Qiyinlik darajasi: {difficulty}.

MUHIM QOIDALAR:
1. Faqat JSON formatda qaytar, boshqa hech narsa yozma
2. Har savolda 4 ta variant bo'lsin
3. Faqat bitta to'g'ri javob bo'lsin
4. Savollar mantiqli va aniq bo'lsin
5. Variantlar bir-biridan farqli bo'lsin
{f'6. Faqat "{topic}" mavzusidan savol tuz' if topic else ""}

JSON format (qat'iy shu ko'rinishda):
[
  {{
    "q": "savol matni",
    "opts": ["variant A", "variant B", "variant C", "variant D"],
    "ans": 0
  }}
]

ans — to'g'ri javob indeksi (0=A, 1=B, 2=C, 3=D)

Hozir {fan} fanidan{f' ({topic} mavzusidan)' if topic else ''} {count} ta savol yoz:"""


async def generate_questions(fan: str, count: int, lang: str = "uz",
                              difficulty: str = "o'rta", topic: str = "") -> list:
    """Groq AI orqali savollar generatsiya qilish — xatolarga chidamli"""
    prompt = build_prompt(fan, count, lang, difficulty, topic)
    log.info(f"AI so'rov: {fan} | mavzu: {topic or 'yo\'q'} | {count} ta | {lang} | {difficulty}")

    loop = asyncio.get_event_loop()

    def _call():
        return groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Siz faqat JSON formatda javob beradigan test tuzuvchisiz. "
                        "Hech qanday izoh, markdown, kod bloki yozma. "
                        "Faqat [ ... ] JSON massivi."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=4000,
        )

    resp = await loop.run_in_executor(None, _call)
    raw = resp.choices[0].message.content.strip()
    log.info(f"AI xom javob (dastlabki 200 belgi): {raw[:200]}")

    questions = _safe_parse_json(raw)

    if not questions:
        log.warning("Birinchi urinish muvaffaqiyatsiz, qayta so'rov yuborilmoqda...")
        # Qayta so'rov — yanada qattiqroq
        def _call2():
            return groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "Faqat sof JSON massivi qaytar. Hech narsa boshqa yo'q."
                    },
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "["},
                ],
            temperature=0.3,
            max_tokens=4000,
            )
        resp2 = await loop.run_in_executor(None, _call2)
        raw2 = "[" + resp2.choices[0].message.content.strip()
        log.info(f"AI 2-javob: {raw2[:200]}")
        questions = _safe_parse_json(raw2)

    # Tekshirish va tozalash
    valid = []
    for q in (questions or []):
        if (isinstance(q, dict) and
                "q" in q and "opts" in q and "ans" in q and
                len(q["opts"]) >= 2 and
                0 <= int(q.get("ans", 0)) < len(q["opts"])):
            valid.append({
                "q": str(q["q"])[:255],
                "opts": [str(o)[:100] for o in q["opts"]],
                "ans": int(q["ans"])
            })

    log.info(f"AI {len(valid)} ta savol yaratdi")
    return valid


def _safe_parse_json(text: str) -> Optional[list]:
    """JSON ni xavfsiz parse qilish — bir necha usul bilan"""
    if not text:
        return None

    # 1. Markdown code block olib tashlash
    text = re.sub(r'```(?:json)?', '', text)
    text = re.sub(r'```', '', text)
    text = text.strip().strip('`').strip()

    # 2. [ ... ] qismni ajratib olish
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        text = match.group(0)

    # 3. To'g'ridan parse
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # 4. Har bir { } blokni alohida parse qilish
    try:
        items = []
        for m in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    items.append(obj)
            except Exception:
                # Yaroqsiz qatorlarni tuzatib ko'rish
                fixed = _fix_json_obj(m.group(0))
                try:
                    obj = json.loads(fixed)
                    if isinstance(obj, dict):
                        items.append(obj)
                except Exception:
                    pass
        if items:
            return items
    except Exception:
        pass

    return None


def _fix_json_obj(s: str) -> str:
    """Oddiy JSON xatolarini tuzatish"""
    # Oxirgi vergulni olib tashlash
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*\]', ']', s)
    # Yagona qo'shtirnoqni ikkilikka almashtirish
    s = re.sub(r"(?<!\\)'", '"', s)
    return s


# ============================================================
#  AKKAUNTLAR SAQLASH
# ============================================================
def load_phones() -> list:
    phones = list(PHONE_NUMBERS)
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE) as f:
                for p in json.load(f):
                    if p not in phones:
                        phones.append(p)
        except Exception:
            pass
    return phones

def save_extra_phones(all_phones: list):
    extra = [p for p in all_phones if p not in PHONE_NUMBERS]
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(extra, f, ensure_ascii=False, indent=2)

# ============================================================
#  MA'LUMOT TUZILMALARI
# ============================================================
@dataclass
class QuizRequest:
    user_id: int
    chat_id: int
    questions: list
    fan_name: str
    variant_num: int
    time_choice: str
    order_choice: str
    total_variants: int = 1
    source: str = "file"   # "ai" yoki "file"

@dataclass
class UserState:
    step: str = "idle"
    # AI rejim
    fan_name: str = ""
    topic: str = ""          # mavzu (ixtiyoriy)
    q_count: int = 10
    lang: str = "uz"
    difficulty: str = "o'rta"
    questions: list = field(default_factory=list)
    total_questions: int = 0
    per_variant: int = 25
    time_choice: str = "30"
    order_choice: str = "order"

# ============================================================
#  GLOBAL
# ============================================================
user_states: dict = {}
admin_states: dict = {}
request_queue: deque = deque()
queue_lock = asyncio.Lock()
account_pool: list = []
account_busy: dict = {}
account_phones: dict = {}
all_clients: list = []   # barcha ulanган клиентлар (pool + notify)
bot_client: TelegramClient = None

# ============================================================
#  VAQT HISOBLASH
# ============================================================
SETUP_SECONDS = 20
SECONDS_PER_QUESTION = 2

def estimate_seconds(n: int) -> int:
    return SETUP_SECONDS + n * SECONDS_PER_QUESTION

def format_wait(s: int) -> str:
    if s < 60:   return f"{s} soniya"
    elif s < 3600:
        m, sec = divmod(s, 60)
        return f"{m} daq {sec} sek" if sec else f"{m} daqiqa"
    else:
        h, m2 = divmod(s, 3600)
        return f"{h} soat {m2//60} daq" if m2 else f"{h} soat"

def calc_wait(new_reqs: list) -> str:
    total_acc = len(account_pool)
    if not total_acc: return "?"
    slots = [0] * total_acc
    for req in list(request_queue):
        slots[slots.index(min(slots))] += estimate_seconds(len(req.questions))
    if not new_reqs:
        return format_wait(min(slots)) if any(slots) else "0 soniya"
    start = min(slots)
    total_new = sum(estimate_seconds(len(r.questions)) for r in new_reqs)
    return format_wait(int(start + total_new))

# ============================================================
#  POOL
# ============================================================
async def pool_add(client, phone):
    account_pool.append(client)
    account_busy[id(client)] = False
    account_phones[id(client)] = phone

async def pool_remove(phone) -> bool:
    for c in account_pool:
        if account_phones.get(id(c)) == phone:
            if account_busy.get(id(c)): return False
            await c.disconnect()
            account_pool.remove(c)
            account_busy.pop(id(c), None)
            account_phones.pop(id(c), None)
            return True
    return False

async def get_free():
    """Bo'sh va ulangan akkaunt olish — uzilgan bo'lsa qayta ulaydi"""
    while True:
        for c in account_pool:
            if account_busy.get(id(c)):
                continue
            try:
                if not c.is_connected():
                    log.info(f"Akkaunt uzilgan, qayta ulanmoqda: {account_phones.get(id(c))}")
                    await c.connect()
                if await c.is_user_authorized():
                    account_busy[id(c)] = True
                    return c
            except Exception as e:
                log.error(f"Akkaunt tekshirishda xato: {e}")
        await asyncio.sleep(3)

def release(c): account_busy[id(c)] = False
def is_admin(uid): return uid in ADMIN_IDS

# ============================================================
#  QUIZ YARATISH (@QuizBot ga yuborish)
# ============================================================
async def send_poll(userbot, peer, q, opts, ans):
    answers = [PollAnswer(
        text=TextWithEntities(text=o[:100], entities=[]),
        option=bytes([i])
    ) for i, o in enumerate(opts)]
    poll = Poll(
        id=random.randint(1, 2**31),
        question=TextWithEntities(text=q[:255], entities=[]),
        answers=answers, quiz=True,
        public_voters=False, multiple_choice=False, closed=False,
    )
    await userbot(SendMediaRequest(
        peer=peer,
        media=InputMediaPoll(poll=poll, correct_answers=[bytes([ans])]),
        message="", random_id=random.randint(1, 2**63),
    ))

async def make_quiz(userbot: TelegramClient, req: QuizRequest) -> Optional[str]:
    try:
        qbot = await userbot.get_entity("@QuizBot")
        title = f"{req.fan_name} — Variant {req.variant_num}"

        await userbot.send_message(qbot, "/newquiz"); await asyncio.sleep(3)
        await userbot.send_message(qbot, title);     await asyncio.sleep(3)
        await userbot.send_message(qbot, "/skip");   await asyncio.sleep(3)

        for i, q in enumerate(req.questions):
            try:
                # Reklama — savoldan OLDIN matn yuboriladi
                if AD_EVERY > 0 and i > 0 and i % AD_EVERY == 0:
                    await userbot.send_message(qbot, AD_TEXT)
                    await asyncio.sleep(2)

                await send_poll(userbot, qbot, q["q"], q["opts"], q["ans"])
                log.info(f"  [{i+1}/{len(req.questions)}] OK")
                await asyncio.sleep(2)
            except Exception as e:
                log.error(f"  [{i+1}] xato: {e}")
                await asyncio.sleep(3)

        await userbot.send_message(qbot, "/done"); await asyncio.sleep(5)

        # Vaqt
        msg = (await userbot.get_messages(qbot, limit=1))[0]
        if msg.reply_markup:
            tmap = {"15": "15", "30": "30", "60": "60", "0": "No limit"}
            target = tmap.get(req.time_choice, "30")
            clicked = False
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    if target in btn.text:
                        await msg.click(text=btn.text); clicked = True; break
                if clicked: break
            if not clicked:
                await msg.click(text=msg.reply_markup.rows[0].buttons[0].text)
        await asyncio.sleep(3)

        # Tartib
        msg = (await userbot.get_messages(qbot, limit=1))[0]
        if msg.reply_markup:
            clicked = False
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    if req.order_choice.lower() in btn.text.lower():
                        await msg.click(text=btn.text); clicked = True; break
                if clicked: break
            if not clicked:
                await msg.click(text=msg.reply_markup.rows[0].buttons[0].text)
        await asyncio.sleep(4)

        # Havola — QuizBot dan olish (bir necha urinish)
        raw_url = None
        for attempt in range(3):
            await asyncio.sleep(3)
            msgs = await userbot.get_messages(qbot, limit=8)
            log.info(f"Havola qidirilmoqda (urinish {attempt+1}), {len(msgs)} ta xabar")
            for m in msgs:
                if m.text:
                    # startgroup yoki start parametrli URL
                    urls = re.findall(r'https?://t\.me/[^\s\)\"\']+', m.text)
                    for url in urls:
                        if 'start' in url or 'quiz' in url.lower():
                            raw_url = url; break
                    if not raw_url and urls:
                        raw_url = urls[0]
                if raw_url: break
                if m.reply_markup:
                    for row in m.reply_markup.rows:
                        for btn in row.buttons:
                            if hasattr(btn, 'url') and btn.url:
                                raw_url = btn.url; break
                        if raw_url: break
                if raw_url: break
                if m.entities:
                    for ent in m.entities:
                        if hasattr(ent, 'url') and ent.url:
                            raw_url = ent.url; break
                if raw_url: break
            if raw_url:
                log.info(f"Havola topildi: {raw_url}")
                break
            log.warning(f"Havola topilmadi, {attempt+1}-urinish")

        if not raw_url:
            log.error("Havola 3 urinishdan keyin ham topilmadi")
            return None

        # Domain QuizBot ga, startgroup → start
        fixed = re.sub(r'(https?://t\.me/)([^?/]+)',
                       lambda m: m.group(1) + "QuizBot", raw_url, count=1)
        fixed = fixed.replace("?startgroup=", "?start=")
        return fixed

    except Exception as e:
        log.error(f"make_quiz xato: {e}")
        return None

# ============================================================
#  NAVBAT ISHLOVCHISI
# ============================================================
async def queue_worker():
    log.info("Navbat ishlovchisi ishga tushdi")
    while True:
        if request_queue and account_pool:
            async with queue_lock:
                req = request_queue.popleft() if request_queue else None
            if req:
                userbot = await get_free()
                asyncio.create_task(run_request(userbot, req))
        await asyncio.sleep(1)

async def run_request(userbot, req: QuizRequest):
    import time
    phone = account_phones.get(id(userbot), "?")
    started = time.time()
    try:
        await bot_client.send_message(
            req.chat_id,
            f"⏳ **Yaratilmoqda...**\n📚 {req.fan_name} V{req.variant_num}\n"
            f"❓ {len(req.questions)} savol | 📱 `{phone}`\n"
            f"🕐 ~{format_wait(estimate_seconds(len(req.questions)))}"
        )
        url = await make_quiz(userbot, req)
        elapsed = int(time.time() - started)
        tl = {"15": "15s", "30": "30s", "60": "60s", "0": "Chegarasiz"}

        if url:
            # DB ga quiz saqlash
            db_save_quiz(
                user_id    = req.user_id,
                fan_name   = req.fan_name,
                q_count    = len(req.questions),
                variant_num= req.variant_num,
                url        = url,
                time_choice= req.time_choice,
                order_type = req.order_choice,
                source     = getattr(req, 'source', 'file'),
            )
            await bot_client.send_message(
                req.chat_id,
                f"✅ **Quiz tayyor!**\n\n"
                f"📚 {req.fan_name} — Variant {req.variant_num}\n"
                f"❓ {len(req.questions)} savol\n"
                f"⏱ {tl.get(req.time_choice, req.time_choice)} | "
                f"🔀 {'Aralash' if req.order_choice=='shuffle' else 'Ketma-ket'}\n"
                f"🕐 {format_wait(elapsed)}\n\n"
                f"🔗 {url}"
            )
        else:
            # Havola topilmadi — pulni qaytaramiz
            refund = calc_file_price(len(req.questions))
            db_add_balance(req.user_id, refund, f"Qaytarildi: quiz V{req.variant_num} xato")
            bal_left = db_get_balance(req.user_id)
            await bot_client.send_message(
                req.chat_id,
                f"❌ **Quiz yaratishda xato!**\n\n"
                f"Havola olinmadi — @QuizBot javob bermadi.\n"
                f"💰 **{refund:,} so'm qaytarildi** | Balans: {bal_left:,} so'm\n\n"
                f"Qayta urinib ko'ring:"
            )
    except Exception as e:
        # Xato — pulni qaytaramiz
        try:
            refund = calc_file_price(len(req.questions))
            db_add_balance(req.user_id, refund, f"Qaytarildi: xato — {str(e)[:50]}")
            bal_left = db_get_balance(req.user_id)
            await bot_client.send_message(
                req.chat_id,
                f"❌ **Xato yuz berdi!**\n\n`{e}`\n\n"
                f"💰 **{refund:,} so'm qaytarildi** | Balans: {bal_left:,} so'm"
            )
        except Exception as e2:
            log.error(f"Qaytarish xatosi: {e2}")
        log.error(f"run_request xato: {e}")
    finally:
        release(userbot)

# ============================================================
#  MAIN
# ============================================================
async def main():
    global bot_client

    bot_client = TelegramClient(
        _os.path.join(_os.path.dirname(DB_FILE), "bot_session"),
        API_ID, API_HASH
    )
    await bot_client.start(bot_token=BOT_TOKEN)
    log.info("Bot ulandi!")

    # DB ishga tushirish
    db_init()
    log.info(f"DB tayyor: {DB_FILE} | Jami users: {db_count_users()} ta")

    for phone in load_phones():
        try:
            sess_dir = _os.path.dirname(DB_FILE)
            session = _os.path.join(sess_dir, f"userbot_{phone.replace('+','').replace(' ','')}")
            client = TelegramClient(session, API_ID, API_HASH)

            # 2FA paroli ko'rinadigan qilib so'rash
            async def custom_password():
                pwd = input(f"🔐 2FA paroli ({phone}): ")
                return pwd

            await client.start(phone=phone, password=custom_password)
            all_clients.append(client)
            account_phones[id(client)] = phone

            # NOTIFY_PHONE faqat bildirishnoma uchun — quiz pool ga qo'shilmaydi
            if phone == NOTIFY_PHONE:
                log.info(f"Notify akkaunt ulandi (pool ga qo'shilmadi): {phone}")
            else:
                account_pool.append(client)
                account_busy[id(client)] = False
                log.info(f"Quiz akkaunt ulandi: {phone}")
        except Exception as e:
            log.error(f"Ulanmadi {phone}: {e}")

    # ============================================================
    #  KNOPKALAR
    # ============================================================
    def main_menu(adm=False):
        btns = [
            [Button.text("🤖 AI test tuzish",       resize=True)],
            [Button.text("📂 Fayldan quiz yaratish", resize=True),
             Button.text("✏️ Matn kiritish",         resize=True)],
            [Button.text("📋 Mening quizlarim",      resize=True)],
            [Button.text("💳 To'lov qilish",         resize=True),
             Button.text("💰 Balansni ko'rish",      resize=True)],
            [Button.text("❓ Yordam",                resize=True)],
        ]
        if adm: btns.append([Button.text("🔧 Admin panel", resize=True)])
        return btns

    def ai_settings_btns(state: UserState):
        topic_show = state.topic if state.topic else "Barcha mavzu"
        return [
            [Button.text("📝 Fan nomini o'zgartirish"),
             Button.text("📌 Mavzuni o'zgartirish")],
            [Button.text("🔢 5 ta"),  Button.text("🔢 10 ta"),
             Button.text("🔢 15 ta"), Button.text("🔢 20 ta"),
             Button.text("🔢 25 ta")],
            [Button.text("🟢 Oson"), Button.text("🟡 O'rta"), Button.text("🔴 Qiyin")],
            [Button.text("🇺🇿 O'zbek"), Button.text("🇷🇺 Rus"), Button.text("🇬🇧 Ingliz")],
            [Button.text(f"✅ Yaratish — {state.q_count} ta savol")],
            [Button.text("🔙 Bosh menyu")],
        ]

    def time_btns():
        return [
            [Button.text("⏱ 15s"), Button.text("⏱ 30s")],
            [Button.text("⏱ 60s"), Button.text("⏱ Chegarasiz")],
        ]

    def order_btns():
        return [[Button.text("📋 Ketma-ket"), Button.text("🔀 Aralash")]]

    def variant_btns(total):
        rows, row = [], []
        for n in [5, 10, 15, 20, 25, 30, 50]:
            if total // n >= 1:
                row.append(Button.text(f"{n} ta"))
                if len(row) == 4: rows.append(row); row = []
        if row: rows.append(row)
        rows.append([Button.text("Hammasi bitta quiz")])
        return rows

    def answer_btns(opts):
        letters = ["A","B","C","D","E","F"]
        rows, row = [], []
        for i, opt in enumerate(opts):
            row.append(Button.text(f"{letters[i] if i<6 else i+1}. {opt[:18]}"))
            if len(row) == 2: rows.append(row); row = []
        if row: rows.append(row)
        rows.append([Button.text("⏭ O'tkazib yuborish"), Button.text("🔙 Bosh menyu")])
        return rows

    # ============================================================
    #  HANDLERLAR
    # ============================================================

    @bot_client.on(events.NewMessage(pattern="/start"))
    async def cmd_start(event):
        uid = event.sender_id
        user_states[uid] = UserState()

        # Foydalanuvchini DB ga saqlash
        sender = await event.get_sender()
        db_save_user(
            user_id    = uid,
            first_name = getattr(sender, 'first_name', '') or '',
            last_name  = getattr(sender, 'last_name',  '') or '',
            username   = getattr(sender, 'username',   '') or '',
        )

        await event.respond(
            "👋 **Salom! AI Quiz Bot**\n\n"
            "🤖 AI yordamida istalgan fandan test tuzing!\n"
            "📁 Fayl yuklang yoki matn kiriting\n\n"
            "Boshlash uchun tugmani bosing 👇",
            buttons=main_menu(is_admin(uid))
        )

    @bot_client.on(events.NewMessage(func=lambda e: e.file))
    async def on_file(event):
        uid = event.sender_id
        adm = is_admin(uid)
        log.info(f"Fayl keldi: user={uid}")

        if admin_states.get(uid, {}).get("step"):
            log.info(f"Admin holati aktiv, fayl ignore: user={uid}")
            return

        msg = await event.respond("📥 O'qilmoqda...")
        log.info(f"Fayl yuklanmoqda: user={uid}")

        try:
            import io

            buf = io.BytesIO()
            await event.download_media(file=buf)
            buf.seek(0)
            data = buf.read()
            log.info(f"Fayl yuklandi: {len(data)} bayt, user={uid}")

            if not data:
                await msg.edit("❌ Fayl bo'sh yoki yuklanmadi!")
                return

            # Kengaytma va MIME
            fname = ""
            mime  = ""
            try:
                fname = (event.file.name or "").lower()
                mime  = str(getattr(event.file, 'mime_type', '') or '')
            except Exception:
                pass

            log.info(f"Fayl: name={fname}, mime={mime}, user={uid}")

            content = ""

            if fname.endswith(".docx") or "officedocument.wordprocessingml" in mime:
                try:
                    from docx import Document
                    doc = Document(io.BytesIO(data))
                    content = "\n".join(p.text for p in doc.paragraphs)
                    log.info(f"DOCX o'qildi: {len(content)} belgi")
                except Exception as e:
                    log.error(f"DOCX xato: {e}")
                    await msg.edit(f"❌ DOCX o'qishda xato: {e}")
                    return

            elif fname.endswith(".pdf") or "pdf" in mime:
                try:
                    import PyPDF2
                    reader = PyPDF2.PdfReader(io.BytesIO(data))
                    content = "".join(p.extract_text() or "" for p in reader.pages)
                    log.info(f"PDF o'qildi: {len(content)} belgi")
                except Exception as e:
                    log.error(f"PDF xato: {e}")
                    await msg.edit(f"❌ PDF o'qishda xato: {e}")
                    return

            else:
                # TXT yoki boshqa
                content = data.decode("utf-8", errors="ignore")
                log.info(f"TXT o'qildi: {len(content)} belgi")

            if not content.strip():
                await msg.edit(
                    "❌ Fayldan matn o'qib bo'lmadi!\n\n"
                    "Qo'llab-quvvatlanadigan: **DOCX, PDF, TXT**"
                )
                return

            qs = _parse_questions(content)
            log.info(f"Parse natija: {len(qs)} savol, user={uid}")

            if qs:
                q_count = len(qs)
                price   = calc_file_price(q_count)
                bal     = db_get_balance(uid)
                blocks  = (q_count + 24) // 25

                # Savollarni RAM da saqlaymiz
                state = UserState(
                    step="wait_payment_file" if bal < price else "ask_fan_name",
                    questions=qs,
                    total_questions=q_count
                )
                user_states[uid] = state
                log.info(f"State saqlandi: step={state.step}, {q_count} savol, user={uid}")

                if bal < price:
                    await msg.edit(
                        f"📂 **{q_count} ta savol topildi!**\n\n"
                        f"💰 Narx: {blocks} × 2 000 = **{price:,} so'm**\n"
                        f"💼 Balansda: {bal:,} so'm\n"
                        f"❌ Yetishmaydi: **{price - bal:,} so'm**\n\n"
                        f"📌 Savollar saqlanib qoldi"
                    )
                    await event.respond(
                        "To'lov qiling:",
                        buttons=[
                            [Button.text(f"💳 {price:,} so'm to'lash")],
                            [Button.text("🔙 Bosh menyu")],
                        ]
                    )
                else:
                    db_deduct_balance(uid, price, f"Fayl quiz: {q_count} ta savol")
                    bal_left = db_get_balance(uid)
                    await msg.edit(
                        f"📂 **{q_count} ta savol topildi!**\n"
                        f"💰 -{price:,} so'm | Qoldi: **{bal_left:,} so'm**"
                    )
                    await event.respond(
                        "Fan nomini yozing:",
                        buttons=[[Button.text("🔙 Bosh menyu")]]
                    )

            else:
                # Shablon topilmadi — manual (bepul)
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                if not lines:
                    await msg.edit("❌ Faylda matn topilmadi!")
                    return

                state = UserState(step="manual_start")
                state.__dict__['raw_lines']     = lines
                state.__dict__['manual_q_idx']  = 0
                state.__dict__['manual_q_text'] = ""
                state.__dict__['manual_opts']   = []
                user_states[uid] = state
                log.info(f"Manual rejim: {len(lines)} qator, user={uid}")

                await msg.edit(
                    f"⚠️ **Shablon aniqlanmadi**\n\n"
                    f"{len(lines)} ta qator topildi.\n"
                    f"To'g'ri javoblarni siz belgilaysiz 👇\n"
                    f"_(Bu rejim bepul)_"
                )
                await event.respond(
                    "Davom etamizmi?",
                    buttons=[
                        [Button.text("▶️ Davom etish")],
                        [Button.text("🔙 Bosh menyu")],
                    ]
                )

        except Exception as e:
            log.error(f"on_file xato: {e}", exc_info=True)
            try:
                await msg.edit(f"❌ Xato: {e}")
            except Exception:
                await event.respond(f"❌ Xato: {e}", buttons=main_menu(adm))

    @bot_client.on(events.NewMessage(func=lambda e: not e.file and not e.text.startswith("/")))
    async def on_msg(event):
        uid = event.sender_id
        text = event.text.strip()
        adm = is_admin(uid)
        astate = admin_states.get(uid, {})

        # Har xabarda last_seen yangilash
        sender = await event.get_sender()
        db_save_user(
            user_id    = uid,
            first_name = getattr(sender, 'first_name', '') or '',
            last_name  = getattr(sender, 'last_name',  '') or '',
            username   = getattr(sender, 'username',   '') or '',
        )

        # Admin oraliq holat
        if astate.get("step") == "wait_phone":
            await _admin_add_phone(event, uid, text); return
        if astate.get("step") == "wait_code":
            await _admin_enter_code(event, uid, text); return
        if astate.get("step") == "wait_password":
            await _admin_enter_pass(event, uid, text); return
        if astate.get("step") == "wait_remove":
            await _admin_do_remove(event, uid, text); return

        state = user_states.get(uid, UserState())

        # ---- BOSH MENYU KNOPKALARI ----
        if text == "🔙 Bosh menyu":
            user_states[uid] = UserState()
            admin_states.pop(uid, None)
            await event.respond("🏠 Bosh menyu", buttons=main_menu(adm))
            return

        if text == "📋 Mening quizlarim":
            quizzes = db_get_user_quizzes(uid, limit=20)
            total_q = db_count_user_quizzes(uid)
            if not quizzes:
                await event.respond(
                    "📋 **Mening quizlarim**\n\n"
                    "Hali quiz yaratmagansiz.\n\n"
                    "🤖 AI yoki 📂 fayl orqali quiz tuzing!",
                    buttons=main_menu(adm)
                )
                return

            lines = [f"📋 **Mening quizlarim ({total_q} ta)**\n"]
            for q in quizzes:
                qid, fan, q_count, variant, url, source, created = q
                src_icon = "🤖" if source == "ai" else "📂"
                date = created[:10] if created else ""
                lines.append(
                    f"{src_icon} **{fan}** V{variant} — {q_count} savol\n"
                    f"   📅 {date} | [▶️ Ochish]({url})"
                )

            await event.respond(
                "\n\n".join(lines),
                buttons=[[Button.text("🔙 Bosh menyu")]],
                link_preview=False
            )
            return

        if text == "❓ Yordam":
            await event.respond(
                "📋 **YORDAM**\n\n"
                "**🤖 AI test tuzish** — 2 000 so'm\n"
                "Istalgan fan va mavzudan AI avtomatik test tuzadi\n\n"
                "**📂 Fayldan quiz yaratish** — har 25 savolga 2 000 so'm\n"
                "Tayyor testingizni yuklang, bot quizga aylantiradi\n\n"
                "━━━━━━━━━━━━━━━\n"
                "**📌 Fayl formatlari:** DOCX, PDF, TXT\n\n"
                "**Shablon 1:**\n```\n1.Savol\na.Variant\n#b.To'g'ri\nc.Variant\n```\n\n"
                "**Shablon 2:**\n```\nSavol\na.Variant\n#b.To'g'ri\nc.Variant\n```\n\n"
                "**Shablon 3:**\n```\nSavol\n=====\n#To'g'ri\nVariant\n+++++\n```\n\n"
                "**# belgisi** = to'g'ri javob",
                buttons=main_menu(adm)
            )
            return

        if text == "🔧 Admin panel":
            if not adm: await event.respond("⛔ Admin emassiz!"); return
            await _show_admin(event); return

        if text == "📂 Fayldan quiz yaratish":
            user_states[uid] = UserState(step="wait_file")
            await event.respond(
                "📂 **Fayldan quiz yaratish**\n\n"
                "DOCX, PDF yoki TXT fayl yuboring\n\n"
                "📌 **Narx:** Har 25 savolga 2 000 so'm\n"
                "_(50 savol = 4 000, 100 savol = 8 000)_\n\n"
                "❓ /yordam — fayl formati haqida",
                buttons=[[Button.text("🔙 Bosh menyu")]]
            ); return

        if text == "✏️ Matn kiritish":
            user_states[uid] = UserState(step="wait_text")
            await event.respond("✏️ Savollarni yuboring:\n/yordam — shablonlar",
                buttons=[[Button.text("🔙 Bosh menyu")]]); return

        # ---- AI TEST TUZISH ----
        if text == "🤖 AI test tuzish":
            state = UserState(step="ai_ask_fan")
            user_states[uid] = state
            await event.respond(
                "🤖 **AI Test Tuzish**\n\nQaysi fandan test kerak?\n\n"
                "_(Misol: Matematika, Fizika, Tarix, Python...)_",
                buttons=[[Button.text("🔙 Bosh menyu")]]
            ); return

        if state.step == "ai_ask_fan":
            state.fan_name = text
            state.step = "ai_ask_topic"
            user_states[uid] = state
            await event.respond(
                f"📚 Fan: **{text}**\n\n"
                f"Qaysi mavzudan savol tuzish kerak?\n\n"
                f"_(Misol: Kasrlar, Fotosintez, II Jahon urushi...)_\n\n"
                f"Barcha mavzudan bo'lsa 👇 tugmani bosing:",
                buttons=[
                    [Button.text("📖 Barcha mavzudan")],
                    [Button.text("🔙 Bosh menyu")],
                ]
            ); return

        if state.step == "ai_ask_topic":
            if text == "📖 Barcha mavzudan":
                state.topic = ""
            else:
                state.topic = text
            state.step = "ai_settings"
            user_states[uid] = state
            topic_show = state.topic if state.topic else "Barcha mavzu"
            await event.respond(
                f"📝 Fan: **{state.fan_name}**\n"
                f"📌 Mavzu: **{topic_show}**\n"
                f"🔢 Savol: {state.q_count} ta | 🎯 {state.difficulty} | 🌐 {state.lang}\n\n"
                f"Sozlash yoki yaratish:",
                buttons=ai_settings_btns(state)
            ); return

        if state.step == "ai_settings":
            topic_show = state.topic if state.topic else "Barcha mavzu"

            def _show_settings():
                return (
                    f"📝 Fan: **{state.fan_name}**\n"
                    f"📌 Mavzu: **{topic_show}**\n"
                    f"🔢 {state.q_count} ta | 🎯 {state.difficulty} | 🌐 {state.lang}"
                )

            # Fan nomini o'zgartirish
            if text == "📝 Fan nomini o'zgartirish":
                state.step = "ai_ask_fan"
                user_states[uid] = state
                await event.respond("Yangi fan nomini yozing:",
                    buttons=[[Button.text("🔙 Bosh menyu")]]); return

            # Mavzuni o'zgartirish
            if text == "📌 Mavzuni o'zgartirish":
                state.step = "ai_ask_topic"
                user_states[uid] = state
                await event.respond(
                    f"📚 Fan: **{state.fan_name}**\n\nYangi mavzuni yozing:",
                    buttons=[
                        [Button.text("📖 Barcha mavzudan")],
                        [Button.text("🔙 Bosh menyu")],
                    ]
                ); return

            # Savol soni
            if re.match(r'^🔢 (\d+) ta$', text):
                state.q_count = int(re.match(r'^🔢 (\d+) ta$', text).group(1))
                user_states[uid] = state
                await event.respond(_show_settings(), buttons=ai_settings_btns(state)); return

            # Qiyinlik
            diff_map = {"🟢 Oson": "oson", "🟡 O'rta": "o'rta", "🔴 Qiyin": "qiyin"}
            if text in diff_map:
                state.difficulty = diff_map[text]
                user_states[uid] = state
                await event.respond(_show_settings(), buttons=ai_settings_btns(state)); return

            # Til
            lang_map = {"🇺🇿 O'zbek": "uz", "🇷🇺 Rus": "ru", "🇬🇧 Ingliz": "en"}
            if text in lang_map:
                state.lang = lang_map[text]
                user_states[uid] = state
                await event.respond(_show_settings(), buttons=ai_settings_btns(state)); return

            # YARATISH
            if text.startswith("✅ Yaratish"):
                # Balans tekshirish
                bal = db_get_balance(uid)
                if bal < AI_PRICE:
                    # Stateni saqlab qo'yamiz — to'lovdan keyin qaytamiz
                    state.step = "wait_payment"
                    user_states[uid] = state
                    await event.respond(
                        f"❌ **Balans yetarli emas!**\n\n"
                        f"💰 Balans: {bal:,} so'm\n"
                        f"💳 Kerak: {AI_PRICE:,} so'm\n\n"
                        f"📌 Sozlamalaringiz saqlanib qoldi!\n"
                        f"To'lov qilib, qaytib keling — boshidan kiritmasiz:",
                        buttons=[
                            [Button.text(f"💳 {AI_PRICE:,} so'm to'lash")],
                            [Button.text("🔙 Bosh menyu")],
                        ]
                    )
                    return

                state.step = "ai_generating"
                user_states[uid] = state
                topic_label = f" | 📌 {state.topic}" if state.topic else ""
                await event.respond(
                    f"🤖 **AI test tuzmoqda...**\n\n"
                    f"📚 {state.fan_name}{topic_label}\n"
                    f"🔢 {state.q_count} ta | 🎯 {state.difficulty} | 🌐 {state.lang}\n\n"
                    f"⏳ Bir oz kuting..."
                )
                try:
                    qs = await generate_questions(
                        state.fan_name, state.q_count,
                        state.lang, state.difficulty, state.topic
                    )
                    if not qs:
                        await event.respond("❌ AI savol yarata olmadi! Qayta urining.",
                            buttons=main_menu(adm)); return

                    state.questions = qs
                    state.total_questions = len(qs)
                    state.per_variant = len(qs)
                    state.step = "ask_time"
                    state.__dict__['source'] = "ai"   # manba belgisi
                    user_states[uid] = state

                    # Balansdan yechish faqat savollar muvaffaqiyatli yaratilganda
                    db_deduct_balance(uid, AI_PRICE, f"AI test: {state.fan_name}")
                    bal_left = db_get_balance(uid)

                    await event.respond(
                        f"✅ **{len(qs)} ta savol tayyor!**\n"
                        f"💰 -{AI_PRICE:,} so'm | Qoldi: {bal_left:,} so'm\n\n"
                        f"⏱ Vaqt:",
                        buttons=time_btns()
                    )
                except Exception as e:
                    log.error(f"AI xato: {e}")
                    await event.respond(
                        f"❌ AI xato: {e}\n\nGROQ_API_KEY ni tekshiring!",
                        buttons=main_menu(adm)
                    )
                return

        # ---- MATN HOLAT ----
        if state.step == "wait_text":
            qs = _parse_questions(text)
            if qs:
                state.questions = qs
                state.total_questions = len(qs)
                state.step = "ask_fan_name"
                user_states[uid] = state
                await event.respond(f"✅ **{len(qs)} ta savol!**\n\nFan nomini yozing:",
                    buttons=[[Button.text("🔙 Bosh menyu")]]); return
            else:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                state.step = "manual_start"
                state.__dict__['raw_lines'] = lines
                state.__dict__['manual_q_idx'] = 0
                state.__dict__['manual_q_text'] = ""
                state.__dict__['manual_opts'] = []
                user_states[uid] = state
                await event.respond(
                    f"⚠️ Shablon aniqlanmadi. {len(lines)} ta qator.\nJavoblarni siz ko'rsatasiz:",
                    buttons=[[Button.text("▶️ Davom etish")], [Button.text("🔙 Bosh menyu")]]
                ); return

        # ---- MANUAL REJIM ----
        if state.step == "manual_start" and text == "▶️ Davom etish":
            state.step = "manual_detect"
            state.questions = []
            state.__dict__['manual_q_idx'] = 0
            user_states[uid] = state
            await _ask_manual(event, uid, state); return

        if state.step == "manual_answer":
            await _handle_manual(event, uid, state, text); return

        # ---- FAN NOMI ----
        if state.step == "ask_fan_name":
            state.fan_name = text
            state.step = "ask_split"
            user_states[uid] = state
            total = state.total_questions
            await event.respond(
                f"📚 **{text}** | ❓ {total} savol\n\nHar variantda necha ta?",
                buttons=variant_btns(total)); return

        # ---- VARIANT SONI ----
        if state.step == "ask_split":
            n = 0 if text == "Hammasi bitta quiz" else int(re.sub(r'\D', '', text) or 0)
            state.per_variant = state.total_questions if n == 0 else max(1, min(n, state.total_questions))
            pv, total = state.per_variant, state.total_questions
            nv = (total + pv - 1) // pv
            state.step = "ask_time"
            user_states[uid] = state
            await event.respond(
                f"✅ **{nv} ta variant** × {pv} savol\n\n⏱ Vaqt:",
                buttons=time_btns()); return

        # ---- VAQT ----
        if state.step == "ask_time":
            tm = {"⏱ 15s": "15", "⏱ 30s": "30", "⏱ 60s": "60", "⏱ Chegarasiz": "0"}
            if text not in tm:
                await event.respond("Tugmadan tanlang!", buttons=time_btns()); return
            state.time_choice = tm[text]
            state.step = "ask_order"
            user_states[uid] = state
            await event.respond("🔀 Tartib:", buttons=order_btns()); return

        # ---- TARTIB ----
        if state.step == "ask_order":
            if text == "📋 Ketma-ket":   state.order_choice = "order"
            elif text == "🔀 Aralash":   state.order_choice = "shuffle"
            else:
                await event.respond("Tugmadan tanlang!", buttons=order_btns()); return

            total, pv = state.total_questions, state.per_variant
            nv = (total + pv - 1) // pv
            state.step = "idle"
            user_states[uid] = state
            qs = state.questions
            tl = {"15": "15s", "30": "30s", "60": "60s", "0": "Chegarasiz"}
            new_reqs = [
                QuizRequest(
                    user_id=uid, chat_id=event.chat_id,
                    questions=qs[v*pv:min((v+1)*pv, total)],
                    fan_name=state.fan_name, variant_num=v+1,
                    time_choice=state.time_choice,
                    order_choice=state.order_choice, total_variants=nv,
                    source=getattr(state, 'source', 'file'),
                ) for v in range(nv)
            ]
            pos = len(request_queue) + 1
            free_acc = sum(1 for v in account_busy.values() if not v)
            wait_str = calc_wait(new_reqs)

            await event.respond(
                f"📋 **Xulosa:**\n\n"
                f"📚 {state.fan_name}\n"
                f"❓ {total} savol → {nv} ta variant × {pv}\n"
                f"⏱ {tl.get(state.time_choice)} | "
                f"🔀 {'Aralash' if state.order_choice=='shuffle' else 'Ketma-ket'}\n\n"
                f"📍 Navbat: #{pos}\n"
                f"🟢 Bo'sh: {free_acc}/{len(account_pool)}\n"
                f"⏳ ~{wait_str}",
                buttons=main_menu(adm)
            )
            async with queue_lock:
                for req in new_reqs:
                    request_queue.append(req)
            return

    # ============================================================
    #  MANUAL REJIM FUNKSIYALARI
    # ============================================================
    async def _ask_manual(event, uid, state):
        lines = state.__dict__.get('raw_lines', [])
        idx   = state.__dict__.get('manual_q_idx', 0)
        if idx >= len(lines):
            if not state.questions:
                await event.respond("❌ Savol qo'shilmadi!", buttons=main_menu(is_admin(uid)))
                user_states[uid] = UserState(); return
            state.step = "ask_fan_name"
            state.total_questions = len(state.questions)
            user_states[uid] = state
            await event.respond(
                f"✅ **{len(state.questions)} ta savol tayyor!**\n\nFan nomini yozing:",
                buttons=[[Button.text("🔙 Bosh menyu")]]); return

        q_text = lines[idx]
        opts = []
        i = idx + 1
        while i < len(lines) and len(opts) < 6:
            opts.append(lines[i]); i += 1

        if len(opts) < 2:
            state.__dict__['manual_q_idx'] = i
            user_states[uid] = state
            await _ask_manual(event, uid, state); return

        state.__dict__['manual_q_text'] = q_text
        state.__dict__['manual_opts']   = opts
        state.__dict__['manual_q_idx']  = i
        state.step = "manual_answer"
        user_states[uid] = state

        letters = ["A","B","C","D","E","F"]
        opts_txt = "\n".join(f"  {letters[j] if j<6 else j+1}. {o}" for j, o in enumerate(opts))
        done = len(state.questions)
        approx = len(lines) // (len(opts) + 1)

        await event.respond(
            f"📝 **Savol {done+1}** (~{approx} ta):\n\n❓ {q_text}\n\n{opts_txt}\n\n✅ To'g'ri javob:",
            buttons=answer_btns(opts)
        )

    async def _handle_manual(event, uid, state, text):
        if text == "⏭ O'tkazib yuborish":
            await _ask_manual(event, uid, state); return
        letters = ["A","B","C","D","E","F"]
        opts = state.__dict__.get('manual_opts', [])
        correct = None
        for i, opt in enumerate(opts):
            ltr = letters[i] if i < 6 else str(i+1)
            if text.startswith(f"{ltr}."):
                correct = i; break
        if correct is None:
            await event.respond("Tugmadan tanlang!", buttons=answer_btns(opts)); return
        state.questions.append({
            "q": state.__dict__.get('manual_q_text', ''),
            "opts": opts, "ans": correct
        })
        user_states[uid] = state
        await _ask_manual(event, uid, state)

    # ============================================================
    #  ADMIN PANEL
    # ============================================================
    async def _show_admin(event):
        busy = sum(1 for v in account_busy.values() if v)
        free = len(account_pool) - busy
        rows = [f"  {i+1}. {'🔴' if account_busy.get(id(c)) else '🟢'} `{account_phones.get(id(c),'?')}`"
                for i, c in enumerate(account_pool)]
        total_users = db_count_users()
        stats = db_payment_stats()
        await event.respond(
            f"🔧 **ADMIN PANEL**\n\n"
            f"👥 Foydalanuvchilar: **{total_users} ta**\n"
            f"💰 Bugungi daromad: **{stats['today']:,} so'm**\n"
            f"💵 Jami daromad: **{stats['total']:,} so'm**\n"
            f"⏳ Kutilayotgan to'lov: **{stats['pending']} ta**\n\n"
            f"📱 Akkauntlar: **{len(account_pool)} ta**\n"
            f"  🟢 Bo'sh: {free} | 🔴 Band: {busy}\n"
            f"📋 Navbat: **{len(request_queue)} ta**\n\n" +
            ("\n".join(rows) if rows else "  (yo'q)"),
            buttons=[
                [Button.text("👥 Userlar ro'yxati"), Button.text("💳 To'lovlar")],
                [Button.text("➕ Akkaunt qo'shish"), Button.text("➖ Akkaunt o'chirish")],
                [Button.text("📊 Holat"),            Button.text("📋 Navbat")],
                [Button.text("⬇️ DB yuklash"),        Button.text("⬆️ DB yuklash (yangi)")],
                [Button.text("🗑 Navbatni tozalash"), Button.text("🔙 Bosh menyu")],
            ]
        )

    @bot_client.on(events.NewMessage(pattern="/admin"))
    async def cmd_admin(event):
        if not is_admin(event.sender_id):
            await event.respond("⛔ Admin emassiz!"); return
        await _show_admin(event)

    # ============================================================
    #  DB YUKLAB OLISH VA YUKLASH
    # ============================================================

    @bot_client.on(events.NewMessage(pattern="/dbyuklash"))
    async def cmd_db_download(event):
        """DB faylini foydalanuvchiga yuborish"""
        if not is_admin(event.sender_id):
            await event.respond("⛔ Admin emassiz!"); return

        if not _os.path.exists(DB_FILE):
            await event.respond("❌ DB fayl topilmadi!")
            return

        size = _os.path.getsize(DB_FILE)
        await event.respond(
            f"📦 **Ma'lumotlar bazasi**\n\n"
            f"📁 `{_os.path.basename(DB_FILE)}`\n"
            f"💾 Hajm: {size / 1024:.1f} KB\n\n"
            f"⬇️ Yuklanmoqda..."
        )
        await bot_client.send_file(
            event.chat_id,
            DB_FILE,
            caption=(
                f"🗄 **bot.db** — {size / 1024:.1f} KB\n"
                f"📅 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"Bu faylni tahrir qilib /newdbyuklash orqali qayta yuboring."
            ),
            force_document=True
        )
        log.info(f"DB yuklandi: admin={event.sender_id}, size={size}")

    @bot_client.on(events.NewMessage(pattern="/newdbyuklash"))
    async def cmd_db_upload_prompt(event):
        """Yangi DB yuklash uchun ko'rsatma"""
        if not is_admin(event.sender_id):
            await event.respond("⛔ Admin emassiz!"); return

        admin_states[event.sender_id] = {"step": "wait_db_file"}
        await event.respond(
            "📤 **Yangi DB yuklash**\n\n"
            "⚠️ **Diqqat!** Joriy ma'lumotlar bazasi almashtiriladi!\n\n"
            "1. /dbyuklash orqali eski DB ni yuklang\n"
            "2. SQLite editor bilan tahrirlang\n"
            "3. Tahrirlangan `.db` faylni shu yerga yuboring\n\n"
            "Bot fayl qabul qilgach avtomatik qayta ishga tushadi.\n\n"
            "/cancel — bekor qilish",
            buttons=[[Button.text("🔙 Bosh menyu")]]
        )

    @bot_client.on(events.NewMessage(
        func=lambda e: e.file and
        admin_states.get(e.sender_id, {}).get("step") == "wait_db_file"
    ))
    async def cmd_db_receive(event):
        """Yangi DB faylini qabul qilish va almashtirish"""
        uid = event.sender_id
        if not is_admin(uid):
            return

        fname = getattr(event.file, 'name', '') or ''
        if not fname.lower().endswith('.db') and not fname.lower().endswith('.sqlite'):
            await event.respond(
                "❌ Faqat `.db` yoki `.sqlite` fayl yuboring!\n"
                "Qayta urinib ko'ring yoki /cancel."
            )
            return

        try:
            msg = await event.respond("📥 Yangi DB yuklanmoqda...")

            import io, shutil

            # Yangi DB ni olish
            buf = io.BytesIO()
            await event.download_media(file=buf)
            buf.seek(0)
            new_data = buf.read()

            if len(new_data) < 100:
                await msg.edit("❌ Fayl juda kichik yoki buzilgan!")
                return

            # SQLite fayl ekanligini tekshirish
            if not new_data.startswith(b'SQLite format 3'):
                await msg.edit("❌ Bu SQLite fayl emas!")
                return

            # Eski DB ni zaxiralash
            backup_path = DB_FILE + ".backup"
            if _os.path.exists(DB_FILE):
                shutil.copy2(DB_FILE, backup_path)
                log.info(f"DB zaxira: {backup_path}")

            # Yangi DB ni yozish
            with open(DB_FILE, 'wb') as f:
                f.write(new_data)

            admin_states.pop(uid, None)

            # Statistika
            import sqlite3 as _sq
            con = _sq.connect(DB_FILE)
            users_n  = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            quizzes_n = con.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0]
            pays_n   = con.execute("SELECT COUNT(*) FROM payments WHERE status='confirmed'").fetchone()[0]
            con.close()

            await msg.edit(
                f"✅ **DB muvaffaqiyatli yangilandi!**\n\n"
                f"👥 Foydalanuvchilar: {users_n} ta\n"
                f"🎯 Quizlar: {quizzes_n} ta\n"
                f"💰 To'lovlar: {pays_n} ta\n\n"
                f"💾 Zaxira: `{_os.path.basename(backup_path)}`\n\n"
                f"✅ Bot yangi DB bilan ishlaydi!"
            )
            log.info(f"DB yangilandi: admin={uid}, size={len(new_data)}")

        except Exception as e:
            log.error(f"DB yuklash xato: {e}")
            admin_states.pop(uid, None)
            await event.respond(f"❌ Xato: {e}")

    @bot_client.on(events.NewMessage(
        func=lambda e: not e.file and not e.text.startswith("/")
        and e.sender_id in ADMIN_IDS
        and e.text.strip() in ["➕ Akkaunt qo'shish","➖ Akkaunt o'chirish",
                                "📊 Holat","📋 Navbat","🗑 Navbatni tozalash",
                                "🔙 Admin panel", "👥 Userlar ro'yxati",
                                "💳 To'lovlar",
                                "⬇️ DB yuklash", "⬆️ DB yuklash (yangi)"]
    ))
    async def admin_btns(event):
        uid = event.sender_id
        text = event.text.strip()

        if text == "🔙 Admin panel":
            admin_states.pop(uid, None)
            await _show_admin(event)

        elif text == "⬇️ DB yuklash":
            # /dbyuklash bilan bir xil
            if not _os.path.exists(DB_FILE):
                await event.respond("❌ DB fayl topilmadi!",
                    buttons=[[Button.text("🔙 Admin panel")]]); return
            size = _os.path.getsize(DB_FILE)
            await bot_client.send_file(
                event.chat_id,
                DB_FILE,
                caption=(
                    f"🗄 **bot.db** — {size/1024:.1f} KB\n"
                    f"📅 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                    f"Tahrir qilib /newdbyuklash orqali qayta yuboring."
                ),
                force_document=True
            )

        elif text == "⬆️ DB yuklash (yangi)":
            admin_states[uid] = {"step": "wait_db_file"}
            await event.respond(
                "📤 **Yangi DB yuklash**\n\n"
                "⚠️ Joriy ma'lumotlar bazasi almashtiriladi!\n\n"
                "Tahrirlangan `.db` faylni yuboring:\n\n"
                "/cancel — bekor",
                buttons=[[Button.text("🔙 Admin panel")]]
            )

        elif text == "👥 Userlar ro'yxati":
            total = db_count_users()
            users = db_get_users(limit=20)
            if not users:
                await event.respond("👥 Hali foydalanuvchi yo'q.",
                    buttons=[[Button.text("🔙 Admin panel")]]); return
            lines = [f"👥 **Foydalanuvchilar: {total} ta**\n"]
            for u in users:
                user_id, first, last, uname, created, last_seen = u
                name = f"{first} {last}".strip() or "Nomsiz"
                uname_str = f"@{uname}" if uname else "username yo'q"
                lines.append(
                    f"• {name} ({uname_str})\n"
                    f"  ID: `{user_id}` | {last_seen[:10]}"
                )
            if total > 20:
                lines.append(f"\n_...va yana {total-20} ta_")
            await event.respond(
                "\n".join(lines),
                buttons=[[Button.text("🔙 Admin panel")]]
            )

        elif text == "➕ Akkaunt qo'shish":
            admin_states[uid] = {"step": "wait_phone"}
            await event.respond("📱 Telefon raqam:\n_(+998901234567)_",
                buttons=[[Button.text("🔙 Admin panel")]])

        elif text == "➖ Akkaunt o'chirish":
            if not account_pool:
                await event.respond("❌ Akkaunt yo'q!"); return
            btns = [[Button.text(account_phones.get(id(c), "?"))] for c in account_pool]
            btns.append([Button.text("🔙 Admin panel")])
            admin_states[uid] = {"step": "wait_remove"}
            await event.respond("Qaysi raqamni o'chirish?", buttons=btns)

        elif text == "📊 Holat":
            lines = ["📊 **HOLAT**\n"]
            for i, c in enumerate(account_pool):
                ph = account_phones.get(id(c), "?")
                bs = "🔴 Band" if account_busy.get(id(c)) else "🟢 Bo'sh"
                au = "✅" if await c.is_user_authorized() else "❌"
                lines.append(f"{i+1}. `{ph}` {bs} {au}")
            lines.append(f"\n📋 Navbat: {len(request_queue)} ta")
            await event.respond("\n".join(lines), buttons=[[Button.text("🔙 Admin panel")]])

        elif text == "📋 Navbat":
            if not request_queue:
                await event.respond("📋 Bo'sh!", buttons=[[Button.text("🔙 Admin panel")]]); return
            lines = [f"📋 **{len(request_queue)} ta**\n"]
            for i, req in enumerate(list(request_queue)[:15]):
                secs = estimate_seconds(len(req.questions))
                lines.append(f"{i+1}. `{req.user_id}` {req.fan_name} V{req.variant_num} "
                             f"({len(req.questions)}ta) ~{format_wait(secs)}")
            lines.append(f"\n⏳ Umumiy: ~{calc_wait([])}")
            await event.respond("\n".join(lines), buttons=[[Button.text("🔙 Admin panel")]])

        elif text == "🗑 Navbatni tozalash":
            async with queue_lock:
                n = len(request_queue); request_queue.clear()
            await event.respond(f"🗑 {n} ta bekor qilindi.",
                buttons=[[Button.text("🔙 Admin panel")]])

        elif text == "💳 To'lovlar":
            stats = db_payment_stats()
            # Oxirgi 10 to'lov
            con = sqlite3.connect(DB_FILE)
            last_pays = con.execute(
                """SELECT p.id, p.user_id, p.amount, p.card_num, p.status, p.paid_at
                   FROM payments p ORDER BY p.id DESC LIMIT 10"""
            ).fetchall()
            con.close()

            # Kartalar holati
            card_lines = []
            for card in HUMO_CARDS:
                assigned = card_assignments.get(card)
                if assigned:
                    card_lines.append(f"  🔴 `{card[-9:]}` → user `{assigned}`")
                else:
                    card_lines.append(f"  🟢 `{card[-9:]}` bo'sh")

            pay_lines = []
            for p in last_pays:
                pid, uid2, amt, card, status, paid = p
                icon = {"confirmed": "✅", "pending": "⏳", "expired": "❌"}.get(status, "❓")
                pay_lines.append(
                    f"{icon} #{pid} | `{uid2}` | {amt:,} so'm | ...{card[-9:]}"
                )

            lines = [
                f"💳 **TO'LOV STATISTIKASI**\n",
                f"💵 Jami daromad: **{stats['total']:,} so'm**",
                f"📅 Bugun: **{stats['today']:,} so'm**",
                f"⏳ Kutilayotgan: **{stats['pending']} ta**\n",
                f"**Kartalar:**",
                *card_lines,
                f"\n**Oxirgi to'lovlar:**",
                *(pay_lines if pay_lines else ["  (yo'q)"]),
            ]
            await event.respond(
                "\n".join(lines),
                buttons=[[Button.text("🔙 Admin panel")]]
            )

    # ============================================================
    #  ADMIN: AKKAUNT QO'SHISH
    # ============================================================
    async def _admin_add_phone(event, uid, phone):
        phone = phone.strip()
        if not re.match(r'^\+\d{10,15}$', phone):
            await event.respond("❌ Format: +998901234567"); return
        if phone in [account_phones.get(id(c)) for c in account_pool]:
            await event.respond(f"⚠️ `{phone}` allaqachon bor!")
            admin_states.pop(uid, None); return
        await event.respond(f"📲 `{phone}` ga kod...")
        try:
            sess_dir = _os.path.dirname(DB_FILE)
            session = _os.path.join(sess_dir, f"userbot_{phone.replace('+','').replace(' ','')}")
            client = TelegramClient(session, API_ID, API_HASH)
            await client.connect()
            result = await client.send_code_request(phone)
            admin_states[uid] = {"step": "wait_code", "phone": phone,
                                  "client": client, "hash": result.phone_code_hash}
            await event.respond("✅ Kod yuborildi!\n\nKodni yuboring:\n_(12345)_",
                buttons=[[Button.text("🔙 Admin panel")]])
        except Exception as e:
            await event.respond(f"❌ {e}")
            admin_states.pop(uid, None)

    async def _admin_enter_code(event, uid, code):
        astate = admin_states.get(uid, {})
        client, phone, ph_hash = astate.get("client"), astate.get("phone"), astate.get("hash")
        if not client:
            await event.respond("❌ Sessiya tugadi."); admin_states.pop(uid, None); return
        try:
            await client.sign_in(phone=phone, code=code.strip().replace(" ",""),
                                  phone_code_hash=ph_hash)
            await pool_add(client, phone)
            save_extra_phones([account_phones.get(id(c)) for c in account_pool
                               if account_phones.get(id(c))])
            admin_states.pop(uid, None)
            await event.respond(f"✅ `{phone}` qo'shildi! Jami: **{len(account_pool)} ta**",
                buttons=[[Button.text("🔙 Admin panel")]])
        except SessionPasswordNeededError:
            admin_states[uid]["step"] = "wait_password"
            await event.respond("🔐 Parol kerak:", buttons=[[Button.text("🔙 Admin panel")]])
        except PhoneCodeInvalidError:
            await event.respond("❌ Kod noto'g'ri! Qayta:")
        except Exception as e:
            await event.respond(f"❌ {e}")
            try: await client.disconnect()
            except: pass
            admin_states.pop(uid, None)

    async def _admin_enter_pass(event, uid, password):
        astate = admin_states.get(uid, {})
        client, phone = astate.get("client"), astate.get("phone")
        if not client: admin_states.pop(uid, None); return
        try:
            await client.sign_in(password=password)
            await pool_add(client, phone)
            save_extra_phones([account_phones.get(id(c)) for c in account_pool
                               if account_phones.get(id(c))])
            admin_states.pop(uid, None)
            await event.respond(f"✅ `{phone}` qo'shildi! Jami: **{len(account_pool)} ta**",
                buttons=[[Button.text("🔙 Admin panel")]])
        except Exception as e:
            await event.respond(f"❌ Parol xato: {e}")
            try: await client.disconnect()
            except: pass
            admin_states.pop(uid, None)

    async def _admin_do_remove(event, uid, phone):
        phone = phone.strip()
        if await pool_remove(phone):
            save_extra_phones([account_phones.get(id(c)) for c in account_pool
                               if account_phones.get(id(c))])
            await event.respond(f"✅ `{phone}` o'chirildi! Qoldi: **{len(account_pool)} ta**",
                buttons=[[Button.text("🔙 Admin panel")]])
        else:
            await event.respond(f"❌ `{phone}` topilmadi yoki band!",
                buttons=[[Button.text("🔙 Admin panel")]])
        admin_states.pop(uid, None)

    # ============================================================
    #  PARSER (ichki)
    # ============================================================
    def _parse_questions(text: str) -> list:
        text = text.strip()

        # Ajratgich satrlarni aniqlash
        def is_separator(s: str) -> bool:
            """===== yoki +++++ yoki --- kabi satrlar"""
            return bool(re.match(r'^[=+\-_*]{3,}$', s.strip()))

        if "=====" in text and "+++++" in text:
            qs = []
            for block in re.split(r'\+{3,}', text):
                block = block.strip()
                if not block: continue
                parts = re.split(r'={3,}', block, maxsplit=1)
                if len(parts) < 2: continue
                q_text = parts[0].strip()
                # Ajratgich satrlarni o'tkazib yuborish
                opts_raw = [
                    l.strip() for l in parts[1].strip().splitlines()
                    if l.strip() and not is_separator(l.strip())
                ]
                if not q_text or not opts_raw: continue
                options, correct, idx = [], 0, 0
                for opt in opts_raw:
                    if opt.startswith("#"):
                        correct = idx; options.append(opt[1:].strip())
                    else:
                        options.append(opt)
                    idx += 1
                if len(options) >= 2:
                    qs.append({"q": q_text, "opts": options, "ans": correct})
            return qs

        qs = []
        lines = [l.rstrip() for l in text.splitlines()]
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1; continue

            # Ajratgich satrni o'tkazib yuborish
            if is_separator(line):
                i += 1; continue

            is_q = bool(re.match(r'^\d+[\.\)]\s*.+', line)) or \
                   not re.match(r'^[a-zA-Z#][\.\)]\s*', line)
            if is_q:
                q_text = re.sub(r'^\d+[\.\)]\s*', '', line).strip()
                if not q_text: i += 1; continue
                options, correct, opt_idx = [], 0, 0
                i += 1
                while i < len(lines):
                    vline = lines[i].strip()
                    if not vline:
                        i += 1; break

                    # Variant ichidagi ajratgich satrni o'tkazib yuborish
                    if is_separator(vline):
                        i += 1; continue

                    if re.match(r'^\d+[\.\)]\s*.+', vline) and options:
                        break
                    m = re.match(r'^(#?)([a-zA-Z]?)[\.\)]\s*(.*)', vline)
                    if m:
                        is_correct = bool(m.group(1))
                        opt_text = m.group(3).strip()
                        if opt_text and not is_separator(opt_text):
                            if is_correct: correct = opt_idx
                            options.append(opt_text); opt_idx += 1
                        i += 1
                    elif vline.startswith("#"):
                        clean = re.sub(r'^#[a-dA-D]?[\.\)]\s*', '', vline[1:]).strip() or vline[1:].strip()
                        if clean and not is_separator(clean):
                            correct = opt_idx; options.append(clean); opt_idx += 1
                        i += 1
                    else:
                        i += 1
                if q_text and len(options) >= 2:
                    qs.append({"q": q_text, "opts": options, "ans": correct})
            else:
                i += 1
        return qs

    # ============================================================
    #  TO'LOV HANDLERLARI
    # ============================================================

    @bot_client.on(events.NewMessage(pattern="💳 To'lov qilish"))
    async def cmd_pay(event):
        uid = event.sender_id
        bal = db_get_balance(uid)
        await event.respond(
            f"💳 **To'lov**\n\n"
            f"💰 Hozirgi balans: **{bal:,} so'm**\n"
            f"🤖 1 ta AI test narxi: **{AI_PRICE:,} so'm**\n\n"
            f"Karta orqali to'lash uchun tugmani bosing:",
            buttons=[
                [Button.text(f"💳 {AI_PRICE:,} so'm to'lash")],
                [Button.text("💰 Balansni ko'rish")],
                [Button.text("🔙 Bosh menyu")],
            ]
        )

    @bot_client.on(events.NewMessage(func=lambda e: not e.file
                                     and e.text.strip() == "💰 Balansni ko'rish"))
    async def cmd_balance(event):
        uid = event.sender_id
        bal = db_get_balance(uid)
        tests = bal // AI_PRICE
        await event.respond(
            f"💰 **Balans: {bal:,} so'm**\n"
            f"🤖 Yozib olish mumkin: **{tests} ta** AI test\n\n"
            f"{'✅ Test tuzish mumkin!' if bal >= AI_PRICE else '❌ Balans yetarli emas. To\'lash kerak.'}",
            buttons=[
                [Button.text(f"💳 {AI_PRICE:,} so'm to'lash")],
                [Button.text("🔙 Bosh menyu")],
            ]
        )

    @bot_client.on(events.NewMessage(
        func=lambda e: not e.file and
        bool(re.match(r'^💳 [\d\s,.]+ so\'m to\'lash$', e.text.strip()))
    ))
    async def pay_request(event):
        uid  = event.sender_id
        text = event.text.strip()

        # Summani xabar matnidan ajratib olamiz
        m = re.search(r'([\d\s,.]+)\s*so\'m', text)
        try:
            pay_amount = int(m.group(1).replace(' ','').replace(',','').replace('.','')) if m else AI_PRICE
        except Exception:
            pay_amount = AI_PRICE

        # Avvalgi kutilayotgan to'lov bormi?
        pending = db_get_pending(uid)
        if pending:
            pay_id, card_num, amount, expires = pending
            await event.respond(
                f"⏳ **Kutilayotgan to'lov mavjud**\n\n"
                f"💳 Karta: `{card_num}`\n"
                f"💰 Summa: **{amount:,} so'm**\n"
                f"⏰ Muddat: {expires[11:16]}\n\n"
                f"Shu kartaga {amount:,} so'm o'tkazing!",
                buttons=[[Button.text("🔙 Bosh menyu")]]
            )
            return

        # Bo'sh karta olish
        card = get_free_card(uid)
        if not card:
            await event.respond(
                "⚠️ Hozir barcha kartalar band!\n"
                "Bir daqiqadan so'ng qayta urining.",
                buttons=[[Button.text("🔙 Bosh menyu")]]
            )
            return

        # To'lov yaratish
        pay_id = db_create_payment(uid, card, pay_amount)

        await event.respond(
            f"💳 **To'lov ma'lumotlari**\n\n"
            f"🏦 Bank: **Humo**\n"
            f"💳 Karta: `{card}`\n"
            f"💰 Summa: **{pay_amount:,} so'm**\n"
            f"⏰ Muddat: **3 daqiqa**\n\n"
            f"⚡ Pul o'tkazganingizdan so'ng\n"
            f"**avtomatik** tasdiqlanadi!\n\n"
            f"❗ Faqat shu kartaga va aynan\n"
            f"**{pay_amount:,} so'm** o'tkazing!",
            buttons=[[Button.text("🔙 Bosh menyu")]]
        )
        log.info(f"To'lov yaratildi: user={uid}, karta={card}, summa={pay_amount}, id={pay_id}")

    # ============================================================
    #  @HUMOCARDBOT XABAR TINGLOVCHI
    # ============================================================
    # notify_client global — /notify_ulash orqali o'rnatilishi mumkin
    notify_client_holder = {"client": None}

    def setup_notify_listener(client):
        """Notify client ga @humocardbot handler o'rnatish"""
        @client.on(events.NewMessage(from_users="humocardbot"))
        async def on_humo_notify(event):
            text = event.text or ""
            log.info(f"humocardbot xabari: {text[:150]}")
            amount = _parse_amount(text)
            card   = _parse_card(text)
            log.info(f"Parse natijasi: summa={amount}, karta={card}")
            if not amount:
                log.warning(f"Summa aniqlanmadi: {text[:80]}")
                return
            if not card:
                log.warning(f"Karta aniqlanmadi: {text[:80]}")
                return
            log.info(f"To'lov aniqlandi: karta={card}, summa={amount}")
            con = sqlite3.connect(DB_FILE)
            row = con.execute(
                """SELECT id, user_id, amount FROM payments
                   WHERE card_num=? AND status='pending'
                   AND expires_at > datetime('now','localtime')
                   ORDER BY id DESC LIMIT 1""",
                (card,)
            ).fetchone()
            con.close()
            if not row:
                log.warning(f"Mos to'lov topilmadi: karta={card}")
                return
            pay_id, user_id, expected_amount = row
            if amount < expected_amount:
                await bot_client.send_message(
                    user_id,
                    f"⚠️ **Noto'g'ri summa!**\n\n"
                    f"Kerak: **{expected_amount:,} so'm**\n"
                    f"Tushgan: **{amount:,} so'm**\n\n"
                    f"Farq: {expected_amount - amount:,} so'm qo'shimcha yuboring!"
                )
                return
            db_confirm_payment(pay_id)
            db_add_balance(user_id, amount, f"To'lov #{pay_id} tasdiqlandi")
            release_card(card)
            bal = db_get_balance(user_id)
            tests = bal // AI_PRICE
            prev_state = user_states.get(user_id)
            has_pending_ai   = prev_state and prev_state.step == "wait_payment" and prev_state.fan_name
            has_pending_file = prev_state and prev_state.step == "wait_payment_file" and prev_state.questions
            if has_pending_file:
                q_count = prev_state.total_questions
                price   = calc_file_price(q_count)
                if bal >= price:
                    db_deduct_balance(user_id, price, f"Fayl quiz: {q_count} ta savol")
                    bal_left = db_get_balance(user_id)
                    prev_state.step = "ask_fan_name"
                    user_states[user_id] = prev_state
                    await bot_client.send_message(
                        user_id,
                        f"✅ **To'lov tasdiqlandi! +{amount:,} so'm**\n\n"
                        f"📂 {q_count} ta savol tayyor\n"
                        f"💰 -{price:,} so'm | Balans: {bal_left:,} so'm\n\n"
                        f"Fan nomini yozing:",
                        buttons=[[Button.text("🔙 Bosh menyu")]]
                    )
                else:
                    await bot_client.send_message(
                        user_id,
                        f"✅ +{amount:,} so'm | Balans: {bal:,} so'm\n"
                        f"⚠️ Hali yetarli emas. Kerak: {price:,} so'm",
                        buttons=[[Button.text(f"💳 {price-bal:,} so'm to'lash"),
                                  Button.text("🔙 Bosh menyu")]]
                    )
            elif has_pending_ai:
                await bot_client.send_message(
                    user_id,
                    f"✅ **To'lov tasdiqlandi! +{amount:,} so'm**\n\n"
                    f"💼 Balans: **{bal:,} so'm**\n\n"
                    f"🤖 Oldingi sozlamalar:\n"
                    f"📚 {prev_state.fan_name}"
                    f"{f' | 📌 {prev_state.topic}' if prev_state.topic else ''}\n"
                    f"🔢 {prev_state.q_count} ta | 🎯 {prev_state.difficulty}\n\n"
                    f"⏳ AI test tuzilmoqda..."
                )
                try:
                    qs = await generate_questions(
                        prev_state.fan_name, prev_state.q_count,
                        prev_state.lang, prev_state.difficulty, prev_state.topic
                    )
                    if not qs:
                        await bot_client.send_message(user_id, "❌ AI savol yarata olmadi!")
                        return
                    db_deduct_balance(user_id, AI_PRICE, f"AI test: {prev_state.fan_name}")
                    bal_left = db_get_balance(user_id)
                    prev_state.questions = qs
                    prev_state.total_questions = len(qs)
                    prev_state.per_variant = len(qs)
                    prev_state.step = "ask_time"
                    user_states[user_id] = prev_state
                    await bot_client.send_message(
                        user_id,
                        f"✅ **{len(qs)} ta savol tayyor!**\n"
                        f"💰 Balans: {bal_left:,} so'm\n\n⏱ Vaqt:",
                        buttons=[[Button.text("⏱ 15s"), Button.text("⏱ 30s")],
                                 [Button.text("⏱ 60s"), Button.text("⏱ Chegarasiz")]]
                    )
                except Exception as e:
                    log.error(f"AI xato (to'lovdan keyin): {e}")
                    await bot_client.send_message(user_id, f"❌ AI xato: {e}")
            else:
                await bot_client.send_message(
                    user_id,
                    f"✅ **To'lov tasdiqlandi!**\n\n"
                    f"💰 +{amount:,} so'm\n"
                    f"💼 Balans: **{bal:,} so'm**\n"
                    f"🤖 {tests} ta AI test mumkin 🎉",
                    buttons=[[Button.text("🤖 AI test tuzish", resize=True),
                              Button.text("🔙 Bosh menyu", resize=True)]]
                )
            log.info(f"✅ To'lov tasdiqlandi: user={user_id}, +{amount} so'm")
        log.info(f"✅ @humocardbot tinglash aktiv: {client}")

    # Mavjud ulangan notify clientni sozlash
    for c in all_clients:
        if account_phones.get(id(c)) == NOTIFY_PHONE:
            notify_client_holder["client"] = c
            setup_notify_listener(c)
            log.info(f"✅ Notify aktiv: {NOTIFY_PHONE}")
            break
    else:
        log.warning(f"⚠️ Notify akkaunt topilmadi: {NOTIFY_PHONE} — /notify_ulash buyrug'ini ishlating")

    # ============================================================
    #  /notify_ulash — bot orqali notify akkauntni ulash
    # ============================================================
    @bot_client.on(events.NewMessage(pattern="/notify_ulash"))
    async def cmd_notify_connect(event):
        if not is_admin(event.sender_id): return
        if notify_client_holder["client"]:
            phone = account_phones.get(id(notify_client_holder["client"]), "?")
            await event.respond(f"✅ Notify akkaunt allaqachon ulangan: `{phone}`")
            return
        if not NOTIFY_PHONE:
            await event.respond("❌ NOTIFY_PHONE environment variable o'rnatilmagan!")
            return

        await event.respond(
            f"📱 **Notify akkaunt ulash**\n\n"
            f"Raqam: `{NOTIFY_PHONE}`\n\n"
            f"📲 Kod yuborilmoqda..."
        )

        try:
            sess_dir = _os.path.dirname(DB_FILE)
            session  = _os.path.join(sess_dir, f"userbot_{NOTIFY_PHONE.replace('+','').replace(' ','')}")
            client   = TelegramClient(session, API_ID, API_HASH)
            await client.connect()
            result = await client.send_code_request(NOTIFY_PHONE)
            admin_states[event.sender_id] = {
                "step":   "wait_notify_code",
                "phone":  NOTIFY_PHONE,
                "client": client,
                "hash":   result.phone_code_hash,
            }
            await event.respond(
                f"✅ Kod yuborildi!\n\n"
                f"`{NOTIFY_PHONE}` ga kelgan **SMS/Telegram kodni** yuboring\n"
                f"_(Misol: 1 2 3 4 5 — bo'shliq bilan yoki birga)_\n\n"
                f"/cancel — bekor"
            )
        except Exception as e:
            await event.respond(f"❌ Xato: {e}")
            admin_states.pop(event.sender_id, None)

    @bot_client.on(events.NewMessage(
        func=lambda e: not e.file and not e.text.startswith("/")
        and admin_states.get(e.sender_id, {}).get("step") == "wait_notify_code"
        and e.sender_id in ADMIN_IDS
    ))
    async def on_notify_code(event):
        uid   = event.sender_id
        code  = event.text.strip().replace(" ", "")
        astate = admin_states.get(uid, {})
        client = astate.get("client")
        phone  = astate.get("phone")
        ph_hash = astate.get("hash")
        if not client:
            admin_states.pop(uid, None); return
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=ph_hash)
            # Ulandi — listener o'rnatamiz
            all_clients.append(client)
            account_phones[id(client)] = phone
            notify_client_holder["client"] = client
            setup_notify_listener(client)
            admin_states.pop(uid, None)
            await event.respond(
                f"✅ **Notify akkaunt ulandi!**\n\n"
                f"📱 `{phone}`\n"
                f"🔔 @humocardbot xabarlari endi qabul qilinadi!"
            )
            log.info(f"Notify akkaunt ulandi: {phone}")
        except SessionPasswordNeededError:
            admin_states[uid]["step"] = "wait_notify_pass"
            await event.respond("🔐 2FA parol kerak. Parolni yuboring:")
        except PhoneCodeInvalidError:
            await event.respond("❌ Kod noto'g'ri! Qayta yuboring:")
        except Exception as e:
            await event.respond(f"❌ Xato: {e}")
            try: await client.disconnect()
            except: pass
            admin_states.pop(uid, None)

    @bot_client.on(events.NewMessage(
        func=lambda e: not e.file and not e.text.startswith("/")
        and admin_states.get(e.sender_id, {}).get("step") == "wait_notify_pass"
        and e.sender_id in ADMIN_IDS
    ))
    async def on_notify_pass(event):
        uid    = event.sender_id
        astate = admin_states.get(uid, {})
        client = astate.get("client")
        phone  = astate.get("phone")
        if not client:
            admin_states.pop(uid, None); return
        try:
            await client.sign_in(password=event.text.strip())
            all_clients.append(client)
            account_phones[id(client)] = phone
            notify_client_holder["client"] = client
            setup_notify_listener(client)
            admin_states.pop(uid, None)
            await event.respond(
                f"✅ **Notify akkaunt ulandi!**\n📱 `{phone}`\n"
                f"🔔 @humocardbot endi tinglanadi!"
            )
        except Exception as e:
            await event.respond(f"❌ Parol xato: {e}")
            try: await client.disconnect()
            except: pass
            admin_states.pop(uid, None)
            """@humocardbot dan kelgan xabarni parse qilish"""
            text = event.text or ""
            log.info(f"humocardbot xabari: {text[:150]}")

            amount = _parse_amount(text)
            card   = _parse_card(text)

            log.info(f"Parse natijasi: summa={amount}, karta={card}")

            if not amount:
                log.warning(f"Summa aniqlanmadi: {text[:80]}")
                return
            if not card:
                log.warning(f"Karta aniqlanmadi: {text[:80]}")
                return

    # ============================================================
    #  YORDAMCHI: XABARDAN SUMMA VA KARTA AJRATIB OLISH
    # ============================================================
    def _parse_amount(text: str) -> Optional[int]:
        """
        Xabardan summani topish.
        Humo formatlari:
          ➕ 2.000,00 UZS
          ➕ **2.000,00 UZS**
          +2000 UZS
          2 000,00 UZS
        """
        # Markdown bold va belgilarni tozalash
        clean = text.replace('*', '').replace('_', '').replace('`', '')

        patterns = [
            # "2.000,00 UZS" — nuqta minglik, vergul kasr (Humo asosiy format)
            r'[➕\+]?\s*([\d]{1,3}(?:\.[\d]{3})*),\d{2}\s*UZS',
            # "2 000,00 UZS"
            r'[➕\+]?\s*([\d]{1,3}(?:\s[\d]{3})*),\d{2}\s*UZS',
            # "2000 UZS" yoki "2000,00 UZS"
            r'[➕\+]?\s*(\d+)(?:,\d+)?\s*UZS',
            # Umumiy fallback
            r'(\d[\d\.\s]+\d)\s*UZS',
        ]

        for pat in patterns:
            m = re.search(pat, clean, re.IGNORECASE)
            if m:
                raw = m.group(1)
                # Nuqta va bo'shliqlarni olib tashlaymiz (minglik ajratgich)
                raw = raw.replace('.', '').replace(' ', '').replace('\xa0', '')
                try:
                    return int(raw)
                except Exception:
                    continue
        return None

    def _parse_card(text: str) -> Optional[str]:
        """
        Xabardan karta oxirgi 4 raqamini topib, DB dan to'liq raqamni olish.
        Humo format: HUMOCARD *8906
        """
        # Oxirgi 4 raqamni topish: "*8906", "* 8906", "**8906"
        m = re.search(r'\*+\s*(\d{4})\b', text)
        if not m:
            return None
        last4 = m.group(1)

        # DB dagi kartalar ichidan oxirgi 4 raqami mos keladiganni topish
        for card in HUMO_CARDS:
            if card.replace(' ', '').endswith(last4):
                return card
        return None

    # ============================================================
    #  MUDDATI O'TGAN TO'LOVLARNI BEKOR QILISH (fon task)
    # ============================================================
    async def expire_checker():
        while True:
            await asyncio.sleep(60)
            expired = db_expire_old()
            if expired:
                log.info(f"⏰ {expired} ta to'lov muddati o'tdi, bekor qilindi")

    asyncio.create_task(expire_checker())
    asyncio.create_task(queue_worker())
    log.info(f"✅ AI Quiz Bot tayyor! {len(account_pool)} ta akkaunt.")
    await bot_client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
