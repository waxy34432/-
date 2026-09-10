# bot.py
# Всё в одном файле: конфиг, база данных, клавиатуры, обработчики пользователя и админки.
# Токен и ID админов НЕ хранятся в этом файле — они берутся из переменных окружения
# (файл .env, который не должен попадать в репозиторий, см. .env.example и .gitignore).
# Запуск: python bot.py

import asyncio
import logging
import os
import sys

import aiosqlite
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ======================================================================
# КОНФИГ — секреты берутся из окружения, а не из кода
# ======================================================================

load_dotenv()  # подхватывает переменные из файла .env, если он есть рядом с bot.py

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# ADMIN_IDS в .env указывается как список ID через запятую, например:
# ADMIN_IDS=123456789,987654321
_admin_ids_raw = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = [
    int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip().lstrip("-").isdigit()
]

if not BOT_TOKEN:
    sys.exit(
        "BOT_TOKEN не найден. Создай файл .env рядом с bot.py и укажи в нём "
        "BOT_TOKEN=твой_токен_от_BotFather (см. .env.example)."
    )

if not ADMIN_IDS:
    logging.warning(
        "ADMIN_IDS пуст или не задан в .env — админ-панель (/admin) будет недоступна никому."
    )

# Текст приветствия по умолчанию (используется только при первом запуске,
# пока база данных ещё пустая). Дальше меняется через /admin.
DEFAULT_GREETING = (
    "Привет! 👋\n\n"
    "Это бот для выбора города и района.\n"
    "Нажми на кнопку ниже, чтобы начать."
)

# Реквизиты для оплаты по умолчанию (используются только при первом запуске).
# Дальше меняются через /admin -> «Реквизиты оплаты».
DEFAULT_REQUISITES = (
    "Реквизиты для оплаты пока не заданы администратором.\n"
    "Свяжитесь с администратором."
)

DB_PATH = "bot.db"

# Сколько товаров-кнопок создаётся автоматически под каждым округом/районом
PRODUCTS_PER_DISTRICT = 5


# ======================================================================
# АВТО-ОЧИСТКА ЧАТА: удаляем предыдущее сообщение бота при любом переходе,
# а входящие сообщения пользователя удаляем сразу после обработки.
# ======================================================================

# chat_id -> message_id последнего отправленного ботом сообщения в этом чате.
# Хранится в памяти процесса (сбрасывается при перезапуске бота — это ок,
# в худшем случае просто не удалится одно старое сообщение).
LAST_BOT_MSG: dict[int, int] = {}


async def push_to_chat(bot: Bot, chat_id: int, text: str = None, reply_markup=None, photo: str = None):
    """Удаляет предыдущее отслеживаемое сообщение бота в этом чате и отправляет новое
    (текст или фото с подписью). Используется вместо edit_text/answer почти везде,
    чтобы в чате всегда было видно только последний шаг."""
    old_id = LAST_BOT_MSG.get(chat_id)
    if old_id:
        try:
            await bot.delete_message(chat_id, old_id)
        except Exception:
            pass
    if photo:
        new_msg = await bot.send_photo(chat_id, photo, caption=text, reply_markup=reply_markup)
    else:
        new_msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    LAST_BOT_MSG[chat_id] = new_msg.message_id
    return new_msg


class DeleteUserMessageMiddleware(BaseMiddleware):
    """Удаляет сообщение пользователя сразу после того, как хендлер его обработал —
    чтобы в чате не копились введённые пользователем тексты/скриншоты."""

    async def __call__(self, handler, event: Message, data):
        result = await handler(event, data)
        try:
            await event.delete()
        except Exception:
            pass
        return result


# ======================================================================
# БАЗА ДАННЫХ (SQLite) — приветствие, города, округа/районы
# ======================================================================

# Стартовый набор городов и их административных округов/районов.
# Используется только при первом запуске, когда база данных ещё пустая.
# Дальше всё это меняется через /admin.
SEED_DATA = {
    "Москва": [
        "ЦАО", "САО", "СВАО", "ВАО", "ЮВАО", "ЮАО",
        "ЮЗАО", "ЗАО", "СЗАО", "Зеленоградский АО",
        "Новомосковский АО", "Троицкий АО",
    ],
    "Санкт-Петербург": [
        "Адмиралтейский", "Василеостровский", "Выборгский", "Калининский",
        "Кировский", "Колпинский", "Красногвардейский", "Красносельский",
        "Кронштадтский", "Курортный", "Московский", "Невский",
        "Петроградский", "Петродворцовый", "Приморский", "Пушкинский",
        "Фрунзенский", "Центральный",
    ],
    "Нижний Новгород": [
        "Автозаводский", "Канавинский", "Ленинский", "Московский",
        "Нижегородский", "Приокский", "Советский", "Сормовский",
    ],
    "Красноярск": [
        "Железнодорожный", "Кировский", "Ленинский",
        "Октябрьский", "Свердловский", "Советский", "Центральный",
    ],
}


async def init_db():
    """Создаёт таблицы (если их ещё нет) и заполняет стартовыми данными."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS cities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS districts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                FOREIGN KEY (city_id) REFERENCES cities (id) ON DELETE CASCADE
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                district_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                price REAL,
                FOREIGN KEY (district_id) REFERENCES districts (id) ON DELETE CASCADE
            )"""
        )
        await db.commit()

        # миграция: если база создавалась старой версией бота (без цен),
        # добавляем колонку price к уже существующей таблице products
        cur = await db.execute("PRAGMA table_info(products)")
        columns = [row[1] for row in await cur.fetchall()]
        if "price" not in columns:
            await db.execute("ALTER TABLE products ADD COLUMN price REAL")
            await db.commit()

        cur = await db.execute("SELECT value FROM settings WHERE key = 'greeting'")
        row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES ('greeting', ?)",
                (DEFAULT_GREETING,),
            )
            await db.commit()

        cur = await db.execute("SELECT value FROM settings WHERE key = 'requisites'")
        row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES ('requisites', ?)",
                (DEFAULT_REQUISITES,),
            )
            await db.commit()

        cur = await db.execute("SELECT COUNT(*) FROM cities")
        (count,) = await cur.fetchone()
        if count == 0:
            for city_name, districts in SEED_DATA.items():
                cur = await db.execute(
                    "INSERT INTO cities (name) VALUES (?)", (city_name,)
                )
                city_id = cur.lastrowid
                for d in districts:
                    cur2 = await db.execute(
                        "INSERT INTO districts (city_id, name) VALUES (?, ?)",
                        (city_id, d),
                    )
                    district_id = cur2.lastrowid
                    for i in range(1, PRODUCTS_PER_DISTRICT + 1):
                        await db.execute(
                            "INSERT INTO products (district_id, name) VALUES (?, ?)",
                            (district_id, f"Товар {i}"),
                        )
            await db.commit()


# ---------- Приветствие ----------

async def get_greeting() -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = 'greeting'")
        row = await cur.fetchone()
        return row[0] if row else DEFAULT_GREETING


async def set_greeting(text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES ('greeting', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (text,),
        )
        await db.commit()


# ---------- Реквизиты для оплаты ----------

async def get_requisites() -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = 'requisites'")
        row = await cur.fetchone()
        return row[0] if row else DEFAULT_REQUISITES


async def set_requisites(text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES ('requisites', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (text,),
        )
        await db.commit()


# ---------- Города ----------

async def get_cities():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name FROM cities ORDER BY id")
        return await cur.fetchall()


async def get_city(city_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name FROM cities WHERE id = ?", (city_id,))
        return await cur.fetchone()


async def add_city(name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO cities (name) VALUES (?)", (name,))
        await db.commit()
        return cur.lastrowid


async def rename_city(city_id: int, new_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE cities SET name = ? WHERE id = ?", (new_name, city_id))
        await db.commit()


async def delete_city(city_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM districts WHERE city_id = ?", (city_id,))
        await db.execute("DELETE FROM cities WHERE id = ?", (city_id,))
        await db.commit()


# ---------- Округа / районы ----------

async def get_districts(city_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, name FROM districts WHERE city_id = ? ORDER BY id", (city_id,)
        )
        return await cur.fetchall()


async def get_district(district_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, city_id, name FROM districts WHERE id = ?", (district_id,)
        )
        return await cur.fetchone()


async def add_district(city_id: int, name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO districts (city_id, name) VALUES (?, ?)", (city_id, name)
        )
        district_id = cur.lastrowid
        for i in range(1, PRODUCTS_PER_DISTRICT + 1):
            await db.execute(
                "INSERT INTO products (district_id, name) VALUES (?, ?)",
                (district_id, f"Товар {i}"),
            )
        await db.commit()
        return district_id


async def rename_district(district_id: int, new_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE districts SET name = ? WHERE id = ?", (new_name, district_id)
        )
        await db.commit()


async def delete_district(district_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM products WHERE district_id = ?", (district_id,))
        await db.execute("DELETE FROM districts WHERE id = ?", (district_id,))
        await db.commit()


# ---------- Товары ----------

async def get_products(district_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, name, price FROM products WHERE district_id = ? ORDER BY id",
            (district_id,),
        )
        return await cur.fetchall()


async def get_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, district_id, name, price FROM products WHERE id = ?",
            (product_id,),
        )
        return await cur.fetchone()


async def set_product(product_id: int, name: str = None, price=None, update_price: bool = False):
    """Обновляет название товара и, если update_price=True, его цену
    (price может быть None — это значит "цена не указана")."""
    async with aiosqlite.connect(DB_PATH) as db:
        if update_price:
            await db.execute(
                "UPDATE products SET name = ?, price = ? WHERE id = ?",
                (name, price, product_id),
            )
        else:
            await db.execute(
                "UPDATE products SET name = ? WHERE id = ?", (name, product_id)
            )
        await db.commit()


async def bulk_set_products_by_slot(slot_index: int, name: str, price=None, update_price: bool = False):
    """Меняет название (и, если update_price=True, цену) товара №slot_index
    сразу во всех округах/районах всех городов. slot_index считается по порядку
    добавления товара внутри округа/района (1 = первый добавленный товар и т.д.).
    Возвращает количество изменённых записей."""
    updated = 0
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM districts")
        district_ids = [row[0] for row in await cur.fetchall()]

        for district_id in district_ids:
            cur = await db.execute(
                "SELECT id FROM products WHERE district_id = ? ORDER BY id",
                (district_id,),
            )
            product_ids = [row[0] for row in await cur.fetchall()]
            if len(product_ids) < slot_index:
                continue
            target_id = product_ids[slot_index - 1]
            if update_price:
                await db.execute(
                    "UPDATE products SET name = ?, price = ? WHERE id = ?",
                    (name, price, target_id),
                )
            else:
                await db.execute(
                    "UPDATE products SET name = ? WHERE id = ?", (name, target_id)
                )
            updated += 1

        await db.commit()
    return updated


# ---------- Заказы ----------

async def init_orders_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                city_name TEXT,
                district_name TEXT,
                product_name TEXT,
                price REAL,
                grams REAL,
                total REAL,
                status TEXT NOT NULL DEFAULT 'waiting_payment',
                screenshot_file_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        await db.commit()


async def create_order(user_id, username, city_name, district_name, product_name, price, grams, total) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO orders
               (user_id, username, city_name, district_name, product_name, price, grams, total, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting_payment')""",
            (user_id, username, city_name, district_name, product_name, price, grams, total),
        )
        await db.commit()
        return cur.lastrowid


async def get_order(order_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT id, user_id, username, city_name, district_name, product_name,
                      price, grams, total, status, screenshot_file_id
               FROM orders WHERE id = ?""",
            (order_id,),
        )
        return await cur.fetchone()


async def set_order_screenshot(order_id: int, file_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE orders SET screenshot_file_id = ?, status = 'screenshot_sent' WHERE id = ?",
            (file_id, order_id),
        )
        await db.commit()


async def set_order_status(order_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        await db.commit()


async def get_active_orders():
    """Заказы, которые ещё требуют внимания администратора (не завершены и не отклонены)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT id, username, product_name, total, status FROM orders
               WHERE status IN ('waiting_payment', 'screenshot_sent', 'payment_confirmed')
               ORDER BY id DESC"""
        )
        return await cur.fetchall()


# ======================================================================
# ФОРМАТИРОВАНИЕ ЦЕН И РАЗБОР ВВОДА "Название | Цена"
# ======================================================================

ORDER_STATUS_LABELS = {
    "waiting_payment": "⏳ Ожидает оплаты",
    "screenshot_sent": "📸 Скриншот прислан, ждёт проверки",
    "payment_confirmed": "✅ Оплата подтверждена, ждёт отправки товара",
    "completed": "📦 Завершён",
    "rejected": "❌ Отклонён",
}


def format_price(price) -> str:
    if price is None:
        return "цена не указана"
    price = float(price)
    if price == int(price):
        return f"{int(price)}₽"
    return f"{price:g}₽"


def format_product_button_text(name: str, price) -> str:
    if price is None:
        return name
    return f"{name} — {format_price(price)}"


def parse_name_and_price(text: str):
    """Разбирает ввод админа вида 'Название | Цена'.
    Если '|' нет — считаем, что прислали только название, цену не трогаем.
    Возвращает (name, price, price_provided)."""
    text = (text or "").strip()
    if "|" not in text:
        return text, None, False

    name_part, price_part = text.split("|", 1)
    name = name_part.strip()
    price_str = (
        price_part.strip()
        .replace("₽", "")
        .replace("руб.", "")
        .replace("руб", "")
        .replace(",", ".")
        .strip()
    )
    if not price_str:
        return name, None, True  # прислали "Название |" - явно очищают цену
    try:
        price = float(price_str)
    except ValueError:
        price = None
    return name, price, True


# ======================================================================
# КЛАВИАТУРЫ ДЛЯ ОБЫЧНОГО ПОЛЬЗОВАТЕЛЯ
# ======================================================================

def add_main_menu_button(builder: InlineKeyboardBuilder) -> InlineKeyboardBuilder:
    """Добавляет кнопку «Главное меню» — есть почти на каждом экране бота."""
    builder.button(text="🏠 Главное меню", callback_data="back_to_start")
    return builder


def start_keyboard(show_admin_button: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏙 Выбрать город", callback_data="choose_city")
    if show_admin_button:
        builder.button(text="⚙️ Админ-панель", callback_data="admin:menu")
    builder.adjust(1)
    return builder.as_markup()


def cities_keyboard(cities) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for city_id, name in cities:
        builder.button(text=name, callback_data=f"city:{city_id}")
    builder.button(text="⬅️ Назад", callback_data="back_to_start")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


def districts_keyboard(districts, city_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for district_id, name in districts:
        builder.button(text=name, callback_data=f"district:{district_id}")
    builder.button(text="⬅️ К списку городов", callback_data="choose_city")
    add_main_menu_button(builder)
    builder.adjust(2)
    return builder.as_markup()


def products_keyboard(products, city_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product_id, name, price in products:
        builder.button(
            text=format_product_button_text(name, price), callback_data=f"product:{product_id}"
        )
    builder.button(text="⬅️ К округам/районам", callback_data=f"city:{city_id}")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


# ======================================================================
# ОБРАБОТЧИКИ ДЛЯ ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ
# ======================================================================

user_router = Router()


class UserStates(StatesGroup):
    waiting_grams = State()
    waiting_screenshot = State()


@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    greeting = await get_greeting()
    await push_to_chat(message.bot, message.chat.id,
        greeting, reply_markup=start_keyboard(show_admin_button=is_admin(message.from_user.id))
    )


@user_router.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    greeting = await get_greeting()
    await push_to_chat(callback.bot, callback.message.chat.id,
        greeting,
        reply_markup=start_keyboard(show_admin_button=is_admin(callback.from_user.id)),
    )
    await callback.answer()


@user_router.callback_query(F.data == "choose_city")
async def choose_city(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    cities = await get_cities()
    if not cities:
        await callback.answer("Список городов пока пуст.", show_alert=True)
        return
    await push_to_chat(callback.bot, callback.message.chat.id,
        "Выбери город:", reply_markup=cities_keyboard(cities)
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("city:"))
async def choose_district(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    city_id = int(callback.data.split(":")[1])
    city = await get_city(city_id)
    if not city:
        await callback.answer("Этот город больше не существует.", show_alert=True)
        return

    districts = await get_districts(city_id)
    if not districts:
        await callback.answer(
            "Для этого города пока не добавлены округа/районы.", show_alert=True
        )
        return

    await push_to_chat(callback.bot, callback.message.chat.id,
        f"Город: {city[1]}\nВыбери округ/район:",
        reply_markup=districts_keyboard(districts, city_id),
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("district:"))
async def district_chosen(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    district_id = int(callback.data.split(":")[1])
    district = await get_district(district_id)
    if not district:
        await callback.answer("Этот округ/район больше не существует.", show_alert=True)
        return

    _, city_id, district_name = district
    products = await get_products(district_id)
    if not products:
        await callback.answer(
            "Для этого округа/района пока не добавлены товары.", show_alert=True
        )
        return

    await push_to_chat(callback.bot, callback.message.chat.id,
        f"📍 Округ/район: {district_name}\nВыбери товар:",
        reply_markup=products_keyboard(products, city_id),
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("product:"))
async def product_chosen(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":")[1])
    product = await get_product(product_id)
    if not product:
        await callback.answer("Этот товар больше не существует.", show_alert=True)
        return

    _, district_id, product_name, product_price = product
    district = await get_district(district_id)
    district_name = district[2] if district else "?"
    city = await get_city(district[1]) if district else None
    city_name = city[1] if city else "?"

    await state.set_state(UserStates.waiting_grams)
    await state.update_data(
        product_id=product_id,
        product_name=product_name,
        product_price=product_price,
        district_name=district_name,
        city_name=city_name,
    )

    price_line = f"💰 Цена: {format_price(product_price)}\n" if product_price is not None else ""
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"🏙 Город: {city_name}\n📍 Округ/район: {district_name}\n🛒 Товар: {product_name}\n"
        f"{price_line}\n"
        f"Введи нужное количество грамм числом (например: 5 или 10.5)."
    )
    await callback.answer()


@user_router.message(UserStates.waiting_grams)
async def grams_entered(message: Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        grams = float(raw)
        if grams <= 0:
            raise ValueError
    except ValueError:
        await push_to_chat(message.bot, message.chat.id,
            "Не похоже на число. Введи количество грамм числом, например: 5 или 10.5"
        )
        return

    data = await state.get_data()
    product = await get_product(data["product_id"])
    if not product:
        await state.clear()
        await push_to_chat(message.bot, message.chat.id,
            "Этот товар больше не существует. Отправьте /start, чтобы начать заново."
        )
        return

    requisites = await get_requisites()
    grams_display = int(grams) if grams == int(grams) else grams

    price = data.get("product_price")
    total = price * grams if price is not None else None
    if price is not None:
        total_line = f"💰 Цена за грамм: {format_price(price)}\n💵 Итого: {format_price(total)}\n"
    else:
        total_line = "💰 Цена не указана, уточните сумму у администратора.\n"

    order_id = await create_order(
        user_id=message.from_user.id,
        username=message.from_user.username or message.from_user.full_name,
        city_name=data["city_name"],
        district_name=data["district_name"],
        product_name=data["product_name"],
        price=price,
        grams=grams,
        total=total,
    )

    await state.set_state(UserStates.waiting_screenshot)
    await state.update_data(order_id=order_id)

    builder = InlineKeyboardBuilder()
    add_main_menu_button(builder)
    builder.adjust(1)

    await push_to_chat(message.bot, message.chat.id,
        f"⏳ Заказ №{order_id} оформлен, ожидает оплаты.\n\n"
        f"🏙 Город: {data['city_name']}\n"
        f"📍 Округ/район: {data['district_name']}\n"
        f"🛒 Товар: {data['product_name']}\n"
        f"⚖️ Количество: {grams_display} г\n"
        f"{total_line}\n"
        f"💳 Реквизиты для оплаты:\n{requisites}\n\n"
        f"📸 Оплати и пришли сюда скриншот оплаты — заказ отправится администратору на проверку.",
        reply_markup=builder.as_markup(),
    )


@user_router.message(UserStates.waiting_screenshot, F.photo)
async def order_screenshot_received(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    order = await get_order(order_id) if order_id else None
    if not order:
        await state.clear()
        await push_to_chat(message.bot, message.chat.id,
            "Этот заказ не найден. Отправьте /start, чтобы начать заново.",
        )
        return

    file_id = message.photo[-1].file_id
    await set_order_screenshot(order_id, file_id)
    await state.clear()

    builder = InlineKeyboardBuilder()
    add_main_menu_button(builder)
    builder.adjust(1)
    await push_to_chat(message.bot, message.chat.id,
        f"📸 Скриншот получен, заказ №{order_id} отправлен администратору на проверку.\n"
        f"Как только оплата подтвердится, тебе придёт сообщение.",
        reply_markup=builder.as_markup(),
    )

    # уведомляем всех админов новым сообщением с кнопками подтверждения/отклонения
    (_, user_id, username, city_name, district_name, product_name,
     order_price, order_grams, order_total, _, screenshot_file_id) = order
    grams_disp = int(order_grams) if order_grams == int(order_grams) else order_grams
    total_line = f"💵 Сумма: {format_price(order_total)}\n" if order_total is not None else ""
    caption = (
        f"🆕 Новый заказ №{order_id}\n"
        f"👤 Пользователь: {username} (id {user_id})\n"
        f"🏙 {city_name} — 📍 {district_name}\n"
        f"🛒 {product_name}\n"
        f"⚖️ Количество: {grams_disp} г\n"
        f"{total_line}\n"
        f"Проверь скриншот оплаты и подтверди."
    )
    order_builder = InlineKeyboardBuilder()
    order_builder.button(text="✅ Подтвердить оплату", callback_data=f"admin:order_confirm:{order_id}")
    order_builder.button(text="❌ Отклонить", callback_data=f"admin:order_reject:{order_id}")
    order_builder.adjust(1)
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_photo(
                admin_id, screenshot_file_id, caption=caption, reply_markup=order_builder.as_markup()
            )
        except Exception:
            logging.exception("Не удалось отправить уведомление о заказе админу %s", admin_id)


@user_router.message(UserStates.waiting_screenshot)
async def order_screenshot_wrong_type(message: Message, state: FSMContext):
    await push_to_chat(message.bot, message.chat.id,
        "Пришли, пожалуйста, именно скриншот (фото) оплаты."
    )


# ======================================================================
# АДМИН-ПАНЕЛЬ
# ======================================================================

admin_router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


class AdminStates(StatesGroup):
    waiting_greeting = State()
    waiting_requisites = State()
    waiting_new_city_name = State()
    waiting_city_rename = State()
    waiting_new_district_name = State()
    waiting_district_rename = State()
    waiting_product_rename = State()
    waiting_bulk_product = State()
    waiting_order_delivery = State()


def admin_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить приветствие", callback_data="admin:greeting")
    builder.button(text="💳 Реквизиты оплаты", callback_data="admin:requisites")
    builder.button(text="🏙 Управление городами", callback_data="admin:cities")
    builder.button(text="🛒 Поменять товар", callback_data="admin:products")
    builder.button(text="🌐 Товар во всех районах", callback_data="admin:bulk_products")
    builder.button(text="📦 Заказы", callback_data="admin:orders")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return  # обычные пользователи не видят и не знают об этой команде
    await state.clear()
    await push_to_chat(message.bot, message.chat.id,"⚙️ Админ-панель", reply_markup=admin_main_menu())


@admin_router.callback_query(F.data == "admin:menu")
async def admin_menu_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await push_to_chat(callback.bot, callback.message.chat.id,"⚙️ Админ-панель", reply_markup=admin_main_menu())
    await callback.answer()


# ---------- Приветствие ----------

@admin_router.callback_query(F.data == "admin:greeting")
async def admin_greeting(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    current = await get_greeting()
    await state.set_state(AdminStates.waiting_greeting)
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"Текущий текст приветствия:\n\n{current}\n\n"
        f"Пришли новый текст сообщением (можно с эмодзи и переносами строк)."
    )
    await callback.answer()


@admin_router.message(AdminStates.waiting_greeting)
async def save_greeting(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await set_greeting(message.html_text or message.text)
    await state.clear()
    await push_to_chat(message.bot, message.chat.id,"✅ Приветствие обновлено.", reply_markup=admin_main_menu())


# ---------- Реквизиты для оплаты ----------

@admin_router.callback_query(F.data == "admin:requisites")
async def admin_requisites(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    current = await get_requisites()
    await state.set_state(AdminStates.waiting_requisites)
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"Текущие реквизиты для оплаты:\n\n{current}\n\n"
        f"Пришли новый текст реквизитов сообщением (можно с эмодзи и переносами строк). "
        f"Именно этот текст будет высылаться пользователю после ввода количества грамм."
    )
    await callback.answer()


@admin_router.message(AdminStates.waiting_requisites)
async def save_requisites(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await set_requisites(message.html_text or message.text)
    await state.clear()
    await push_to_chat(message.bot, message.chat.id,"✅ Реквизиты для оплаты обновлены.", reply_markup=admin_main_menu())


# ---------- Города: список ----------

async def cities_list_markup():
    cities = await get_cities()
    builder = InlineKeyboardBuilder()
    for city_id, name in cities:
        builder.button(text=name, callback_data=f"admin:city:{city_id}")
    builder.button(text="➕ Добавить город", callback_data="admin:city_add")
    builder.button(text="⬅️ Назад", callback_data="admin:menu")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


@admin_router.callback_query(F.data == "admin:cities")
async def admin_cities(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await push_to_chat(callback.bot, callback.message.chat.id,
        "🏙 Города. Выбери город, чтобы его отредактировать, или добавь новый.",
        reply_markup=await cities_list_markup(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:city_add")
async def admin_city_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_new_city_name)
    await push_to_chat(callback.bot, callback.message.chat.id,"Пришли название нового города сообщением.")
    await callback.answer()


@admin_router.message(AdminStates.waiting_new_city_name)
async def save_new_city(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await add_city(message.text.strip())
    await state.clear()
    await push_to_chat(message.bot, message.chat.id,
        f"✅ Город «{message.text.strip()}» добавлен.",
        reply_markup=await cities_list_markup(),
    )


# ---------- Карточка одного города ----------

def city_card_markup(city_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Переименовать", callback_data=f"admin:city_rename:{city_id}")
    builder.button(text="📍 Округа/районы", callback_data=f"admin:districts:{city_id}")
    builder.button(text="🗑 Удалить город", callback_data=f"admin:city_delete:{city_id}")
    builder.button(text="⬅️ К списку городов", callback_data="admin:cities")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


@admin_router.callback_query(F.data.startswith("admin:city:"))
async def admin_city_card(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    city = await get_city(city_id)
    if not city:
        await callback.answer("Город не найден.", show_alert=True)
        return
    districts = await get_districts(city_id)
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"🏙 Город: {city[1]}\nОкругов/районов: {len(districts)}",
        reply_markup=city_card_markup(city_id),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:city_rename:"))
async def admin_city_rename_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waiting_city_rename)
    await state.update_data(city_id=city_id)
    await push_to_chat(callback.bot, callback.message.chat.id,"Пришли новое название города сообщением.")
    await callback.answer()


@admin_router.message(AdminStates.waiting_city_rename)
async def admin_city_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    city_id = data["city_id"]
    await rename_city(city_id, message.text.strip())
    await state.clear()
    await push_to_chat(message.bot, message.chat.id,"✅ Город переименован.", reply_markup=city_card_markup(city_id))


@admin_router.callback_query(F.data.startswith("admin:city_delete:"))
async def admin_city_delete(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    await delete_city(city_id)
    await callback.answer("Город и все его округа/районы удалены.", show_alert=True)
    await push_to_chat(callback.bot, callback.message.chat.id,
        "🏙 Города:", reply_markup=await cities_list_markup()
    )


# ---------- Округа/районы конкретного города ----------

async def districts_list_markup(city_id: int):
    districts = await get_districts(city_id)
    builder = InlineKeyboardBuilder()
    for district_id, name in districts:
        builder.button(text=name, callback_data=f"admin:district:{district_id}")
    builder.button(text="➕ Добавить округ/район", callback_data=f"admin:district_add:{city_id}")
    builder.button(text="⬅️ К городу", callback_data=f"admin:city:{city_id}")
    add_main_menu_button(builder)
    builder.adjust(2)
    return builder.as_markup()


@admin_router.callback_query(F.data.startswith("admin:districts:"))
async def admin_districts(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    city = await get_city(city_id)
    if not city:
        await callback.answer("Город не найден.", show_alert=True)
        return
    await state.update_data(city_id=city_id)
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"📍 Округа/районы города «{city[1]}»:",
        reply_markup=await districts_list_markup(city_id),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:district_add:"))
async def admin_district_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waiting_new_district_name)
    await state.update_data(city_id=city_id)
    await push_to_chat(callback.bot, callback.message.chat.id,"Пришли название нового округа/района сообщением.")
    await callback.answer()


@admin_router.message(AdminStates.waiting_new_district_name)
async def admin_district_add_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    city_id = data["city_id"]
    await add_district(city_id, message.text.strip())
    await state.clear()
    await push_to_chat(message.bot, message.chat.id,
        f"✅ Округ/район «{message.text.strip()}» добавлен.",
        reply_markup=await districts_list_markup(city_id),
    )


# ---------- Карточка одного округа/района ----------

def district_card_markup(district_id: int, city_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Переименовать", callback_data=f"admin:district_rename:{district_id}")
    builder.button(text="🗑 Удалить", callback_data=f"admin:district_delete:{district_id}")
    builder.button(text="⬅️ Назад", callback_data=f"admin:districts:{city_id}")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


@admin_router.callback_query(F.data.startswith("admin:district:"))
async def admin_district_card(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    district_id = int(callback.data.split(":")[2])
    district = await get_district(district_id)
    if not district:
        await callback.answer("Округ/район не найден.", show_alert=True)
        return
    _, city_id, name = district
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"📍 Округ/район: {name}",
        reply_markup=district_card_markup(district_id, city_id),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:district_rename:"))
async def admin_district_rename_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    district_id = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waiting_district_rename)
    await state.update_data(district_id=district_id)
    await push_to_chat(callback.bot, callback.message.chat.id,"Пришли новое название округа/района сообщением.")
    await callback.answer()


@admin_router.message(AdminStates.waiting_district_rename)
async def admin_district_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    district_id = data["district_id"]
    district = await get_district(district_id)
    city_id = district[1]
    await rename_district(district_id, message.text.strip())
    await state.clear()
    await push_to_chat(message.bot, message.chat.id,
        "✅ Округ/район переименован.",
        reply_markup=district_card_markup(district_id, city_id),
    )


@admin_router.callback_query(F.data.startswith("admin:district_delete:"))
async def admin_district_delete(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    district_id = int(callback.data.split(":")[2])
    district = await get_district(district_id)
    city_id = district[1] if district else None
    await delete_district(district_id)
    await callback.answer("Удалено.", show_alert=True)
    if city_id:
        await push_to_chat(callback.bot, callback.message.chat.id,
            "📍 Округа/районы:", reply_markup=await districts_list_markup(city_id)
        )


# ---------- Поменять товар: города -> округа/районы -> товары ----------

async def products_cities_markup():
    cities = await get_cities()
    builder = InlineKeyboardBuilder()
    for city_id, name in cities:
        builder.button(text=name, callback_data=f"admin:pcity:{city_id}")
    builder.button(text="⬅️ Назад", callback_data="admin:menu")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


async def products_districts_markup(city_id: int):
    districts = await get_districts(city_id)
    builder = InlineKeyboardBuilder()
    for district_id, name in districts:
        builder.button(text=name, callback_data=f"admin:pdistrict:{district_id}")
    builder.button(text="⬅️ К списку городов", callback_data="admin:products")
    add_main_menu_button(builder)
    builder.adjust(2)
    return builder.as_markup()


async def products_list_markup(district_id: int, city_id: int):
    products = await get_products(district_id)
    builder = InlineKeyboardBuilder()
    for product_id, name, price in products:
        builder.button(
            text=format_product_button_text(name, price),
            callback_data=f"admin:product:{product_id}",
        )
    builder.button(text="⬅️ К округам/районам", callback_data=f"admin:pcity:{city_id}")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


@admin_router.callback_query(F.data == "admin:products")
async def admin_products(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await push_to_chat(callback.bot, callback.message.chat.id,
        "🛒 Поменять товар. Выбери город:",
        reply_markup=await products_cities_markup(),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:pcity:"))
async def admin_products_city(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    city = await get_city(city_id)
    if not city:
        await callback.answer("Город не найден.", show_alert=True)
        return
    districts = await get_districts(city_id)
    if not districts:
        await callback.answer("У этого города пока нет округов/районов.", show_alert=True)
        return
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"🏙 {city[1]}. Выбери округ/район:",
        reply_markup=await products_districts_markup(city_id),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:pdistrict:"))
async def admin_products_district(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    district_id = int(callback.data.split(":")[2])
    district = await get_district(district_id)
    if not district:
        await callback.answer("Округ/район не найден.", show_alert=True)
        return
    _, city_id, district_name = district
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"📍 {district_name}. Выбери товар, который хочешь переименовать:",
        reply_markup=await products_list_markup(district_id, city_id),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:product:"))
async def admin_product_rename_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    product_id = int(callback.data.split(":")[2])
    product = await get_product(product_id)
    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_product_rename)
    await state.update_data(product_id=product_id)
    _, _, current_name, current_price = product
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"Текущее название: {current_name}\n"
        f"Текущая цена: {format_price(current_price)}\n\n"
        f"Пришли новое название и цену в формате:\n"
        f"Название | Цена\n"
        f"Например: Шишки OG Kush | 1500\n\n"
        f"Если пришлёшь текст без «|» — поменяется только название, цена останется прежней."
    )
    await callback.answer()


@admin_router.message(AdminStates.waiting_product_rename)
async def admin_product_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    product_id = data["product_id"]
    product = await get_product(product_id)
    if not product:
        await state.clear()
        await push_to_chat(message.bot, message.chat.id,"Товар не найден, возможно, он был удалён.")
        return
    _, district_id, _, current_price = product
    district = await get_district(district_id)
    city_id = district[1]

    name, price, price_provided = parse_name_and_price(message.text)
    if price_provided:
        await set_product(product_id, name=name, price=price, update_price=True)
    else:
        await set_product(product_id, name=name, update_price=False)

    await state.clear()
    result_price = price if price_provided else current_price
    await push_to_chat(message.bot, message.chat.id,
        f"✅ Товар обновлён: «{name}» — {format_price(result_price)}.",
        reply_markup=await products_list_markup(district_id, city_id),
    )


# ---------- Изменить товар сразу во всех городах и районах ----------

def bulk_slots_markup():
    builder = InlineKeyboardBuilder()
    for slot in range(1, PRODUCTS_PER_DISTRICT + 1):
        builder.button(text=f"Товар №{slot}", callback_data=f"admin:bulk_slot:{slot}")
    builder.button(text="⬅️ Назад", callback_data="admin:menu")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


@admin_router.callback_query(F.data == "admin:bulk_products")
async def admin_bulk_products(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await push_to_chat(callback.bot, callback.message.chat.id,
        "🌐 Изменить товар сразу во всех городах и районах.\n\n"
        "Выбери номер позиции товара (по порядку добавления в каждом округе/районе). "
        "Новое название и цена применятся ко всем округам/районам всех городов сразу:",
        reply_markup=bulk_slots_markup(),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:bulk_slot:"))
async def admin_bulk_slot_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    slot = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waiting_bulk_product)
    await state.update_data(slot=slot)
    await push_to_chat(callback.bot, callback.message.chat.id,
        f"Меняем товар №{slot} сразу во ВСЕХ округах/районах всех городов.\n\n"
        f"Пришли новое название и цену в формате:\n"
        f"Название | Цена\n"
        f"Например: Шишки OG Kush | 1500\n\n"
        f"Если пришлёшь текст без «|» — поменяется только название, "
        f"цена у каждого товара останется своей прежней."
    )
    await callback.answer()


@admin_router.message(AdminStates.waiting_bulk_product)
async def admin_bulk_slot_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    slot = data["slot"]

    name, price, price_provided = parse_name_and_price(message.text)
    updated = await bulk_set_products_by_slot(
        slot, name, price=price, update_price=price_provided
    )

    await state.clear()
    price_note = f" по цене {format_price(price)}" if price_provided else ""
    await push_to_chat(message.bot, message.chat.id,
        f"✅ Товар №{slot} переименован в «{name}»{price_note} "
        f"сразу в {updated} округах/районах.",
        reply_markup=admin_main_menu(),
    )


# ---------- Заказы: список + подтверждение оплаты + отправка товара ----------

def order_detail_text(order) -> str:
    (order_id, user_id, username, city_name, district_name, product_name,
     price, grams, total, status, _screenshot) = order
    grams_disp = int(grams) if grams == int(grams) else grams
    total_line = f"💵 Сумма: {format_price(total)}\n" if total is not None else ""
    return (
        f"Заказ №{order_id} — {ORDER_STATUS_LABELS.get(status, status)}\n"
        f"👤 Пользователь: {username} (id {user_id})\n"
        f"🏙 {city_name} — 📍 {district_name}\n"
        f"🛒 {product_name}\n"
        f"⚖️ Количество: {grams_disp} г\n"
        f"{total_line}"
    )


def order_actions_markup(order) -> InlineKeyboardMarkup:
    order_id, status = order[0], order[9]
    builder = InlineKeyboardBuilder()
    if status == "screenshot_sent":
        builder.button(text="✅ Подтвердить оплату", callback_data=f"admin:order_confirm:{order_id}")
        builder.button(text="❌ Отклонить", callback_data=f"admin:order_reject:{order_id}")
    elif status == "payment_confirmed":
        builder.button(text="📍 Отправить фото и координаты", callback_data=f"admin:order_send:{order_id}")
    builder.button(text="⬅️ К заказам", callback_data="admin:orders")
    add_main_menu_button(builder)
    builder.adjust(1)
    return builder.as_markup()


@admin_router.callback_query(F.data == "admin:orders")
async def admin_orders(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    orders = await get_active_orders()
    builder = InlineKeyboardBuilder()
    for order_id, username, product_name, total, status in orders:
        label = ORDER_STATUS_LABELS.get(status, status)
        total_part = f", {format_price(total)}" if total is not None else ""
        builder.button(
            text=f"№{order_id} {username} — {product_name}{total_part} [{label}]",
            callback_data=f"admin:order_view:{order_id}",
        )
    builder.button(text="⬅️ Назад", callback_data="admin:menu")
    add_main_menu_button(builder)
    builder.adjust(1)
    text = "📦 Активные заказы:" if orders else "📦 Активных заказов нет."
    await push_to_chat(callback.bot, callback.message.chat.id, text, reply_markup=builder.as_markup())
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:order_view:"))
async def admin_order_view(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    screenshot_file_id = order[10]
    text = order_detail_text(order)
    markup = order_actions_markup(order)
    old_id = LAST_BOT_MSG.get(callback.message.chat.id)
    if old_id:
        try:
            await callback.bot.delete_message(callback.message.chat.id, old_id)
        except Exception:
            pass
    if screenshot_file_id:
        new_msg = await callback.bot.send_photo(
            callback.message.chat.id, screenshot_file_id, caption=text, reply_markup=markup
        )
    else:
        new_msg = await callback.bot.send_message(callback.message.chat.id, text, reply_markup=markup)
    LAST_BOT_MSG[callback.message.chat.id] = new_msg.message_id
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:order_confirm:"))
async def admin_order_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await set_order_status(order_id, "payment_confirmed")
    order = await get_order(order_id)
    user_id = order[1]

    await callback.answer("Оплата подтверждена.")
    await push_to_chat(
        callback.bot, callback.message.chat.id,
        order_detail_text(order), reply_markup=order_actions_markup(order),
    )

    builder = InlineKeyboardBuilder()
    add_main_menu_button(builder)
    builder.adjust(1)
    try:
        await push_to_chat(
            callback.bot, user_id,
            f"✅ Оплата по заказу №{order_id} подтверждена!\n"
            f"Ожидай фото и координаты товара — их пришлёт администратор.",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        logging.exception("Не удалось уведомить пользователя %s о подтверждении оплаты", user_id)


@admin_router.callback_query(F.data.startswith("admin:order_reject:"))
async def admin_order_reject(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await set_order_status(order_id, "rejected")
    order = await get_order(order_id)
    user_id = order[1]

    await callback.answer("Заказ отклонён.")
    await push_to_chat(
        callback.bot, callback.message.chat.id,
        order_detail_text(order), reply_markup=order_actions_markup(order),
    )

    builder = InlineKeyboardBuilder()
    add_main_menu_button(builder)
    builder.adjust(1)
    try:
        await push_to_chat(
            callback.bot, user_id,
            f"❌ Оплата по заказу №{order_id} не подтверждена. Свяжитесь с администратором.",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        logging.exception("Не удалось уведомить пользователя %s об отклонении заказа", user_id)


@admin_router.callback_query(F.data.startswith("admin:order_send:"))
async def admin_order_send_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    order_id = int(callback.data.split(":")[2])
    order = await get_order(order_id)
    if not order or order[9] != "payment_confirmed":
        await callback.answer("Этот заказ сейчас нельзя отправить.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_order_delivery)
    await state.update_data(order_id=order_id)
    await push_to_chat(
        callback.bot, callback.message.chat.id,
        f"Пришли фото товара для заказа №{order_id}. В подписи к фото укажи координаты "
        f"(текст подписи целиком уйдёт покупателю вместе с фото)."
    )
    await callback.answer()


@admin_router.message(AdminStates.waiting_order_delivery, F.photo)
async def admin_order_send_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    order = await get_order(order_id) if order_id else None
    if not order:
        await state.clear()
        await push_to_chat(message.bot, message.chat.id, "Заказ не найден.", reply_markup=admin_main_menu())
        return

    user_id = order[1]
    file_id = message.photo[-1].file_id
    caption = message.caption or ""
    delivery_text = f"📍 Твой заказ №{order_id} готов к получению!\n\n{caption}".strip()

    await set_order_status(order_id, "completed")
    await state.clear()

    builder = InlineKeyboardBuilder()
    add_main_menu_button(builder)
    builder.adjust(1)
    try:
        await push_to_chat(
            message.bot, user_id, delivery_text, reply_markup=builder.as_markup(), photo=file_id
        )
        result_text = f"✅ Фото и координаты отправлены пользователю по заказу №{order_id}."
    except Exception:
        logging.exception("Не удалось отправить доставку пользователю %s", user_id)
        result_text = (
            f"⚠️ Не удалось отправить сообщение пользователю (возможно, он заблокировал бота). "
            f"Заказ №{order_id} всё равно помечен как завершённый."
        )

    await push_to_chat(message.bot, message.chat.id, result_text, reply_markup=admin_main_menu())


@admin_router.message(AdminStates.waiting_order_delivery)
async def admin_order_send_wrong_type(message: Message, state: FSMContext):
    await push_to_chat(message.bot, message.chat.id, "Пришли, пожалуйста, именно фото товара (с координатами в подписи).")


# ======================================================================
# ЗАПУСК БОТА
# ======================================================================

async def main():
    logging.basicConfig(level=logging.INFO)

    await init_db()
    await init_orders_table()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # удаляем сообщения пользователя сразу после обработки (авто-очистка чата)
    dp.message.middleware(DeleteUserMessageMiddleware())

    # порядок важен: сначала админские хендлеры, чтобы /admin и admin:* callback'и
    # обрабатывались раньше общих
    dp.include_router(admin_router)
    dp.include_router(user_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
