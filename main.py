import os
import requests

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
data = {"chat_id": CHAT_ID, "text": "✅ Test GitHub → Telegram OK"}

r = requests.post(url, json=data, timeout=20)
r.raise_for_status()
print("Sent OK")
