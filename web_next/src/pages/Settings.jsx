import { useState } from "react";
import { Button, Card, Form, Input, Space, Typography, message } from "antd";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api.js";
import { formatError } from "../err.js";
import { useSession } from "../store.js";
import { LlmKeyForm } from "../LlmKeyForm.jsx";
import { PageHeader } from "../ui.jsx";

const { Paragraph } = Typography;

export function Keys() {
  return (
    <Space direction="vertical" style={{ width: "100%", maxWidth: 640 }}>
      <PageHeader title="个人模型" hint="没填就用实验室或平台的。测一下才知道能不能用。">
        密钥能不能用
      </PageHeader>
      <Card>
        <ol className="fs-llm-uses">
          <li>选服务商（本机 MiniMax 选第一项）</li>
          <li>贴上控制台里的密钥</li>
          <li>保存后再测通</li>
        </ol>
        <LlmKeyForm load={() => api.getMyKey()} save={(v) => api.putMyKey(v)} test={() => api.testMyKey()} />
      </Card>
    </Space>
  );
}

export function LabLlm() {
  const { id } = useParams();
  return (
    <Space direction="vertical" style={{ width: "100%", maxWidth: 640 }}>
      <PageHeader title="实验室模型" hint="组员没填个人模型时用这里。不能改实验算力。">
        仅导师可改。
      </PageHeader>
      <Card>
        <ol className="fs-llm-uses">
          <li>选服务商</li>
          <li>贴上实验室共用密钥</li>
          <li>保存后再测通</li>
        </ol>
        <LlmKeyForm
          reloadKey={id}
          load={() => api.getLabKey(id)}
          save={(v) => api.putLabKey(id, v)}
          test={() => api.testLabKey(id)}
          saveLabel="保存实验室模型"
        />
      </Card>
    </Space>
  );
}

function MyAuthorCard({ me }) {
  const labId = useSession((s) => s.labId);
  const member = (me?.labs || []).some((l) => l.lab_id === labId);
  const q = useQuery({
    queryKey: ["my-author", labId],
    queryFn: () => api.myAuthor(labId).then((r) => r.data),
    enabled: !!labId && member,
  });
  const [busy, setBusy] = useState("");
  if (!member || !q.data) return null;
  const claim = async (authorId) => {
    setBusy(authorId || "undo");
    try {
      await api.claimAuthor(labId, authorId);
      message.success(authorId ? "认领好了" : "已撤销");
      q.refetch();
    } catch (e) {
      message.error(formatError(e, "没认领上"));
    } finally {
      setBusy("");
    }
  };
  const mine = q.data.author;
  const picks = q.data.suggestions || [];
  return (
    <Card title="我在论文里的署名">
      {mine ? (
        <>
          <Paragraph>
            组里的论文里，<b>{mine.name}</b> 就是你（{mine.paper_count || 0} 篇）。助手说“{me.display_name || "你"}的论文”会按这个人算。
          </Paragraph>
          <ul className="fs-author-papers">
            {(mine.papers || []).slice(0, 5).map((p) => (
              <li key={p.id}>
                <Link to={`/literature/${p.id}`}>{p.title || "无题"}</Link>
              </li>
            ))}
          </ul>
          <Button size="small" type="link" loading={busy === "undo"} onClick={() => claim(null)}>
            认错了，撤销
          </Button>
        </>
      ) : picks.length ? (
        <>
          <Paragraph type="secondary">下面这些作者名字和你一样。点“这是我”才算，不点不会自动认。</Paragraph>
          <ul className="fs-author-papers">
            {picks.map((a) => (
              <li key={a.id}>
                <span>
                  <b>{a.name}</b>
                  <span className="fs-muted">
                    {" · "}
                    {a.paper_count || 0} 篇{(a.affiliations || [])[0] ? ` · ${a.affiliations[0]}` : ""}
                  </span>
                  {(a.papers || [])[0] ? <span className="fs-muted fs-lit-why">《{a.papers[0].title}》</span> : null}
                </span>
                {a.member ? (
                  <span className="fs-muted">已被 {a.member.name} 认领</span>
                ) : (
                  <Button size="small" loading={busy === a.id} onClick={() => claim(a.id)}>
                    这是我
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <Paragraph type="secondary">组里的论文里还没找到和你同名的作者。也可以在文献名单里点作者名，再点“这是我”。</Paragraph>
      )}
    </Card>
  );
}

export function MePage({ me, setMe }) {
  const [savingName, setSavingName] = useState(false);
  const [savingPass, setSavingPass] = useState(false);
  return (
    <Space direction="vertical" style={{ maxWidth: 520, width: "100%" }}>
      <PageHeader title="我的资料" hint="至少 8 位，要有字母和数字。">
        名字会出现在笔记和评论里。
      </PageHeader>
      <Card title="资料">
        <Paragraph>{me.email}</Paragraph>
        <Paragraph type="secondary">
          {me.product || "蜂算科研"} {me.version ? `v${me.version}` : ""} · {me.engine_credit || "powered by OpenLens"}
        </Paragraph>
        <Form
          initialValues={{ display_name: me.display_name }}
          onFinish={async (v) => {
            setSavingName(true);
            try {
              const r = await api.patchMe({ display_name: v.display_name });
              setMe({ ...me, display_name: r.data.display_name });
              message.success("已保存");
            } catch (e) {
              message.error(formatError(e, "没保存上"));
            } finally {
              setSavingName(false);
            }
          }}
        >
          <Form.Item name="display_name">
            <Input placeholder="显示名" />
          </Form.Item>
          <Button htmlType="submit" type="primary" loading={savingName}>
            {savingName ? "正在保存" : "保存"}
          </Button>
        </Form>
      </Card>
      <MyAuthorCard me={me} />
      <Card title="修改密码">
        {me?.is_guest ? (
          <Paragraph type="secondary">这是共用的体验账号，密码不在这里改。提问、文献、写作和检索与正式使用相同。</Paragraph>
        ) : (
          <>
        <Paragraph type="secondary">至少 8 位，要有字母和数字。</Paragraph>
        <Form
          onFinish={async (v) => {
            setSavingPass(true);
            try {
              await api.changePassword({ current_password: v.current_password, new_password: v.new_password });
              message.success("密码已更新");
            } catch (e) {
              message.error(formatError(e, "没改成"));
            } finally {
              setSavingPass(false);
            }
          }}
        >
          <Form.Item name="current_password" rules={[{ required: true }]}>
            <Input.Password placeholder="当前密码" />
          </Form.Item>
          <Form.Item name="new_password" rules={[{ required: true, min: 8 }]}>
            <Input.Password placeholder="新密码" />
          </Form.Item>
          <Button htmlType="submit" loading={savingPass}>{savingPass ? "正在更新" : "更新密码"}</Button>
        </Form>
          </>
        )}
      </Card>
    </Space>
  );
}
