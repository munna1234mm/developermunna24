"""
Local server for HIT Checker.
- Login works through the website UI (no terminal input needed)
- OTP sent via your own Telegram bot
- Proxies API calls to live backend after authentication
"""

import http.server
import http.cookiejar
import urllib.request
import urllib.error
import json
import os
import ssl
import random
import threading
import time

REMOTE_HOST = "https://hitchkr.replit.app"
PORT = int(os.environ.get("PORT", 8080))
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
# Rebranded Name
APP_NAME = "Nexvora Hitter"

# Your Telegram Bot Token
BOT_TOKEN = "8680374467:AAGin7F5co5ax8Y1wb6zdoZvVnUieaqz7x4"
ADMIN_ID = "8766583877"

# SSL context
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# Cookie jar for backend session
cookie_jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cookie_jar),
    urllib.request.HTTPSHandler(context=ssl_ctx)
)

# OTP storage: {telegram_id: otp_code}
otp_store = {}

# Session storage: {telegram_id: user_data}
sessions = {}
current_session_user = None


def send_telegram_message(chat_id, text):
    """Send a message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, context=ssl_ctx)
        result = json.loads(resp.read().decode())
        return result.get("ok", False)
    except Exception as e:
        print(f"  [!] Telegram error: {e}")
        return False


def try_backend_auth(user_id, otp):
    """Try to authenticate with the real backend too."""
    global current_session_user
    try:
        # Request OTP from backend
        url1 = REMOTE_HOST + "/api/auth/request-otp"
        body1 = json.dumps({"userId": user_id}).encode()
        req1 = urllib.request.Request(url1, data=body1, method="POST")
        req1.add_header("Content-Type", "application/json")
        req1.add_header("User-Agent", "Mozilla/5.0")
        req1.add_header("Origin", REMOTE_HOST)
        req1.add_header("Referer", REMOTE_HOST + "/autohitter")
        opener.open(req1)
    except:
        pass


def send_local_file(handler, path):
    """Try to serve from local scraped .html file."""
    local_file = os.path.join(STATIC_DIR, path.lstrip("/") + ".html")
    if os.path.isfile(local_file):
        with open(local_file, "r", encoding="utf-8") as f:
            content = f.read()
        body = content.encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return True
    return False


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        content_length = self.headers.get("Content-Length")
        if content_length:
            raw = self.rfile.read(int(content_length))
            try:
                return json.loads(raw.decode())
            except:
                return {}
        return {}

    def _proxy(self, method, body=None):
        global current_session_user
        """Proxy to live backend."""
        url = REMOTE_HOST + self.path
        
        if body is None:
            content_length = self.headers.get("Content-Length")
            if content_length:
                body = self.rfile.read(int(content_length))

        req = urllib.request.Request(url, data=body, method=method)
        for h in ["Content-Type", "Accept"]:
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        req.add_header("Origin", REMOTE_HOST)
        req.add_header("Referer", REMOTE_HOST + "/autohitter")

        try:
            resp = opener.open(req)
            resp_body = resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "connection", "content-encoding", "content-length"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            resp_body = e.read()
            if e.code == 401:
                current_session_user = None
                print("  [AUTH] Backend session expired (401). Local session cleared.")
            
            # Only fall back to local files for GET requests (read-only data)
            # POST/PUT/DELETE are action endpoints - never use static mock data
            if e.code in (401, 403) and method == "GET":
                path_clean = self.path.split("?")[0]
                if send_local_file(self, path_clean):
                    return
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _handle_api(self, method):
        global current_session_user
        path = self.path.split("?")[0]

        # ---- AUTH: Session check ----
        if path == "/api/auth/session":
            if current_session_user:
                # OPTIONAL: We could verify with backend here, but it might slow down every page load
                # For now, we trust the local session unless a proxy call fails
                self._send_json({"authenticated": True, "user": current_session_user})
            else:
                # If no local user, check if backend already has a session (maybe from cookies)
                # This makes it "work well" when the script is restarted but cookies remain
                try:
                    url = REMOTE_HOST + "/api/auth/session"
                    req = urllib.request.Request(url)
                    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
                    resp = opener.open(req, timeout=5)
                    data = json.loads(resp.read().decode())
                    if data.get("authenticated"):
                        current_session_user = data.get("user")
                        self._send_json({"authenticated": True, "user": current_session_user})
                        print(f"  [AUTH] Restored session from backend for {current_session_user.get('firstName')}")
                    else:
                        self._send_json({"authenticated": False})
                except:
                    self._send_json({"authenticated": False})
            return

        # ---- AUTH: Request OTP ----
        if path == "/api/auth/request-otp" and method == "POST":
            # Proxy to real backend to trigger their OTP
            body_dict = self._read_body()
            uid = body_dict.get("userId", "")
            body_json = json.dumps(body_dict).encode()
            
            # Reset current user on new OTP request
            current_session_user = None
            
            # Reuse _proxy but pass pre-read body
            self._proxy(method, body=body_json)
            
            if uid:
                send_telegram_message(uid, f"OTP অফিশিয়াল @HitChkBot থেকে পাঠানো হয়েছে। সেটি কপি করে এখানে দিন।\nসহায়তার জন্য: @autohittrobot")
            return

        # ---- AUTH: Verify OTP ----
        if path == "/api/auth/verify-otp" and method == "POST":
            url = REMOTE_HOST + self.path
            body_dict = self._read_body()
            body_json = json.dumps(body_dict).encode()
            req = urllib.request.Request(url, data=body_json, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            req.add_header("Origin", REMOTE_HOST)
            req.add_header("Referer", REMOTE_HOST + "/autohitter")
            
            try:
                resp = opener.open(req)
                resp_body = resp.read()
                data = json.loads(resp_body.decode())
                
                if data.get("success"):
                    current_session_user = data.get("user")
                    print(f"  [AUTH] Backend success for {current_session_user.get('firstName')}")
                
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ("transfer-encoding", "connection", "content-encoding", "content-length"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
            except urllib.error.HTTPError as e:
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                resp_body = e.read()
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
            except Exception as e:
                self._send_json({"success": False, "message": str(e)}, 500)
            return

        # ---- AUTH: Logout ----
        if path == "/api/auth/logout":
            current_session_user = None
            # Clear cookies
            cookie_jar.clear()
            self._send_json({"success": True, "message": "Logged out successfully"})
            print("  [AUTH] User logged out locally and cookies cleared.")
            return

        # ---- All other API calls ----
        self._proxy(method)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._handle_api("GET")
        else:
            # Check if the path maps to an actual file
            path = self.path.split("?")[0]
            file_path = os.path.join(STATIC_DIR, path.lstrip("/"))

            if os.path.isfile(file_path) or path == "/":
                super().do_GET()
            else:
                # SPA fallback: serve index.html for all routes
                # so React router can handle client-side navigation
                self.path = "/index.html"
                super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._handle_api("POST")
        else:
            self.send_error(405)

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._handle_api("PUT")
        else:
            self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._handle_api("DELETE")
        else:
            self.send_error(405)

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            self._handle_api("PATCH")
        else:
            self.send_error(405)


if __name__ == "__main__":
    import threading
    import time

    def bot_polling():
        """Poll Telegram for /start commands and reply with chat ID."""
        bot_api = f"https://api.telegram.org/bot{BOT_TOKEN}"
        offset = 0

        # Clear old updates first
        try:
            clear_url = f"{bot_api}/getUpdates?offset=-1&timeout=0"
            req = urllib.request.Request(clear_url)
            resp = urllib.request.urlopen(req, context=ssl_ctx)
            data = json.loads(resp.read().decode())
            if data.get("ok") and data.get("result"):
                offset = data["result"][-1]["update_id"] + 1
        except:
            pass

        print("  [BOT] Polling started - bot is listening for /start")

        while True:
            try:
                url = f"{bot_api}/getUpdates?offset={offset}&timeout=30"
                req = urllib.request.Request(url)
                resp = urllib.request.urlopen(req, context=ssl_ctx, timeout=35)
                data = json.loads(resp.read().decode())

                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message", {})
                        text = msg.get("text", "")
                        chat = msg.get("chat", {})
                        chat_id = chat.get("id")
                        first_name = chat.get("first_name", "User")

                        if not chat_id:
                            continue

                        if text == "/start":
                            reply = (
                                f"Welcome to Nexvora Hitter Bot, {first_name}!\n\n"
                                f"Your Telegram Chat ID:\n"
                                f"`{chat_id}`\n\n"
                                f"Tap the ID above to copy it,\n"
                                f"then paste it on the login page."
                            )
                            send_data = json.dumps({
                                "chat_id": chat_id,
                                "text": reply,
                                "parse_mode": "Markdown"
                            }).encode()
                            send_req = urllib.request.Request(
                                f"{bot_api}/sendMessage",
                                data=send_data, method="POST"
                            )
                            send_req.add_header("Content-Type", "application/json")
                            urllib.request.urlopen(send_req, context=ssl_ctx)
                            print(f"  [BOT] /start from {first_name} ({chat_id})")

                        elif text == "/admin":
                            if str(chat_id) != ADMIN_ID:
                                reply = "Access Denied. You are not the admin."
                            else:
                                reply = (
                                    f"Admin Panel - {APP_NAME}\n\n"
                                    f"Chat ID: `{chat_id}`\n"
                                    f"Name: {first_name}\n"
                                    f"Status: Active"
                                )
                            send_data = json.dumps({
                                "chat_id": chat_id,
                                "text": reply,
                                "parse_mode": "Markdown"
                            }).encode()
                            send_req = urllib.request.Request(
                                f"{bot_api}/sendMessage",
                                data=send_data, method="POST"
                            )
                            send_req.add_header("Content-Type", "application/json")
                            urllib.request.urlopen(send_req, context=ssl_ctx)

            except Exception as e:
                time.sleep(3)

    # Update bot username API response (in background)
    def update_bot_username():
        bot_username_file = os.path.join(STATIC_DIR, "api", "bot", "username.html")
        try:
            bot_info_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
            req = urllib.request.Request(bot_info_url)
            resp = urllib.request.urlopen(req, context=ssl_ctx, timeout=10)
            bot_data = json.loads(resp.read().decode())
            if bot_data.get("ok"):
                bot_uname = bot_data["result"].get("username", "autohitterrobot")
                with open(bot_username_file, "w", encoding="utf-8") as f:
                    json.dump({"username": bot_uname}, f)
                print(f"  [BOT] Username: @{bot_uname}")
        except Exception as e:
            print(f"  [BOT] Could not fetch bot info: {e}")

    # Self-ping to stay awake on Render free tier
    def keep_alive():
        url = os.environ.get("RENDER_EXTERNAL_URL")
        if not url:
            url = f"http://127.0.0.1:{PORT}/"
        
        while True:
            try:
                time.sleep(600)  # 10 minutes
                req = urllib.request.Request(url)
                req.add_header("User-Agent", "KeepAlive-Bot")
                urllib.request.urlopen(req, timeout=10)
                # print(f"  [KEEP-ALIVE] Pinged {url}")
            except:
                pass

    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[*] {APP_NAME} running at http://127.0.0.1:{PORT}/")
    print(f"    Login: Via website (OTP from your bot)")
    print("    Press Ctrl+C to stop")

    # Start bot tasks in background so server starts immediately
    def start_bot():
        update_bot_username()
        # Start keep-alive thread
        threading.Thread(target=keep_alive, daemon=True).start()
        bot_polling()

    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()

