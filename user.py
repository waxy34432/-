# handlers/user.py
# Обработчики для обычных пользователей: приветствие, выбор города, выбор округа/района

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart

import database as db
import keyboards as kb

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    greeting = await db.get_greeting()
    await message.answer(greeting, reply_markup=kb.start_keyboard())


@router.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery):
    greeting = await db.get_greeting()
    await callback.message.edit_text(greeting, reply_markup=kb.start_keyboard())
    await callback.answer()


@router.callback_query(F.data == "choose_city")
async def choose_city(callback: CallbackQuery):
    cities = await db.get_cities()
    if not cities:
        await callback.answer("Список городов пока пуст.", show_alert=True)
        return
    await callback.message.edit_text(
        "Выбери город:", reply_markup=kb.cities_keyboard(cities)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("city:"))
async def choose_district(callback: CallbackQuery):
    city_id = int(callback.data.split(":")[1])
    city = await db.get_city(city_id)
    if not city:
        await callback.answer("Этот город больше не существует.", show_alert=True)
        return

    districts = await db.get_districts(city_id)
    if not districts:
        await callback.answer(
            "Для этого города пока не добавлены округа/районы.", show_alert=True
        )
        return

    await callback.message.edit_text(
        f"Город: {city[1]}\nВыбери округ/район:",
        reply_markup=kb.districts_keyboard(districts, city_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("district:"))
async def district_chosen(callback: CallbackQuery):
    district_id = int(callback.data.split(":")[1])
    district = await db.get_district(district_id)
    if not district:
        await callback.answer("Этот округ/район больше не существует.", show_alert=True)
        return

    _, city_id, district_name = district
    city = await db.get_city(city_id)
    city_name = city[1] if city else "?"

    await callback.message.edit_text(
        f"Вы выбрали:\n🏙 Город: {city_name}\n📍 Округ/район: {district_name}\n\n"
        f"Отправьте /start, чтобы начать заново.",
    )
    await callback.answer()
