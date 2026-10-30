"""共用体验账号：只进演示实验室，改不了密码，模型与正式使用同一把。"""

import json

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.models.runs import UsageLedger
from fengsuan.app.services.llm import assert_lab_quota, lab_token_budget
from fengsuan.app.services.seed import ensure_demo_lab


def test_guest_stays_in_demo_lab(client):
    guest = client.post("/api/v1/auth/guest")
    assert guest.status_code == 200, guest.text
    headers = {"Authorization": f"Bearer {guest.json()['access']}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["is_guest"] is True
    assert body["is_admin"] is False
    assert body["org"]["name"] == "体验机构"
    assert len(body["labs"]) == 1
    lab = body["labs"][0]
    assert lab["name"] == "体验实验室"
    assert lab["role"] == "teacher"
    lab_id = lab["lab_id"]

    denied = client.get("/api/v1/admin/orgs", headers=headers)
    assert denied.status_code == 403

    password = client.post(
        "/api/v1/auth/me/password",
        headers=headers,
        json={"current_password": "nope1234", "new_password": "abcdef12"},
    )
    assert password.status_code == 403

    invite = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=headers,
        json={"email": "person@example.com", "role": "student"},
    )
    assert invite.status_code == 200, invite.text

    assert client.delete(f"/api/v1/labs/{lab_id}", headers=headers).status_code == 403
    created = client.post(
        "/api/v1/labs",
        headers=headers,
        json={"name": "游客私组", "code": "guest-extra"},
    )
    assert created.status_code == 403

    admin = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "adminpass1"},
    )
    assert admin.status_code == 200, admin.text
    admin_headers = {"Authorization": f"Bearer {admin.json()['access']}"}
    admin_me = client.get("/api/v1/auth/me", headers=admin_headers).json()
    assert admin_me["is_admin"] is True
    assert admin_me["is_guest"] is False
    assert client.delete(f"/api/v1/labs/{lab_id}", headers=admin_headers).status_code == 403

    made = client.post(
        "/api/v1/labs",
        headers=admin_headers,
        json={"name": "正式组", "code": "real-lab"},
    )
    assert made.status_code == 200, made.text
    real_id = made.json()["id"]

    db = SessionLocal()
    try:
        papers = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
            .all()
        )
        assert len(papers) == 3
        papers[0].title = "改过的演示题录"
        db.commit()
        ensure_demo_lab(db)
        db.expire_all()
        assert db.get(LiteratureItem, papers[0].id).title == "改过的演示题录"
        again = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
            .count()
        )
        assert again == 3

        assert lab_token_budget(db, lab_id) == 2_000_000
        assert lab_token_budget(db, real_id) == 2_000_000
        db.add(
            UsageLedger(
                lab_id=lab_id,
                user_id=body["id"],
                feature="tutor",
                model="t",
                prompt_tokens=2_000_000,
                completion_tokens=0,
            )
        )
        db.add(
            UsageLedger(
                lab_id=real_id,
                user_id=admin_me["id"],
                feature="tutor",
                model="t",
                prompt_tokens=200_000,
                completion_tokens=0,
            )
        )
        db.commit()
        try:
            assert_lab_quota(db, lab_id, "tutor")
        except ClassifiedHTTPException as exc:
            assert exc.status_code == 429
            assert "体验额度" in exc.detail
        else:
            raise AssertionError("体验额度用尽后仍放行")
        assert_lab_quota(db, real_id, "tutor")
    finally:
        db.close()


def test_demo_lab_reuses_configured_model_key(client):
    guest = client.post("/api/v1/auth/guest")
    headers = {"Authorization": f"Bearer {guest.json()['access']}"}
    me = client.get("/api/v1/auth/me", headers=headers).json()
    demo_id = me["labs"][0]["lab_id"]
    admin = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "adminpass1"},
    )
    admin_headers = {"Authorization": f"Bearer {admin.json()['access']}"}
    made = client.post(
        "/api/v1/labs",
        headers=admin_headers,
        json={"name": "有密钥的组", "code": "keyed-lab"},
    )
    assert made.status_code == 200, made.text
    from fengsuan.app.core.security import encrypt_secret
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.credential import LLMCredential
    from fengsuan.app.services.llm import resolve_llm

    db = SessionLocal()
    try:
        db.add(
            LLMCredential(
                owner_type="lab",
                owner_id=made.json()["id"],
                provider="minimax",
                base_url="https://example.com/v1",
                api_key_encrypted=encrypt_secret("sk-demo-fallback"),
                model_chat="demo-model",
                is_active=True,
            )
        )
        db.commit()
        resolved = resolve_llm(db, me["id"], demo_id)
        assert resolved.model == "demo-model"
        assert resolved.api_key == "sk-demo-fallback"
        shown = client.get(f"/api/v1/labs/{demo_id}/llm-credentials", headers=headers)
        assert shown.status_code == 200, shown.text
        assert shown.json()["configured"] is True
        assert shown.json()["shared"] is True
        assert shown.json()["model_chat"] == "demo-model"
        assert "sk-demo" not in json.dumps(shown.json())
        blocked = client.put(
            f"/api/v1/labs/{demo_id}/llm-credentials",
            headers=headers,
            json={"provider": "openai", "base_url": "https://example.com/v1", "api_key": "sk-other", "model_chat": "x"},
        )
        assert blocked.status_code == 403
    finally:
        db.close()
