import logging
import os
import requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
)
from telegram.error import TelegramError
from telegram.helpers import escape_markdown

# ══════════════════════════════════════════════
# ⚙️  CONFIG — Render Dashboard-এ env var দাও
# ══════════════════════════════════════════════
BOT_TOKEN    = os.environ.get("BOT_TOKEN",    "YOUR_BOT_TOKEN")
API_KEY      = os.environ.get("API_KEY",      "YOUR_SMM_API_KEY")
API_URL      = "https://hdsmmpanel.com/api/v2"
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL",  "")
PORT         = int(os.environ.get("PORT",     "8443"))
BKASH_NUMBER = os.environ.get("BKASH_NUMBER", "01XXXXXXXXX")
NAGAD_NUMBER = os.environ.get("NAGAD_NUMBER", "01XXXXXXXXX")

ADMIN_ID = 7341620431

MAIN_CHANNEL   = -1003991490219   # @jmsmmchanel
ORDER_CHANNEL  = -1003972094422   # @jmsmmorderpanel
BACKUP_CHANNEL = -1003901109971   # @jmsmmbackup

REQUIRED_CHANNELS = [
    {"id": MAIN_CHANNEL,   "name": "📢 Main Channel",   "link": "https://t.me/jmsmmchanel"},
    {"id": ORDER_CHANNEL,  "name": "📋 Order Channel",  "link": "https://t.me/jmsmmorderpanel"},
    {"id": BACKUP_CHANNEL, "name": "🔁 Backup Channel", "link": "https://t.me/jmsmmbackup"},
]
# ══════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# In-memory balance store  { user_id(int): float }
user_balances: dict = {}

# Conversation states
(
    ORDER_SERVICE, ORDER_LINK, ORDER_QUANTITY, ORDER_CONFIRM,
    STATUS_INPUT, CANCEL_INPUT, REFILL_INPUT, REFILL_STATUS_INPUT,
    FUND_AMOUNT, FUND_PROOF,
) = range(10)


# ══════════════════════════════════════════════
# 🛠  Helpers
# ══════════════════════════════════════════════
def safe_name(name: str) -> str:
    """Markdown v1 unsafe chars থেকে name-কে safe করে"""
    return escape_markdown(name, version=1)


# ══════════════════════════════════════════════
# 🔒  Channel Join Check
# ══════════════════════════════════════════════
async def is_member(bot, user_id: int, channel_id: int) -> bool:
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status not in [ChatMember.LEFT, ChatMember.BANNED]
    except TelegramError:
        return False


async def check_all_channels(bot, user_id: int) -> list:
    """Join করেনি এমন channel list ফেরত দেয়"""
    not_joined = []
    for ch in REQUIRED_CHANNELS:
        if not await is_member(bot, user_id, ch["id"]):
            not_joined.append(ch)
    return not_joined


async def send_join_prompt(target, not_joined: list):
    lines = ["⛔ *বট ব্যবহার করতে নিচের সব চ্যানেলে Join করো:*\n"]
    kb = []
    for ch in not_joined:
        lines.append(f"• {ch['name']}")
        kb.append([InlineKeyboardButton(f"➕ {ch['name']}", url=ch["link"])])
    kb.append([InlineKeyboardButton("✅ Join করেছি — চেক করো", callback_data="check_join")])
    text = "\n".join(lines)
    if isinstance(target, Update):
        await target.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await target.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


async def require_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        return True
    not_joined = await check_all_channels(ctx.bot, uid)
    if not_joined:
        q = update.callback_query
        await send_join_prompt(q if q else update, not_joined)
        return False
    return True


# ══════════════════════════════════════════════
# 🔧  SMM Panel API
# ══════════════════════════════════════════════
def smm_request(params: dict) -> dict:
    params["key"] = API_KEY
    try:
        r = requests.post(API_URL, data=params, timeout=15)
        return r.json()
    except requests.exceptions.Timeout:
        return {"error": "⏱ Server timeout"}
    except Exception as e:
        return {"error": str(e)}


def api_balance():
    d = smm_request({"action": "balance"})
    if "error" in d:
        return None, d["error"]
    return d.get("balance"), d.get("currency", "USD")


def api_services():
    d = smm_request({"action": "services"})
    return d if isinstance(d, list) else []


def api_add_order(service, link, qty):
    return smm_request({
        "action": "add", "service": service,
        "link": link, "quantity": qty
    })


def api_order_status(oid):
    return smm_request({"action": "status", "order": oid})


def api_multi_status(oids):
    return smm_request({"action": "status", "orders": oids})


def api_cancel(oids):
    d = smm_request({"action": "cancel", "orders": oids})
    return d if isinstance(d, list) else [d]


def api_refill(oid):
    return smm_request({"action": "refill", "order": oid})


def api_refill_status(rid):
    return smm_request({"action": "refill_status", "refill": rid})


# ══════════════════════════════════════════════
# 🎨  Keyboards
# ══════════════════════════════════════════════
def kb_main():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰  ব্যালেন্স",        callback_data="balance"),
            InlineKeyboardButton("📋  সার্ভিস লিস্ট",   callback_data="services"),
        ],
        [
            InlineKeyboardButton("🛒  নতুন অর্ডার",      callback_data="new_order"),
            InlineKeyboardButton("🔍  অর্ডার স্ট্যাটাস", callback_data="order_status"),
        ],
        [
            InlineKeyboardButton("🔄  রিফিল",            callback_data="refill"),
            InlineKeyboardButton("📊  রিফিল স্ট্যাটাস",  callback_data="refill_status"),
        ],
        [
            InlineKeyboardButton("🚫  অর্ডার ক্যান্সেল", callback_data="cancel_order"),
            InlineKeyboardButton("💳  ফান্ড যোগ করো",    callback_data="fund_request"),
        ],
    ])


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠  মেইন মেনু", callback_data="main_menu")]
    ])


def kb_back_services():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️  ক্যাটাগরিতে ফিরো", callback_data="services")],
        [InlineKeyboardButton("🏠  মেইন মেনু",         callback_data="main_menu")],
    ])


# ══════════════════════════════════════════════
# 📢  Order Channel Logger
# ══════════════════════════════════════════════
async def log_to_channel(bot, text: str):
    try:
        await bot.send_message(ORDER_CHANNEL, text, parse_mode="Markdown")
    except TelegramError as e:
        logger.warning(f"Channel log failed: {e}")


# ══════════════════════════════════════════════
# 🏠  Start / Menu
# ══════════════════════════════════════════════
WELCOME = (
    "╔══════════════════════╗\n"
    "║  🌐  HD SMM Panel Bot  ║\n"
    "╚══════════════════════╝\n\n"
    "স্বাগতম! নিচের মেনু থেকে যা করতে চাও বেছে নাও 👇"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_join(update, ctx):
        return
    await update.message.reply_text(WELCOME, reply_markup=kb_main())


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await require_join(update, ctx):
        return
    await update.message.reply_text(WELCOME, reply_markup=kb_main())


async def cb_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return
    await q.edit_message_text(WELCOME, reply_markup=kb_main())


async def cb_check_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("⏳ চেক করা হচ্ছে...")
    uid = q.from_user.id
    not_joined = await check_all_channels(ctx.bot, uid)
    if not_joined:
        await send_join_prompt(q, not_joined)
    else:
        await q.edit_message_text(WELCOME, reply_markup=kb_main())


# ══════════════════════════════════════════════
# 🛠  Admin — /admin command only
# ══════════════════════════════════════════════
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ এই command শুধু Admin ব্যবহার করতে পারবে।")
        return
    total_bal = sum(user_balances.values())
    text = (
        "╔══════════════════════╗\n"
        "║     🛠  Admin Panel     ║\n"
        "╚══════════════════════╝\n\n"
        f"👥 Users (balance আছে): {len(user_balances)}\n"
        f"💰 মোট User Balance: {total_bal:.0f} টাকা\n\n"
        "অপশন বেছে নাও:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 User List", callback_data="admin_userlist")],
        [InlineKeyboardButton("🏠 মেইন মেনু", callback_data="main_menu")],
    ])
    await update.message.reply_text(text, reply_markup=kb)


async def cb_admin_userlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # Admin check আগে, তারপর answer
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔ Admin only!", show_alert=True)
        return
    await q.answer()
    if not user_balances:
        text = "📊 এখনো কোনো user balance নেই।"
    else:
        lines = ["📊 *User Balance List*\n"]
        for uid, bal in list(user_balances.items())[:30]:
            lines.append(f"👤 `{uid}` → {bal:.0f} টাকা")
        text = "\n".join(lines)
    await q.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Admin Panel", callback_data="admin_back")]
        ])
    )


async def cb_admin_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # Admin check আগে, তারপর answer
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔ Admin only!", show_alert=True)
        return
    await q.answer()
    total_bal = sum(user_balances.values())
    text = (
        "╔══════════════════════╗\n"
        "║     🛠  Admin Panel     ║\n"
        "╚══════════════════════╝\n\n"
        f"👥 Users (balance আছে): {len(user_balances)}\n"
        f"💰 মোট User Balance: {total_bal:.0f} টাকা\n\n"
        "অপশন বেছে নাও:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 User List", callback_data="admin_userlist")],
        [InlineKeyboardButton("🏠 মেইন মেনু", callback_data="main_menu")],
    ])
    await q.edit_message_text(text, reply_markup=kb)


# ══════════════════════════════════════════════
# 💰  Balance
# ══════════════════════════════════════════════
async def cb_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return
    uid = q.from_user.id
    await q.edit_message_text("⏳ ব্যালেন্স চেক হচ্ছে...")
    smm_bal, currency = api_balance()
    local_bal = user_balances.get(uid, 0.0)
    smm_line = (
        f"❌ SMM Error: {currency}"
        if smm_bal is None
        else f"💵 SMM Panel: {smm_bal} {currency}"
    )
    text = (
        "┌──────────────────────┐\n"
        "│      💰  ব্যালেন্স       │\n"
        "├──────────────────────┤\n"
        f"│  {smm_line}\n"
        f"│  👤 তোমার ব্যালেন্স: {local_bal:.0f} টাকা\n"
        "└──────────────────────┘"
    )
    await q.edit_message_text(text, reply_markup=kb_back())


# ══════════════════════════════════════════════
# 📋  Services
# ══════════════════════════════════════════════
async def cb_services(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return
    await q.edit_message_text("⏳ সার্ভিস লোড হচ্ছে...")
    services = api_services()
    if not services:
        await q.edit_message_text("❌ সার্ভিস লোড করা যায়নি।", reply_markup=kb_back())
        return
    cats = {}
    for s in services:
        cats.setdefault(s.get("category", "Other"), []).append(s)
    ctx.bot_data["categories"] = cats
    cat_list = list(cats.keys())[:20]
    rows = []
    for i in range(0, len(cat_list), 2):
        row = [InlineKeyboardButton(
            f"📁 {cat_list[i][:22]}", callback_data=f"cat_{cat_list[i][:28]}"
        )]
        if i + 1 < len(cat_list):
            row.append(InlineKeyboardButton(
                f"📁 {cat_list[i+1][:22]}", callback_data=f"cat_{cat_list[i+1][:28]}"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠  মেইন মেনু", callback_data="main_menu")])
    await q.edit_message_text(
        f"📋 *মোট {len(services)} টি সার্ভিস*\nক্যাটাগরি বেছে নাও 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows)
    )


async def cb_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    cat = q.data[4:]
    svcs = ctx.bot_data.get("categories", {}).get(cat, [])
    if not svcs:
        await q.answer("এই ক্যাটাগরিতে সার্ভিস নেই", show_alert=True)
        return
    await q.answer()
    lines = [f"📁 *{safe_name(cat)}* — {len(svcs)} টি সার্ভিস\n"]
    for s in svcs[:15]:
        lines.append(
            f"🆔 `{s.get('service')}` {safe_name(str(s.get('name', '')))}\n"
            f"   💵 ${s.get('rate','?')}/১০০০  |  📦 {s.get('min')}–{s.get('max')}\n"
        )
    if len(svcs) > 15:
        lines.append(f"\n_আরও {len(svcs)-15} টি সার্ভিস আছে_")
    await q.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=kb_back_services()
    )


# ══════════════════════════════════════════════
# 🛒  New Order (Conversation)
# ══════════════════════════════════════════════
async def cb_new_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "🛒 *নতুন অর্ডার — ধাপ ১/৩*\n\n"
        "Service ID লিখো (যেমন: `123`)\n\n"
        "_/cancel দিয়ে বের হও_",
        parse_mode="Markdown"
    )
    return ORDER_SERVICE


async def order_step_service(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["svc"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ *ধাপ ২/৩*\n\n"
        f"🆔 Service: `{ctx.user_data['svc']}`\n\n"
        "Target Link দাও:",
        parse_mode="Markdown"
    )
    return ORDER_LINK


async def order_step_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["link"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ *ধাপ ৩/৩*\n\n🔗 Link সেট হয়েছে\n\nQuantity লিখো (সংখ্যা):",
        parse_mode="Markdown"
    )
    return ORDER_QUANTITY


async def order_step_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("❌ শুধু সংখ্যা লিখো! আবার চেষ্টা করো:")
        return ORDER_QUANTITY
    ctx.user_data["qty"] = int(txt)
    s  = ctx.user_data["svc"]
    lk = ctx.user_data["link"]
    q2 = ctx.user_data["qty"]
    await update.message.reply_text(
        "📋 *অর্ডার নিশ্চিত করো*\n\n"
        f"🆔 Service  : `{s}`\n"
        f"🔗 Link     : `{lk}`\n"
        f"📦 Quantity : `{q2}`\n\n"
        "সব ঠিক আছে?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅  কনফার্ম", callback_data="order_confirm"),
            InlineKeyboardButton("🚫  বাতিল",  callback_data="order_cancel_confirm"),
        ]])
    )
    return ORDER_CONFIRM


async def order_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "order_cancel_confirm":
        await q.edit_message_text("🚫 অর্ডার বাতিল।", reply_markup=kb_back())
        return ConversationHandler.END
    await q.edit_message_text("⏳ অর্ডার দেওয়া হচ্ছে...")
    user = q.from_user
    res  = api_add_order(
        ctx.user_data["svc"],
        ctx.user_data["link"],
        ctx.user_data["qty"]
    )
    if "error" in res:
        text = f"❌ Error: {res['error']}"
    else:
        oid  = res.get("order", "N/A")
        text = (
            "╔══════════════════════╗\n"
            "║  ✅  অর্ডার সফল হয়েছে!  ║\n"
            "╚══════════════════════╝\n\n"
            f"🆔 Order ID: `{oid}`"
        )
        await log_to_channel(
            ctx.bot,
            f"🛒 *নতুন অর্ডার*\n"
            f"👤 [{safe_name(user.full_name)}](tg://user?id={user.id}) (`{user.id}`)\n"
            f"🆔 Service: `{ctx.user_data['svc']}`\n"
            f"🔗 Link: `{ctx.user_data['link']}`\n"
            f"📦 Qty: `{ctx.user_data['qty']}`\n"
            f"✅ Order ID: `{oid}`"
        )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


async def conv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 বাতিল হয়েছে।", reply_markup=kb_main())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 🔍  Order Status (Conversation)
# ══════════════════════════════════════════════
async def cb_order_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "🔍 *অর্ডার স্ট্যাটাস*\n\n"
        "Order ID লিখো।\n"
        "একাধিক হলে: `101,202,303`\n\n"
        "_/cancel দিয়ে বের হও_",
        parse_mode="Markdown"
    )
    return STATUS_INPUT


async def order_status_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ids = update.message.text.strip()
    await update.message.reply_text("⏳ চেক করা হচ্ছে...")
    if "," in ids:
        res = api_multi_status(ids)
        if isinstance(res, dict) and "error" in res:
            text = f"❌ {res['error']}"
        else:
            lines = ["📊 *Multiple Order Status*\n"]
            for oid, info in res.items():
                if isinstance(info, dict) and "error" in info:
                    lines.append(f"❌ `{oid}` — {info['error']}")
                else:
                    lines.append(
                        f"🆔 `{oid}` → {info.get('status','N/A')}\n"
                        f"   📦 Rem: {info.get('remains','N/A')}  "
                        f"💵 {info.get('charge','N/A')} {info.get('currency','')}"
                    )
            text = "\n".join(lines)
    else:
        res = api_order_status(ids)
        if "error" in res:
            text = f"❌ {res['error']}"
        else:
            text = (
                f"🔍 *Order* `{ids}`\n\n"
                f"📌 Status      : {res.get('status','N/A')}\n"
                f"🔢 Start Count : {res.get('start_count','N/A')}\n"
                f"📦 Remains     : {res.get('remains','N/A')}\n"
                f"💵 Charge      : {res.get('charge','N/A')} {res.get('currency','USD')}"
            )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 🚫  Cancel Order (Conversation)
# ══════════════════════════════════════════════
async def cb_cancel_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "🚫 *অর্ডার ক্যান্সেল*\n\n"
        "Order ID লিখো (একাধিক হলে comma দিয়ে)\n\n"
        "_/cancel দিয়ে বের হও_",
        parse_mode="Markdown"
    )
    return CANCEL_INPUT


async def cancel_order_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ids = update.message.text.strip()
    await update.message.reply_text("⏳ ক্যান্সেল করা হচ্ছে...")
    results = api_cancel(ids)
    lines   = ["🚫 *Cancel Result*\n"]
    for item in results:
        oid = item.get("order", "?")
        c   = item.get("cancel", {})
        if isinstance(c, dict) and "error" in c:
            lines.append(f"❌ `{oid}` — {c['error']}")
        else:
            lines.append(f"✅ `{oid}` — ক্যান্সেল সফল")
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=kb_back()
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 🔄  Refill (Conversation)
# ══════════════════════════════════════════════
async def cb_refill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "🔄 *Refill তৈরি করো*\n\nOrder ID লিখো:\n\n_/cancel দিয়ে বের হও_",
        parse_mode="Markdown"
    )
    return REFILL_INPUT


async def refill_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    oid = update.message.text.strip()
    await update.message.reply_text("⏳ Refill তৈরি হচ্ছে...")
    res = api_refill(oid)
    if "error" in res:
        text = f"❌ {res['error']}"
    else:
        text = f"✅ *Refill সফল!*\n\n🔄 Refill ID: `{res.get('refill','N/A')}`"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 📊  Refill Status (Conversation)
# ══════════════════════════════════════════════
async def cb_refill_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "📊 *Refill স্ট্যাটাস*\n\nRefill ID লিখো:\n\n_/cancel দিয়ে বের হও_",
        parse_mode="Markdown"
    )
    return REFILL_STATUS_INPUT


async def refill_status_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rid = update.message.text.strip()
    await update.message.reply_text("⏳ স্ট্যাটাস চেক হচ্ছে...")
    res = api_refill_status(rid)
    if "error" in res:
        text = f"❌ {res['error']}"
    else:
        text = f"📊 *Refill* `{rid}` *স্ট্যাটাস*\n\n📌 Status: {res.get('status','N/A')}"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 💳  Fund Request
# ══════════════════════════════════════════════
def get_fund_info_msg():
    return (
        "💳 *ফান্ড যোগ করার নিয়ম*\n\n"
        "নিচের যেকোনো মাধ্যমে Send Money করো:\n\n"
        "┌────────────────────────┐\n"
        f"│  📱 *bKash* : `{BKASH_NUMBER}`  │\n"
        f"│  📱 *Nagad* : `{NAGAD_NUMBER}`  │\n"
        "└────────────────────────┘\n\n"
        "⚠️ *Send Money* করতে হবে (Payment নয়)\n\n"
        "💵 সর্বনিম্ন: *২০ টাকা*\n\n"
        "পেমেন্ট করার পর নিচের বাটনে চাপো 👇"
    )


async def cb_fund_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return
    await q.edit_message_text(
        get_fund_info_msg(),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ পেমেন্ট করেছি", callback_data="fund_paid")],
            [InlineKeyboardButton("🏠 মেইন মেনু",     callback_data="main_menu")],
        ])
    )


async def cb_fund_paid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "💰 *কত টাকা পেমেন্ট করেছ?*\n\n"
        "_(সর্বনিম্ন ২০ টাকা। শুধু সংখ্যা লিখো, যেমন: 50 বা 200)_\n\n"
        "_/cancel দিয়ে বের হও_",
        parse_mode="Markdown"
    )
    return FUND_AMOUNT


async def fund_get_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().replace(",", ".")
    try:
        amount = float(txt)
        if amount < 20:
            await update.message.reply_text("❌ সর্বনিম্ন ২০ টাকা! আবার লিখো:")
            return FUND_AMOUNT
    except ValueError:
        await update.message.reply_text("❌ সঠিক সংখ্যা লিখো! (যেমন: 50 বা 200)")
        return FUND_AMOUNT
    ctx.user_data["fund_amount"] = amount
    await update.message.reply_text(
        f"✅ Amount: *{amount:.0f} টাকা*\n\n"
        "এখন তোমার *Transaction ID* দাও:\n"
        "_(bKash/Nagad TrxID, যেমন: 8N3K5X2P1Q)_\n\n"
        "_/cancel দিয়ে বের হও_",
        parse_mode="Markdown"
    )
    return FUND_PROOF


async def fund_get_proof(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    amount = ctx.user_data.get("fund_amount", 0)
    trx_id = update.message.text.strip()

    admin_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"✅ Approve {amount:.0f}৳",
            callback_data=f"fund_approve_{user.id}_{amount:.2f}"
        ),
        InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"fund_reject_{user.id}_{amount:.2f}"
        ),
    ]])

    admin_msg = (
        "╔══════════════════════╗\n"
        "║   💳  Fund Request     ║\n"
        "╚══════════════════════╝\n\n"
        f"👤 User: [{safe_name(user.full_name)}](tg://user?id={user.id})\n"
        f"🆔 User ID: `{user.id}`\n"
        f"💰 Amount: *{amount:.0f} টাকা*\n"
        f"🔖 TrxID: `{trx_id}`"
    )

    try:
        await ctx.bot.send_message(
            ADMIN_ID, admin_msg,
            parse_mode="Markdown",
            reply_markup=admin_kb
        )
    except TelegramError as e:
        logger.error(f"Admin notify failed: {e}")

    await log_to_channel(
        ctx.bot,
        f"💳 *Fund Request*\n"
        f"👤 [{safe_name(user.full_name)}](tg://user?id={user.id}) (`{user.id}`)\n"
        f"💰 Amount: {amount:.0f} টাকা\n"
        f"🔖 TrxID: `{trx_id}`\n"
        f"⏳ Status: অপেক্ষায়..."
    )

    await update.message.reply_text(
        "╔══════════════════════╗\n"
        "║  ⏳  Request পাঠানো হয়েছে  ║\n"
        "╚══════════════════════╝\n\n"
        f"💰 Amount : *{amount:.0f} টাকা*\n"
        f"🔖 TrxID  : `{trx_id}`\n\n"
        "Admin যাচাই করার পর তোমার ব্যালেন্সে যোগ হবে ✅",
        parse_mode="Markdown",
        reply_markup=kb_back()
    )
    return ConversationHandler.END


# ── Admin: Approve / Reject ──────────────────
async def admin_fund_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔ শুধু Admin পারবে!", show_alert=True)
        return
    await q.answer()

    # callback_data format: fund_approve_{user_id}_{amount}
    # e.g. "fund_approve_123456_50.00"
    parts   = q.data.split("_")   # ['fund','approve','123456','50.00']
    action  = parts[1]            # 'approve' or 'reject'
    user_id = int(parts[2])
    amount  = float(parts[3])

    if action == "approve":
        user_balances[user_id] = user_balances.get(user_id, 0.0) + amount
        new_bal = user_balances[user_id]
        try:
            await ctx.bot.send_message(
                user_id,
                "╔══════════════════════╗\n"
                "║  ✅  ফান্ড অনুমোদিত!   ║\n"
                "╚══════════════════════╝\n\n"
                f"💰 *{amount:.0f} টাকা* তোমার ব্যালেন্সে যোগ হয়েছে!\n"
                f"💳 নতুন ব্যালেন্স: *{new_bal:.0f} টাকা*",
                parse_mode="Markdown",
                reply_markup=kb_main()
            )
        except TelegramError:
            pass
        orig     = q.message.text or q.message.caption or ""
        new_text = orig + f"\n\n✅ *Approved* — {amount:.0f} টাকা যোগ হয়েছে"
        await q.edit_message_text(new_text, parse_mode="Markdown")
        await log_to_channel(
            ctx.bot,
            f"✅ *Fund Approved*\n"
            f"👤 User ID: `{user_id}`\n"
            f"💰 Amount: {amount:.0f} টাকা\n"
            f"💳 New Balance: {new_bal:.0f} টাকা"
        )
    else:
        try:
            await ctx.bot.send_message(
                user_id,
                "╔══════════════════════╗\n"
                "║  ❌  ফান্ড বাতিল হয়েছে  ║\n"
                "╚══════════════════════╝\n\n"
                f"*{amount:.0f} টাকা* এর request Admin reject করেছে।\n"
                "সমস্যা হলে Admin-এর সাথে যোগাযোগ করো।",
                parse_mode="Markdown",
                reply_markup=kb_main()
            )
        except TelegramError:
            pass
        orig     = q.message.text or q.message.caption or ""
        new_text = orig + "\n\n❌ *Rejected*"
        await q.edit_message_text(new_text, parse_mode="Markdown")
        await log_to_channel(
            ctx.bot,
            f"❌ *Fund Rejected*\n"
            f"👤 User ID: `{user_id}`\n"
            f"💰 Amount: {amount:.0f} টাকা"
        )


# ══════════════════════════════════════════════
# 🚀  Build Application
# ══════════════════════════════════════════════
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(CommandHandler("admin", cmd_admin))

    # Simple callbacks
    app.add_handler(CallbackQueryHandler(cb_main_menu,        pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(cb_check_join,       pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(cb_balance,          pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(cb_services,         pattern="^services$"))
    app.add_handler(CallbackQueryHandler(cb_category,         pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(cb_admin_userlist,   pattern="^admin_userlist$"))
    app.add_handler(CallbackQueryHandler(cb_admin_back,       pattern="^admin_back$"))
    app.add_handler(CallbackQueryHandler(cb_fund_request,     pattern="^fund_request$"))
    app.add_handler(CallbackQueryHandler(admin_fund_decision, pattern="^fund_(approve|reject)_"))

    def make_conv(pat, entry_fn, states):
        return ConversationHandler(
            entry_points=[CallbackQueryHandler(entry_fn, pattern=pat)],
            states=states,
            fallbacks=[CommandHandler("cancel", conv_cancel)],
            allow_reentry=True,
        )

    app.add_handler(make_conv("^new_order$", cb_new_order, {
        ORDER_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_step_service)],
        ORDER_LINK:    [MessageHandler(filters.TEXT & ~filters.COMMAND, order_step_link)],
        ORDER_QUANTITY:[MessageHandler(filters.TEXT & ~filters.COMMAND, order_step_qty)],
        ORDER_CONFIRM: [CallbackQueryHandler(
            order_confirm_cb, pattern="^order_(confirm|cancel_confirm)$"
        )],
    }))

    app.add_handler(make_conv("^order_status$", cb_order_status, {
        STATUS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_status_input)],
    }))

    app.add_handler(make_conv("^cancel_order$", cb_cancel_order, {
        CANCEL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cancel_order_input)],
    }))

    app.add_handler(make_conv("^refill$", cb_refill, {
        REFILL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, refill_input)],
    }))

    app.add_handler(make_conv("^refill_status$", cb_refill_status, {
        REFILL_STATUS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, refill_status_input)],
    }))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_fund_paid, pattern="^fund_paid$")],
        states={
            FUND_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, fund_get_amount)],
            FUND_PROOF:  [MessageHandler(filters.TEXT & ~filters.COMMAND, fund_get_proof)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        allow_reentry=True,
    ))

    return app


# ══════════════════════════════════════════════
# 🌐  Entry Point
# ══════════════════════════════════════════════
if __name__ == "__main__":
    application = build_app()

    if WEBHOOK_URL:
        logger.info(f"🌐 Webhook mode — port {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("🚀 Polling mode (local)")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
