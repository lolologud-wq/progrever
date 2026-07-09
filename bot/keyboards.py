from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📋 Все аккаунты"), KeyboardButton("➕ Добавить аккаунт")],
            [KeyboardButton("📊 Сводка"),       KeyboardButton("🔥 Прогреть сейчас")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Все аккаунты",       callback_data="accounts_list")],
        [InlineKeyboardButton("➕ Добавить аккаунт",   callback_data="add_account")],
        [InlineKeyboardButton("📊 Сводка",             callback_data="svodka")],
        [InlineKeyboardButton("🔥 Прогреть сейчас",   callback_data="run_cycle")],
    ])


def accounts_list_kb(accounts: list[dict]) -> InlineKeyboardMarkup:
    from database import get_status_emoji
    rows = []
    for acc in accounts:
        emoji = get_status_emoji(acc)
        name = f"@{acc['username']}" if acc.get("username") else acc["phone"]
        score = acc.get("score", 0)
        label = f"{emoji} {name} {score}% ▶"
        rows.append([InlineKeyboardButton(label, callback_data=f"acc_{acc['id']}")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def account_detail_kb(
    acc_id: int,
    auto_on: bool,
    is_trusted: bool,
    hold_on: bool = True,
    show_run_now: bool = False,
) -> InlineKeyboardMarkup:
    auto_label  = "⏸ Авто ВЫКЛ" if auto_on else "▶️ Авто ВКЛ"
    trust_label = "🟣 Trusted ВЫКЛ" if is_trusted else "🟣 Trusted ВКЛ"
    hold_label  = "🛏 Холд ВЫКЛ" if hold_on else "🛏 Холд ВКЛ"

    rows = []
    if show_run_now:
        rows.append([
            InlineKeyboardButton("⚡ Выполнить сейчас", callback_data=f"run_now_{acc_id}"),
        ])

    hold_row = [InlineKeyboardButton(hold_label, callback_data=f"toggle_hold_{acc_id}")]
    if hold_on:
        hold_row.append(
            InlineKeyboardButton("🔄 Перезапуск холда", callback_data=f"do_hold_{acc_id}")
        )

    rows += [
        [
            InlineKeyboardButton("🤖 SpamBot",          callback_data=f"do_spambot_{acc_id}"),
            InlineKeyboardButton("📢 Вступить в канал", callback_data=f"do_join_{acc_id}"),
        ],
        [
            InlineKeyboardButton("💬 Сообщ. в группе",  callback_data=f"do_grpmsg_{acc_id}"),
            InlineKeyboardButton("📝 Обновить профиль", callback_data=f"do_profile_{acc_id}"),
        ],
        [
            InlineKeyboardButton("🏠 Создать группу",   callback_data=f"do_create_grp_{acc_id}"),
            InlineKeyboardButton("📺 Создать канал",    callback_data=f"do_channel_{acc_id}"),
        ],
        [
            InlineKeyboardButton("📬 Написать в канал", callback_data=f"do_chpost_{acc_id}"),
        ],
        hold_row,
        [
            InlineKeyboardButton(auto_label,             callback_data=f"toggle_auto_{acc_id}"),
            InlineKeyboardButton(trust_label,            callback_data=f"toggle_trust_{acc_id}"),
        ],
        [
            InlineKeyboardButton("🗑 Удалить",           callback_data=f"delete_{acc_id}"),
            InlineKeyboardButton("◀️ К аккаунтам",      callback_data="accounts_list"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def confirm_delete_kb(acc_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{acc_id}"),
            InlineKeyboardButton("❌ Отмена",      callback_data=f"acc_{acc_id}"),
        ]
    ])


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")]
    ])


def back_to_accounts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ К аккаунтам", callback_data="accounts_list")]
    ])


def back_to_account_kb(acc_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ К аккаунту", callback_data=f"acc_{acc_id}")]
    ])


def hold_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("0ч (без холда)", callback_data="hold_0"),
            InlineKeyboardButton("12ч",            callback_data="hold_12"),
        ],
        [
            InlineKeyboardButton("24ч",            callback_data="hold_24"),
            InlineKeyboardButton("48ч",            callback_data="hold_48"),
        ],
        [InlineKeyboardButton("✏️ Своё число часов", callback_data="hold_custom")],
    ])


def warmup_days_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("3 дня",  callback_data="warmup_3"),
            InlineKeyboardButton("5 дней", callback_data="warmup_5"),
            InlineKeyboardButton("7 дней", callback_data="warmup_7"),
        ],
        [
            InlineKeyboardButton("10 дней", callback_data="warmup_10"),
            InlineKeyboardButton("14 дней", callback_data="warmup_14"),
            InlineKeyboardButton("21 день", callback_data="warmup_21"),
        ],
        [InlineKeyboardButton("✏️ Своё число дней", callback_data="warmup_custom")],
    ])
