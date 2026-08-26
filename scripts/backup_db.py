import shutil
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "cases.db"
BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def main():
    if not DB_PATH.exists():
        print(f"バックアップ対象が見つかりません: {DB_PATH}")
        sys.exit(1)

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"cases_{timestamp}.db"
    shutil.copy2(DB_PATH, dest)
    print(f"バックアップ完了: {dest}")


if __name__ == "__main__":
    main()
