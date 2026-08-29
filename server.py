import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

import stripe
from dotenv import load_dotenv
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()  # ローカル開発用。本番（Render）では.envは存在せず、Variablesがそのまま使われる。

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024  # 200KB で十分。壊れた巨大データは弾く

app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("SECRET_KEY"):
    app.logger.warning(
        "SECRET_KEY未設定：開発用の一時キーで起動します。"
        "本番環境ではRenderのVariablesにSECRET_KEYを設定してください（未設定だと再起動のたびに全員ログアウトされます）。"
    )

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
if not stripe.api_key:
    app.logger.warning(
        "STRIPE_SECRET_KEY未設定：決済（アップグレード）は動作しません。"
        "Stripeのテストモードのシークレットキーを環境変数に設定してください。"
    )

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
if not STRIPE_WEBHOOK_SECRET:
    app.logger.warning(
        "STRIPE_WEBHOOK_SECRET未設定：Stripeからの通知（解約など）を受け取れません。"
        "StripeのWebhookエンドポイント設定画面で発行される署名シークレットを環境変数に設定してください。"
    )

DB_PATH = Path(__file__).parent / "cases.db"
URGENT_DAYS = 3  # これ以上未入力が続いたら「要対応」として赤く表示

PATIENT_LABEL_MAX = 20
DOCTOR_NAME_MAX = 20
ITEM_MAX = 50
PASSWORD_MIN = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VERIFY_TOKEN_HOURS = 24
RESET_TOKEN_HOURS = 1

# ---------------------------------------------------------------------------
# プラン設計（第18回・課金①）
#   フリー：案件を累計20件まで登録可能（お試し用）
#   プロ　：月980円で案件登録が無制限
# 招待コード等の複雑さは持ち込まず、まずはこの2プランのみ。
# ---------------------------------------------------------------------------
PLAN_FREE = "free"
PLAN_PRO = "pro"
FREE_PLAN_CASE_LIMIT = 20
PRO_PLAN_PRICE_JPY = 980


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def column_exists(db, table, column):
    cols = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
    return column in cols


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                plan TEXT NOT NULL DEFAULT 'free',
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                patient_label TEXT NOT NULL,
                doctor_name TEXT NOT NULL,
                surgery_date TEXT NOT NULL,
                item TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '未入力',
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                memo TEXT,
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'staff' CHECK (role IN ('admin', 'staff')),
                email_verified INTEGER NOT NULL DEFAULT 0,
                verification_token TEXT,
                verification_expires TEXT,
                reset_token TEXT,
                reset_expires TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
            )
            """
        )
        # 既存のcases.db（memo列・tenant_id列がまだ無い状態）にも安全に追従させる
        if not column_exists(db, "cases", "memo"):
            db.execute("ALTER TABLE cases ADD COLUMN memo TEXT")
        if not column_exists(db, "cases", "tenant_id"):
            db.execute("ALTER TABLE cases ADD COLUMN tenant_id INTEGER")
        if not column_exists(db, "tenants", "plan"):
            db.execute("ALTER TABLE tenants ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'")
        if not column_exists(db, "tenants", "stripe_customer_id"):
            db.execute("ALTER TABLE tenants ADD COLUMN stripe_customer_id TEXT")
        if not column_exists(db, "tenants", "stripe_subscription_id"):
            db.execute("ALTER TABLE tenants ADD COLUMN stripe_subscription_id TEXT")

        tenant_count = db.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
        if tenant_count == 0:
            seed_demo_tenants(db)

        # tenant_id未設定の既存データは、移行時に混ざらないよう先頭テナントに寄せる
        default_tenant_id = db.execute("SELECT id FROM tenants ORDER BY id LIMIT 1").fetchone()[0]
        db.execute("UPDATE cases SET tenant_id = ? WHERE tenant_id IS NULL", (default_tenant_id,))

        count = db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        if count == 0:
            seed_demo_data(db)


def seed_demo_tenants(db):
    db.execute("INSERT INTO tenants (slug, name) VALUES ('sakura', 'さくらクリニック')")
    db.execute("INSERT INTO tenants (slug, name) VALUES ('midori', 'みどり病院')")
    db.commit()


def seed_demo_data(db):
    tenant_ids = [row[0] for row in db.execute("SELECT id FROM tenants ORDER BY id")]
    sakura_id, midori_id = tenant_ids[0], tenant_ids[1]

    today = date.today()
    demo_rows = [
        (sakura_id, "患者A", "佐藤医師", today - timedelta(days=5), "手術で使用した材料の入力", "未入力"),
        (sakura_id, "患者B", "鈴木医師", today - timedelta(days=4), "術後診断の入力", "未入力"),
        (sakura_id, "患者E", "鈴木医師", today - timedelta(days=6), "手術で使用した材料の入力", "確認済み"),
        (midori_id, "患者C", "佐藤医師", today - timedelta(days=2), "手術で使用した材料の入力", "未入力"),
        (midori_id, "患者D", "高橋医師", today - timedelta(days=1), "術後診断の入力", "未入力"),
        (midori_id, "患者F", "高橋医師", today - timedelta(days=8), "術後診断の入力", "確認済み"),
    ]
    now = datetime.now().isoformat()
    for tenant_id, patient_label, doctor_name, surgery_date, item, status in demo_rows:
        resolved_at = now if status == "確認済み" else None
        db.execute(
            """
            INSERT INTO cases (tenant_id, patient_label, doctor_name, surgery_date, item, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, patient_label, doctor_name, surgery_date.isoformat(), item, status, now, resolved_at),
        )
    db.commit()


def row_to_case(row):
    surgery_date = date.fromisoformat(row["surgery_date"])
    days_elapsed = (date.today() - surgery_date).days
    return {
        "id": row["id"],
        "patient_label": row["patient_label"],
        "doctor_name": row["doctor_name"],
        "surgery_date": row["surgery_date"],
        "item": row["item"],
        "status": row["status"],
        "days_elapsed": days_elapsed,
        "urgent": row["status"] == "未入力" and days_elapsed >= URGENT_DAYS,
    }


# ---------------------------------------------------------------------------
# 認証（サインアップ・ログイン・パスワード再発行・ロール）
# ---------------------------------------------------------------------------


def get_user_by_id(user_id):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def current_user():
    """今のセッションでログイン中のユーザー行（無ければNone）を返す。

    第16回（課題28）まではクリニック切替ヘッダ(X-Clinic-Id)を利用者が自由に
    書き換えられる暫定方式だったが、今回からログインセッションだけを信頼できる
    情報源にする。tenant_idは必ずここ（＝ログイン中のユーザー自身が所属する
    クリニック）から取得し、リクエスト側からの自己申告は一切受け付けない。
    """
    if "user" not in g:
        user_id = session.get("user_id")
        g.user = get_user_by_id(user_id) if user_id else None
    return g.user


def get_tenant(tenant_id):
    db = get_db()
    return db.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()


def login_required_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    return wrapped


def login_required_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return jsonify({"error": "ログインが必要です。"}), 401
        return view(*args, **kwargs)

    return wrapped


def admin_required_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"error": "ログインが必要です。"}), 401
        if user["role"] != "admin":
            return jsonify({"error": "管理者のみ実行できます。"}), 403
        return view(*args, **kwargs)

    return wrapped


def token_expired(expires_iso):
    return expires_iso is None or datetime.fromisoformat(expires_iso) < datetime.now()


@app.route("/signup")
def signup_page():
    return render_template("signup.html")


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    tenant_id_raw = data.get("tenant_id")
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not tenant_id_raw or not email or not password:
        return jsonify({"error": "すべての項目を入力してください。"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "メールアドレスの形式が正しくありません。"}), 400
    if len(password) < PASSWORD_MIN:
        return jsonify({"error": f"パスワードは{PASSWORD_MIN}文字以上にしてください。"}), 400

    try:
        tenant_id = int(tenant_id_raw)
    except ValueError:
        return jsonify({"error": "クリニックの指定が正しくありません。"}), 400

    db = get_db()
    tenant = db.execute("SELECT id FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    if tenant is None:
        return jsonify({"error": "存在しないクリニックです。"}), 400

    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing is not None:
        return jsonify({"error": "このメールアドレスはすでに登録されています。"}), 400

    # そのクリニックで最初に登録した人が自動的に管理者、2人目以降は一般ユーザー。
    user_count = db.execute(
        "SELECT COUNT(*) FROM users WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()[0]
    role = "admin" if user_count == 0 else "staff"

    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=VERIFY_TOKEN_HOURS)).isoformat()
    db.execute(
        """
        INSERT INTO users (tenant_id, email, password_hash, role, email_verified,
                            verification_token, verification_expires, created_at)
        VALUES (?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            tenant_id,
            email,
            generate_password_hash(password),
            role,
            token,
            expires,
            datetime.now().isoformat(),
        ),
    )
    db.commit()

    verify_url = url_for("verify_email", token=token)
    app.logger.info("【デモ】確認リンク（本来はメール送信）: %s", verify_url)
    return jsonify({"ok": True, "verify_url": verify_url}), 201


@app.route("/verify/<token>")
def verify_email(token):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE verification_token = ?", (token,)).fetchone()
    if row is None:
        return redirect(url_for("login_page", error="invalid_token"))
    if token_expired(row["verification_expires"]):
        return redirect(url_for("login_page", error="expired_token"))

    db.execute(
        "UPDATE users SET email_verified = 1, verification_token = NULL, verification_expires = NULL WHERE id = ?",
        (row["id"],),
    )
    db.commit()
    return redirect(url_for("login_page", verified="1"))


@app.route("/login")
def login_page():
    return render_template(
        "login.html",
        verified=request.args.get("verified"),
        error=request.args.get("error"),
        reset=request.args.get("reset"),
    )


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    # メール未登録とパスワード不一致を同じメッセージにし、
    # どちらが間違っているかを外部から推測されないようにする。
    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "メールアドレスまたはパスワードが違います。"}), 401
    if not user["email_verified"]:
        return (
            jsonify({"error": "メールアドレスの確認がまだ完了していません。届いた確認リンクをクリックしてください。"}),
            403,
        )

    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"ok": True})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
@login_required_api
def me():
    user = current_user()
    tenant = get_tenant(user["tenant_id"])
    return jsonify(
        {
            "email": user["email"],
            "role": user["role"],
            "tenant_id": user["tenant_id"],
            "tenant_name": tenant["name"],
        }
    )


@app.route("/forgot-password")
def forgot_password_page():
    return render_template("forgot_password.html")


@app.route("/api/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user is None:
        # 登録の有無を外部から判別できないよう、未登録でも同じ形式の応答にする。
        return jsonify({"ok": True, "reset_url": None})

    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=RESET_TOKEN_HOURS)).isoformat()
    db.execute(
        "UPDATE users SET reset_token = ?, reset_expires = ? WHERE id = ?",
        (token, expires, user["id"]),
    )
    db.commit()

    reset_url = url_for("reset_password_page", token=token)
    app.logger.info("【デモ】パスワード再設定リンク（本来はメール送信）: %s", reset_url)
    return jsonify({"ok": True, "reset_url": reset_url})


@app.route("/reset-password/<token>")
def reset_password_page(token):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE reset_token = ?", (token,)).fetchone()
    valid = row is not None and not token_expired(row["reset_expires"])
    return render_template("reset_password.html", token=token, valid=valid)


@app.route("/api/reset-password/<token>", methods=["POST"])
def reset_password(token):
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if len(password) < PASSWORD_MIN:
        return jsonify({"error": f"パスワードは{PASSWORD_MIN}文字以上にしてください。"}), 400

    db = get_db()
    row = db.execute("SELECT * FROM users WHERE reset_token = ?", (token,)).fetchone()
    if row is None or token_expired(row["reset_expires"]):
        return jsonify({"error": "リンクの有効期限が切れています。もう一度お試しください。"}), 400

    db.execute(
        "UPDATE users SET password_hash = ?, reset_token = NULL, reset_expires = NULL WHERE id = ?",
        (generate_password_hash(password), row["id"]),
    )
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 画面・案件API
# ---------------------------------------------------------------------------


@app.route("/")
@login_required_page
def index():
    user = current_user()
    tenant = get_tenant(user["tenant_id"])
    is_staging = os.environ.get("APP_ENV", "") == "staging"
    return render_template(
        "index.html",
        urgent_days=URGENT_DAYS,
        is_staging=is_staging,
        user_email=user["email"],
        is_admin=user["role"] == "admin",
        tenant_name=tenant["name"],
        tenant_plan=tenant["plan"],
    )


@app.route("/api/tenants", methods=["GET"])
def list_tenants():
    # サインアップ画面のクリニック選択に使うため、名前一覧のみ認証不要で公開する。
    db = get_db()
    rows = db.execute("SELECT id, slug, name FROM tenants ORDER BY id").fetchall()
    return jsonify({"tenants": [dict(r) for r in rows]})


@app.route("/api/cases", methods=["GET"])
@login_required_api
def list_cases():
    tenant_id = current_user()["tenant_id"]

    status = request.args.get("status", "all")
    db = get_db()
    if status == "open":
        rows = db.execute(
            "SELECT * FROM cases WHERE tenant_id = ? AND status = '未入力'", (tenant_id,)
        ).fetchall()
    elif status == "resolved":
        rows = db.execute(
            "SELECT * FROM cases WHERE tenant_id = ? AND status = '確認済み'", (tenant_id,)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM cases WHERE tenant_id = ?", (tenant_id,)).fetchall()

    cases = [row_to_case(r) for r in rows]
    cases.sort(key=lambda c: (-c["days_elapsed"] if c["status"] == "未入力" else 999))

    open_cases = db.execute(
        "SELECT * FROM cases WHERE tenant_id = ? AND status = '未入力'", (tenant_id,)
    ).fetchall()
    open_cases = [row_to_case(r) for r in open_cases]
    summary = {
        "open_count": len(open_cases),
        "urgent_count": sum(1 for c in open_cases if c["urgent"]),
    }
    return jsonify({"cases": cases, "summary": summary})


@app.route("/api/cases", methods=["POST"])
@admin_required_api
def create_case():
    tenant_id = current_user()["tenant_id"]

    db = get_db()
    tenant = get_tenant(tenant_id)
    if tenant["plan"] != PLAN_PRO:
        case_count = db.execute(
            "SELECT COUNT(*) FROM cases WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()[0]
        if case_count >= FREE_PLAN_CASE_LIMIT:
            return (
                jsonify(
                    {
                        "error": f"フリープランの上限（{FREE_PLAN_CASE_LIMIT}件）に達しました。"
                        "プロプランにアップグレードすると無制限に登録できます。",
                        "upgrade_required": True,
                    }
                ),
                403,
            )

    data = request.get_json(silent=True) or {}
    patient_label = (data.get("patient_label") or "").strip()
    doctor_name = (data.get("doctor_name") or "").strip()
    surgery_date = (data.get("surgery_date") or "").strip()
    item = (data.get("item") or "").strip()

    if not patient_label or not doctor_name or not surgery_date or not item:
        return jsonify({"error": "すべての項目を入力してください。"}), 400
    if len(patient_label) > PATIENT_LABEL_MAX:
        return jsonify({"error": f"患者ラベルは{PATIENT_LABEL_MAX}文字以内にしてください。"}), 400
    if len(doctor_name) > DOCTOR_NAME_MAX:
        return jsonify({"error": f"医師名は{DOCTOR_NAME_MAX}文字以内にしてください。"}), 400
    if len(item) > ITEM_MAX:
        return jsonify({"error": f"内容は{ITEM_MAX}文字以内にしてください。"}), 400
    try:
        date.fromisoformat(surgery_date)
    except ValueError:
        return jsonify({"error": "手術日の形式が正しくありません。"}), 400

    now = datetime.now().isoformat()
    db.execute(
        """
        INSERT INTO cases (tenant_id, patient_label, doctor_name, surgery_date, item, status, created_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, '未入力', ?, NULL)
        """,
        (tenant_id, patient_label, doctor_name, surgery_date, item, now),
    )
    db.commit()
    return jsonify({"ok": True}), 201


@app.route("/api/cases/<int:case_id>/resolve", methods=["POST"])
@login_required_api
def resolve_case(case_id):
    tenant_id = current_user()["tenant_id"]

    db = get_db()
    # tenant_idも条件に含めることで、他テナントのIDを推測して送っても
    # 「対象なし」として扱われ、絶対に更新できないようにする。
    row = db.execute(
        "SELECT * FROM cases WHERE id = ? AND tenant_id = ?", (case_id, tenant_id)
    ).fetchone()
    if row is None:
        return jsonify({"error": "対象の案件が見つかりません。"}), 404

    now = datetime.now().isoformat()
    db.execute(
        "UPDATE cases SET status = '確認済み', resolved_at = ? WHERE id = ? AND tenant_id = ?",
        (now, case_id, tenant_id),
    )
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 課金（Stripeテストモード・第18回）
#   チェックアウト後の反映は、今回はまだWebhookを使わず、成功時に戻ってくる
#   /billing/success でStripeにセッションの状態を直接問い合わせて確定させる、
#   一番シンプルなやり方にした。継続課金の運用（請求失敗・解約）はWebhookが
#   必要になる次回（第19回・課金②）に回す。
# ---------------------------------------------------------------------------


@app.route("/pricing")
@login_required_page
def pricing_page():
    user = current_user()
    tenant = get_tenant(user["tenant_id"])
    return render_template(
        "pricing.html",
        is_admin=user["role"] == "admin",
        tenant_plan=tenant["plan"],
        free_limit=FREE_PLAN_CASE_LIMIT,
        pro_price=PRO_PLAN_PRICE_JPY,
        stripe_configured=bool(stripe.api_key),
    )


@app.route("/api/checkout/create-session", methods=["POST"])
@admin_required_api
def create_checkout_session():
    user = current_user()
    tenant = get_tenant(user["tenant_id"])

    if tenant["plan"] == PLAN_PRO:
        return jsonify({"error": "すでにプロプランです。"}), 400
    if not stripe.api_key:
        return jsonify({"error": "決済の準備ができていません（STRIPE_SECRET_KEY未設定）。"}), 500

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "jpy",
                        "product_data": {"name": "入力漏れ確認ダッシュボード プロプラン"},
                        "unit_amount": PRO_PLAN_PRICE_JPY,
                        "recurring": {"interval": "month"},
                    },
                    "quantity": 1,
                }
            ],
            client_reference_id=str(tenant["id"]),
            customer_email=user["email"],
            success_url=url_for("billing_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("pricing_page", _external=True),
        )
    except stripe.error.StripeError as e:
        app.logger.error("Stripe決済セッション作成に失敗: %s", e)
        return jsonify({"error": "決済ページの準備に失敗しました。時間を置いて再度お試しください。"}), 500

    return jsonify({"url": checkout_session.url})


@app.route("/billing/success")
@login_required_page
def billing_success():
    user = current_user()
    session_id = request.args.get("session_id")

    error = None
    if not session_id:
        error = "決済情報が確認できませんでした。"
    else:
        try:
            checkout_session = stripe.checkout.Session.retrieve(session_id)
        except stripe.error.StripeError as e:
            app.logger.error("Stripeセッション確認に失敗: %s", e)
            checkout_session = None
            error = "決済情報の確認に失敗しました。"

        if checkout_session is not None:
            # client_reference_idを必ずログイン中のテナントと突き合わせ、
            # 他人のsession_idを送りつけて勝手にアップグレードされないようにする。
            # StripeのSessionオブジェクトはdictではなく属性アクセスなので.get()は使えない。
            if getattr(checkout_session, "client_reference_id", None) != str(user["tenant_id"]):
                error = "この決済情報は現在のログインと一致しません。"
            elif getattr(checkout_session, "payment_status", None) != "paid":
                error = "お支払いが完了していません。"
            else:
                db = get_db()
                db.execute(
                    "UPDATE tenants SET plan = ?, stripe_customer_id = ?, stripe_subscription_id = ? WHERE id = ?",
                    (
                        PLAN_PRO,
                        getattr(checkout_session, "customer", None),
                        getattr(checkout_session, "subscription", None),
                        user["tenant_id"],
                    ),
                )
                db.commit()

    return render_template("billing_success.html", error=error)


@app.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    """Stripeからの通知を受け取る窓口（第19回・課金②）。

    今回は範囲を「解約されたら停止」の1本に絞り、customer.subscription.deleted
    （解約が確定したタイミングでStripeが送ってくるイベント）だけを処理する。
    支払い失敗の猶予処理などは次回以降の拡張範囲。
    """
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        app.logger.error("STRIPE_WEBHOOK_SECRET未設定のため、Webhook通知を処理できません。")
        return jsonify({"error": "Webhookの準備ができていません。"}), 500

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        app.logger.warning("Stripe Webhookの検証に失敗: %s", e)
        return jsonify({"error": "署名の検証に失敗しました。"}), 400

    event_type = getattr(event, "type", None)
    app.logger.warning("[DEBUG] Stripe Webhook受信: type=%r", event_type)

    if event_type == "customer.subscription.deleted":
        subscription = getattr(getattr(event, "data", None), "object", None)
        subscription_id = getattr(subscription, "id", None)
        app.logger.warning("[DEBUG] 解約イベント: subscription_id=%r", subscription_id)

        db = get_db()
        cursor = db.execute(
            "UPDATE tenants SET plan = ? WHERE stripe_subscription_id = ?",
            (PLAN_FREE, subscription_id),
        )
        db.commit()
        app.logger.warning("[DEBUG] 解約イベント: 更新件数=%d", cursor.rowcount)

    # 未対応のイベント種類も200を返しておく（そうしないとStripeが同じ通知を再送し続ける）
    return jsonify({"received": True})


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
