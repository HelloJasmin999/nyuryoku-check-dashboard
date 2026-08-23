import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024  # 200KB で十分。壊れた巨大データは弾く

DB_PATH = Path(__file__).parent / "cases.db"
URGENT_DAYS = 3  # これ以上未入力が続いたら「要対応」として赤く表示

PATIENT_LABEL_MAX = 20
DOCTOR_NAME_MAX = 20
ITEM_MAX = 50


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


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_label TEXT NOT NULL,
                doctor_name TEXT NOT NULL,
                surgery_date TEXT NOT NULL,
                item TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '未入力',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        count = db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        if count == 0:
            seed_demo_data(db)


def seed_demo_data(db):
    today = date.today()
    demo_rows = [
        ("患者A", "佐藤医師", today - timedelta(days=5), "手術で使用した材料の入力", "未入力"),
        ("患者B", "鈴木医師", today - timedelta(days=4), "術後診断の入力", "未入力"),
        ("患者C", "佐藤医師", today - timedelta(days=2), "手術で使用した材料の入力", "未入力"),
        ("患者D", "高橋医師", today - timedelta(days=1), "術後診断の入力", "未入力"),
        ("患者E", "鈴木医師", today - timedelta(days=6), "手術で使用した材料の入力", "確認済み"),
        ("患者F", "高橋医師", today - timedelta(days=8), "術後診断の入力", "確認済み"),
    ]
    now = datetime.now().isoformat()
    for patient_label, doctor_name, surgery_date, item, status in demo_rows:
        resolved_at = now if status == "確認済み" else None
        db.execute(
            """
            INSERT INTO cases (patient_label, doctor_name, surgery_date, item, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (patient_label, doctor_name, surgery_date.isoformat(), item, status, now, resolved_at),
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


@app.route("/")
def index():
    environment_name = os.environ.get("RAILWAY_ENVIRONMENT_NAME", "")
    is_staging = environment_name == "staging"
    return render_template("index.html", urgent_days=URGENT_DAYS, is_staging=is_staging)


@app.route("/api/cases", methods=["GET"])
def list_cases():
    status = request.args.get("status", "all")
    db = get_db()
    if status == "open":
        rows = db.execute("SELECT * FROM cases WHERE status = '未入力'").fetchall()
    elif status == "resolved":
        rows = db.execute("SELECT * FROM cases WHERE status = '確認済み'").fetchall()
    else:
        rows = db.execute("SELECT * FROM cases").fetchall()

    cases = [row_to_case(r) for r in rows]
    cases.sort(key=lambda c: (-c["days_elapsed"] if c["status"] == "未入力" else 999))

    open_cases = db.execute("SELECT * FROM cases WHERE status = '未入力'").fetchall()
    open_cases = [row_to_case(r) for r in open_cases]
    summary = {
        "open_count": len(open_cases),
        "urgent_count": sum(1 for c in open_cases if c["urgent"]),
    }
    return jsonify({"cases": cases, "summary": summary})


@app.route("/api/cases", methods=["POST"])
def create_case():
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

    db = get_db()
    now = datetime.now().isoformat()
    db.execute(
        """
        INSERT INTO cases (patient_label, doctor_name, surgery_date, item, status, created_at, resolved_at)
        VALUES (?, ?, ?, ?, '未入力', ?, NULL)
        """,
        (patient_label, doctor_name, surgery_date, item, now),
    )
    db.commit()
    return jsonify({"ok": True}), 201


@app.route("/api/cases/<int:case_id>/resolve", methods=["POST"])
def resolve_case(case_id):
    db = get_db()
    row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        return jsonify({"error": "対象の案件が見つかりません。"}), 404

    now = datetime.now().isoformat()
    db.execute(
        "UPDATE cases SET status = '確認済み', resolved_at = ? WHERE id = ?",
        (now, case_id),
    )
    db.commit()
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
