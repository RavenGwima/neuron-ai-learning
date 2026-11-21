import json
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import time
from datetime import date
from contextlib import contextmanager
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
logging.basicConfig(
    filename=str(BASE_DIR / "neuron-server.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS = {
    "OPENROUTER_CHAT_MODEL": "openai/gpt-6-luna",
    "OPENROUTER_QUESTION_MODEL": "deepseek/deepseek-v4.1-flash-1017",
}
MAX_BODY_SIZE = 16_384
SESSION_MAX_AGE = 60 * 60 * 24 * 30
PASSWORD_ITERATIONS = 310_000
COUNTRIES = {
    "ID": ("Indonesia", "Bahasa Indonesia"),
    "RU": ("Rusia", "bahasa Rusia"),
    "US": ("Amerika Serikat", "bahasa Inggris"),
    "GB": ("Britania Raya", "bahasa Inggris"),
    "JP": ("Jepang", "bahasa Jepang"),
    "KR": ("Korea Selatan", "bahasa Korea"),
    "CN": ("Tiongkok", "bahasa Mandarin"),
    "FR": ("Prancis", "bahasa Prancis"),
    "DE": ("Jerman", "bahasa Jerman"),
    "ES": ("Spanyol", "bahasa Spanyol"),
    "BR": ("Brasil", "bahasa Portugis"),
    "IN": ("India", "bahasa Hindi"),
    "SA": ("Arab Saudi", "bahasa Arab"),
    "EG": ("Mesir", "bahasa Arab"),
    "TR": ("Turki", "bahasa Turki"),
    "VN": ("Vietnam", "bahasa Vietnam"),
    "TH": ("Thailand", "bahasa Thai"),
}
DEFAULT_COUNTRY = "ID"
RANKS = ("SD", "SMP", "SMA", "Mahasiswa", "Pakar", "Ilmuwan")
RANK_DIFFICULTY = {
    "SD": "Konsep dasar dengan contoh konkret dan paling banyak satu langkah penalaran.",
    "SMP": "Penerapan konsep dengan satu atau dua langkah dan hubungan sebab-akibat sederhana.",
    "SMA": "Penerapan beberapa konsep atau rumus dengan penalaran bertahap.",
    "Mahasiswa": "Analisis abstrak atau penerapan beberapa konsep yang saling terkait.",
    "Pakar": "Kasus tingkat lanjut yang memerlukan analisis mendalam, strategi, atau evaluasi bukti; bukan soal definisi dasar.",
    "Ilmuwan": "Sintesis tingkat sangat lanjut, penilaian asumsi atau data, dan penalaran kompleks; tetap dapat dijawab dari informasi soal.",
}
QUESTION_ATTEMPTS = 2
GRADES = ("SD", "SMP", "SMA/SMK", "Kuliah")
SUBJECTS = {
    "math": "Matematika",
    "indonesian": "Bahasa Indonesia",
    "english": "Bahasa Inggris",
    "ipa": "IPA Terpadu",
    "physics": "Fisika",
    "chemistry": "Kimia",
    "biology": "Biologi",
    "history": "Sejarah",
    "geography": "Geografi",
    "economics": "Ekonomi",
    "sociology": "Sosiologi",
    "civics": "PPKn",
    "informatics": "Informatika",
    "religion": "Pendidikan Agama",
    "arabic": "Bahasa Arab",
    "arts": "Seni Budaya",
    "pe": "PJOK",
    "craft": "Prakarya",
    "regional": "Bahasa Daerah",
    "ips": "IPS Terpadu",
    "entrepreneurship": "Kewirausahaan",
}


def database_path():
    configured_path = os.environ.get("NEURON_DB_PATH", "").strip()
    return Path(configured_path or str(BASE_DIR / "neuron.sqlite3"))


@contextmanager
def connect_database():
    connection = sqlite3.connect(str(database_path()), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )


def normalize_email(email):
    if not isinstance(email, str):
        raise ValueError("Alamat email tidak valid.")
    email = email.strip().casefold()
    if len(email) > 254 or email.count("@") != 1:
        raise ValueError("Alamat email tidak valid.")
    local, domain = email.split("@")
    if not local or not domain or "." not in domain:
        raise ValueError("Alamat email tidak valid.")
    return email


def initialize_database():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                display_name TEXT NOT NULL,
                password_salt BLOB,
                password_hash BLOB,
                google_sub TEXT UNIQUE,
                role TEXT NOT NULL DEFAULT 'user',
                country TEXT NOT NULL DEFAULT 'ID',
                is_guest INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                csrf_token TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state_hash TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_progress (
                account_id TEXT PRIMARY KEY REFERENCES accounts(account_id) ON DELETE CASCADE,
                xp INTEGER NOT NULL DEFAULT 0,
                rank_points INTEGER NOT NULL DEFAULT 0,
                study_minutes INTEGER NOT NULL DEFAULT 0,
                streak INTEGER NOT NULL DEFAULT 0,
                last_study TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );
            """
        )
    admin_email = os.environ.get("NEURON_ADMIN_EMAIL", "").strip()
    admin_password = os.environ.get("NEURON_ADMIN_PASSWORD", "")
    if not admin_email and not admin_password:
        return
    if not admin_email or not admin_password:
        logging.warning("Admin bootstrap skipped: set both NEURON_ADMIN_EMAIL and NEURON_ADMIN_PASSWORD.")
        return
    try:
        admin_email = normalize_email(admin_email)
    except ValueError:
        logging.error("Admin bootstrap skipped: NEURON_ADMIN_EMAIL is invalid.")
        return
    if len(admin_password) < 12:
        logging.error("Admin bootstrap skipped: NEURON_ADMIN_PASSWORD must contain at least 12 characters.")
        return
    salt = secrets.token_bytes(16)
    with connect_database() as connection:
        existing = connection.execute(
            "SELECT account_id FROM accounts WHERE email = ?", (admin_email,)
        ).fetchone()
        if existing:
            connection.execute(
                "UPDATE accounts SET role = 'admin', is_guest = 0, password_salt = ?, password_hash = ? "
                "WHERE account_id = ?",
                (salt, password_digest(admin_password, salt), existing["account_id"]),
            )
        else:
            connection.execute(
                "INSERT INTO accounts (account_id, email, display_name, password_salt, password_hash, role, country, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'admin', ?, ?)",
                (
                    create_account_id(),
                    admin_email,
                    admin_email.split("@", 1)[0][:24],
                    salt,
                    password_digest(admin_password, salt),
                    DEFAULT_COUNTRY,
                    int(time.time()),
                ),
            )


def create_account_id():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "NRN-" + "".join(secrets.choice(alphabet) for _ in range(10))


def public_account(account):
    return {
        "id": account["account_id"],
        "email": account["email"],
        "name": account["display_name"],
        "role": account["role"],
        "country": account["country"],
        "guest": bool(account["is_guest"]),
    }


def progress_for_account(connection, account_id):
    progress = connection.execute(
        "SELECT xp, rank_points, study_minutes, streak, last_study, revision "
        "FROM account_progress WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    if progress is None:
        return {
            "xp": 0,
            "rank_points": 0,
            "study_minutes": 0,
            "streak": 0,
            "last_study": "",
            "revision": 0,
        }
    return dict(progress)


def progress_rank(rank_points):
    return RANKS[min(len(RANKS) - 1, rank_points // 100)]


def required_integer(data, name, minimum, maximum):
    value = data.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError("{} harus berupa angka dari {} sampai {}.".format(name, minimum, maximum))
    return value


def handle_progress_get(headers):
    session = current_session(headers)
    if session is None:
        return 401, {"error": "Masuk atau lanjutkan sebagai tamu terlebih dahulu."}
    with connect_database() as connection:
        return 200, {"progress": progress_for_account(connection, session["account_id"])}


def handle_progress_post(headers, body):
    session = current_session(headers)
    if session is None:
        return 401, {"error": "Masuk atau lanjutkan sebagai tamu terlebih dahulu."}
    if not csrf_valid(session, headers):
        return 403, {"error": "Token keamanan tidak valid. Muat ulang halaman."}
    try:
        data = parse_json_body(body)
        values = {
            "xp": required_integer(data, "xp", 0, 1_000_000_000),
            "rank_points": required_integer(data, "rank_points", 0, 1_000_000_000),
            "study_minutes": required_integer(data, "study_minutes", 0, 1_000_000_000),
            "streak": required_integer(data, "streak", 0, 365_000),
        }
        last_study = data.get("last_study", "")
        if not isinstance(last_study, str) or len(last_study) > 10:
            raise ValueError("Tanggal belajar tidak valid.")
        if last_study:
            try:
                date.fromisoformat(last_study)
            except ValueError:
                raise ValueError("Tanggal belajar harus menggunakan format YYYY-MM-DD.")
        revision = required_integer(data, "revision", 0, 2_147_483_647)
        now = int(time.time())
        with connect_database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revision FROM account_progress WHERE account_id = ?",
                (session["account_id"],),
            ).fetchone()
            if current is None:
                if revision != 0:
                    return 409, {"error": "Progres berubah di server. Muat ulang progres."}
                connection.execute(
                    "INSERT INTO account_progress "
                    "(account_id, xp, rank_points, study_minutes, streak, last_study, revision, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        session["account_id"],
                        values["xp"],
                        values["rank_points"],
                        values["study_minutes"],
                        values["streak"],
                        last_study,
                        now,
                    ),
                )
            else:
                if current["revision"] != revision:
                    return 409, {"error": "Progres berubah di server. Muat ulang progres."}
                connection.execute(
                    "UPDATE account_progress SET xp = ?, rank_points = ?, study_minutes = ?, "
                    "streak = ?, last_study = ?, revision = revision + 1, updated_at = ? "
                    "WHERE account_id = ? AND revision = ?",
                    (
                        values["xp"],
                        values["rank_points"],
                        values["study_minutes"],
                        values["streak"],
                        last_study,
                        now,
                        session["account_id"],
                        revision,
                    ),
                )
            return 200, {
                "progress": progress_for_account(connection, session["account_id"])
            }
    except ValueError as error:
        return 400, {"error": str(error)}


def handle_admin_accounts(headers):
    session = current_session(headers)
    if session is None or session["role"] != "admin":
        return 403, {"error": "Hanya admin yang dapat mengelola akun."}
    with connect_database() as connection:
        rows = connection.execute(
            "SELECT accounts.account_id, accounts.email, accounts.display_name, accounts.role, "
            "accounts.is_guest, COALESCE(account_progress.xp, 0) AS xp, "
            "COALESCE(account_progress.rank_points, 0) AS rank_points "
            "FROM accounts LEFT JOIN account_progress USING (account_id) "
            "ORDER BY accounts.display_name COLLATE NOCASE LIMIT 500"
        ).fetchall()
    return 200, {
        "accounts": [
            {
                "id": row["account_id"],
                "email": row["email"],
                "name": row["display_name"],
                "role": row["role"],
                "guest": bool(row["is_guest"]),
                "xp": row["xp"],
                "level": row["xp"] // 100 + 1,
                "rank_points": row["rank_points"],
                "rank": progress_rank(row["rank_points"]),
            }
            for row in rows
        ]
    }


def handle_admin_progress_post(headers, body):
    session = current_session(headers)
    if session is None or session["role"] != "admin":
        return 403, {"error": "Hanya admin yang dapat mengubah level dan rank."}
    if not csrf_valid(session, headers):
        return 403, {"error": "Token keamanan tidak valid. Muat ulang halaman."}
    try:
        data = parse_json_body(body)
        account_id = require_string(data, "account_id", 40)
        level = required_integer(data, "level", 1, 10_000_000)
        rank = require_string(data, "rank", 20)
        if rank not in RANKS:
            raise ValueError("Rank tidak dikenal.")
        with connect_database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT account_id FROM accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if account is None:
                return 404, {"error": "Akun tidak ditemukan."}
            current = progress_for_account(connection, account_id)
            rank_points = RANKS.index(rank) * 100
            xp = (level - 1) * 100
            now = int(time.time())
            connection.execute(
                "INSERT INTO account_progress "
                "(account_id, xp, rank_points, study_minutes, streak, last_study, revision, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET xp = excluded.xp, "
                "rank_points = excluded.rank_points, revision = excluded.revision, "
                "updated_at = excluded.updated_at",
                (
                    account_id,
                    xp,
                    rank_points,
                    current["study_minutes"],
                    current["streak"],
                    current["last_study"],
                    current["revision"] + 1,
                    now,
                ),
            )
            updated = progress_for_account(connection, account_id)
        return 200, {
            "account_id": account_id,
            "level": level,
            "rank": rank,
            "progress": updated,
        }
    except ValueError as error:
        return 400, {"error": str(error)}


def handle_leaderboard(headers, mode):
    session = current_session(headers)
    if session is None:
        return 401, {"error": "Masuk atau lanjutkan sebagai tamu terlebih dahulu."}
    if mode not in ("rank", "study"):
        return 400, {"error": "Mode leaderboard tidak dikenal."}
    order_by = (
        "COALESCE(progress.rank_points, 0) DESC, COALESCE(progress.xp, 0) DESC"
        if mode == "rank"
        else "COALESCE(progress.study_minutes, 0) DESC, COALESCE(progress.xp, 0) DESC"
    )
    with connect_database() as connection:
        rows = connection.execute(
            "SELECT accounts.account_id, accounts.display_name, "
            "COALESCE(progress.xp, 0) AS xp, COALESCE(progress.rank_points, 0) AS rank_points, "
            "COALESCE(progress.study_minutes, 0) AS study_minutes "
            "FROM accounts LEFT JOIN account_progress AS progress USING (account_id) "
            "WHERE accounts.role != 'admin' AND accounts.is_guest = 0 "
            "AND (COALESCE(progress.xp, 0) > 0 OR COALESCE(progress.rank_points, 0) > 0 "
            "OR COALESCE(progress.study_minutes, 0) > 0) "
            "ORDER BY {}, accounts.display_name COLLATE NOCASE LIMIT 100".format(order_by)
        ).fetchall()
    return 200, {
        "mode": mode,
        "players": [
            {
                "id": row["account_id"],
                "name": row["display_name"],
                "xp": row["xp"],
                "level": row["xp"] // 100 + 1,
                "rank_points": row["rank_points"],
                "rank": progress_rank(row["rank_points"]),
                "study_minutes": row["study_minutes"],
            }
            for row in rows
        ],
    }


def current_session(headers):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
    except CookieError:
        return None
    morsel = cookie.get("neuron_session")
    if morsel is None:
        return None
    token_hash = hashlib.sha256(morsel.value.encode("utf-8")).hexdigest()
    now = int(time.time())
    with connect_database() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        return connection.execute(
            "SELECT accounts.*, sessions.csrf_token, sessions.token_hash FROM sessions "
            "JOIN accounts USING (account_id) WHERE sessions.token_hash = ? AND sessions.expires_at >= ?",
            (token_hash, now),
        ).fetchone()


def create_session(account_id, headers):
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + SESSION_MAX_AGE
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect_database() as connection:
        connection.execute(
            "INSERT INTO sessions (token_hash, account_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, account_id, csrf_token, expires_at),
        )
        account = connection.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
    forwarded_proto = headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
    public_url = os.environ.get("PUBLIC_APP_URL", "")
    secure = forwarded_proto == "https" or urlsplit(public_url).scheme == "https"
    cookie = (
        "neuron_session={}; Path=/; HttpOnly; SameSite=Lax; Max-Age={}".format(
            token, SESSION_MAX_AGE
        )
    )
    if secure:
        cookie += "; Secure"
    return account, csrf_token, cookie


def csrf_valid(session, headers):
    supplied = headers.get("X-CSRF-Token", "")
    return bool(supplied and hmac.compare_digest(session["csrf_token"], supplied))


def parse_json_body(body):
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Permintaan harus berupa JSON yang valid.")
    if not isinstance(data, dict):
        raise ValueError("Format permintaan tidak valid.")
    return data


def new_session_result(account_id, headers):
    account, csrf_token, cookie = create_session(account_id, headers)
    return 200, {"user": public_account(account), "csrf_token": csrf_token}, [
        ("Set-Cookie", cookie)
    ]


def handle_auth_post(route, headers, body):
    try:
        data = parse_json_body(body) if body else {}
        if route == "/api/auth/guest":
            account_id = create_account_id()
            name = "Tamu " + account_id[-4:]
            with connect_database() as connection:
                connection.execute(
                    "INSERT INTO accounts (account_id, display_name, role, country, is_guest, created_at) "
                    "VALUES (?, ?, 'guest', ?, 1, ?)",
                    (account_id, name, DEFAULT_COUNTRY, int(time.time())),
                )
            return new_session_result(account_id, headers)

        if route == "/api/auth/register":
            email = normalize_email(data.get("email"))
            password = data.get("password")
            name = data.get("name", email.split("@", 1)[0])
            if not isinstance(password, str) or len(password) < 10 or len(password) > 256:
                raise ValueError("Kata sandi harus terdiri dari 10–256 karakter.")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 24:
                raise ValueError("Nama tampilan harus terdiri dari 1–24 karakter.")
            salt = secrets.token_bytes(16)
            account_id = create_account_id()
            role = "user"
            with connect_database() as connection:
                connection.execute(
                    "INSERT INTO accounts (account_id, email, display_name, password_salt, password_hash, role, country, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        account_id,
                        email,
                        name.strip(),
                        salt,
                        password_digest(password, salt),
                        role,
                        DEFAULT_COUNTRY,
                        int(time.time()),
                    ),
                )
            return new_session_result(account_id, headers)

        if route == "/api/auth/login":
            email = normalize_email(data.get("email"))
            password = data.get("password")
            if not isinstance(password, str) or len(password) > 256:
                raise ValueError("Email atau kata sandi salah.")
            with connect_database() as connection:
                account = connection.execute(
                    "SELECT account_id, password_salt, password_hash FROM accounts "
                    "WHERE email = ? AND is_guest = 0",
                    (email,),
                ).fetchone()
            if (
                account is None
                or account["password_salt"] is None
                or account["password_hash"] is None
                or not hmac.compare_digest(
                    password_digest(password, account["password_salt"]),
                    account["password_hash"],
                )
            ):
                return 401, {"error": "Email atau kata sandi salah."}, []
            return new_session_result(account["account_id"], headers)

        if route == "/api/auth/logout":
            session = current_session(headers)
            if session is None:
                return 401, {"error": "Sesi tidak ditemukan."}, []
            if not csrf_valid(session, headers):
                return 403, {"error": "Token keamanan tidak valid. Muat ulang halaman."}, []
            with connect_database() as connection:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?",
                    (session["token_hash"],),
                )
            forwarded_proto = headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
            secure = forwarded_proto == "https" or urlsplit(
                os.environ.get("PUBLIC_APP_URL", "")
            ).scheme == "https"
            cookie = "neuron_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
            if secure:
                cookie += "; Secure"
            return 200, {"ok": True}, [("Set-Cookie", cookie)]

        if route == "/api/account":
            session = current_session(headers)
            if session is None:
                return 401, {"error": "Masuk atau lanjutkan sebagai tamu terlebih dahulu."}, []
            if not csrf_valid(session, headers):
                return 403, {"error": "Token keamanan tidak valid. Muat ulang halaman."}, []
            name = data.get("name", session["display_name"])
            country = data.get("country", session["country"])
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 24:
                raise ValueError("Nama tampilan harus terdiri dari 1–24 karakter.")
            if not isinstance(country, str) or country not in COUNTRIES:
                raise ValueError("Negara tidak tersedia.")
            with connect_database() as connection:
                connection.execute(
                    "UPDATE accounts SET display_name = ?, country = ? WHERE account_id = ?",
                    (name.strip(), country, session["account_id"]),
                )
                account = connection.execute(
                    "SELECT * FROM accounts WHERE account_id = ?",
                    (session["account_id"],),
                ).fetchone()
            return 200, {"user": public_account(account)}, []

        return 404, {"error": "Rute autentikasi tidak ditemukan."}, []
    except ValueError as error:
        return 400, {"error": str(error)}, []
    except sqlite3.IntegrityError:
        return 409, {"error": "Email atau ID akun sudah digunakan."}, []


def google_oauth_configured():
    return bool(
        os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        and os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        and os.environ.get("PUBLIC_APP_URL", "").strip()
    )


def google_redirect_uri():
    public_url = os.environ.get("PUBLIC_APP_URL", "").strip().rstrip("/")
    if urlsplit(public_url).scheme != "https":
        raise RuntimeError("Login Google memerlukan PUBLIC_APP_URL HTTPS yang valid.")
    return public_url + "/api/auth/google/callback"


def google_start(headers):
    if not google_oauth_configured():
        return 503, {"error": "Login Google belum dikonfigurasi oleh admin."}, []
    state = secrets.token_urlsafe(32)
    with connect_database() as connection:
        connection.execute("DELETE FROM oauth_states WHERE expires_at < ?", (int(time.time()),))
        connection.execute(
            "INSERT INTO oauth_states (state_hash, expires_at) VALUES (?, ?)",
            (hashlib.sha256(state.encode("utf-8")).hexdigest(), int(time.time()) + 600),
        )
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    forwarded_proto = headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
    secure = forwarded_proto == "https" or urlsplit(
        os.environ.get("PUBLIC_APP_URL", "")
    ).scheme == "https"
    cookie = (
        "neuron_oauth_state={}; Path=/api/auth/google/callback; HttpOnly; "
        "SameSite=Lax; Max-Age=600"
    ).format(state)
    if secure:
        cookie += "; Secure"
    return (
        200,
        {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)},
        [("Set-Cookie", cookie)],
    )


def google_callback(query, headers):
    if not google_oauth_configured():
        raise ValueError("Login Google belum dikonfigurasi oleh admin.")
    state = query.get("state", [""])[0]
    code = query.get("code", [""])[0]
    if not state or not code:
        raise ValueError("Login Google dibatalkan atau responsnya tidak lengkap.")
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
    except CookieError:
        raise ValueError("Sesi login Google tidak valid. Silakan coba lagi.")
    state_cookie = cookie.get("neuron_oauth_state")
    if state_cookie is None or not hmac.compare_digest(state, state_cookie.value):
        raise ValueError("Sesi login Google tidak cocok dengan browser ini.")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    now = int(time.time())
    with connect_database() as connection:
        valid_state = connection.execute(
            "SELECT state_hash FROM oauth_states WHERE state_hash = ? AND expires_at >= ?",
            (state_hash, now),
        ).fetchone()
        if valid_state is None:
            raise ValueError("Sesi login Google kedaluwarsa. Silakan coba lagi.")
        connection.execute("DELETE FROM oauth_states WHERE state_hash = ?", (state_hash,))

    token_request = Request(
        "https://oauth2.googleapis.com/token",
        data=urlencode(
            {
                "code": code,
                "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"].strip(),
                "redirect_uri": google_redirect_uri(),
                "grant_type": "authorization_code",
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(token_request, timeout=20) as response:
            tokens = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Google tidak dapat menyelesaikan proses login. Coba lagi.")
    if not isinstance(tokens, dict):
        raise RuntimeError("Google tidak mengembalikan data token yang valid.")
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Google tidak mengembalikan token login yang valid.")
    profile_request = Request(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": "Bearer " + access_token},
    )
    try:
        with urlopen(profile_request, timeout=20) as response:
            profile = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Profil Google tidak dapat dibaca. Coba login lagi.")
    if not isinstance(profile, dict):
        raise RuntimeError("Google tidak mengembalikan profil akun yang valid.")
    if profile.get("email_verified") is not True:
        raise ValueError("Gunakan akun Google dengan email terverifikasi.")
    email = normalize_email(profile.get("email"))
    subject = profile.get("sub")
    name = profile.get("name", email.split("@", 1)[0])
    if not isinstance(subject, str) or not subject:
        raise ValueError("Google tidak mengirim identitas akun yang valid.")
    if not isinstance(name, str) or not name.strip():
        name = email.split("@", 1)[0]
    name = name.strip()[:24]
    admin_email = os.environ.get("NEURON_ADMIN_EMAIL", "").strip().casefold()
    admin_password = os.environ.get("NEURON_ADMIN_PASSWORD", "")
    role = (
        "admin"
        if email == admin_email and len(admin_password) >= 12
        else "user"
    )
    try:
        with connect_database() as connection:
            account = connection.execute(
                "SELECT account_id FROM accounts WHERE google_sub = ? OR email = ?",
                (subject, email),
            ).fetchone()
            if account:
                connection.execute(
                    "UPDATE accounts SET google_sub = ?, email = ?, display_name = ?, role = CASE WHEN ? = 'admin' THEN 'admin' ELSE role END, is_guest = 0 "
                    "WHERE account_id = ?",
                    (subject, email, name, role, account["account_id"]),
                )
                account_id = account["account_id"]
            else:
                account_id = create_account_id()
                connection.execute(
                    "INSERT INTO accounts (account_id, email, display_name, google_sub, role, country, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (account_id, email, name, subject, role, DEFAULT_COUNTRY, now),
                )
    except sqlite3.IntegrityError:
        raise ValueError("Email Google ini sudah terhubung ke akun lain.")
    return account_id


def load_dotenv():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, value = entry.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name and name not in os.environ:
            os.environ[name] = value


def openrouter_messages(messages, model_setting, json_mode=False, max_tokens=900):
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    model = os.environ.get(model_setting, "").strip() or DEFAULT_MODELS.get(model_setting, "")
    if not api_key or api_key.startswith("isi_dengan_") or not model:
        raise RuntimeError(
            "Server belum dikonfigurasi. Isi OPENROUTER_API_KEY dan {} melalui environment server.".format(model_setting)
        )
    if model.endswith(":batch"):
        raise RuntimeError(
            "{} memakai varian :batch yang berjalan asinkron. "
            "Gunakan ID model tanpa :batch untuk chat dan soal langsung.".format(model_setting)
        )

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.25 if json_mode else 0.7,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": (
                os.environ.get("PUBLIC_APP_URL")
                or os.environ.get("RENDER_EXTERNAL_URL")
                or "http://localhost:8000"
            ),
            "X-Title": "Neuron Belajar",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError("OpenRouter menolak permintaan (HTTP {}).".format(error.code))
    except URLError:
        raise RuntimeError("Tidak dapat terhubung ke OpenRouter. Periksa jaringan server.")
    except TimeoutError:
        raise RuntimeError("OpenRouter terlalu lama merespons. Coba lagi.")

    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Respons OpenRouter tidak memiliki jawaban yang dapat dibaca.")


def require_string(data, name, maximum):
    value = data.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("{} harus berupa teks 1–{} karakter.".format(name, maximum))
    return value.strip()


def parse_question_response(content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI mengirim jawaban kosong.")
    candidate = content.strip()
    try:
        generated = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Jawaban AI tidak berisi objek JSON.")
        try:
            generated = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as error:
            raise ValueError("JSON dari AI tidak dapat dibaca.") from error
    if not isinstance(generated, dict):
        raise ValueError("JSON soal dari AI harus berupa objek.")
    return generated


def normalize_question(generated, subject_id):
    generated_subject = generated.get("subjectId", generated.get("subject_id", subject_id))
    if generated_subject != subject_id:
        raise ValueError("AI mengirim soal yang tidak sesuai dengan mapel pilihan.")

    question = generated.get("question", generated.get("q"))
    choices = generated.get("choices", generated.get("options"))
    correct_index = generated.get("correctIndex", generated.get("correct_index"))
    if isinstance(correct_index, str) and correct_index.isdigit():
        correct_index = int(correct_index)
    if (
        not isinstance(question, str)
        or not question.strip()
        or len(question) > 1200
        or not isinstance(choices, list)
        or len(choices) != 4
        or any(not isinstance(choice, str) or not choice.strip() or len(choice) > 300 for choice in choices)
        or len({choice.strip().casefold() for choice in choices}) != 4
        or not isinstance(correct_index, int)
        or isinstance(correct_index, bool)
        or correct_index < 0
        or correct_index >= len(choices)
    ):
        raise ValueError("Struktur soal AI tidak sesuai format.")
    return {
        "subjectId": subject_id,
        "q": question.strip(),
        "a": [choice.strip() for choice in choices],
        "c": correct_index,
    }


def make_question(data, language="bahasa Indonesia"):
    subject_id = require_string(data, "subject_id", 40)
    if subject_id not in SUBJECTS:
        raise ValueError("Mata pelajaran tidak dikenal.")
    rank = require_string(data, "rank", 20)
    if rank not in RANKS:
        raise ValueError("Rank tidak dikenal.")
    recent = data.get("recent_questions", [])
    if not isinstance(recent, list) or any(not isinstance(item, str) for item in recent):
        raise ValueError("recent_questions harus berupa daftar teks.")
    recent = [item[:400] for item in recent[-20:]]
    subject = SUBJECTS[subject_id]

    prompt = (
        "Buat satu soal pilihan ganda baru yang hanya menguji mata pelajaran "
        + subject
        + ". Rank target: "
        + rank
        + ". Standar kesulitan wajib: "
        + RANK_DIFFICULTY[rank]
        + " Semakin tinggi rank, semakin sulit penalarannya; jangan menurunkan soal "
        "Pakar atau Ilmuwan menjadi pertanyaan hafalan tingkat SD. Tulis seluruh soal, "
        "opsi jawaban, dan teks yang terlihat pengguna dalam "
        + language
        + ". Jangan mencampurkan mata pelajaran lain. Hindari mengulang soal "
        "berikut: "
        + json.dumps(recent, ensure_ascii=False)
        + '. Kembalikan hanya satu objek JSON tanpa markdown, tepat dengan bentuk {"subjectId":"'
        + subject_id
        + '","question":"teks soal","choices":["opsi 1","opsi 2","opsi 3","opsi 4"],'
        '"correctIndex":0}. Harus tepat empat opsi berbeda dan hanya satu jawaban benar. '
        "correctIndex adalah indeks berbasis nol: 0 berarti opsi pertama, 3 berarti opsi keempat. "
        "Pastikan soal sesuai mapel dan rank, kunci jawaban benar secara faktual, serta pilihan salah masuk akal."
    )
    messages = [
        {
            "role": "system",
            "content": "Anda membuat soal pendidikan yang akurat. Ikuti bahasa keluaran, skema JSON, tingkat kesulitan, dan cakupan mapel secara ketat.",
        },
        {"role": "user", "content": prompt},
    ]
    validation_error = ""
    for attempt in range(QUESTION_ATTEMPTS):
        attempt_messages = list(messages)
        if attempt:
            attempt_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Buat ulang soalnya. Respons sebelumnya gagal validasi: "
                        + validation_error
                        + ". Keluarkan hanya JSON valid dengan empat opsi dan correctIndex berbasis nol."
                    ),
                }
            )
        try:
            content = openrouter_messages(
                attempt_messages,
                "OPENROUTER_QUESTION_MODEL",
                json_mode=True,
            )
            return normalize_question(parse_question_response(content), subject_id)
        except ValueError as error:
            validation_error = str(error)
            logging.warning(
                "Invalid generated question for %s/%s on attempt %s/%s: %s",
                subject_id,
                rank,
                attempt + 1,
                QUESTION_ATTEMPTS,
                validation_error,
            )
    raise RuntimeError("AI gagal membuat soal dengan format yang benar setelah dicoba ulang. Coba buat soal lagi.")


def make_question_batch(data, language="bahasa Indonesia"):
    subject_id = require_string(data, "subject_id", 40)
    if subject_id not in SUBJECTS:
        raise ValueError("Mata pelajaran tidak dikenal.")
    rank = require_string(data, "rank", 20)
    if rank not in RANKS:
        raise ValueError("Rank tidak dikenal.")
    recent = data.get("recent_questions", [])
    if not isinstance(recent, list) or any(not isinstance(item, str) for item in recent):
        raise ValueError("recent_questions harus berupa daftar teks.")
    recent = [item[:400] for item in recent[-20:]]
    subject = SUBJECTS[subject_id]
    prompt = (
        "Buat tepat 10 soal pilihan ganda yang unik dan berbeda untuk mata pelajaran "
        + subject
        + ". Rank target: "
        + rank
        + ". Standar kesulitan wajib: "
        + RANK_DIFFICULTY[rank]
        + " Tulis semua soal, opsi jawaban, dan teks yang terlihat pengguna dalam "
        + language
        + ". Hindari soal berikut dan jangan mengulang ide atau jawaban yang sama: "
        + json.dumps(recent, ensure_ascii=False)
        + '. Kembalikan hanya objek JSON dengan bentuk {"questions":[{"subjectId":"'
        + subject_id
        + '","question":"teks","choices":["opsi 1","opsi 2","opsi 3","opsi 4"],'
        '"correctIndex":0}, ...]}. Daftar harus berisi tepat 10 soal, masing-masing '
        "dengan empat opsi berbeda dan satu jawaban benar. correctIndex berbasis nol. "
        "Pastikan kunci faktual, soal berbeda satu sama lain, dan tingkat kesulitan konsisten."
    )
    messages = [
        {
            "role": "system",
            "content": "Anda membuat soal pendidikan yang akurat. Keluarkan hanya JSON valid sesuai skema dan jumlah soal yang diminta.",
        },
        {"role": "user", "content": prompt},
    ]
    validation_error = ""
    for attempt in range(QUESTION_ATTEMPTS):
        attempt_messages = list(messages)
        if attempt:
            attempt_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Buat ulang seluruh paket. Paket sebelumnya gagal validasi: "
                        + validation_error
                        + ". Keluarkan tepat 10 soal unik dalam JSON valid."
                    ),
                }
            )
        try:
            content = openrouter_messages(
                attempt_messages,
                "OPENROUTER_QUESTION_MODEL",
                json_mode=True,
                max_tokens=4500,
            )
            generated = parse_question_response(content)
            questions = generated.get("questions")
            if not isinstance(questions, list) or len(questions) != 10:
                raise ValueError("Paket AI harus berisi tepat 10 soal.")
            if any(not isinstance(question, dict) for question in questions):
                raise ValueError("Setiap soal dalam paket AI harus berupa objek JSON.")
            normalized = [
                normalize_question(question, subject_id) for question in questions
            ]
            texts = [question["q"].casefold() for question in normalized]
            if len(set(texts)) != 10:
                raise ValueError("Paket AI berisi soal yang berulang.")
            recent_texts = {question.casefold() for question in recent}
            if any(text in recent_texts for text in texts):
                raise ValueError("Paket AI mengulang soal dari sesi sebelumnya.")
            return {"questions": normalized}
        except ValueError as error:
            validation_error = str(error)
            logging.warning(
                "Invalid generated question batch for %s/%s on attempt %s/%s: %s",
                subject_id,
                rank,
                attempt + 1,
                QUESTION_ATTEMPTS,
                validation_error,
            )
    raise RuntimeError("AI gagal membuat paket 10 soal yang valid. Coba buat paket lagi.")


def tutor_reply(data, language="bahasa Indonesia"):
    subject_id = require_string(data, "subject_id", 40)
    if subject_id not in SUBJECTS:
        raise ValueError("Mata pelajaran tidak dikenal.")
    grade = require_string(data, "grade", 20)
    if grade not in GRADES:
        raise ValueError("Tingkat belajar tidak dikenal.")
    conversation = data.get("conversation", [])
    if not isinstance(conversation, list) or len(conversation) > 12:
        raise ValueError("Riwayat percakapan tidak valid.")

    messages = [
        {
            "role": "system",
            "content": (
                "Anda tutor belajar yang ramah dan teliti. Bantu murid tingkat "
                + grade
                + " memahami "
                + SUBJECTS[subject_id]
                + " saja. Jawab hanya dalam "
                + language
                + ". Jangan mengganti atau mencampur mata pelajaran. Jelaskan "
                "langkah demi langkah, akui jika tidak yakin, dan jangan mengarang fakta."
            ),
        }
    ]
    for item in conversation:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            messages.append({"role": item["role"], "content": content[:1500]})
    if not any(message["role"] == "user" for message in messages[1:]):
        raise ValueError("Kirim pertanyaan terlebih dahulu.")
    return {"reply": openrouter_messages(messages, "OPENROUTER_CHAT_MODEL")}


def health_status():
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    chat_model = os.environ.get("OPENROUTER_CHAT_MODEL", "").strip() or DEFAULT_MODELS["OPENROUTER_CHAT_MODEL"]
    question_model = os.environ.get("OPENROUTER_QUESTION_MODEL", "").strip() or DEFAULT_MODELS["OPENROUTER_QUESTION_MODEL"]
    configured = bool(
        api_key
        and not api_key.startswith("isi_dengan_")
        and not chat_model.endswith(":batch")
        and not question_model.endswith(":batch")
    )
    return {"ok": True, "configured": configured}


def request_origin_allowed(headers):
    origin = headers.get("Origin")
    if not origin:
        return True
    origin_url = urlsplit(origin)
    request_host = headers.get("Host", "").strip()
    local_origins = ("http://127.0.0.1:8000", "http://localhost:8000")
    same_origin = (
        origin_url.scheme in ("http", "https")
        and origin_url.netloc == request_host
    )
    return origin in local_origins or same_origin


def handle_api_post(route, headers, body):
    if route not in ("/api/questions", "/api/questions/batch", "/api/chat"):
        return 404, {"error": "Rute API tidak ditemukan."}

    if not request_origin_allowed(headers):
        return 403, {"error": "Permintaan lintas situs tidak diizinkan."}

    session = current_session(headers)
    if session is None:
        return 401, {"error": "Masuk atau lanjutkan sebagai tamu untuk memakai fitur AI."}
    language = COUNTRIES.get(session["country"], COUNTRIES[DEFAULT_COUNTRY])[1]
    try:
        data = parse_json_body(body)
        if route == "/api/questions":
            result = make_question(data, language)
        elif route == "/api/questions/batch":
            result = make_question_batch(data, language)
        else:
            result = tutor_reply(data, language)
        return 200, result
    except ValueError as error:
        return 400, {"error": str(error)}
    except RuntimeError as error:
        logging.error("%s: %s", route, error)
        return 502, {"error": str(error)}


class NeuronHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        self.handle_get(head_only=False)

    def do_HEAD(self):
        self.handle_get(head_only=True)

    def handle_get(self, head_only):
        path = urlsplit(self.path).path
        if path == "/api/health":
            body = json.dumps(health_status(), ensure_ascii=False).encode("utf-8")
            self.send_bytes(200, body, "application/json; charset=utf-8", head_only)
            return
        if path == "/api/auth/config":
            data = {
                "google_enabled": google_oauth_configured(),
                "countries": [
                    {"code": code, "name": country[0], "language": country[1]}
                    for code, country in COUNTRIES.items()
                ],
            }
            self.send_json(200, data, head_only=head_only)
            return
        if path == "/api/auth/me":
            session = current_session(self.headers)
            data = {
                "user": public_account(session) if session else None,
                "csrf_token": session["csrf_token"] if session else None,
            }
            self.send_json(200, data, head_only=head_only)
            return
        if path == "/api/auth/google/start":
            status, data, response_headers = google_start(self.headers)
            self.send_json(status, data, head_only=head_only, extra_headers=response_headers)
            return
        if path == "/api/progress":
            status, data = handle_progress_get(self.headers)
            self.send_json(status, data, head_only=head_only)
            return
        if path == "/api/leaderboard":
            query = parse_qs(urlsplit(self.path).query)
            status, data = handle_leaderboard(
                self.headers, query.get("mode", [""])[0]
            )
            self.send_json(status, data, head_only=head_only)
            return
        if path == "/api/admin/accounts":
            status, data = handle_admin_accounts(self.headers)
            self.send_json(status, data, head_only=head_only)
            return
        if path == "/api/auth/google/callback":
            query = parse_qs(urlsplit(self.path).query)
            forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
            secure = forwarded_proto == "https" or urlsplit(
                os.environ.get("PUBLIC_APP_URL", "")
            ).scheme == "https"
            expired_state_cookie = (
                "neuron_oauth_state=; Path=/api/auth/google/callback; "
                "HttpOnly; SameSite=Lax; Max-Age=0"
            )
            if secure:
                expired_state_cookie += "; Secure"
            try:
                account_id = google_callback(query, self.headers)
                _, _, cookie = create_session(account_id, self.headers)
                self.send_redirect(
                    "/",
                    [
                        ("Set-Cookie", cookie),
                        ("Set-Cookie", expired_state_cookie),
                    ],
                )
            except (ValueError, RuntimeError) as error:
                logging.warning("Google sign-in failed: %s", error)
                self.send_redirect(
                    "/?auth_error=google",
                    [("Set-Cookie", expired_state_cookie)],
                )
            return
        if path == "/api/admin/summary":
            session = current_session(self.headers)
            if session is None or session["role"] != "admin":
                self.send_json(403, {"error": "Hanya admin yang dapat membuka ringkasan ini."}, head_only=head_only)
                return
            with connect_database() as connection:
                summary = {
                    "accounts": connection.execute(
                        "SELECT COUNT(*) FROM accounts WHERE is_guest = 0"
                    ).fetchone()[0],
                    "guests": connection.execute(
                        "SELECT COUNT(*) FROM accounts WHERE is_guest = 1"
                    ).fetchone()[0],
                }
            self.send_json(200, summary, head_only=head_only)
            return
        if path in ("/", "/index.html"):
            self.send_bytes(
                200,
                (BASE_DIR / "index.html").read_bytes(),
                "text/html; charset=utf-8",
                head_only,
            )
            return
        self.send_json(404, {"error": "Halaman tidak ditemukan."}, head_only=head_only)

    def do_POST(self):
        route = urlsplit(self.path).path
        routes = (
            "/api/questions",
            "/api/questions/batch",
            "/api/chat",
            "/api/auth/guest",
            "/api/auth/register",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/account",
            "/api/progress",
            "/api/admin/progress",
        )
        if route not in routes:
            self.send_json(404, {"error": "Rute API tidak ditemukan."})
            return
        if not request_origin_allowed(self.headers):
            self.send_json(403, {"error": "Permintaan lintas situs tidak diizinkan."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY_SIZE or (route != "/api/auth/guest" and length == 0):
                raise ValueError("Ukuran permintaan harus di bawah 16 KB.")
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return
        body = self.rfile.read(length)
        if route in ("/api/auth/guest", "/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/account"):
            status, result, response_headers = handle_auth_post(route, self.headers, body)
        elif route == "/api/progress":
            status, result = handle_progress_post(self.headers, body)
            response_headers = []
        elif route == "/api/admin/progress":
            status, result = handle_admin_progress_post(self.headers, body)
            response_headers = []
        else:
            status, result = handle_api_post(route, self.headers, body)
            response_headers = []
        self.send_json(status, result, extra_headers=response_headers)

    def log_message(self, format, *args):
        logging.info("%s - %s", self.address_string(), format % args)

    def send_json(self, status, data, head_only=False, extra_headers=()):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_bytes(
            status,
            body,
            "application/json; charset=utf-8",
            head_only,
            extra_headers,
        )

    def send_bytes(self, status, body, content_type, head_only=False, extra_headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for header, value in extra_headers:
            self.send_header(header, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def send_redirect(self, location, extra_headers=()):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        for header, value in extra_headers:
            self.send_header(header, value)
        self.end_headers()


if __name__ == "__main__":
    load_dotenv()
    initialize_database()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), NeuronHandler)
    logging.info("Neuron berjalan di %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Server dihentikan.")
    finally:
        server.server_close()
