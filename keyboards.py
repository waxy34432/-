# keyboards.py
# Клавиатуры, которые видит обычный пользователь

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def start_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура под приветственным сообщением - одна кнопка 'Выбрать город'."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🏙 Выбрать город", callback_data="choose_city")
    return builder.as_markup()


def cities_keyboard(cities) -> InlineKeyboardMarkup:
    """cities: список кортежей (id, name) из базы данных."""
    builder = InlineKeyboardBuilder()
    for city_id, name in cities:
        builder.button(text=name, callback_data=f"city:{city_id}")
    builder.button(text="⬅️ Назад", callback_data="back_to_start")
    builder.adjust(1)
    return builder.as_markup()


def districts_keyboard(districts, city_id: int) -> InlineKeyboardMarkup:
    """districts: список кортежей (id, name) из базы данных."""
    builder = InlineKeyboardBuilder()
    for district_id, name in districts:
        builder.button(text=name, callback_data=f"district:{district_id}")
    builder.button(text="⬅️ К списку городов", callback_data="choose_city")
    builder.adjust(2)
    return builder.as_markup()
