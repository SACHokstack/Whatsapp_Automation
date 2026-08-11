import time
import requests
from datetime import datetime

URL = "https://whatsapp-automation-dvax.onrender.com/"
INTERVAL = 10 * 60  # 10 minutes

while True:
    try:
        response = requests.get(URL, timeout=30)
        print(f"[{datetime.now()}] Status: {response.status_code}")
    except Exception as e:
        print(f"[{datetime.now()}] Error: {e}")

    time.sleep(INTERVAL)