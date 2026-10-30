import json
import time
import requests

# API endpoint (do not change)
API_BASE = "https://danger-guild-management-web.vercel.app/join"

# File containing the accounts list
ACCOUNTS_FILE = "accounts.json"


def load_accounts(filepath):
    """
    Load accounts from a JSON file.
    Expected format:
    [
        {"uid": "123456789", "password": "pass1"},
        {"uid": "987654321", "password": "pass2"}
    ]
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Support {"accounts": [...]} wrapper too
        if isinstance(data, dict) and "accounts" in data:
            data = data["accounts"]

        if not isinstance(data, list):
            raise ValueError("accounts.json must contain a JSON array of objects")

        return data

    except FileNotFoundError:
        print(f"[ERROR] {filepath} not found. Please create it first.")
        return []
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in {filepath}: {e}")
        return []


def join_guild(guild_id, uid, password, session=None):
    """
    Send a GET request to the join endpoint.
    Returns (success: bool, message: str).
    """
    url = f"{API_BASE}?guild_id={guild_id}&uid={uid}&password={password}"

    try:
        requester = session if session else requests
        resp = requester.get(url, timeout=15)

        try:
            body = resp.json()
        except ValueError:
            body = resp.text

        if resp.status_code == 200:
            return True, f"SUCCESS — {body}"
        else:
            return False, f"HTTP {resp.status_code} — {body}"

    except requests.exceptions.Timeout:
        return False, "Request timed out"
    except requests.exceptions.ConnectionError:
        return False, "Connection error"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def main():
    print("=" * 55)
    print("   Guild Join Bot — Danger Guild Management")
    print("=" * 55)

    # ---------- Ask for Guild ID ----------
    guild_id = input("Enter Guild ID : ").strip()
    if not guild_id:
        print("[ERROR] Guild ID cannot be empty. Exiting.")
        return

    # ---------- Load accounts ----------
    accounts = load_accounts(ACCOUNTS_FILE)
    if not accounts:
        print("[ERROR] No accounts loaded. Exiting.")
        return

    print("-" * 55)
    print(f"[INFO] Guild ID   : {guild_id}")
    print(f"[INFO] Accounts   : {len(accounts)} found")
    print("-" * 55)

    session = requests.Session()
    success_count = 0
    fail_count = 0

    for idx, acc in enumerate(accounts, start=1):
        uid = str(acc.get("uid", "")).strip()
        password = str(acc.get("password", "")).strip()

        if not uid or not password:
            print(f"[{idx}/{len(accounts)}] SKIPPED — missing uid or password")
            fail_count += 1
            continue

        masked_pw = password[:2] + "*" * (len(password) - 2) if len(password) > 2 else "***"
        print(f"[{idx}/{len(accounts)}] UID: {uid} | PW: {masked_pw} ... ", end="", flush=True)

        ok, msg = join_guild(guild_id, uid, password, session)

        if ok:
            success_count += 1
            print("✅ JOINED")
        else:
            fail_count += 1
            print(f"❌ FAILED — {msg}")

        time.sleep(0.5)

    print("-" * 55)
    print(f"[DONE] Success: {success_count} | Failed: {fail_count} | Total: {len(accounts)}")
    print("=" * 55)


if __name__ == "__main__":
    main()