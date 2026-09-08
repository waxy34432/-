# database.py
# Вся работа с базой данных (SQLite). Тут же лежат стартовые данные
# (Москва, СПб, Нижний Новгород, Красноярск + их округа/районы),
# которые потом можно полностью менять через админ-панель.

import aiosqlite
from config import DB_PATH, DEFAULT_GREETING

# Стартовый набор городов и их административных округов/районов.
# Названия и количество можно потом свободно менять через админку -
# это только данные для первого запуска, когда база ещё пустая.
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

        # приветствие по умолчанию, если ещё не задано
        cur = await db.execute("SELECT value FROM settings WHERE key = 'greeting'")
        row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES ('greeting', ?)",
                (DEFAULT_GREETING,),
            )
            await db.commit()

        # стартовые города/округа, если таблица городов пустая
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
