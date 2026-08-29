from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import server
from server import app


def signup(client, tenant_id, email, password="Passw0rd!"):
    return client.post(
        "/api/signup",
        json={"tenant_id": tenant_id, "email": email, "password": password},
    )


def signup_and_verify(client, tenant_id, email, password="Passw0rd!"):
    res = signup(client, tenant_id, email, password)
    verify_url = res.get_json()["verify_url"]
    client.get(verify_url)
    return password


def login(client, email, password):
    return client.post("/api/login", json={"email": email, "password": password})


def get_tenant_ids(client):
    data = client.get("/api/tenants").get_json()
    return [t["id"] for t in data["tenants"]]


def new_case_payload(patient_label="患者Z"):
    return {
        "patient_label": patient_label,
        "doctor_name": "テスト医師",
        "surgery_date": "2026-01-01",
        "item": "テスト項目",
    }


def test_index_redirects_to_login_when_not_authenticated():
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_signup_creates_unverified_user_and_blocks_login():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    signup(client, tenant_a, "unverified@example.com")

    res = login(client, "unverified@example.com", "Passw0rd!")
    assert res.status_code == 403


def test_verify_then_login_succeeds():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    password = signup_and_verify(client, tenant_a, "verified@example.com")

    res = login(client, "verified@example.com", password)
    assert res.status_code == 200
    assert client.get("/").status_code == 200


def test_login_wrong_password_rejected():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    signup_and_verify(client, tenant_a, "pwcheck@example.com")

    res = login(client, "pwcheck@example.com", "WrongPassword1")
    assert res.status_code == 401


def test_logout_clears_session():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    password = signup_and_verify(client, tenant_a, "logout-check@example.com")
    login(client, "logout-check@example.com", password)
    assert client.get("/").status_code == 200

    client.post("/api/logout")
    assert client.get("/").status_code == 302


def test_first_user_of_tenant_is_admin_second_is_staff():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)

    pw1 = signup_and_verify(client, tenant_a, "first@example.com")
    login(client, "first@example.com", pw1)
    role1 = client.get("/api/me").get_json()["role"]
    client.post("/api/logout")

    pw2 = signup_and_verify(client, tenant_a, "second@example.com")
    login(client, "second@example.com", pw2)
    role2 = client.get("/api/me").get_json()["role"]

    assert role1 == "admin"
    assert role2 == "staff"


def test_staff_cannot_add_case_but_admin_can():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)

    admin_pw = signup_and_verify(client, tenant_a, "admin1@example.com")
    staff_pw = signup_and_verify(client, tenant_a, "staff1@example.com")

    login(client, "staff1@example.com", staff_pw)
    staff_res = client.post("/api/cases", json=new_case_payload())
    assert staff_res.status_code == 403
    client.post("/api/logout")

    login(client, "admin1@example.com", admin_pw)
    admin_res = client.post("/api/cases", json=new_case_payload())
    assert admin_res.status_code == 201


def test_tenant_cannot_see_other_tenants_cases():
    """課題28で作ったテナント分離を、ヘッダ自己申告ではなくログインセッションで再確認する。"""
    client = app.test_client()
    tenant_a, tenant_b = get_tenant_ids(client)

    pw_a = signup_and_verify(client, tenant_a, "iso-a@example.com")
    login(client, "iso-a@example.com", pw_a)
    create_res = client.post("/api/cases", json=new_case_payload("越境テスト患者"))
    assert create_res.status_code == 201

    list_as_a = client.get("/api/cases").get_json()
    labels_a = [c["patient_label"] for c in list_as_a["cases"]]
    assert "越境テスト患者" in labels_a
    client.post("/api/logout")

    pw_b = signup_and_verify(client, tenant_b, "iso-b@example.com")
    login(client, "iso-b@example.com", pw_b)
    list_as_b = client.get("/api/cases").get_json()
    labels_b = [c["patient_label"] for c in list_as_b["cases"]]
    assert "越境テスト患者" not in labels_b


def test_tenant_cannot_resolve_other_tenants_case():
    client = app.test_client()
    tenant_a, tenant_b = get_tenant_ids(client)

    pw_a = signup_and_verify(client, tenant_a, "resolve-a@example.com")
    login(client, "resolve-a@example.com", pw_a)
    list_as_a = client.get("/api/cases").get_json()
    case_of_a = list_as_a["cases"][0]
    client.post("/api/logout")

    pw_b = signup_and_verify(client, tenant_b, "resolve-b@example.com")
    login(client, "resolve-b@example.com", pw_b)
    resolve_res = client.post(f"/api/cases/{case_of_a['id']}/resolve")
    assert resolve_res.status_code == 404


def test_forgot_password_does_not_leak_existence():
    client = app.test_client()
    res = client.post("/api/forgot-password", json={"email": "nobody@example.com"})
    assert res.status_code == 200
    assert res.get_json()["reset_url"] is None


def test_password_reset_flow_changes_password():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    old_password = signup_and_verify(client, tenant_a, "reset-flow@example.com")

    forgot_res = client.post(
        "/api/forgot-password", json={"email": "reset-flow@example.com"}
    )
    reset_url = forgot_res.get_json()["reset_url"]
    token = reset_url.rsplit("/", 1)[-1]

    new_password = "NewPassw0rd!"
    reset_res = client.post(f"/api/reset-password/{token}", json={"password": new_password})
    assert reset_res.status_code == 200

    assert login(client, "reset-flow@example.com", old_password).status_code == 401
    assert login(client, "reset-flow@example.com", new_password).status_code == 200


# ---------------------------------------------------------------------------
# 課金（第18回・Stripeテストモード）
# ---------------------------------------------------------------------------


def upgrade_to_pro(client, tenant_id):
    """テスト内でStripeの決済成功を模擬し、テナントをプロプランにする。"""
    fake_session = SimpleNamespace(
        client_reference_id=str(tenant_id),
        payment_status="paid",
        customer="cus_test",
        subscription="sub_test",
    )
    with patch("server.stripe.checkout.Session.retrieve", return_value=fake_session):
        return client.get("/billing/success?session_id=cs_test_dummy")


def test_free_plan_blocks_case_creation_after_limit():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "limit-admin@example.com")
    login(client, "limit-admin@example.com", admin_pw)

    # このテナントにはサンプルデータが最初から数件入っているため、
    # 「あと何件で上限か」を実際の残数から数える。
    existing = client.get("/api/cases").get_json()["cases"]
    remaining = server.FREE_PLAN_CASE_LIMIT - len(existing)
    for i in range(remaining):
        res = client.post("/api/cases", json=new_case_payload(f"患者{i}"))
        assert res.status_code == 201

    over_res = client.post("/api/cases", json=new_case_payload("超過患者"))
    assert over_res.status_code == 403
    assert over_res.get_json()["upgrade_required"] is True


def test_checkout_session_requires_admin():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    # 最初の登録者は自動的に管理者になるため、staffロールを検証するには
    # 先にもう1人（管理者役）を登録しておく必要がある。
    signup_and_verify(client, tenant_a, "checkout-admin0@example.com")
    staff_pw = signup_and_verify(client, tenant_a, "checkout-staff@example.com")
    login(client, "checkout-staff@example.com", staff_pw)

    res = client.post("/api/checkout/create-session")
    assert res.status_code == 403


def test_checkout_session_without_stripe_key_returns_error(monkeypatch):
    monkeypatch.setattr(server.stripe, "api_key", None)
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "checkout-admin1@example.com")
    login(client, "checkout-admin1@example.com", admin_pw)

    res = client.post("/api/checkout/create-session")
    assert res.status_code == 500


def test_checkout_session_creation_success(monkeypatch):
    monkeypatch.setattr(server.stripe, "api_key", "sk_test_dummy")
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "checkout-admin2@example.com")
    login(client, "checkout-admin2@example.com", admin_pw)

    fake_session = MagicMock(url="https://checkout.stripe.com/test-session")
    with patch("server.stripe.checkout.Session.create", return_value=fake_session) as mock_create:
        res = client.post("/api/checkout/create-session")

    assert res.status_code == 200
    assert res.get_json()["url"] == "https://checkout.stripe.com/test-session"
    assert mock_create.called


def test_billing_success_upgrades_plan_when_payment_confirmed():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "billing-ok@example.com")
    login(client, "billing-ok@example.com", admin_pw)
    tenant_id = client.get("/api/me").get_json()["tenant_id"]

    res = upgrade_to_pro(client, tenant_id)
    assert res.status_code == 200
    assert "プロプラン" in res.get_data(as_text=True)
    assert "現在のプラン" in client.get("/pricing").get_data(as_text=True)


def test_billing_success_rejects_mismatched_tenant():
    client = app.test_client()
    tenant_a, tenant_b = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "billing-mismatch@example.com")
    login(client, "billing-mismatch@example.com", admin_pw)

    fake_session = SimpleNamespace(
        client_reference_id=str(tenant_b),
        payment_status="paid",
        customer="cus_x",
        subscription="sub_x",
    )
    with patch("server.stripe.checkout.Session.retrieve", return_value=fake_session):
        res = client.get("/billing/success?session_id=cs_test_mismatch")

    assert "一致しません" in res.get_data(as_text=True)


# ---------------------------------------------------------------------------
# プラン制限とメータリング（第20回・①機能ゲート＝CSVエクスポート）
# ---------------------------------------------------------------------------


def test_csv_export_blocked_on_free_plan():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "export-free@example.com")
    login(client, "export-free@example.com", admin_pw)

    res = client.get("/api/cases/export")
    assert res.status_code == 403
    assert res.get_json()["upgrade_required"] is True


def test_csv_export_requires_admin():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    signup_and_verify(client, tenant_a, "export-admin0@example.com")
    staff_pw = signup_and_verify(client, tenant_a, "export-staff@example.com")
    login(client, "export-staff@example.com", staff_pw)

    res = client.get("/api/cases/export")
    assert res.status_code == 403


def test_csv_export_succeeds_on_pro_plan():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "export-pro@example.com")
    login(client, "export-pro@example.com", admin_pw)
    tenant_id = client.get("/api/me").get_json()["tenant_id"]
    upgrade_to_pro(client, tenant_id)

    client.post("/api/cases", json=new_case_payload("CSV確認用患者"))
    res = client.get("/api/cases/export")

    assert res.status_code == 200
    assert res.mimetype == "text/csv"
    body = res.get_data(as_text=True)
    assert "患者,担当医師" in body
    assert "CSV確認用患者" in body


# ---------------------------------------------------------------------------
# プラン制限とメータリング（第20回・②使用量の可視化）
# ---------------------------------------------------------------------------


def test_case_list_summary_reports_usage_against_free_limit():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "usage-free@example.com")
    login(client, "usage-free@example.com", admin_pw)

    summary = client.get("/api/cases").get_json()["summary"]
    assert summary["plan"] == "free"
    assert summary["case_limit"] == server.FREE_PLAN_CASE_LIMIT
    assert summary["case_count"] == len(client.get("/api/cases").get_json()["cases"])


def test_case_list_summary_has_no_limit_on_pro_plan():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "usage-pro@example.com")
    login(client, "usage-pro@example.com", admin_pw)
    tenant_id = client.get("/api/me").get_json()["tenant_id"]
    upgrade_to_pro(client, tenant_id)

    summary = client.get("/api/cases").get_json()["summary"]
    assert summary["plan"] == "pro"
    assert summary["case_limit"] is None


def test_pro_plan_has_no_case_limit():
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "pro-admin@example.com")
    login(client, "pro-admin@example.com", admin_pw)
    tenant_id = client.get("/api/me").get_json()["tenant_id"]

    upgrade_to_pro(client, tenant_id)

    for i in range(server.FREE_PLAN_CASE_LIMIT + 5):
        res = client.post("/api/cases", json=new_case_payload(f"プロ患者{i}"))
        assert res.status_code == 201


# ---------------------------------------------------------------------------
# 継続課金・Webhook（第19回・課金②、まずは「解約されたら停止」の1本だけ）
# ---------------------------------------------------------------------------


def get_plan(tenant_id):
    with app.app_context():
        return server.get_tenant(tenant_id)["plan"]


def test_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(server, "STRIPE_WEBHOOK_SECRET", "whsec_test_dummy")
    client = app.test_client()

    with patch(
        "server.stripe.Webhook.construct_event",
        side_effect=server.stripe.error.SignatureVerificationError("bad signature", "sig_header"),
    ):
        res = client.post(
            "/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "invalid"}
        )

    assert res.status_code == 400


def test_webhook_without_secret_configured_returns_error(monkeypatch):
    monkeypatch.setattr(server, "STRIPE_WEBHOOK_SECRET", None)
    client = app.test_client()

    res = client.post("/webhooks/stripe", data=b"{}")
    assert res.status_code == 500


def test_webhook_subscription_deleted_downgrades_plan(monkeypatch):
    monkeypatch.setattr(server, "STRIPE_WEBHOOK_SECRET", "whsec_test_dummy")
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "cancel-admin@example.com")
    login(client, "cancel-admin@example.com", admin_pw)
    tenant_id = client.get("/api/me").get_json()["tenant_id"]

    upgrade_to_pro(client, tenant_id)
    assert get_plan(tenant_id) == server.PLAN_PRO

    fake_event = SimpleNamespace(
        type="customer.subscription.deleted",
        data=SimpleNamespace(object=SimpleNamespace(id="sub_test")),
    )
    with patch("server.stripe.Webhook.construct_event", return_value=fake_event):
        res = client.post(
            "/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "dummy"}
        )

    assert res.status_code == 200
    assert get_plan(tenant_id) == server.PLAN_FREE


def test_webhook_ignores_unknown_subscription_id(monkeypatch):
    monkeypatch.setattr(server, "STRIPE_WEBHOOK_SECRET", "whsec_test_dummy")
    client = app.test_client()
    tenant_a, _ = get_tenant_ids(client)
    admin_pw = signup_and_verify(client, tenant_a, "cancel-noop-admin@example.com")
    login(client, "cancel-noop-admin@example.com", admin_pw)
    tenant_id = client.get("/api/me").get_json()["tenant_id"]

    upgrade_to_pro(client, tenant_id)

    fake_event = SimpleNamespace(
        type="customer.subscription.deleted",
        data=SimpleNamespace(object=SimpleNamespace(id="sub_does_not_match_anyone")),
    )
    with patch("server.stripe.Webhook.construct_event", return_value=fake_event):
        res = client.post(
            "/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "dummy"}
        )

    assert res.status_code == 200
    # 一致するテナントが無いので、この人自身のプランは変わらない
    assert get_plan(tenant_id) == server.PLAN_PRO


def test_webhook_ignores_unrelated_event_types(monkeypatch):
    monkeypatch.setattr(server, "STRIPE_WEBHOOK_SECRET", "whsec_test_dummy")
    client = app.test_client()

    fake_event = SimpleNamespace(type="invoice.paid", data=SimpleNamespace(object=SimpleNamespace()))
    with patch("server.stripe.Webhook.construct_event", return_value=fake_event):
        res = client.post(
            "/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "dummy"}
        )

    assert res.status_code == 200
