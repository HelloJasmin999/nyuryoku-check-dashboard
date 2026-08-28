import pytest

import server


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """各テストごとに、本物のcases.dbとは別の使い捨てDBを使う。

    users.emailにUNIQUE制約があるため、共有DBのままだとテストを2回実行
    しただけで「同じメールアドレスは登録済み」エラーになってしまう。
    テストのたびにまっさらなDBを用意することで、本物のデモデータも
    汚さず、何度実行しても同じ結果になるようにする。
    """
    db_path = tmp_path / "test_cases.db"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    server.init_db()
    yield
