"""
Run bot and web server together using asyncio + threading
"""
import asyncio
import threading
import os
from web import app
from bot import dp, bot

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

async def run_bot():
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Web server started")

    # Run bot in main thread
    print("✅ Bot started")
    asyncio.run(run_bot())
