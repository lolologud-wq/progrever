from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Все аккаунты", callback_data="accounts_list")],
        [InlineKeyboardButton("➕ Добавить аккаунт", callback_data="add_account")],
        [InlineKeyboardButton("🔥 Запустить прогрев сейчас", callback_data="run_cycle")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
    ])


def accounts_list_kb(accounts: list[dict]) -> InlineKeyboardMarkup:
    from database import get_status_emoji
    rows = []
    for acc in accounts:
        emoji = get_status_emoji(acc)
        label = f"{emoji} {acc['phone']}"
        if acc.get("first_name"):
            label += f" ({acc['first_name']})"
        rows.append([InlineKeyboardButton(label, callback_data=f"acc_{acc['id']}")])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def account_detail_kb(account_id: int, is_trusted: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📜 Логи", callback_data=f"logs_{account_id}"),
            InlineKeyboardButton("🔥 Прогреть", callback_data=f"warm_{account_id}"),
        ],
        [
            InlineKeyboardButton(
                "⭐ Снять трастовый" if is_trusted else "⭐ Сделать трастовым",
                callback_data=f"toggle_trust_{account_id}"
            ),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{account_id}"),
        ],
        [
            InlineKeyboardButton("📝 Смен. стратегию", callback_data=f"strategy_{account_id}"),
            InlineKeyboardButton("🔄 Обнов. инфо", callback_data=f"refresh_{account_id}"),
        ],
        [InlineKeyboardButton("🔙 К списку", callback_data="accounts_list")],
    ]
    return InlineKeyboardMarkup(rows)


def strategy_kb(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1️⃣ Мануал 1 (Трастовый)", callback_data=f"set_strategy_{account_id}_1"),
            InlineKeyboardButton("2️⃣ Мануал 2 (Автономный)", callback_data=f"set_strategy_{account_id}_2"),
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"acc_{account_id}")],
    ])


def confirm_delete_kb(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{account_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"acc_{account_id}"),
        ]
    ])


def back_to_account_kb(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 К аккаунту", callback_data=f"acc_{account_id}")]
    ])


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
    ])


def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕙 Расписание прогрева", callback_data="schedule_info")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
    ])
