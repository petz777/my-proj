import asyncio
import logging
import os
import re
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
STAFF_CHAT_ID = os.getenv("STAFF_CHAT_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Put it into .env")
if not STAFF_CHAT_ID:
    raise RuntimeError("STAFF_CHAT_ID is empty. Put it into .env")

try:
    STAFF_CHAT_ID_INT = int(STAFF_CHAT_ID)
except ValueError:
    raise RuntimeError("STAFF_CHAT_ID must be integer (chat id)")

DB_PATH = "bot.db"

# ====== Меню (быстро правится) ======
MENU = {
    "Классика": [
        {"name": "Американо", "sizes": [250, 350, 450]},
        {"name": "Капучино", "sizes": [250, 350, 450]},
        {"name": "Латте", "sizes": [250, 350, 450]},
        {"name": "Флэт уайт", "sizes": [250, 350]},
    ],
    "Раф": [
        {"name": "Раф классический", "sizes": [250, 350, 450]},
        {"name": "Раф ванильный", "sizes": [250, 350, 450]},
    ],
    "Чай": [
        {"name": "Чёрный чай", "sizes": [350, 450]},
        {"name": "Зелёный чай", "sizes": [350, 450]},
        {"name": "Травяной чай", "sizes": [350, 450]},
    ],
    "Какао": [
        {"name": "Какао классическое", "sizes": [250, 350, 450]},
    ],
}

# ====== Молоко / Добавки ======
MILK_OPTIONS = [
    "Коровье",
    "Кокосовое",
    "Миндальное",
    "Фундучное",
    "Банановое",
    "Безлактозное",
    "Овсяное",
]

ADDON_OPTIONS = [
    "Сироп",
    "Маршмеллоу",
]

MILK_DRINKS = {
    # классика
    "Капучино",
    "Латте",
    "Флэт уайт",
    # рафы
    "Раф классический",
    "Раф ванильный",
    # какао
    "Какао классическое",
}


# ====== FSM ======
class OrderFlow(StatesGroup):
    choosing_category = State()
    choosing_drink = State()
    choosing_size = State()
    choosing_milk = State()
    choosing_addons = State()
    choosing_time = State()
    typing_time = State()
    confirm = State()


# ====== Keyboards ======
def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="☕ Начать заказ")],
            [KeyboardButton(text="ℹ️ Как это работает")],
        ],
        resize_keyboard=True,
        selective=True,
    )


def kb_categories() -> ReplyKeyboardMarkup:
    rows = []
    cats = list(MENU.keys())
    # по 2 в ряд
    for i in range(0, len(cats), 2):
        row = [KeyboardButton(text=cats[i])]
        if i + 1 < len(cats):
            row.append(KeyboardButton(text=cats[i + 1]))
        rows.append(row)
    rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, selective=True)


def kb_back() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Назад")]],
        resize_keyboard=True,
        selective=True,
    )


def ikb_drinks(category: str) -> InlineKeyboardMarkup:
    buttons = []
    for item in MENU.get(category, []):
        buttons.append(
            [InlineKeyboardButton(text=item["name"], callback_data=f"drink:{item['name']}")]
        )
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back_to_categories")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ikb_sizes(sizes: list[int]) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text=f"{s} мл", callback_data=f"size:{s}") for s in sizes]
    buttons = [row]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back_to_drinks")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ikb_milk(selected: str | None) -> InlineKeyboardMarkup:
    buttons = []
    for m in MILK_OPTIONS:
        label = f"✅ {m}" if selected == m else m
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"milk:{m}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back_to_sizes")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ikb_addons(selected: list[str]) -> InlineKeyboardMarkup:
    selected = selected or []
    buttons = []
    for a in ADDON_OPTIONS:
        label = f"✅ {a}" if a in selected else a
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"addon:toggle:{a}")])

    buttons.append([InlineKeyboardButton(text="➡️ Далее", callback_data="addon:done")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back_to_milk_or_sizes")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ikb_time_choices() -> InlineKeyboardMarkup:
    # Убрали +10/+20/+30
    buttons = [
        [InlineKeyboardButton(text="Как можно быстрее", callback_data="time:asap")],
        [InlineKeyboardButton(text="Ввести время (HH:MM)", callback_data="time:manual")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back_to_addons")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ikb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm:yes"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="confirm:no"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back_to_time")],
        ]
    )


# ====== DB ======
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    drink TEXT NOT NULL,
    size_ml INTEGER NOT NULL,
    milk TEXT,
    addons TEXT,
    pickup_time TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
"""


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, coltype: str):
    """Добавляет колонку, если её нет (мягкая миграция)."""
    async with db.execute(f"PRAGMA table_info({table});") as cur:
        cols = [row[1] async for row in cur]  # row[1] = name
    if column not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        # на случай если база была создана старой версией
        await _ensure_column(db, "orders", "milk", "TEXT")
        await _ensure_column(db, "orders", "addons", "TEXT")
        await db.commit()


async def upsert_user(m: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users(user_id, username, first_name, last_name, created_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name
            """,
            (
                m.from_user.id,
                m.from_user.username,
                m.from_user.first_name,
                m.from_user.last_name,
                datetime.utcnow().isoformat(),
            ),
        )
        await db.commit()


async def save_order(
    user_id: int,
    category: str,
    drink: str,
    size_ml: int,
    pickup_time: str,
    milk: str | None,
    addons: list[str] | None,
) -> int:
    addons_text = ", ".join(addons) if addons else None
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO orders(user_id, category, drink, size_ml, milk, addons, pickup_time, status, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, 'new', ?)
            """,
            (user_id, category, drink, size_ml, milk, addons_text, pickup_time, datetime.utcnow().isoformat()),
        )
        await db.commit()
        return cur.lastrowid


# ====== Helpers ======
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def is_milk_drink(drink: str) -> bool:
    return drink in MILK_DRINKS


def format_order_preview(data: dict) -> str:
    milk = data.get("milk")
    addons = data.get("addons") or []

    lines = [
        "🧾 <b>Проверь заказ</b>",
        "",
        f"Категория: <b>{data['category']}</b>",
        f"Напиток: <b>{data['drink']}</b>",
        f"Объём: <b>{data['size_ml']} мл</b>",
    ]

    if milk:
        lines.append(f"Молоко: <b>{milk}</b>")

    if addons:
        lines.append(f"Добавки: <b>{', '.join(addons)}</b>")

    lines += [
        f"Время: <b>{data['pickup_time']}</b>",
        "",
        "Оплата на месте (картой/наличными).",
    ]
    return "\n".join(lines)


# ====== Router/Handlers ======
router = Router()


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await upsert_user(m)
    await state.clear()
    await m.answer(
        "Привет! ☕\n"
        "Здесь можно быстро оформить предзаказ и забрать без очереди.",
        reply_markup=kb_main(),
    )


@router.message(F.text == "ℹ️ Как это работает")
async def how_it_works(m: Message):
    await m.answer(
        "1) Нажимаешь «Начать заказ»\n"
        "2) Выбираешь напиток, объём и доп. опции\n"
        "3) Выбираешь время\n"
        "4) Забираешь готовый напиток ❤️",
        reply_markup=kb_main(),
    )


@router.message(F.text == "☕ Начать заказ")
async def begin_order(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderFlow.choosing_category)
    await m.answer("Выбери категорию:", reply_markup=kb_categories())


# --- Категории ---
@router.message(OrderFlow.choosing_category, F.text == "⬅️ Назад")
async def back_to_main(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Ок, вернулись в меню.", reply_markup=kb_main())


@router.message(OrderFlow.choosing_category)
async def choose_category(m: Message, state: FSMContext):
    cat = (m.text or "").strip()
    if cat not in MENU:
        await m.answer("Не понял категорию 😅 Выбери кнопкой ниже.", reply_markup=kb_categories())
        return
    await state.update_data(category=cat)
    await state.set_state(OrderFlow.choosing_drink)
    await m.answer(
        f"Категория: <b>{cat}</b>\nТеперь выбери напиток:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_back(),
    )
    await m.answer("Напитки:", reply_markup=ikb_drinks(cat))


# --- Напитки ---
@router.message(OrderFlow.choosing_drink, F.text == "⬅️ Назад")
async def drink_back(m: Message, state: FSMContext):
    await state.set_state(OrderFlow.choosing_category)
    await m.answer("Выбери категорию:", reply_markup=kb_categories())


@router.callback_query(F.data == "nav:back_to_categories")
async def cb_back_categories(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await state.set_state(OrderFlow.choosing_category)
    await cq.message.answer("Выбери категорию:", reply_markup=kb_categories())


@router.callback_query(OrderFlow.choosing_drink, F.data.startswith("drink:"))
async def cb_choose_drink(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    drink = cq.data.split("drink:", 1)[1]
    data = await state.get_data()
    cat = data.get("category")

    sizes = []
    for item in MENU.get(cat, []):
        if item["name"] == drink:
            sizes = item["sizes"]
            break

    if not sizes:
        await cq.message.answer("Что-то пошло не так: не нашёл размеры. Попробуй ещё раз.")
        return

    # сбрасываем опции ниже по цепочке
    await state.update_data(drink=drink, sizes=sizes, size_ml=None, milk=None, addons=[])
    await state.set_state(OrderFlow.choosing_size)

    await cq.message.answer(
        f"Ок, <b>{drink}</b>. Теперь выбери объём:",
        parse_mode=ParseMode.HTML,
        reply_markup=ikb_sizes(sizes),
    )


@router.callback_query(F.data == "nav:back_to_drinks")
async def cb_back_to_drinks(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    data = await state.get_data()
    cat = data.get("category")
    await state.set_state(OrderFlow.choosing_drink)
    await cq.message.answer("Выбери напиток:", reply_markup=ikb_drinks(cat))


# --- Объём ---
@router.callback_query(OrderFlow.choosing_size, F.data.startswith("size:"))
async def cb_choose_size(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    size_ml = int(cq.data.split("size:", 1)[1])
    data = await state.get_data()
    drink = data.get("drink", "")

    await state.update_data(size_ml=size_ml)

    # Если молочный — выбираем молоко
    if is_milk_drink(drink):
        current_milk = (await state.get_data()).get("milk") or "Коровье"
        await state.update_data(milk=current_milk)
        await state.set_state(OrderFlow.choosing_milk)
        await cq.message.answer(
            f"Объём: <b>{size_ml} мл</b>\nВыбери молоко:",
            parse_mode=ParseMode.HTML,
            reply_markup=ikb_milk(current_milk),
        )
        return

    # Если не молочный — сразу в добавки
    await state.update_data(milk=None, addons=[])
    await state.set_state(OrderFlow.choosing_addons)
    await cq.message.answer(
        f"Объём: <b>{size_ml} мл</b>\nДобавки (если нужно):",
        parse_mode=ParseMode.HTML,
        reply_markup=ikb_addons([]),
    )


# --- Молоко ---
@router.callback_query(OrderFlow.choosing_milk, F.data.startswith("milk:"))
async def cb_choose_milk(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    milk = cq.data.split("milk:", 1)[1]
    await state.update_data(milk=milk)

    # после молока -> добавки
    data = await state.get_data()
    addons = data.get("addons") or []
    await state.set_state(OrderFlow.choosing_addons)
    await cq.message.answer(
        f"Молоко: <b>{milk}</b>\nДобавки (если нужно):",
        parse_mode=ParseMode.HTML,
        reply_markup=ikb_addons(addons),
    )


@router.callback_query(F.data == "nav:back_to_milk_or_sizes")
async def cb_back_to_milk_or_sizes(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    data = await state.get_data()
    drink = data.get("drink", "")
    if is_milk_drink(drink):
        await state.set_state(OrderFlow.choosing_milk)
        current_milk = data.get("milk") or "Коровье"
        await cq.message.answer("Выбери молоко:", reply_markup=ikb_milk(current_milk))
    else:
        await state.set_state(OrderFlow.choosing_size)
        sizes = data.get("sizes", [250, 350, 450])
        await cq.message.answer("Выбери объём:", reply_markup=ikb_sizes(sizes))


# --- Добавки (мультивыбор) ---
@router.callback_query(OrderFlow.choosing_addons, F.data.startswith("addon:toggle:"))
async def cb_toggle_addon(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    addon = cq.data.split("addon:toggle:", 1)[1]
    data = await state.get_data()
    addons = list(data.get("addons") or [])
    if addon in addons:
        addons.remove(addon)
    else:
        addons.append(addon)
    await state.update_data(addons=addons)
    await cq.message.edit_reply_markup(reply_markup=ikb_addons(addons))


@router.callback_query(OrderFlow.choosing_addons, F.data == "addon:done")
async def cb_addons_done(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await state.set_state(OrderFlow.choosing_time)
    await cq.message.answer("Теперь выбери время:", reply_markup=ikb_time_choices())


@router.callback_query(F.data == "nav:back_to_sizes")
async def cb_back_to_sizes(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    data = await state.get_data()
    sizes = data.get("sizes", [250, 350, 450])
    await state.set_state(OrderFlow.choosing_size)
    await cq.message.answer("Выбери объём:", reply_markup=ikb_sizes(sizes))


@router.callback_query(F.data == "nav:back_to_addons")
async def cb_back_to_addons(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    data = await state.get_data()
    addons = data.get("addons") or []
    await state.set_state(OrderFlow.choosing_addons)
    await cq.message.answer("Добавки (если нужно):", reply_markup=ikb_addons(addons))


# --- Время ---
@router.callback_query(OrderFlow.choosing_time, F.data.startswith("time:"))
async def cb_choose_time(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    choice = cq.data.split("time:", 1)[1]

    if choice == "asap":
        pickup_time = "как можно быстрее"
        await state.update_data(pickup_time=pickup_time)
        await state.set_state(OrderFlow.confirm)
        data = await state.get_data()
        await cq.message.answer(format_order_preview(data), parse_mode=ParseMode.HTML, reply_markup=ikb_confirm())
        return

    if choice == "manual":
        await state.set_state(OrderFlow.typing_time)
        await cq.message.answer(
            "Введи время в формате <b>HH:MM</b> (например 14:20).",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back(),
        )
        return


@router.message(OrderFlow.typing_time, F.text == "⬅️ Назад")
async def back_from_typing_time(m: Message, state: FSMContext):
    await state.set_state(OrderFlow.choosing_time)
    await m.answer("Ок, выбери время:", reply_markup=ikb_time_choices())


@router.message(OrderFlow.typing_time)
async def typed_time(m: Message, state: FSMContext):
    t = (m.text or "").strip()
    if not TIME_RE.match(t):
        await m.answer("Не похоже на HH:MM 😅 Пример: 09:40 или 18:15")
        return
    await state.update_data(pickup_time=t)
    await state.set_state(OrderFlow.confirm)
    data = await state.get_data()
    await m.answer(format_order_preview(data), parse_mode=ParseMode.HTML, reply_markup=ikb_confirm())


@router.callback_query(F.data == "nav:back_to_time")
async def cb_back_to_time(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await state.set_state(OrderFlow.choosing_time)
    await cq.message.answer("Выбери время:", reply_markup=ikb_time_choices())


# --- Подтверждение ---
@router.callback_query(OrderFlow.confirm, F.data.startswith("confirm:"))
async def cb_confirm(cq: CallbackQuery, state: FSMContext, bot: Bot):
    await cq.answer()
    action = cq.data.split("confirm:", 1)[1]

    if action == "no":
        await state.clear()
        await cq.message.answer("Ок, отменил. Если что — начни заново 🙂", reply_markup=kb_main())
        return

    data = await state.get_data()
    order_id = await save_order(
        user_id=cq.from_user.id,
        category=data["category"],
        drink=data["drink"],
        size_ml=int(data["size_ml"]),
        pickup_time=data["pickup_time"],
        milk=data.get("milk"),
        addons=data.get("addons") or [],
    )

    user_display = cq.from_user.full_name
    if cq.from_user.username:
        user_display += f" (@{cq.from_user.username})"

    milk = data.get("milk")
    addons = data.get("addons") or []

    staff_lines = [
        "🆕 <b>Новый предзаказ</b>",
        "",
        f"№ <b>{order_id}</b>",
        f"Клиент: <b>{user_display}</b>",
        f"Категория: <b>{data['category']}</b>",
        f"Напиток: <b>{data['drink']}</b>",
        f"Объём: <b>{data['size_ml']} мл</b>",
    ]
    if milk:
        staff_lines.append(f"Молоко: <b>{milk}</b>")
    if addons:
        staff_lines.append(f"Добавки: <b>{', '.join(addons)}</b>")
    staff_lines.append(f"Время: <b>{data['pickup_time']}</b>")

    await bot.send_message(STAFF_CHAT_ID_INT, "\n".join(staff_lines), parse_mode=ParseMode.HTML)

    await state.clear()
    await cq.message.answer(
        f"✅ Принято! Заказ № <b>{order_id}</b>\n"
        f"Время: <b>{data['pickup_time']}</b>\n\n"
        "Оплата на месте 🙂",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main(),
    )


# ====== Main ======
async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
