import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "cases.db"


def column_exists(db, table, column):
    cols = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
    return column in cols


def main():
    with sqlite3.connect(DB_PATH) as db:
        if column_exists(db, "cases", "memo"):
            print("すでにmemo列があります。何もしません。")
            return
        db.execute("ALTER TABLE cases ADD COLUMN memo TEXT")
        print("memo列を追加しました。既存の行はmemo=NULLのまま残っています。")


if __name__ == "__main__":
    main()
