# handlers/admin.py
# Админ-панель: изменение приветствия, городов и округов/районов прямо из Telegram,
# без правки кода. Доступна только тем, чей id указан в config.ADMIN_IDS.

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
from config import ADMIN_IDS

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


class AdminStates(StatesGroup):
    waiting_greeting = State()
    waiting_new_city_name = State()
    waiting_city_rename = State()
    waiting_new_district_name = State()
    waiting_district_rename = State()


# ---------- Главное меню админки ----------

def admin_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить приветствие", callback_data="admin:greeting")
    builder.button(text="🏙 Управление городами", callback_data="admin:cities")
    builder.adjust(1)
    return builder.as_markup()


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return  # обычные пользователи не видят и не знают об этой команде
    await state.clear()
    await message.answer("⚙️ Админ-панель", reply_markup=admin_main_menu())


@router.callback_query(F.data == "admin:menu")
async def admin_menu_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("⚙️ Админ-панель", reply_markup=admin_main_menu())
    await callback.answer()


# ---------- Приветствие ----------

@router.callback_query(F.data == "admin:greeting")
async def admin_greeting(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    current = await db.get_greeting()
    await state.set_state(AdminStates.waiting_greeting)
    await callback.message.edit_text(
        f"Текущий текст приветствия:\n\n{current}\n\n"
        f"Пришли новый текст сообщением (можно с эмодзи и переносами строк)."
    )
    await callback.answer()


@router.message(AdminStates.waiting_greeting)
async def save_greeting(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await db.set_greeting(message.html_text or message.text)
    await state.clear()
    await message.answer("✅ Приветствие обновлено.", reply_markup=admin_main_menu())


# ---------- Города: список ----------

async def cities_list_markup():
    cities = await db.get_cities()
    builder = InlineKeyboardBuilder()
    for city_id, name in cities:
        builder.button(text=name, callback_data=f"admin:city:{city_id}")
    builder.button(text="➕ Добавить город", callback_data="admin:city_add")
    builder.button(text="⬅️ Назад", callback_data="admin:menu")
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data == "admin:cities")
async def admin_cities(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text(
        "🏙 Города. Выбери город, чтобы его отредактировать, "
        "или добавь новый.",
        reply_markup=await cities_list_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:city_add")
async def admin_city_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.waiting_new_city_name)
    await callback.message.edit_text("Пришли название нового города сообщением.")
    await callback.answer()


@router.message(AdminStates.waiting_new_city_name)
async def save_new_city(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await db.add_city(message.text.strip())
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


@router.callback_query(F.data.startswith("admin:city:"))
async def admin_city_card(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    city = await db.get_city(city_id)
    if not city:
        await callback.answer("Город не найден.", show_alert=True)
        return
    districts = await db.get_districts(city_id)
    await callback.message.edit_text(
        f"🏙 Город: {city[1]}\nОкругов/районов: {len(districts)}",
        reply_markup=city_card_markup(city_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:city_rename:"))
async def admin_city_rename_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waiting_city_rename)
    await state.update_data(city_id=city_id)
    await callback.message.edit_text("Пришли новое название города сообщением.")
    await callback.answer()


@router.message(AdminStates.waiting_city_rename)
async def admin_city_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    city_id = data["city_id"]
    await db.rename_city(city_id, message.text.strip())
    await state.clear()
    await message.answer("✅ Город переименован.", reply_markup=city_card_markup(city_id))


@router.callback_query(F.data.startswith("admin:city_delete:"))
async def admin_city_delete(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    await db.delete_city(city_id)
    await callback.answer("Город и все его округа/районы удалены.", show_alert=True)
    await callback.message.edit_text(
        "🏙 Города:", reply_markup=await cities_list_markup()
    )


# ---------- Округа/районы конкретного города ----------

async def districts_list_markup(city_id: int):
    districts = await db.get_districts(city_id)
    builder = InlineKeyboardBuilder()
    for district_id, name in districts:
        builder.button(text=name, callback_data=f"admin:district:{district_id}")
    builder.button(text="➕ Добавить округ/район", callback_data=f"admin:district_add:{city_id}")
    builder.button(text="⬅️ К городу", callback_data=f"admin:city:{city_id}")
    builder.adjust(2)
    return builder.as_markup()


@router.callback_query(F.data.startswith("admin:districts:"))
async def admin_districts(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    city = await db.get_city(city_id)
    if not city:
        await callback.answer("Город не найден.", show_alert=True)
        return
    await state.update_data(city_id=city_id)
    await callback.message.edit_text(
        f"📍 Округа/районы города «{city[1]}»:",
        reply_markup=await districts_list_markup(city_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:district_add:"))
async def admin_district_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    city_id = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waiting_new_district_name)
    await state.update_data(city_id=city_id)
    await callback.message.edit_text("Пришли название нового округа/района сообщением.")
    await callback.answer()


@router.message(AdminStates.waiting_new_district_name)
async def admin_district_add_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    city_id = data["city_id"]
    await db.add_district(city_id, message.text.strip())
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


@router.callback_query(F.data.startswith("admin:district:"))
async def admin_district_card(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    district_id = int(callback.data.split(":")[2])
    district = await db.get_district(district_id)
    if not district:
        await callback.answer("Округ/район не найден.", show_alert=True)
        return
    _, city_id, name = district
    await callback.message.edit_text(
        f"📍 Округ/район: {name}",
        reply_markup=district_card_markup(district_id, city_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:district_rename:"))
async def admin_district_rename_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    district_id = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waiting_district_rename)
    await state.update_data(district_id=district_id)
    await callback.message.edit_text("Пришли новое название округа/района сообщением.")
    await callback.answer()


@router.message(AdminStates.waiting_district_rename)
async def admin_district_rename_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    district_id = data["district_id"]
    district = await db.get_district(district_id)
    city_id = district[1]
    await db.rename_district(district_id, message.text.strip())
    await state.clear()
    await message.answer(
        "✅ Округ/район переименован.",
        reply_markup=district_card_markup(district_id, city_id),
    )


@router.callback_query(F.data.startswith("admin:district_delete:"))
async def admin_district_delete(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    district_id = int(callback.data.split(":")[2])
    district = await db.get_district(district_id)
    city_id = district[1] if district else None
    await db.delete_district(district_id)
    await callback.answer("Удалено.", show_alert=True)
    if city_id:
        await callback.message.edit_text(
            "📍 Округа/районы:", reply_markup=await districts_list_markup(city_id)
        )
