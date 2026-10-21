"""Administrator-provisioned accounts and revocable server-side sessions."""
from contextlib import contextmanager
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from .repository import ApiError

COOKIE_NAME = "pump_console_session"
SECURE_COOKIE_NAME = "__Host-pump_console_session"
_DUMMY = None
_DUMMY_LOCK = threading.Lock()


def validate_password(password):
    if (not isinstance(password, str) or len(password) <= 8 or
            not re.search(r"[a-z]", password) or not re.search(r"[0-9]", password)):
        raise ApiError(422, "密码须大于 8 位，并包含小写字母和数字。", "password_policy")
    if len(password) > 128:
        raise ApiError(422, "密码最多支持 128 个字符。", "password_policy")
    try:
        password.encode("utf-8")
    except UnicodeError:
        raise ApiError(422, "密码包含无法使用的字符。", "password_policy")


def password_hash(password):
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=131072, r=8, p=1,
                             dklen=32, maxmem=268435456)
    return "scrypt$131072$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(derived).decode()


def password_matches(password, encoded):
    try:
        kind, n, r, p, salt, expected = encoded.split("$")
        if (kind, n, r, p) != ("scrypt", "131072", "8", "1"):
            return False
        derived = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt, validate=True),
                                 n=131072, r=8, p=1, dklen=32, maxmem=268435456)
        return hmac.compare_digest(derived, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError, UnicodeError):
        return False


def username_value(username):
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", username):
        raise ApiError(422, "用户名须为字母开头的英文、数字、下划线或短横线。", "invalid_username")
    return username.lower()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class AuthStore:
    def __init__(self, path, policy=None, now=None):
        global _DUMMY
        self.path = Path(path)
        self.policy = {"idle_timeout_seconds": 1800, "absolute_timeout_seconds": 28800,
                       "login_window_seconds": 900, "login_max_failures": 8, **(policy or {})}
        self.now = now or time.time
        self.lock = threading.RLock()
        self.hash_slots = threading.BoundedSemaphore(2)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self.db() as db:
            db.executescript("""
CREATE TABLE IF NOT EXISTS users (
 username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL,
 must_change INTEGER NOT NULL, temporary_expires REAL);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL,
 created_at REAL NOT NULL, last_seen REAL NOT NULL,
 FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS login_limits (
 scope TEXT PRIMARY KEY, failures INTEGER NOT NULL, reset_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
 event TEXT NOT NULL, username TEXT NOT NULL, peer TEXT NOT NULL);
""")
        with _DUMMY_LOCK:
            if _DUMMY is None:
                _DUMMY = password_hash(secrets.token_urlsafe(24))
        self.dummy = _DUMMY

    @contextmanager
    def db(self):
        with self.lock:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    def audit(self, db, event, username, peer):
        db.execute("INSERT INTO audit(created_at,event,username,peer) VALUES(?,?,?,?)",
                   (self.now(), event, username[:64], peer[:64]))
        db.execute("DELETE FROM audit WHERE id <= (SELECT COALESCE(MAX(id),0)-10000 FROM audit)")

    def encode_password(self, password):
        if not self.hash_slots.acquire(blocking=False):
            raise ApiError(429, "请求繁忙，请稍后重试。", "login_throttled")
        try:
            return password_hash(password)
        finally:
            self.hash_slots.release()

    def list_users(self):
        with self.db() as db:
            return [{"username": row["username"], "role": row["role"],
                     "must_change_password": bool(row["must_change"]),
                     "temporary_password_expires_at": row["temporary_expires"]}
                    for row in db.execute("SELECT username,role,must_change,temporary_expires FROM users ORDER BY username")]

    def create_user(self, username, password, role="admin", temporary=True, actor=None, peer="ssh"):
        username = username_value(username)
        validate_password(password)
        if role not in ("admin", "viewer"):
            raise ApiError(422, "账号权限须为 admin 或 viewer。", "invalid_role")
        encoded = self.encode_password(password)
        with self.db() as db:
            if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ApiError(409, "账号已存在。", "account_exists")
            db.execute("INSERT INTO users VALUES(?,?,?,?,?)",
                       (username, encoded, role, int(temporary), self.now() + 86400 if temporary else None))
            self.audit(db, "account_created/" + username if actor else "account_created", actor or username, peer)

    def _limits(self, db, scopes):
        now = self.now()
        db.execute("DELETE FROM login_limits WHERE reset_at<=?", (now,))
        for scope, maximum in scopes:
            row = db.execute("SELECT failures FROM login_limits WHERE scope=?", (scope,)).fetchone()
            if row and row["failures"] >= maximum:
                raise ApiError(429, "尝试次数过多，请稍后再试。", "login_throttled")

    def login(self, username, password, peer):
        raw_name = username if isinstance(username, str) else ""
        name = raw_name.lower()[:64]
        valid_shape = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", raw_name))
        scopes = [("user:" + name, self.policy["login_max_failures"]), ("peer:" + peer, 30)]
        with self.db() as db:
            self._limits(db, scopes)
            user = db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone() if valid_shape else None
        if not self.hash_slots.acquire(blocking=False):
            raise ApiError(429, "登录请求繁忙，请稍后重试。", "login_throttled")
        try:
            valid_password = isinstance(password, str) and len(password) <= 128
            matched = password_matches(password if valid_password else "", user["password_hash"] if user else self.dummy)
        finally:
            self.hash_slots.release()
        now = self.now()
        valid = bool(user and valid_password and matched and
                     (not user["temporary_expires"] or now < user["temporary_expires"]))
        with self.db() as db:
            self._limits(db, scopes)
            if not valid:
                for scope, _ in scopes:
                    db.execute("INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET failures=failures+1",
                               (scope, 1, now + self.policy["login_window_seconds"]))
                self.audit(db, "login_failed", name, peer)
            else:
                # A password reset racing with verification cannot authenticate the old credential.
                current = db.execute("SELECT password_hash FROM users WHERE username=?", (name,)).fetchone()
                if not current or current["password_hash"] != user["password_hash"]:
                    raise ApiError(401, "用户名或密码不正确。", "invalid_credentials")
                db.execute("DELETE FROM login_limits WHERE scope=?", ("user:" + name,))
                db.execute("DELETE FROM sessions WHERE created_at<=? OR last_seen<=?",
                           (now-self.policy["absolute_timeout_seconds"], now-self.policy["idle_timeout_seconds"]))
                db.execute("DELETE FROM sessions WHERE token_hash IN (SELECT token_hash FROM sessions WHERE username=? ORDER BY created_at DESC LIMIT -1 OFFSET 4)", (name,))
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (token_hash(token), name, csrf, now, now))
                self.audit(db, "login_succeeded", name, peer)
        if not valid:
            raise ApiError(401, "用户名或密码不正确。", "invalid_credentials")
        return token, self.session(token, allow_password_change=True)

    def session(self, token, touch=False, allow_password_change=False):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ApiError(401, "请登录后继续。", "authentication_required")
        digest, now = token_hash(token), self.now()
        with self.db() as db:
            row = db.execute("""SELECT s.*,u.role,u.must_change FROM sessions s
JOIN users u ON u.username=s.username WHERE s.token_hash=?""", (digest,)).fetchone()
            if row and (now-row["created_at"] >= self.policy["absolute_timeout_seconds"] or
                        now-row["last_seen"] >= self.policy["idle_timeout_seconds"] or now < row["created_at"]-5):
                db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
                row = None
            if row is None:
                result = None
            elif row["must_change"] and not allow_password_change:
                raise ApiError(403, "请先修改初始密码。", "password_change_required")
            else:
                if touch:
                    db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, digest))
                result = {"user": {"username": row["username"], "role": row["role"],
                             "must_change_password": bool(row["must_change"])},
                    "csrf_token": row["csrf"], "authenticated": True,
                    "idle_expires_at": (now if touch else row["last_seen"]) + self.policy["idle_timeout_seconds"],
                    "absolute_expires_at": row["created_at"] + self.policy["absolute_timeout_seconds"]}
        if result is None:
            raise ApiError(401, "登录已失效，请重新登录。", "authentication_required")
        return result

    def csrf(self, session, supplied):
        if not isinstance(supplied, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied) or not hmac.compare_digest(session["csrf_token"], supplied):
            raise ApiError(403, "安全校验失效，请刷新页面后重试。", "csrf_failed")

    def logout(self, token):
        with self.db() as db:
            row = db.execute("SELECT username FROM sessions WHERE token_hash=?", (token_hash(token),)).fetchone()
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
            if row:
                self.audit(db, "logout", row["username"], "browser")

    def change_password(self, token, current_password, new_password):
        session = self.session(token, allow_password_change=True)
        validate_password(new_password)
        name = session["user"]["username"]
        scopes = [("password:" + name, self.policy["login_max_failures"])]
        with self.db() as db:
            self._limits(db, scopes)
            old = db.execute("SELECT password_hash FROM users WHERE username=?", (name,)).fetchone()["password_hash"]
        if not self.hash_slots.acquire(blocking=False):
            raise ApiError(429, "请求繁忙，请稍后重试。", "login_throttled")
        try:
            valid = isinstance(current_password, str) and len(current_password)<=128 and password_matches(current_password, old)
            if valid:
                encoded = password_hash(new_password)
        finally:
            self.hash_slots.release()
        if not valid:
            with self.db() as db:
                db.execute("INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET failures=failures+1",
                           (scopes[0][0], 1, self.now()+self.policy["login_window_seconds"]))
                self.audit(db, "password_change_failed", name, "browser")
            raise ApiError(403, "当前密码不正确。", "invalid_current_password")
        if hmac.compare_digest(current_password.encode(), new_password.encode()):
            raise ApiError(422, "新密码须与当前密码不同。", "password_policy")
        with self.db() as db:
            if db.execute("SELECT password_hash FROM users WHERE username=?", (name,)).fetchone()["password_hash"] != old:
                raise ApiError(409, "账号凭据已更新，请重新登录。", "credential_changed")
            db.execute("UPDATE users SET password_hash=?,must_change=0,temporary_expires=NULL WHERE username=?", (encoded, name))
            db.execute("DELETE FROM sessions WHERE username=?", (name,))
            db.execute("DELETE FROM login_limits WHERE scope=?", (scopes[0][0],))
            self.audit(db, "password_changed", name, "browser")

    def reset_password(self, username, password, temporary=True, actor=None, peer="ssh"):
        username = username_value(username)
        validate_password(password)
        encoded = self.encode_password(password)
        with self.db() as db:
            if not db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ApiError(404, "账号不存在。", "account_not_found")
            db.execute("UPDATE users SET password_hash=?,must_change=?,temporary_expires=? WHERE username=?",
                       (encoded, int(temporary), self.now()+86400 if temporary else None, username))
            db.execute("DELETE FROM sessions WHERE username=?", (username,))
            db.execute("DELETE FROM login_limits WHERE scope=?", ("user:"+username,))
            db.execute("DELETE FROM login_limits WHERE scope=?", ("password:"+username,))
            self.audit(db, "password_reset/" + username if actor else "password_reset", actor or username, peer)


def main():
    parser = argparse.ArgumentParser(description="Manage local console accounts through SSH")
    parser.add_argument("action", choices=("add-user", "reset-password"))
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=("admin", "viewer"), default="viewer")
    parser.add_argument("--database", type=Path, default=Path(__file__).resolve().parents[2]/"data/security/auth.sqlite3")
    args = parser.parse_args()
    password = getpass.getpass("Temporary password (>8 characters, include a-z and 0-9): ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    store = AuthStore(args.database)
    if args.action == "add-user":
        store.create_user(args.username, password, role=args.role)
    else:
        store.reset_password(args.username, password)
    print("Account updated; existing sessions revoked where applicable; password change required at next login.")


if __name__ == "__main__":
    main()
