from telegram.ext import ConversationHandler

# Conversation states for adding an account
AWAIT_PHONE = 1
AWAIT_SESSION = 2
AWAIT_STRATEGY = 3
AWAIT_TRUSTED = 4
