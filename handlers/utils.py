from telegram import InlineKeyboardMarkup
from telegram.ext import ContextTypes

LAST_MSG_ID = "last_message_id"


async def delete_message_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> None:
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def send_or_replace_text(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    **kwargs
):
    chat_id = update.effective_chat.id
    last_id = context.user_data.get(LAST_MSG_ID)

    if last_id:
        await delete_message_safe(context, chat_id, last_id)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        **kwargs  # теперь можно передавать parse_mode и др.
    )
    context.user_data[LAST_MSG_ID] = msg.message_id
    return msg
