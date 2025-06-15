import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite
from datetime import datetime, timedelta
import aiohttp
import json
from aiohttp import web
import hmac
import hashlib
import os

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
BOT_TOKEN = "7779993891:AAFJXC53dzBKK8cHifY62sjPLMVUKNyAlJI"
CRYPTO_BOT_TOKEN = "414765:AAaCmjMlug2sRKqWCUcxCKVp4DaZwPWTOPI"
WEBHOOK_URL = "https://cryptobot-webhook.onrender.com/webhook"  # Замените на ваш URL из Render
WEBHOOK_SECRET = "cryptobot-secret-key-2024"  # Секретный ключ для вебхука

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Admin ID
ADMIN_ID = 7642453177

# States for admin and payment
class AdminStates(StatesGroup):
    waiting_for_product_name = State()
    waiting_for_product_price = State()
    waiting_for_product_quantity = State()

class PaymentStates(StatesGroup):
    waiting_for_amount = State()

# Get USDT price in RUB
async def get_usdt_price():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.binance.com/api/v3/ticker/price?symbol=USDTRUB') as response:
                if response.status == 200:
                    data = await response.json()
                    return float(data['price'])
                else:
                    raise Exception("Failed to get USDT price")
    except Exception as e:
        logging.error(f"Error getting USDT price: {e}")
        return 100  # Fallback price if API fails

# Convert RUB to USDT
async def convert_rub_to_usdt(rub_amount: float) -> float:
    usdt_price = await get_usdt_price()
    return round(rub_amount / usdt_price, 2)

# Verify webhook signature
def verify_webhook_signature(data: str, signature: str) -> bool:
    expected_signature = hmac.new(
        WEBHOOK_SECRET.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected_signature)

# Webhook handler
async def webhook_handler(request: web.Request):
    try:
        # Get signature from headers
        signature = request.headers.get('X-Crypto-Pay-Signature')
        if not signature:
            return web.Response(status=400, text='No signature provided')

        # Read request body
        data = await request.text()
        
        # Verify signature
        if not verify_webhook_signature(data, signature):
            return web.Response(status=400, text='Invalid signature')

        # Parse webhook data
        webhook_data = json.loads(data)
        
        # Process payment
        if webhook_data.get('type') == 'invoice_paid':
            invoice_id = webhook_data['payload']['invoice_id']
            
            # Update payment status in database
            async with aiosqlite.connect('shop.db') as db:
                # Get payment info
                async with db.execute(
                    'SELECT user_id, amount FROM payments WHERE invoice_id = ?',
                    (invoice_id,)
                ) as cursor:
                    payment = await cursor.fetchone()
                
                if payment:
                    user_id, amount = payment
                    
                    # Update payment status
                    await db.execute(
                        'UPDATE payments SET status = "completed" WHERE invoice_id = ?',
                        (invoice_id,)
                    )
                    
                    # Update user balance
                    await db.execute(
                        'UPDATE users SET balance = balance + ? WHERE user_id = ?',
                        (amount, user_id)
                    )
                    
                    await db.commit()
                    
                    # Notify user
                    await bot.send_message(
                        user_id,
                        f"*✅ Платеж успешно завершен!*\n\n"
                        f"Сумма: `{amount}₽`\n"
                        f"Статус: `Оплачено`\n\n"
                        f"_Средства зачислены на ваш баланс_",
                        parse_mode="Markdown"
                    )
        
        return web.Response(status=200, text='OK')
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return web.Response(status=500, text='Internal server error')

# Setup webhook
async def setup_webhook():
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    
    # Start webhook server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    
    # Register webhook with CryptoBot
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://pay.crypt.bot/api/setWebhook",
            headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
            json={
                "url": WEBHOOK_URL,
                "secret": WEBHOOK_SECRET
            }
        ) as response:
            if response.status == 200:
                data = await response.json()
                if data["ok"]:
                    logging.info("Webhook successfully registered")
                else:
                    logging.error(f"Failed to register webhook: {data.get('error')}")
            else:
                logging.error(f"Failed to register webhook: {response.status}")

# Database initialization
async def init_db():
    async with aiosqlite.connect('shop.db') as db:
        # Create products table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create users table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create purchases table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_id INTEGER,
                price REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
        ''')
        
        # Create payments table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL NOT NULL,
                invoice_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        await db.commit()

# Create main keyboard
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🛒 Купить"),
        KeyboardButton(text="👤 Профиль")
    )
    builder.row(
        KeyboardButton(text="💰 Баланс"),
        KeyboardButton(text="⚙️ Настройки")
    )
    return builder.as_markup(resize_keyboard=True)

# Create back button keyboard
def get_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data="back_to_main")
    return builder.as_markup()

# Create payment method keyboard
def get_payment_method_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 CryptoBot", callback_data="pay_cryptobot")
    return builder.as_markup()

# Create admin inline keyboard
def get_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Добавить товар", callback_data="add_product"),
        InlineKeyboardButton(text="📦 Добавить количество", callback_data="add_quantity")
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить товар", callback_data="edit_product")
    )
    return builder.as_markup()

# Create products keyboard
async def get_products_keyboard():
    builder = InlineKeyboardBuilder()
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute('SELECT id, name, price FROM products WHERE quantity > 0') as cursor:
            products = await cursor.fetchall()
            
    for product in products:
        builder.button(
            text=f"{product[1]} | {product[2]}₽",
            callback_data=f"buy_{product[0]}"
        )
    
    builder.adjust(2)  # Arrange buttons in 2 columns
    return builder.as_markup()

# Start command handler
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Create user if not exists
    async with aiosqlite.connect('shop.db') as db:
        await db.execute(
            'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
            (message.from_user.id, message.from_user.username)
        )
        await db.commit()
    
    bot_info = await bot.get_me()
    welcome_text = f"Добро пожаловать в {bot_info.username}\n\nВ данном боте вы можете купить гарантировано различные цифровые товары в 2 клика"
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

# Profile button handler
@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    async with aiosqlite.connect('shop.db') as db:
        # Get user info
        async with db.execute(
            'SELECT username, balance FROM users WHERE user_id = ?',
            (message.from_user.id,)
        ) as cursor:
            user = await cursor.fetchone()
        
        # Get total purchases count
        async with db.execute(
            'SELECT COUNT(*) FROM purchases WHERE user_id = ?',
            (message.from_user.id,)
        ) as cursor:
            purchases_count = (await cursor.fetchone())[0]
        
        # Get successful purchases count
        async with db.execute(
            'SELECT COUNT(*) FROM purchases WHERE user_id = ? AND status = "completed"',
            (message.from_user.id,)
        ) as cursor:
            successful_purchases = (await cursor.fetchone())[0]
        
        # Get total spent
        async with db.execute(
            'SELECT SUM(price) FROM purchases WHERE user_id = ? AND status = "completed"',
            (message.from_user.id,)
        ) as cursor:
            total_spent = (await cursor.fetchone())[0] or 0
    
    if user:
        profile_text = (
            f"*👤 Профиль пользователя*\n\n"
            f"*Имя:* `{user[0]}`\n"
            f"*ID:* `{message.from_user.id}`\n"
            f"*Баланс:* `{user[1]}₽`\n\n"
            f"*📊 Статистика:*\n"
            f"├ Всего покупок: `{purchases_count}`\n"
            f"├ Успешных покупок: `{successful_purchases}`\n"
            f"└ Потрачено всего: `{total_spent}₽`\n\n"
            f"*📅 Дата регистрации:*\n"
            f"`{datetime.now().strftime('%d.%m.%Y')}`"
        )
        await message.answer(profile_text, parse_mode="Markdown", reply_markup=get_back_keyboard())

# Balance button handler
@dp.message(F.text == "💰 Баланс")
async def show_balance(message: types.Message):
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute(
            'SELECT balance FROM users WHERE user_id = ?',
            (message.from_user.id,)
        ) as cursor:
            balance = (await cursor.fetchone())[0]
    
    balance_text = (
        f"*💰 Ваш баланс:* `{balance}₽`\n\n"
        f"_Пополните баланс, чтобы совершать покупки_"
    )
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="💎 Пополнить", callback_data="deposit")
    await message.answer(balance_text, parse_mode="Markdown", reply_markup=keyboard.as_markup())

# Deposit button handler
@dp.callback_query(lambda c: c.data == "deposit")
async def process_deposit(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "*💎 Выберите метод оплаты:*",
        parse_mode="Markdown",
        reply_markup=get_payment_method_keyboard()
    )
    await callback_query.answer()

# CryptoBot payment handler
@dp.callback_query(lambda c: c.data == "pay_cryptobot")
async def process_cryptobot_payment(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(
        "*💎 Введите сумму в рублях:*\n"
        "_Минимальная сумма: 5₽_",
        parse_mode="Markdown"
    )
    await state.set_state(PaymentStates.waiting_for_amount)
    await callback_query.answer()

# Process payment amount
@dp.message(PaymentStates.waiting_for_amount)
async def process_payment_amount(message: types.Message, state: FSMContext):
    try:
        amount_rub = float(message.text)
        if amount_rub < 5:
            await message.answer("Минимальная сумма пополнения: 5₽")
            return
        
        # Convert RUB to USDT
        amount_usdt = await convert_rub_to_usdt(amount_rub)
        
        # Create invoice in CryptoBot
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    "https://pay.crypt.bot/api/createInvoice",
                    headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
                    json={
                        "asset": "USDT",
                        "amount": amount_usdt,
                        "description": f"Пополнение баланса на {amount_rub}₽",
                        "paid_btn_name": "openBot",
                        "paid_btn_url": "https://t.me/your_bot_username",
                        "expires_in": 900,  # 15 minutes
                        "allow_comments": False,
                        "allow_anonymous": False
                    }
                ) as response:
                    response_data = await response.json()
                    
                    if response.status == 200:
                        if response_data["ok"]:
                            invoice = response_data["result"]
                            
                            # Save payment to database
                            async with aiosqlite.connect('shop.db') as db:
                                await db.execute(
                                    '''INSERT INTO payments 
                                       (user_id, amount, invoice_id, expires_at) 
                                       VALUES (?, ?, ?, ?)''',
                                    (message.from_user.id, amount_rub, invoice["invoice_id"],
                                     datetime.now() + timedelta(minutes=15))
                                )
                                await db.commit()
                            
                            # Create payment message
                            payment_text = (
                                f"*💎 Пополнение баланса*\n\n"
                                f"Сумма: `{amount_rub}₽`\n"
                                f"К оплате: `{amount_usdt} USDT`\n"
                                f"Статус: `Ожидает оплаты`\n\n"
                                f"_У вас есть 15 минут на оплату_\n"
                                f"`{datetime.now().strftime('%H:%M')} - {(datetime.now() + timedelta(minutes=15)).strftime('%H:%M')}`"
                            )
                            
                            keyboard = InlineKeyboardBuilder()
                            keyboard.button(
                                text="💎 Оплатить",
                                url=invoice["pay_url"]
                            )
                            
                            await message.answer(
                                payment_text,
                                parse_mode="Markdown",
                                reply_markup=keyboard.as_markup()
                            )
                            
                            # Start payment check timer
                            asyncio.create_task(check_payment_status(
                                message.from_user.id,
                                invoice["invoice_id"],
                                amount_rub
                            ))
                        else:
                            error_message = response_data.get("error", {}).get("message", "Неизвестная ошибка")
                            error_code = response_data.get("error", {}).get("code", "UNKNOWN")
                            await message.answer(
                                f"*❌ Ошибка при создании счета*\n\n"
                                f"Код ошибки: `{error_code}`\n"
                                f"Описание: `{error_message}`\n\n"
                                f"_Пожалуйста, попробуйте позже или обратитесь в поддержку_",
                                parse_mode="Markdown"
                            )
                    else:
                        error_text = await response.text()
                        await message.answer(
                            f"*❌ Ошибка при создании счета*\n\n"
                            f"Статус: `{response.status}`\n"
                            f"Ответ сервера: `{error_text}`\n\n"
                            f"_Пожалуйста, попробуйте позже или обратитесь в поддержку_",
                            parse_mode="Markdown"
                        )
            except aiohttp.ClientError as e:
                await message.answer(
                    f"*❌ Ошибка подключения к серверу*\n\n"
                    f"Тип ошибки: `{type(e).__name__}`\n"
                    f"Описание: `{str(e)}`\n\n"
                    f"_Пожалуйста, попробуйте позже или обратитесь в поддержку_",
                    parse_mode="Markdown"
                )
            except Exception as e:
                await message.answer(
                    f"*❌ Неожиданная ошибка*\n\n"
                    f"Тип ошибки: `{type(e).__name__}`\n"
                    f"Описание: `{str(e)}`\n\n"
                    f"_Пожалуйста, попробуйте позже или обратитесь в поддержку_",
                    parse_mode="Markdown"
                )
        
        await state.clear()
    except ValueError:
        await message.answer(
            "*❌ Ошибка ввода*\n\n"
            "_Пожалуйста, введите корректную сумму (число)_",
            parse_mode="Markdown"
        )

# Payment status checker
async def check_payment_status(user_id: int, invoice_id: str, amount: float):
    await asyncio.sleep(900)  # Wait for 15 minutes
    
    async with aiosqlite.connect('shop.db') as db:
        # Check if payment was completed
        async with db.execute(
            'SELECT status FROM payments WHERE invoice_id = ?',
            (invoice_id,)
        ) as cursor:
            payment = await cursor.fetchone()
        
        if payment and payment[0] == "pending":
            # Payment not completed, update status
            await db.execute(
                'UPDATE payments SET status = "expired" WHERE invoice_id = ?',
                (invoice_id,)
            )
            await db.commit()
            
            # Notify user
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="💎 Попробовать снова", callback_data="pay_cryptobot")
            
            await bot.send_message(
                user_id,
                "*❌ Время оплаты истекло*\n\n"
                "_Ваш счет удалился по истечению времени. "
                "Если вы не успели, попробуйте снова._",
                parse_mode="Markdown",
                reply_markup=keyboard.as_markup()
            )

# Back button handler
@dp.callback_query(lambda c: c.data == "back_to_main")
async def process_back_button(callback_query: types.CallbackQuery):
    await callback_query.message.delete()
    await callback_query.answer()

# Buy button handler
@dp.message(F.text == "🛒 Купить")
async def show_products(message: types.Message):
    keyboard = await get_products_keyboard()
    await message.answer("Выберите товар:", reply_markup=keyboard)

# Admin command handler
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
    else:
        await message.answer("У вас нет доступа к этой команде.")

# Admin callback handlers
@dp.callback_query(lambda c: c.data == "add_product")
async def add_product_start(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("У вас нет доступа к этой функции.")
        return
    
    await callback_query.message.answer("Введите название товара:")
    await state.set_state(AdminStates.waiting_for_product_name)
    await callback_query.answer()

@dp.message(AdminStates.waiting_for_product_name)
async def process_product_name(message: types.Message, state: FSMContext):
    await state.update_data(product_name=message.text)
    await message.answer("Введите цену товара (в рублях):")
    await state.set_state(AdminStates.waiting_for_product_price)

@dp.message(AdminStates.waiting_for_product_price)
async def process_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text)
        await state.update_data(product_price=price)
        await message.answer("Введите количество товара:")
        await state.set_state(AdminStates.waiting_for_product_quantity)
    except ValueError:
        await message.answer("Пожалуйста, введите корректную цену (число):")

@dp.message(AdminStates.waiting_for_product_quantity)
async def process_product_quantity(message: types.Message, state: FSMContext):
    try:
        quantity = int(message.text)
        data = await state.get_data()
        
        async with aiosqlite.connect('shop.db') as db:
            await db.execute(
                'INSERT INTO products (name, price, quantity) VALUES (?, ?, ?)',
                (data['product_name'], data['product_price'], quantity)
            )
            await db.commit()
        
        await message.answer(f"Товар успешно добавлен!\nНазвание: {data['product_name']}\nЦена: {data['product_price']}₽\nКоличество: {quantity}")
        await state.clear()
    except ValueError:
        await message.answer("Пожалуйста, введите корректное количество (целое число):")

# Buy product handler
@dp.callback_query(lambda c: c.data.startswith("buy_"))
async def process_buy_product(callback_query: types.CallbackQuery):
    product_id = int(callback_query.data.split("_")[1])
    
    async with aiosqlite.connect('shop.db') as db:
        async with db.execute('SELECT name, price FROM products WHERE id = ?', (product_id,)) as cursor:
            product = await cursor.fetchone()
    
    if product:
        await callback_query.message.answer(f"Вы выбрали товар: {product[0]}\nЦена: {product[1]}₽\n\nФункция покупки будет реализована позже.")
    else:
        await callback_query.message.answer("Товар не найден.")
    
    await callback_query.answer()

# Main function
async def main():
    await init_db()
    await setup_webhook()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 