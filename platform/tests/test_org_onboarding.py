"""机构申请与运营审批（组织入驻）。"""

from __future__ import annotations


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> str:
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def test_apply_org_locks_workbench_until_approved(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "owner@school.edu",
            "password": "pass1234a",
            "display_name": "张老师",
            "org_name": "示范大学材料系",
            "kind": "university",
            "contact_name": "张老师",
            "apply_note": "组内文献与复现管理",
            "consent": True,
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["access"]
    me = client.get("/api/v1/auth/me", headers=_auth(token))
    assert me.status_code == 200
    body = me.json()
    assert body["workbench_ready"] is False
    assert body["org"]["status"] == "pending"
    assert body["labs"] == []

    labs = client.get("/api/v1/labs", headers=_auth(token))
    assert labs.status_code == 200
    assert labs.json() == []

    create = client.post(
        "/api/v1/labs",
        headers=_auth(token),
        json={"name": "偷建", "code": "steal-1"},
    )
    assert create.status_code == 403


def test_non_admin_cannot_review(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "a@b.com",
            "password": "pass1234a",
            "org_name": "某某课题组",
            "apply_note": "试一下",
            "consent": True,
        },
    )
    assert r.status_code == 200
    token = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(token)).json()["org"]["id"]
    bad = client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(token),
        json={"action": "approve"},
    )
    assert bad.status_code == 403


def test_approve_makes_teacher_and_invite_shares_org(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "pi@lab.org",
            "password": "pass1234a",
            "org_name": "蜂算试点实验室",
            "kind": "independent",
            "apply_note": "试点",
            "consent": True,
        },
    )
    assert r.status_code == 200
    owner_token = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(owner_token)).json()["org"]["id"]

    admin = _admin(client)
    review = client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "approve", "note": "欢迎"},
    )
    assert review.status_code == 200, review.text
    assert review.json()["status"] == "approved"
    assert review.json()["first_lab_id"]

    me = client.get("/api/v1/auth/me", headers=_auth(owner_token)).json()
    assert me["workbench_ready"] is True
    assert me["org"]["status"] == "approved"
    assert len(me["labs"]) >= 1
    assert me["labs"][0]["role"] == "teacher"
    lab_id = me["labs"][0]["lab_id"]

    inv = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=_auth(owner_token),
        json={"email": "student@lab.org", "role": "student"},
    )
    assert inv.status_code == 200, inv.text
    token = inv.json()["token"]

    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": token,
            "email": "student@lab.org",
            "password": "stud1234a",
            "display_name": "学生甲",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    stu = client.get("/api/v1/auth/me", headers=_auth(reg.json()["access"])).json()
    assert stu["org"]["id"] == org_id
    assert stu["workbench_ready"] is True


def test_reject_then_resubmit(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "retry@x.com",
            "password": "pass1234a",
            "org_name": "待驳回机构",
            "apply_note": "第一次",
            "consent": True,
        },
    )
    token = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(token)).json()["org"]["id"]
    admin = _admin(client)
    rej = client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "reject", "note": "请补充单位证明"},
    )
    assert rej.status_code == 200
    me = client.get("/api/v1/auth/me", headers=_auth(token)).json()
    assert me["org"]["status"] == "rejected"
    assert "单位证明" in me["org"]["review_note"]

    again = client.post(
        "/api/v1/auth/orgs/resubmit",
        headers=_auth(token),
        json={"apply_note": "已补充证明材料"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["org"]["status"] == "pending"


def test_pending_org_cannot_invite(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "noinvite@x.com",
            "password": "pass1234a",
            "org_name": "未通过不能邀",
            "apply_note": "x",
            "consent": True,
        },
    )
    token = r.json()["access"]
    assert (
        client.post(
            "/api/v1/labs",
            headers=_auth(token),
            json={"name": "n", "code": "n1"},
        ).status_code
        == 403
    )


def test_teacher_can_create_second_lab_in_own_org(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "multi@lab.org",
            "password": "pass1234a",
            "org_name": "多实验室机构",
            "apply_note": "两间组",
            "consent": True,
        },
    )
    owner = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["org"]["id"]
    admin = _admin(client)
    assert (
        client.post(
            f"/api/v1/admin/orgs/{org_id}/review",
            headers=_auth(admin),
            json={"action": "approve"},
        ).status_code
        == 200
    )

    second = client.post(
        "/api/v1/labs",
        headers=_auth(owner),
        json={"name": "第二组", "code": "lab-two-x"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["name"] == "第二组"

    other = client.post(
        "/api/v1/labs",
        headers=_auth(owner),
        json={"name": "窜租户", "code": "lab-hack", "org_id": "not-mine"},
    )
    assert other.status_code == 403


def test_seed_default_org_approved_admin_login(client):
    admin = _admin(client)
    me = client.get("/api/v1/auth/me", headers=_auth(admin)).json()
    assert me["is_admin"] is True
    assert me["workbench_ready"] is True
    assert me["org"]["status"] == "approved"


def test_public_meta_and_apply_notifies_admin(client):
    pub = client.get("/api/v1/auth/public")
    assert pub.status_code == 200
    assert "组内透明" in pub.json()["consent_text"]

    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "notify@x.com",
            "password": "pass1234a",
            "org_name": "通知测机构",
            "apply_note": "请审批",
            "consent": True,
        },
    )
    assert r.status_code == 200
    admin = _admin(client)
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.activity import Notification, AuditLog

    db = SessionLocal()
    try:
        assert db.query(AuditLog).filter(AuditLog.action == "org.apply").count() >= 1
        admin_me = client.get("/api/v1/auth/me", headers=_auth(admin)).json()
        notes = db.query(Notification).filter(Notification.user_id == admin_me["id"]).all()
        assert any("机构开通申请" in (n.title or "") for n in notes)
    finally:
        db.close()


def test_existing_user_accepts_invite_same_org(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "pi2@lab.org",
            "password": "pass1234a",
            "org_name": "接邀请机构",
            "apply_note": "x",
            "consent": True,
        },
    )
    owner = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["org"]["id"]
    admin = _admin(client)
    assert (
        client.post(
            f"/api/v1/admin/orgs/{org_id}/review",
            headers=_auth(admin),
            json={"action": "approve"},
        ).status_code
        == 200
    )
    lab_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["labs"][0]["lab_id"]

    # 先在同机构建第二组，邀请已是导师的同机构用户（用学生邮箱新建再邀进第二组）
    second = client.post(
        "/api/v1/labs",
        headers=_auth(owner),
        json={"name": "分实验室", "code": "lab-branch"},
    )
    assert second.status_code == 200
    lab2 = second.json()["id"]

    # 注册学生到第一实验室
    inv1 = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=_auth(owner),
        json={"email": "stu2@lab.org", "role": "student"},
    )
    assert inv1.status_code == 200
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv1.json()["token"],
            "email": "stu2@lab.org",
            "password": "stud1234a",
            "consent": True,
        },
    )
    assert reg.status_code == 200
    stu = reg.json()["access"]

    inv2 = client.post(
        f"/api/v1/labs/{lab2}/invites",
        headers=_auth(owner),
        json={"email": "stu2@lab.org", "role": "student"},
    )
    assert inv2.status_code == 200
    prev = client.get(f"/api/v1/auth/invites/{inv2.json()['token']}")
    assert prev.json()["account_exists"] is True

    acc = client.post(
        f"/api/v1/auth/invites/{inv2.json()['token']}/accept",
        headers=_auth(stu),
    )
    assert acc.status_code == 200, acc.text
    me = client.get("/api/v1/auth/me", headers=_auth(stu)).json()
    assert len(me["labs"]) >= 2


def test_suspend_blocks_invite_and_sets_pending(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "sus@lab.org",
            "password": "pass1234a",
            "org_name": "停用测",
            "apply_note": "x",
            "consent": True,
        },
    )
    owner = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["org"]["id"]
    admin = _admin(client)
    client.post(f"/api/v1/admin/orgs/{org_id}/review", headers=_auth(admin), json={"action": "approve"})
    lab_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["labs"][0]["lab_id"]
    client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "suspend", "note": "违规"},
    )
    me = client.get("/api/v1/auth/me", headers=_auth(owner)).json()
    assert me["org"]["status"] == "suspended"
    assert me["workbench_ready"] is False
    assert me["labs"] == []
    inv = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=_auth(owner),
        json={"email": "x@y.com", "role": "student"},
    )
    assert inv.status_code == 403
    lit = client.get(f"/api/v1/labs/{lab_id}/literature", headers=_auth(owner))
    assert lit.status_code == 403


def test_cross_org_invite_rejected_and_dup_name(client):
    a = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "oa@a.org",
            "password": "pass1234a",
            "org_name": "机构甲唯一名",
            "apply_note": "a",
            "consent": True,
        },
    )
    assert a.status_code == 200
    dup = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "ob@b.org",
            "password": "pass1234a",
            "org_name": "机构甲唯一名",
            "apply_note": "b",
            "consent": True,
        },
    )
    assert dup.status_code == 400

    admin = _admin(client)
    org_a = client.get("/api/v1/auth/me", headers=_auth(a.json()["access"])).json()["org"]["id"]
    client.post(f"/api/v1/admin/orgs/{org_a}/review", headers=_auth(admin), json={"action": "approve"})
    lab_a = client.get("/api/v1/auth/me", headers=_auth(a.json()["access"])).json()["labs"][0]["lab_id"]

    b = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "ob2@b.org",
            "password": "pass1234a",
            "org_name": "机构乙唯一名",
            "apply_note": "b",
            "consent": True,
        },
    )
    org_b = client.get("/api/v1/auth/me", headers=_auth(b.json()["access"])).json()["org"]["id"]
    client.post(f"/api/v1/admin/orgs/{org_b}/review", headers=_auth(admin), json={"action": "approve"})

    cross = client.post(
        f"/api/v1/labs/{lab_a}/invites",
        headers=_auth(a.json()["access"]),
        json={"email": "ob2@b.org", "role": "student"},
    )
    assert cross.status_code == 400
    assert "其他机构" in cross.json()["detail"]


def test_admin_sees_org_pending_count(client):
    client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "pc@x.com",
            "password": "pass1234a",
            "org_name": "待审角标机构",
            "apply_note": "x",
            "consent": True,
        },
    )
    me = client.get("/api/v1/auth/me", headers=_auth(_admin(client))).json()
    assert me["org_pending_count"] >= 1


def test_org_notification_href_uses_payload(client):
    from fengsuan.app.services.insight import object_open_href

    assert object_open_href("", "org", "x", extra={"href": "/pending"}) == "/pending"
    assert object_open_href("", "org", "x", extra={"href": "/admin"}) == "/admin"
    assert object_open_href("", "org", "x") == "/admin"


def test_suspended_invite_preview_not_usable_and_approve_href(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "invsus@x.org",
            "password": "pass1234a",
            "org_name": "邀请停用机构",
            "apply_note": "x",
            "consent": True,
        },
    )
    owner = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["org"]["id"]
    admin = _admin(client)
    approved = client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    assert approved.json().get("members_href", "").startswith("/labs/")
    assert approved.json()["members_href"].endswith("/members")

    lab_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["labs"][0]["lab_id"]
    inv = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=_auth(owner),
        json={"email": "newbie@x.org", "role": "student"},
    )
    assert inv.status_code == 200
    token = inv.json()["token"]
    ok = client.get(f"/api/v1/auth/invites/{token}")
    assert ok.status_code == 200
    assert ok.json()["usable"] is True
    assert ok.json()["org_status"] == "approved"

    client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "suspend", "note": "停用测邀请"},
    )
    blocked = client.get(f"/api/v1/auth/invites/{token}")
    assert blocked.status_code == 200
    assert blocked.json()["usable"] is False
    assert blocked.json()["org_status"] == "suspended"


def test_heal_approve_and_workbench_block(client):
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.lab import LabMembership

    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "heal@x.org",
            "password": "pass1234a",
            "org_name": "修复实验室机构",
            "apply_note": "x",
            "consent": True,
        },
    )
    owner = r.json()["access"]
    me0 = client.get("/api/v1/auth/me", headers=_auth(owner)).json()
    assert me0["workbench_block"] == "pending"
    org_id = me0["org"]["id"]
    admin = _admin(client)
    client.post(f"/api/v1/admin/orgs/{org_id}/review", headers=_auth(admin), json={"action": "approve"})
    me1 = client.get("/api/v1/auth/me", headers=_auth(owner)).json()
    assert me1["workbench_ready"] is True
    assert me1["workbench_block"] is None
    lab_id = me1["labs"][0]["lab_id"]
    uid = me1["id"]

    db = SessionLocal()
    try:
        db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == uid).delete()
        db.commit()
    finally:
        db.close()

    me2 = client.get("/api/v1/auth/me", headers=_auth(owner)).json()
    assert me2["workbench_ready"] is False
    assert me2["workbench_block"] == "no_membership"

    heal = client.post(f"/api/v1/admin/orgs/{org_id}/review", headers=_auth(admin), json={"action": "approve"})
    assert heal.status_code == 200
    assert heal.json().get("members_href")
    me3 = client.get("/api/v1/auth/me", headers=_auth(owner)).json()
    assert me3["workbench_ready"] is True
    assert me3["workbench_block"] is None


def test_suspend_revoke_ok_create_blocked_and_admin_filters(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "rev@x.org",
            "password": "pass1234a",
            "org_name": "撤销邀请机构",
            "apply_note": "x",
            "consent": True,
        },
    )
    owner = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["org"]["id"]
    admin = _admin(client)
    client.post(f"/api/v1/admin/orgs/{org_id}/review", headers=_auth(admin), json={"action": "approve"})
    lab_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["labs"][0]["lab_id"]
    inv = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=_auth(owner),
        json={"email": "gone@x.org", "role": "student"},
    )
    assert inv.status_code == 200
    invite_id = inv.json()["id"]

    client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "suspend", "note": "停"},
    )
    listed = client.get(f"/api/v1/labs/{lab_id}/invites", headers=_auth(owner))
    assert listed.status_code == 200
    revoked = client.delete(f"/api/v1/invites/{invite_id}", headers=_auth(owner))
    assert revoked.status_code == 200
    blocked = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=_auth(admin),
        json={"email": "another@x.org", "role": "student"},
    )
    assert blocked.status_code == 403

    users = client.get("/api/v1/admin/users", headers=_auth(admin), params={"org_status": "suspended"})
    assert users.status_code == 200
    assert any(u["email"] == "rev@x.org" for u in users.json())

    audit = client.get("/api/v1/admin/audit", headers=_auth(admin), params={"action": "org.*"})
    assert audit.status_code == 200
    assert any(str(i.get("action", "")).startswith("org.") for i in audit.json()["items"])


def test_soft_deleted_email_can_reapply(client):
    from fengsuan.app.db.base import utcnow
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.identity import User

    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "reuse@x.org",
            "password": "pass1234a",
            "org_name": "回收邮箱机构甲",
            "apply_note": "x",
            "consent": True,
        },
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "reuse@x.org").one()
        u.deleted_at = utcnow()
        db.commit()
    finally:
        db.close()
    again = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "reuse@x.org",
            "password": "pass1234a",
            "org_name": "回收邮箱机构乙",
            "apply_note": "y",
            "consent": True,
        },
    )
    assert again.status_code == 200
    me = client.get("/api/v1/auth/me", headers=_auth(again.json()["access"])).json()
    assert me["email"] == "reuse@x.org"
    assert me["org"]["name"] == "回收邮箱机构乙"


def test_reject_cannot_direct_approve_must_resubmit(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "gate@x.org",
            "password": "pass1234a",
            "org_name": "驳回不可直批机构",
            "apply_note": "x",
            "consent": True,
        },
    )
    token = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(token)).json()["org"]["id"]
    admin = _admin(client)
    client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "reject", "note": "材料不足"},
    )
    bad = client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "approve"},
    )
    assert bad.status_code == 400
    assert "重新提交" in bad.json()["detail"]
    client.post(
        "/api/v1/auth/orgs/resubmit",
        headers=_auth(token),
        json={"apply_note": "已补材料"},
    )
    ok = client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "approve"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"


def test_suspended_can_list_members_and_duplicate_invite_blocked(client):
    r = client.post(
        "/api/v1/auth/apply-org",
        json={
            "email": "memsus@x.org",
            "password": "pass1234a",
            "org_name": "停用可看成员机构",
            "apply_note": "x",
            "consent": True,
        },
    )
    owner = r.json()["access"]
    org_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["org"]["id"]
    admin = _admin(client)
    client.post(f"/api/v1/admin/orgs/{org_id}/review", headers=_auth(admin), json={"action": "approve"})
    lab_id = client.get("/api/v1/auth/me", headers=_auth(owner)).json()["labs"][0]["lab_id"]
    inv1 = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=_auth(owner),
        json={"email": "dup@x.org", "role": "student"},
    )
    assert inv1.status_code == 200
    dup = client.post(
        f"/api/v1/labs/{lab_id}/invites",
        headers=_auth(owner),
        json={"email": "dup@x.org", "role": "student"},
    )
    assert dup.status_code == 400
    assert "未使用的邀请" in dup.json()["detail"]

    client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "suspend", "note": "停"},
    )
    mem = client.get(f"/api/v1/labs/{lab_id}/members", headers=_auth(owner))
    assert mem.status_code == 200
    assert any(m["email"] == "memsus@x.org" for m in mem.json())
    me = client.get("/api/v1/auth/me", headers=_auth(owner)).json()
    assert me["workbench_block"] == "suspended"

    resumed = client.post(
        f"/api/v1/admin/orgs/{org_id}/review",
        headers=_auth(admin),
        json={"action": "resume"},
    )
    assert resumed.status_code == 200
    assert resumed.json().get("members_href", "").endswith("/members")

    orgs = client.get("/api/v1/admin/orgs", headers=_auth(admin), params={"status": "approved"})
    row = next(o for o in orgs.json()["items"] if o["id"] == org_id)
    assert "contact_name" in row


def test_legacy_notification_payload_column_healed(client):
    from sqlalchemy import inspect

    from fengsuan.app.db.migrate import ensure_legacy_columns
    from fengsuan.app.db.session import get_engine

    ensure_legacy_columns()
    eng = get_engine()
    notify = {c["name"] for c in inspect(eng).get_columns("notifications")}
    members = {c["name"] for c in inspect(eng).get_columns("lab_memberships")}
    assert "payload_json" in notify
    assert "title" in members
    assert "direction_id" in members
    assert client.get("/health").status_code == 200
