import { memo, useMemo, useState } from "react";
import { Button, Card, Form, Input, Popconfirm, message } from "antd";
import { api } from "../../api.js";
import { formatError, languageHref } from "../../err.js";
import { MdPreview } from "../../ui.jsx";

function NotesPanelInner({ id, labId, notes, page, me, teacher, fromNote, fromMs, onPage, onChanged, nav }) {
  const [noteForm] = Form.useForm();
  const [editing, setEditing] = useState(null);
  const groups = useMemo(() => {
    const by = {};
    for (const n of notes || []) {
      const k = n.page_no || 0;
      (by[k] ||= []).push(n);
    }
    return Object.entries(by).sort((a, b) => {
      const pa = Number(a[0]);
      const pb = Number(b[0]);
      if (pa === page && pb !== page) return -1;
      if (pb === page && pa !== page) return 1;
      return pa - pb;
    });
  }, [notes, page]);

  return (
    <Card title="笔记">
      {groups.length ? (
        groups.map(([pg, list]) => (
          <div key={pg} className={Number(pg) === page ? "fs-note-current" : ""}>
            <p>
              <Button type="link" size="small" onClick={() => onPage(Number(pg) || 1)}>
                {pg === "0" ? "整篇" : `第 ${pg} 页`}
              </Button>
            </p>
            {list.map((n) => (
              <p key={n.id} className={n.id === fromNote ? "fs-note-focus" : undefined}>
                <span className="fs-muted">{n.display_name || n.user_id} · </span>
                {n.anchor?.quote ? <span className="fs-chip">已锚定原文</span> : null}
                {editing === n.id ? (
                  <span>
                    <Input.TextArea
                      rows={2}
                      defaultValue={n.content_md}
                      onChange={(e) => {
                        n._draft = e.target.value;
                      }}
                    />
                    <Button
                      size="small"
                      type="link"
                      onClick={async () => {
                        try {
                          await api.patchNote(n.id, { content_md: n._draft || n.content_md, page_no: n.page_no });
                          setEditing(null);
                          onChanged();
                        } catch (e) {
                          message.error(formatError(e, "无法改笔记"));
                        }
                      }}
                    >
                      保存
                    </Button>
                    <Button size="small" type="link" onClick={() => setEditing(null)}>
                      取消
                    </Button>
                  </span>
                ) : (
                  <MdPreview text={n.content_md} />
                )}
                {n.user_id === me?.id ? (
                  <Button size="small" type="link" onClick={() => setEditing(n.id)}>
                    改
                  </Button>
                ) : null}
                {n.user_id === me?.id ? (
                  <Button
                    size="small"
                    type="link"
                    onClick={() =>
                      nav(
                        languageHref(labId, {
                          lit: id,
                          note: n.id,
                          page: n.page_no || page,
                          ms: fromMs || undefined,
                        })
                      )
                    }
                  >
                    改文字
                  </Button>
                ) : null}
                {n.user_id === me?.id || teacher ? (
                  <Popconfirm
                    title="删除这条笔记？"
                    onConfirm={async () => {
                      try {
                        await api.deleteNote(n.id);
                        onChanged();
                      } catch (e) {
                        message.error(formatError(e, "无法删除笔记"));
                      }
                    }}
                  >
                    <Button size="small" type="link" danger>
                      删
                    </Button>
                  </Popconfirm>
                ) : null}
              </p>
            ))}
          </div>
        ))
      ) : (
        <p className="fs-muted">还没有笔记。选中原文可以记下来，问完也可以写进笔记。</p>
      )}
      <Form
        form={noteForm}
        onFinish={async (v) => {
          try {
            await api.addNote(id, v.content_md, page);
            noteForm.resetFields();
            onChanged();
          } catch (e) {
            message.error(formatError(e, "无法保存笔记"));
          }
        }}
      >
        <Form.Item name="content_md" rules={[{ required: true }]}>
          <Input.TextArea rows={3} placeholder={`写在第 ${page} 页`} />
        </Form.Item>
        <Button htmlType="submit">记在这一页</Button>
      </Form>
    </Card>
  );
}

export const NotesPanel = memo(NotesPanelInner);
