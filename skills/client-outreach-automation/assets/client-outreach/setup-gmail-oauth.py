#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.server
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


BASE_DIR = Path(__file__).resolve().parent
SCOPE = "https://www.googleapis.com/auth/gmail.compose"


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    server: "OAuthCallbackServer"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler.
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path != "/oauth2callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write("Not found".encode("utf-8"))
            return

        self.server.auth_code = params.get("code", [""])[0]
        self.server.auth_error = params.get("error", [""])[0]
        self.server.auth_state = params.get("state", [""])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            (
                "<!doctype html><meta charset='utf-8'>"
                "<title>Gmail OAuth complete</title>"
                "<body style='font-family: system-ui; padding: 32px;'>"
                "<h1>授權完成</h1>"
                "<p>可以回到終端機繼續。</p>"
                "</body>"
            ).encode("utf-8")
        )
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class OAuthCallbackServer(http.server.HTTPServer):
    auth_code = ""
    auth_error = ""
    auth_state = ""


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def prompt_secret(name: str, existing: str = "") -> str:
    if existing:
        return existing.strip()
    value = input(f"{name}: ").strip()
    if not value:
        raise SystemExit(f"{name} 不可空白。")
    return value


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict[str, str]:
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"OAuth code 換 refresh token 失敗：{detail}") from exc


def write_env(env_file: Path, client_id: str, client_secret: str, refresh_token: str) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "\n".join(
            [
                f"GMAIL_CLIENT_ID={client_id}",
                f"GMAIL_CLIENT_SECRET={client_secret}",
                f"GMAIL_REFRESH_TOKEN={refresh_token}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="建立 Gmail compose refresh token 並寫入 client-outreach/.env。")
    parser.add_argument("--client-id", default=os.environ.get("GMAIL_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.environ.get("GMAIL_CLIENT_SECRET", ""))
    parser.add_argument("--env-file", default=str(BASE_DIR / ".env"))
    parser.add_argument("--no-browser", action="store_true", help="不要自動開瀏覽器，只印出授權網址")
    args = parser.parse_args()

    client_id = prompt_secret("GMAIL_CLIENT_ID", args.client_id)
    client_secret = prompt_secret("GMAIL_CLIENT_SECRET", args.client_secret)
    env_file = Path(args.env_file).resolve()

    port = find_free_port()
    redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
    state = secrets.token_urlsafe(24)
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )

    print("請確認 Google OAuth client 允許 loopback redirect URI。")
    print(f"Redirect URI: {redirect_uri}")
    print("\n授權網址：")
    print(auth_url)

    server = OAuthCallbackServer(("127.0.0.1", port), OAuthCallbackHandler)
    if not args.no_browser:
        webbrowser.open(auth_url)

    print("\n等待瀏覽器授權回傳...")
    server.serve_forever()

    if server.auth_error:
        raise SystemExit(f"Google OAuth 授權失敗：{server.auth_error}")
    if server.auth_state != state:
        raise SystemExit("OAuth state 不一致，已停止。")
    if not server.auth_code:
        raise SystemExit("沒有收到 OAuth authorization code。")

    token_data = exchange_code(client_id, client_secret, server.auth_code, redirect_uri)
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise SystemExit(
            "Google 回應沒有 refresh_token。請確認授權畫面有重新同意，或刪除既有授權後再跑一次。"
        )

    write_env(env_file, client_id, client_secret, refresh_token)
    print(f"\n已寫入：{env_file}")
    print("接著可以執行：python3 client-outreach/run-outreach.py --confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
