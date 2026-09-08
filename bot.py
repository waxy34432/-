# bot.py
# Всё в одном файле: конфиг, база данных, клавиатуры, обработчики пользователя и админки.
# Запуск: python bot.py

import asyncio
import logging

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ======================================================================
# КОНФИГ — правь здесь
# ======================================================================

BOT_TOKEN = "8658784166:AAH43OXK3nxKacZe_W5QJr0QeTSspgljb4Q"

# Telegram ID пользователей с доступом к админ-панели.
# Узнать свой ID можно у бота @userinfobot
ADMIN_IDS = [
   8452100409,6328307164  # <-- замени на свой ID (можно перечислить несколько через запятую)
]

# Текст приветствия по умолчанию (используется только при первом запуске,
# пока база данных ещё пустая). Дальше меняется через /admin.
DEFAULT_GREETING = (
    "Привет! 👋\n\n"
    "Это бот для выбора города и района.\n"
    "Нажми на кнопку ниже, чтобы начать."
)

DB_PATH = "bot.db"


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
        await db.commit()

        cur = await db.execute("SELECT value FROM settings WHERE key = 'greeting'")
        row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES ('greeting', ?)",
                (DEFAULT_GREETING,),
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
                    await db.execute(
                        "INSERT INTO districts (city_id, name) VALUES (?, ?)",
                        (city_id, d),
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
        await db.commit()
        return cur.lastrowid


async def rename_district(district_id: int, new_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE districts SET name = ? WHERE id = ?", (new_name, district_id)
        )
        await db.commit()


async def delete_district(district_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM districts WHERE id = ?", (district_id,))
        await db.commit()


# ======================================================================
# КЛАВИАТУРЫ ДЛЯ ОБЫЧНОГО ПОЛЬЗОВАТЕЛЯ
# ======================================================================

def start_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏙 Выбрать город", callback_data="choose_city")
    return builder.as_markup()


def cities_keyboard(cities) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for city_id, name in cities:
        builder.button(text=name, callback_data=f"city:{city_id}")
    builder.button(text="⬅️ Назад", callback_data="back_to_start")
    builder.adjust(1)
    return builder.as_markup()


def districts_keyboard(districts, city_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for district_id, name in districts:
        builder.button(text=name, callback_data=f"district:{district_id}")
    builder.button(text="⬅️ К списку городов", callback_data="choose_city")
    builder.adjust(2)
    return builder.as_markup()


# ======================================================================
# ОБРАБОТЧИКИ ДЛЯ ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ
# ======================================================================

user_router = Router()


@user_router.message(CommandStart())
async def cmd_start(message: Message):
    greeting = await get_greeting()
    await message.answer(greeting, reply_markup=start_keyboard())


@user_router.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery):
    greeting = await get_greeting()
    await callback.message.edit_text(greeting, reply_markup=start_keyboard())
    await callback.answer()


@user_router.callback_query(F.data == "choose_city")
async def choose_city(callback: CallbackQuery):
    cities = await get_cities()
    if not cities:
        await callback.answer("Список городов пока пуст.", show_alert=True)
        return
    await callback.message.edit_text(
        "Выбери город:", reply_markup=cities_keyboard(cities)
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("city:"))
async def choose_district(callback: CallbackQuery):
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

    await callback.message.edit_text(
        f"Город: {city[1]}\nВыбери округ/район:",
        reply_markup=districts_keyboard(districts, city_id),
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("district:"))
async def district_chosen(callback: CallbackQuery):
    district_id = int(callback.data.split(":")[1])
    district = await get_district(district_id)
    if not district:
        await callback.answer("Этот округ/район больше не существует.", show_alert=True)
        return

    _, city_id, district_name = district
    city = await get_city(city_id)
    city_name = city[1] if city else "?"

    await callback.message.edit_text(
        f"Вы выбрали:\n🏙 Город: {city_name}\n📍 Округ/район: {district_name}\n\n"
        f"Отправьте /start, чтобы начать заново.",
    )
    await callback.answer()


# ======================================================================
# АДМИН-ПАНЕЛЬ
# ======================================================================

admin_router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


class AdminStates(StatesGroup):
    waiting_greeting = State()
    waiting_new_city_name = State()
    waiting_city_rename = State()
    waiting_new_district_name = State()
    waiting_district_rename = State()


def admin_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить приветствие", callback_data="admin:greeting")
    builder.button(text="🏙 Управление городами", callback_data="admin:cities")
    builder.adjust(1)
    return builder.as_markup()


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return  # обычные пользователи не видят и не знают об этой команде
    await state.clear()
    await message.answer("⚙️ Админ-панель", reply_markup=admin_main_menu())


@admin_router.callback_query(F.data == "admin:menu")
async def admin_menu_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("⚙️ Админ-панель", reply_markup=admin_main_menu())
    await callback.answer()


# ---------- Приветствие ----------

@admin_router.callback_query(F.data == "admin:greeting")
async def admin_greeting(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    current = await get_greeting()
    await state.set_state(AdminStates.waiting_greeting)
    await callback.message.edit_text(
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
    await message.answer("✅ Приветствие обновлено.", reply_markup=admin_main_menu())


# ---------- Города: список ----------

async def cities_list_markup():
    cities = await get_cities()
    builder = InlineKeyboardBuilder()
    for city_id, name in cities:
        builder.button(text=name, callback_data=f"admin:city:{city_id}")
    builder.button(text="➕ Добавить город", callback_data="admin:city_add")
    builder.button(text="⬅️ Назад", callback_data="admin:menu")
    builder.adjust(1)
    return builder.as_markup()


@admin_router.callback_query(F.data == "admin:cities")
async def admin_cities(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text(
        "🏙 Города. Выбери город, чтобы его отредактировать, или добавь новый.",
        reply_markup=await cities_list_markup(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:city_add")
async def admin_city_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_new_city_name)
    await callback.message.edit_text("Пришли название нового города сообщением.")
    await callback.answer()


@admin_router.message(AdminStates.waiting_new_city_name)
async def save_new_city(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await add_city(message.text.strip())
    await state.clear()
    await message.answer(
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
    await callback.message.edit_text(
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
    await callback.message.edit_text("Пришли новое название города сообщением.")
    await callback.answer()


@admin_router.message(AdminStates.waiting_city_rename)
async def admin_city_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    city_id = data["city_id"]
    await rename_city(city_id, message.text.strip())
    await state.clear()
    await message.answer("✅ Город переименован.", reply_markup=city_card_markup(city_id))


@admin_router.callback_query(F.data.startswith("admin:city_delete:"))
async def admin_city_delete(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    await delete_city(city_id)
    await callback.answer("Город и все его округа/районы удалены.", show_alert=True)
    await callback.message.edit_text(
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
    await callback.message.edit_text(
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
    await callback.message.edit_text("Пришли название нового округа/района сообщением.")
    await callback.answer()


@admin_router.message(AdminStates.waiting_new_district_name)
async def admin_district_add_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    city_id = data["city_id"]
    await add_district(city_id, message.text.strip())
    await state.clear()
    await message.answer(
        f"✅ Округ/район «{message.text.strip()}» добавлен.",
        reply_markup=await districts_list_markup(city_id),
    )


# ---------- Карточка одного округа/района ----------

def district_card_markup(district_id: int, city_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Переименовать", callback_data=f"admin:district_rename:{district_id}")
    builder.button(text="🗑 Удалить", callback_data=f"admin:district_delete:{district_id}")
    builder.button(text="⬅️ Назад", callback_data=f"admin:districts:{city_id}")
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
    await callback.message.edit_text(
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
    await callback.message.edit_text("Пришли новое название округа/района сообщением.")
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
    await message.answer(
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
        await callback.message.edit_text(
            "📍 Округа/районы:", reply_markup=await districts_list_markup(city_id)
        )


# ======================================================================
# ЗАПУСК БОТА
# ======================================================================

async def main():
    logging.basicConfig(level=logging.INFO)

    if BOT_TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН_ОТ_BOTFATHER":
        raise SystemExit("Сначала укажи свой токен бота в переменной BOT_TOKEN вверху файла.")

    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # порядок важен: сначала админские хендлеры, чтобы /admin и admin:* callback'и
    # обрабатывались раньше общих
    dp.include_router(admin_router)
    dp.include_router(user_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
