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
# ⚙️  CONFIG
# ══════════════════════════════════════════════
BOT_TOKEN    = os.environ.get("BOT_TOKEN",    "YOUR_BOT_TOKEN")
API_KEY      = os.environ.get("API_KEY",      "YOUR_SMM_API_KEY")
API_URL      = "https://hdsmmpanel.com/api/v2"
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL",  "")
PORT         = int(os.environ.get("PORT",     "8443"))
BKASH_NUMBER = os.environ.get("BKASH_NUMBER", "01XXXXXXXXX")
NAGAD_NUMBER = os.environ.get("NAGAD_NUMBER", "01XXXXXXXXX")

ADMIN_ID = 7341620431

MAIN_CHANNEL   = -1003991490219
ORDER_CHANNEL  = -1003972094422
BACKUP_CHANNEL = -1003901109971

REQUIRED_CHANNELS = [
    {"id": MAIN_CHANNEL,   "name": "📢 Main Channel",   "link": "https://t.me/jmsmmchanel"},
    {"id": ORDER_CHANNEL,  "name": "📋 Order Channel",  "link": "https://t.me/jmsmmorderpanel"},
    {"id": BACKUP_CHANNEL, "name": "🔁 Backup Channel", "link": "https://t.me/jmsmmbackup"},
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# In-memory stores
user_balances: dict = {}

# Admin-added services: list of dicts
# { "id": "123", "name": "IG Followers", "rate": "0.5", "min": "100", "max": "10000" }
admin_services: list = []

# Conversation states
(
    ORDER_SERVICE, ORDER_LINK, ORDER_QUANTITY, ORDER_CONFIRM,
    STATUS_INPUT, CANCEL_INPUT, REFILL_INPUT, REFILL_STATUS_INPUT,
    FUND_AMOUNT, FUND_PROOF,
    ADMIN_ADD_SVC,
) = range(11)


# ══════════════════════════════════════════════
# 🛠  Helpers
# ══════════════════════════════════════════════
def safe(text: str) -> str:
    return escape_markdown(str(text), version=1)


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
    not_joined = []
    for ch in REQUIRED_CHANNELS:
        if not await is_member(bot, user_id, ch["id"]):
            not_joined.append(ch)
    return not_joined


async def send_join_prompt(send_fn, not_joined: list):
    """send_fn = update.message.reply_text বা q.edit_message_text"""
    if not_joined:
        lines = ["⛔ *সব চ্যানেলে Join করো:*\n"]
        kb = []
        for ch in not_joined:
            lines.append(f"• {ch['name']}")
            kb.append([InlineKeyboardButton(f"➕ {ch['name']}", url=ch["link"])])
        kb.append([InlineKeyboardButton("✅ Join করেছি — চেক করো", callback_data="check_join")])
        await send_fn("\n".join(lines), parse_mode="Markdown",
                      reply_markup=InlineKeyboardMarkup(kb))
    else:
        await send_fn("⛔ সব চ্যানেলে Join না করলে বট ব্যবহার করা যাবে না।",
                      reply_markup=InlineKeyboardMarkup([[
                          InlineKeyboardButton("✅ Join করেছি — চেক করো",
                                               callback_data="check_join")
                      ]]))


async def require_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        return True
    not_joined = await check_all_channels(ctx.bot, uid)
    if not_joined:
        q = update.callback_query
        if q:
            await send_join_prompt(q.edit_message_text, not_joined)
        else:
            await send_join_prompt(update.message.reply_text, not_joined)
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


# ══════════════════════════════════════════════
# 📢  Channel Logger
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
    if uid == ADMIN_ID:
        await q.edit_message_text(WELCOME, reply_markup=kb_main())
        return
    not_joined = await check_all_channels(ctx.bot, uid)
    if not_joined:
        # এখনো join করেনি — কোন কোন channel বাকি দেখাও
        lines = ["⛔ *এখনো এই চ্যানেলগুলোতে Join হওনি:*\n"]
        kb = []
        for ch in not_joined:
            lines.append(f"• {ch['name']}")
            kb.append([InlineKeyboardButton(f"➕ {ch['name']}", url=ch["link"])])
        kb.append([InlineKeyboardButton("✅ Join করেছি — আবার চেক করো",
                                        callback_data="check_join")])
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await q.edit_message_text(WELCOME, reply_markup=kb_main())


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
    local_bal = user_balances.get(uid, 0.0)

    if uid == ADMIN_ID:
        # Admin — SMM balance + total user balance দেখাবে
        smm_bal, currency = api_balance()
        smm_line = (f"❌ Error: {currency}" if smm_bal is None
                    else f"💵 SMM Panel: {smm_bal} {currency}")
        total_bal = sum(user_balances.values())
        text = (
            "┌──────────────────────┐\n"
            "│    💰  Admin Balance   │\n"
            "├──────────────────────┤\n"
            f"│  {smm_line}\n"
            f"│  👥 Total User: {total_bal:.0f} টাকা\n"
            "└──────────────────────┘"
        )
    else:
        # User — শুধু নিজের balance
        text = (
            "┌──────────────────────┐\n"
            "│      💰  ব্যালেন্স       │\n"
            "├──────────────────────┤\n"
            f"│  👤 তোমার ব্যালেন্স:\n"
            f"│  💵 {local_bal:.0f} টাকা\n"
            "└──────────────────────┘"
        )
    await q.edit_message_text(text, reply_markup=kb_back())


# ══════════════════════════════════════════════
# 📋  Services (Admin-added only)
# ══════════════════════════════════════════════
async def cb_services(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return

    if not admin_services:
        await q.edit_message_text(
            "📋 এখনো কোনো সার্ভিস যোগ করা হয়নি।\n"
            "Admin `/admin` দিয়ে সার্ভিস যোগ করতে পারবে।",
            reply_markup=kb_back()
        )
        return

    lines = [f"📋 *মোট {len(admin_services)} টি সার্ভিস*\n"]
    for i, s in enumerate(admin_services):
        lines.append(
            f"🆔 `{s['id']}` — {safe(s['name'])}\n"
            f"   💵 {s['rate']} টাকা/১০০০  |  📦 {s['min']}–{s['max']}\n"
        )
        if i >= 29:  # max 30
            lines.append(f"_... আরও {len(admin_services)-30} টি_")
            break

    # Service buttons — 2 per row
    kb = []
    for i in range(0, min(len(admin_services), 20), 2):
        row = [InlineKeyboardButton(
            f"🛒 {admin_services[i]['id']}",
            callback_data=f"svc_{admin_services[i]['id']}"
        )]
        if i + 1 < len(admin_services):
            row.append(InlineKeyboardButton(
                f"🛒 {admin_services[i+1]['id']}",
                callback_data=f"svc_{admin_services[i+1]['id']}"
            ))
        kb.append(row)
    kb.append([InlineKeyboardButton("🏠  মেইন মেনু", callback_data="main_menu")])

    await q.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
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
        f"✅ *ধাপ ২/৩*\n\n🆔 Service: `{ctx.user_data['svc']}`\n\nTarget Link দাও:",
        parse_mode="Markdown"
    )
    return ORDER_LINK


async def order_step_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["link"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ *ধাপ ৩/৩*\n\n🔗 Link সেট হয়েছে\n\nQuantity লিখো:",
        parse_mode="Markdown"
    )
    return ORDER_QUANTITY


async def order_step_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("❌ শুধু সংখ্যা লিখো!")
        return ORDER_QUANTITY
    ctx.user_data["qty"] = int(txt)
    await update.message.reply_text(
        "📋 *অর্ডার নিশ্চিত করো*\n\n"
        f"🆔 Service  : `{ctx.user_data['svc']}`\n"
        f"🔗 Link     : `{ctx.user_data['link']}`\n"
        f"📦 Quantity : `{ctx.user_data['qty']}`\n\n"
        "সব ঠিক আছে?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ কনফার্ম", callback_data="oconfirm"),
            InlineKeyboardButton("🚫 বাতিল",  callback_data="ocancel"),
        ]])
    )
    return ORDER_CONFIRM


async def order_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "ocancel":
        await q.edit_message_text("🚫 অর্ডার বাতিল।", reply_markup=kb_back())
        return ConversationHandler.END
    await q.edit_message_text("⏳ অর্ডার দেওয়া হচ্ছে...")
    user = q.from_user
    res  = api_add_order(ctx.user_data["svc"], ctx.user_data["link"], ctx.user_data["qty"])
    if "error" in res:
        text = f"❌ Error: {res['error']}"
    else:
        oid  = res.get("order", "N/A")
        text = (
            "╔══════════════════════╗\n"
            "║  ✅  অর্ডার সফল!       ║\n"
            "╚══════════════════════╝\n\n"
            f"🆔 Order ID: `{oid}`"
        )
        await log_to_channel(ctx.bot,
            f"🛒 *নতুন অর্ডার*\n"
            f"👤 [{safe(user.full_name)}](tg://user?id={user.id}) (`{user.id}`)\n"
            f"🆔 Service: `{ctx.user_data['svc']}`\n"
            f"📦 Qty: `{ctx.user_data['qty']}`\n"
            f"✅ Order ID: `{oid}`"
        )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


async def conv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 বাতিল হয়েছে।", reply_markup=kb_main())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 🔍  Order Status
# ══════════════════════════════════════════════
async def cb_order_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "🔍 *অর্ডার স্ট্যাটাস*\n\nOrder ID লিখো (একাধিক: `101,202`)\n\n_/cancel_",
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
            lines = ["📊 *Multiple Status*\n"]
            for oid, info in res.items():
                if isinstance(info, dict) and "error" in info:
                    lines.append(f"❌ `{oid}` — {info['error']}")
                else:
                    lines.append(
                        f"🆔 `{oid}` → {info.get('status','N/A')}\n"
                        f"   📦 {info.get('remains','N/A')} | 💵 {info.get('charge','N/A')}"
                    )
            text = "\n".join(lines)
    else:
        res = api_order_status(ids)
        if "error" in res:
            text = f"❌ {res['error']}"
        else:
            text = (
                f"🔍 *Order* `{ids}`\n\n"
                f"📌 Status : {res.get('status','N/A')}\n"
                f"🔢 Start  : {res.get('start_count','N/A')}\n"
                f"📦 Remains: {res.get('remains','N/A')}\n"
                f"💵 Charge : {res.get('charge','N/A')} {res.get('currency','USD')}"
            )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 🚫  Cancel Order
# ══════════════════════════════════════════════
async def cb_cancel_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "🚫 *অর্ডার ক্যান্সেল*\n\nOrder ID লিখো (comma দিয়ে একাধিক)\n\n_/cancel_",
        parse_mode="Markdown"
    )
    return CANCEL_INPUT


async def cancel_order_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    results = api_cancel(update.message.text.strip())
    lines   = ["🚫 *Cancel Result*\n"]
    for item in results:
        oid = item.get("order", "?")
        c   = item.get("cancel", {})
        lines.append(
            f"❌ `{oid}` — {c['error']}" if isinstance(c, dict) and "error" in c
            else f"✅ `{oid}` — ক্যান্সেল সফল"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 🔄  Refill
# ══════════════════════════════════════════════
async def cb_refill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "🔄 *Refill*\n\nOrder ID লিখো:\n\n_/cancel_",
        parse_mode="Markdown"
    )
    return REFILL_INPUT


async def refill_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    res = api_refill(update.message.text.strip())
    text = (f"✅ *Refill সফল!*\n\n🔄 ID: `{res.get('refill','N/A')}`"
            if "error" not in res else f"❌ {res['error']}")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 📊  Refill Status
# ══════════════════════════════════════════════
async def cb_refill_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return ConversationHandler.END
    await q.edit_message_text(
        "📊 *Refill Status*\n\nRefill ID লিখো:\n\n_/cancel_",
        parse_mode="Markdown"
    )
    return REFILL_STATUS_INPUT


async def refill_status_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    res = api_refill_status(update.message.text.strip())
    text = (f"📊 *Refill Status*\n\n📌 {res.get('status','N/A')}"
            if "error" not in res else f"❌ {res['error']}")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_back())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# 💳  Fund Request
# ══════════════════════════════════════════════
def get_fund_msg():
    return (
        "💳 *ফান্ড যোগ করার নিয়ম*\n\n"
        "নিচের যেকোনো মাধ্যমে *Send Money* করো:\n\n"
        "┌────────────────────────┐\n"
        f"│  📱 *bKash* : `{BKASH_NUMBER}`  │\n"
        f"│  📱 *Nagad* : `{NAGAD_NUMBER}`  │\n"
        "└────────────────────────┘\n\n"
        "⚠️ Send Money করতে হবে (Payment নয়)\n\n"
        "💵 সর্বনিম্ন: *২০ টাকা*\n\n"
        "পেমেন্ট করার পর নিচের বাটনে চাপো 👇"
    )


async def cb_fund_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_join(update, ctx):
        return
    await q.edit_message_text(
        get_fund_msg(),
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
        "_(সর্বনিম্ন ২০ টাকা)_\n\n_/cancel_",
        parse_mode="Markdown"
    )
    return FUND_AMOUNT


async def fund_get_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().replace(",", ".")
    try:
        amount = float(txt)
        if amount < 20:
            await update.message.reply_text("❌ সর্বনিম্ন ২০ টাকা!")
            return FUND_AMOUNT
    except ValueError:
        await update.message.reply_text("❌ সঠিক সংখ্যা লিখো!")
        return FUND_AMOUNT
    ctx.user_data["fund_amount"] = amount
    await update.message.reply_text(
        f"✅ *{amount:.0f} টাকা*\n\nএখন *Transaction ID* দাও:\n\n_/cancel_",
        parse_mode="Markdown"
    )
    return FUND_PROOF


async def fund_get_proof(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    amount = ctx.user_data.get("fund_amount", 0)
    trx_id = update.message.text.strip()

    # Short callback_data: fa_{uid}_{amount_int}
    admin_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"✅ Approve {amount:.0f}Tk",
            callback_data=f"fa_{user.id}_{int(amount)}"
        ),
        InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"fr_{user.id}_{int(amount)}"
        ),
    ]])

    try:
        await ctx.bot.send_message(
            ADMIN_ID,
            "╔══════════════════════╗\n"
            "║   💳  Fund Request     ║\n"
            "╚══════════════════════╝\n\n"
            f"👤 [{safe(user.full_name)}](tg://user?id={user.id})\n"
            f"🆔 ID: `{user.id}`\n"
            f"💰 Amount: *{amount:.0f} টাকা*\n"
            f"🔖 TrxID: `{trx_id}`",
            parse_mode="Markdown",
            reply_markup=admin_kb
        )
    except TelegramError as e:
        logger.error(f"Admin notify failed: {e}")

    await log_to_channel(ctx.bot,
        f"💳 *Fund Request*\n"
        f"👤 [{safe(user.full_name)}](tg://user?id={user.id}) (`{user.id}`)\n"
        f"💰 {amount:.0f} টাকা | TrxID: `{trx_id}`"
    )

    await update.message.reply_text(
        "⏳ *Request পাঠানো হয়েছে!*\n\n"
        f"💰 {amount:.0f} টাকা\n"
        f"🔖 TrxID: `{trx_id}`\n\n"
        "Admin approve করলে notify পাবে ✅",
        parse_mode="Markdown",
        reply_markup=kb_back()
    )
    return ConversationHandler.END


async def admin_fund_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔ Admin only!", show_alert=True)
        return
    await q.answer()

    parts   = q.data.split("_")  # fa_123456_500  or  fr_123456_500
    action  = "approve" if parts[0] == "fa" else "reject"
    user_id = int(parts[1])
    amount  = float(parts[2])

    orig     = q.message.text or q.message.caption or ""

    if action == "approve":
        user_balances[user_id] = user_balances.get(user_id, 0.0) + amount
        new_bal = user_balances[user_id]
        try:
            await ctx.bot.send_message(
                user_id,
                f"✅ *ফান্ড অনুমোদিত!*\n\n"
                f"💰 {amount:.0f} টাকা যোগ হয়েছে!\n"
                f"💳 নতুন ব্যালেন্স: *{new_bal:.0f} টাকা*",
                parse_mode="Markdown",
                reply_markup=kb_main()
            )
        except TelegramError:
            pass
        await q.edit_message_text(
            orig + f"\n\n✅ Approved — {amount:.0f} টাকা",
            parse_mode="Markdown"
        )
        await log_to_channel(ctx.bot,
            f"✅ *Fund Approved*\n👤 `{user_id}`\n💰 {amount:.0f} টাকা → bal: {new_bal:.0f}"
        )
    else:
        try:
            await ctx.bot.send_message(
                user_id,
                f"❌ *ফান্ড বাতিল হয়েছে*\n\n"
                f"{amount:.0f} টাকার request reject হয়েছে।\n"
                "সমস্যা হলে Admin-এর সাথে যোগাযোগ করো।",
                parse_mode="Markdown",
                reply_markup=kb_main()
            )
        except TelegramError:
            pass
        await q.edit_message_text(orig + "\n\n❌ Rejected", parse_mode="Markdown")
        await log_to_channel(ctx.bot,
            f"❌ *Fund Rejected*\n👤 `{user_id}`\n💰 {amount:.0f} টাকা"
        )


# ══════════════════════════════════════════════
# 🛠  Admin Panel — /admin command
# ══════════════════════════════════════════════
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only!")
        return
    await show_admin_panel(update.message.reply_text)


async def show_admin_panel(send_fn):
    total_bal = sum(user_balances.values())
    text = (
        "╔══════════════════════╗\n"
        "║     🛠  Admin Panel     ║\n"
        "╚══════════════════════╝\n\n"
        f"👥 Users: {len(user_balances)}\n"
        f"💰 Total Balance: {total_bal:.0f} টাকা\n"
        f"📋 Services: {len(admin_services)} টি\n\n"
        "অপশন বেছে নাও:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Service List দেখো",  callback_data="adm_svclist")],
        [InlineKeyboardButton("➕ Service যোগ করো",    callback_data="adm_addsvс")],
        [InlineKeyboardButton("🗑 Service মুছো",       callback_data="adm_delsvс")],
        [InlineKeyboardButton("👥 User List",           callback_data="adm_users")],
        [InlineKeyboardButton("🏠 মেইন মেনু",           callback_data="main_menu")],
    ])
    await send_fn(text, reply_markup=kb)


async def cb_adm_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔ Admin only!", show_alert=True)
        return
    await q.answer()
    await show_admin_panel(q.edit_message_text)


async def cb_adm_svclist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔", show_alert=True)
        return
    await q.answer()
    if not admin_services:
        text = "📋 এখনো কোনো service যোগ করা হয়নি।"
    else:
        lines = ["📋 *Service List*\n"]
        for i, s in enumerate(admin_services):
            lines.append(
                f"{i+1}. 🆔`{s['id']}` — {safe(s['name'])}\n"
                f"   💵 {s['rate']} টাকা | 📦 {s['min']}–{s['max']}"
            )
        text = "\n".join(lines)
    await q.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Admin Panel", callback_data="adm_back")]
        ])
    )


async def cb_adm_addsvс(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔", show_alert=True)
        return
    await q.answer()
    await q.edit_message_text(
        "➕ *Service যোগ করো*\n\n"
        "নিচের format-এ লিখো:\n\n"
        "`ID|Name|Rate|Min|Max`\n\n"
        "উদাহরণ:\n"
        "`123|IG Followers|0.50|100|10000`\n\n"
        "_/cancel দিয়ে বের হও_",
        parse_mode="Markdown"
    )
    return ADMIN_ADD_SVC


async def admin_add_svc_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    txt = update.message.text.strip()
    parts = txt.split("|")
    if len(parts) != 5:
        await update.message.reply_text(
            "❌ সঠিক format নয়!\n\n"
            "`ID|Name|Rate|Min|Max`\n\nআবার চেষ্টা করো:",
            parse_mode="Markdown"
        )
        return ADMIN_ADD_SVC
    svc_id, name, rate, mn, mx = [p.strip() for p in parts]
    # Duplicate check
    if any(s["id"] == svc_id for s in admin_services):
        await update.message.reply_text(
            f"⚠️ ID `{svc_id}` আগেই আছে! অন্য ID দাও।",
            parse_mode="Markdown"
        )
        return ADMIN_ADD_SVC
    admin_services.append({
        "id": svc_id, "name": name,
        "rate": rate, "min": mn, "max": mx
    })
    await update.message.reply_text(
        f"✅ Service যোগ হয়েছে!\n\n"
        f"🆔 `{svc_id}` — {safe(name)}\n"
        f"💵 {rate} টাকা | 📦 {mn}–{mx}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ আরেকটি যোগ করো", callback_data="adm_addsvс")],
            [InlineKeyboardButton("◀️ Admin Panel",     callback_data="adm_back")],
        ])
    )
    return ConversationHandler.END


async def cb_adm_delsvc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔", show_alert=True)
        return
    await q.answer()
    if not admin_services:
        await q.edit_message_text(
            "📋 মুছার মতো কোনো service নেই।",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Admin Panel", callback_data="adm_back")]
            ])
        )
        return
    lines = ["🗑 *কোন Service মুছবে?*\n"]
    kb = []
    for s in admin_services:
        lines.append(f"• `{s['id']}` — {safe(s['name'])}")
        cb = f"delsvc_{s['id']}"[:64]  # ensure under 64 bytes
        kb.append([InlineKeyboardButton(
            f"🗑 {s['id']} — {s['name'][:20]}", callback_data=cb
        )])
    kb.append([InlineKeyboardButton("◀️ Admin Panel", callback_data="adm_back")])
    await q.edit_message_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def cb_delsvc_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔", show_alert=True)
        return
    await q.answer()
    svc_id = q.data[7:]  # remove "delsvc_"
    before = len(admin_services)
    admin_services[:] = [s for s in admin_services if s["id"] != svc_id]
    if len(admin_services) < before:
        text = f"✅ Service `{svc_id}` মুছে ফেলা হয়েছে।"
    else:
        text = f"❌ Service `{svc_id}` পাওয়া যায়নি।"
    await q.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Admin Panel", callback_data="adm_back")]
        ])
    )


async def cb_adm_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔", show_alert=True)
        return
    await q.answer()
    if not user_balances:
        text = "👥 এখনো কোনো user balance নেই।"
    else:
        lines = ["👥 *User Balance*\n"]
        for uid, bal in list(user_balances.items())[:30]:
            lines.append(f"• `{uid}` → {bal:.0f} টাকা")
        text = "\n".join(lines)
    await q.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Admin Panel", callback_data="adm_back")]
        ])
    )


async def cb_adm_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔", show_alert=True)
        return
    await q.answer()
    await show_admin_panel(q.edit_message_text)


# ══════════════════════════════════════════════
# 🚀  Build Application
# ══════════════════════════════════════════════
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(CommandHandler("admin", cmd_admin))

    app.add_handler(CallbackQueryHandler(cb_main_menu,        pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(cb_check_join,       pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(cb_balance,          pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(cb_services,         pattern="^services$"))
    app.add_handler(CallbackQueryHandler(cb_fund_request,     pattern="^fund_request$"))
    app.add_handler(CallbackQueryHandler(admin_fund_decision, pattern="^f[ar]_"))
    app.add_handler(CallbackQueryHandler(cb_adm_panel,        pattern="^adm_panel$"))
    app.add_handler(CallbackQueryHandler(cb_adm_svclist,      pattern="^adm_svclist$"))
    app.add_handler(CallbackQueryHandler(cb_adm_delsvc,       pattern="^adm_delsvс$"))
    app.add_handler(CallbackQueryHandler(cb_delsvc_confirm,   pattern="^delsvc_"))
    app.add_handler(CallbackQueryHandler(cb_adm_users,        pattern="^adm_users$"))
    app.add_handler(CallbackQueryHandler(cb_adm_back,         pattern="^adm_back$"))

    def conv(pat, entry_fn, states):
        return ConversationHandler(
            entry_points=[CallbackQueryHandler(entry_fn, pattern=pat)],
            states=states,
            fallbacks=[CommandHandler("cancel", conv_cancel)],
            allow_reentry=True,
        )

    app.add_handler(conv("^new_order$", cb_new_order, {
        ORDER_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_step_service)],
        ORDER_LINK:    [MessageHandler(filters.TEXT & ~filters.COMMAND, order_step_link)],
        ORDER_QUANTITY:[MessageHandler(filters.TEXT & ~filters.COMMAND, order_step_qty)],
        ORDER_CONFIRM: [CallbackQueryHandler(order_confirm_cb, pattern="^o(confirm|cancel)$")],
    }))
    app.add_handler(conv("^order_status$", cb_order_status, {
        STATUS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_status_input)],
    }))
    app.add_handler(conv("^cancel_order$", cb_cancel_order, {
        CANCEL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cancel_order_input)],
    }))
    app.add_handler(conv("^refill$", cb_refill, {
        REFILL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, refill_input)],
    }))
    app.add_handler(conv("^refill_status$", cb_refill_status, {
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
    # Admin add service conversation
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_adm_addsvс, pattern="^adm_addsvс$")],
        states={
            ADMIN_ADD_SVC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_svc_input)],
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
        logger.info("🚀 Polling mode")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
