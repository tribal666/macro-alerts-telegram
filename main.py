import os
import requests

BOT_TOKEN = os.environ["8669894437:AAGZqV3WGybOafbE48tPVGxqVsn3TkKNjAg"]
CHAT_ID = os.environ["6526554977"]

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

data = {
    "chat_id": CHAT_ID,
    "text": "✅ Test GitHub → Telegram OK"
}

requests.post(url, json=data)
