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
