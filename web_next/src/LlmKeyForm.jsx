import { useEffect, useState } from "react";
import { Button, Collapse, Form, Input, Select, Space, Tag, message } from "antd";
import { formatError, reportPing } from "./err.js";
import { LoadFailed, whenBlocked } from "./ui.jsx";

export const LLM_PRESETS = [
  {
    id: "minimax",
    label: "MiniMax（国内，推荐）",
    provider: "minimax",
    base_url: "https://api.minimaxi.com/v1",
    model_chat: "MiniMax-M3",
    model_vision: "MiniMax-M3",
    kind: "cloud",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    provider: "deepseek",
    base_url: "https://api.deepseek.com/v1",
    model_chat: "deepseek-chat",
    model_vision: "",
    kind: "cloud",
  },
  {
    id: "qwen",
    label: "通义千问（兼容模式）",
    provider: "qwen",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model_chat: "qwen-plus",
    model_vision: "",
    kind: "cloud",
  },
  {
    id: "openai",
    label: "OpenAI",
    provider: "openai",
    base_url: "https://api.openai.com/v1",
    model_chat: "gpt-4o-mini",
    model_vision: "gpt-4o-mini",
    kind: "cloud",
  },
  {
    id: "ollama",
    label: "本机 / 内网 Ollama",
    provider: "ollama",
    base_url: "http://127.0.0.1:11434/v1",
    model_chat: "qwen2.5",
    model_vision: "",
    kind: "onprem",
  },
  {
    id: "vllm",
    label: "内网 vLLM / SGLang",
    provider: "vllm",
    base_url: "",
    model_chat: "",
    model_vision: "",
    kind: "onprem",
  },
  {
    id: "custom",
    label: "其他（自己填地址）",
    provider: "openai",
    base_url: "",
    model_chat: "",
    model_vision: "",
    kind: "cloud",
  },
];

export function matchLlmPreset(row) {
  if (!row?.configured) return "minimax";
  const url = String(row.base_url || "")
    .trim()
    .replace(/\/+$/, "");
  const provider = String(row.provider || "").toLowerCase();
  for (const p of LLM_PRESETS) {
    if (p.id === "custom") continue;
    const pu = String(p.base_url || "")
      .trim()
      .replace(/\/+$/, "");
    if (pu && url === pu) return p.id;
    if (provider && provider === p.provider && (!url || !pu || url === pu)) return p.id;
  }
  return "custom";
}

function valuesFromRow(row) {
  if (!row?.configured) {
    const p = LLM_PRESETS.find((x) => x.id === "minimax");
    return {
      preset: "minimax",
      provider: p.provider,
      base_url: p.base_url,
      model_chat: p.model_chat,
      model_vision: p.model_vision,
      model_embed: "",
      model_rerank: "",
      kind: p.kind,
      api_key: "",
    };
  }
  return {
    preset: matchLlmPreset(row),
    provider: row.provider || "openai",
    base_url: row.base_url || "",
    model_chat: row.model_chat || "",
    model_vision: row.model_vision || "",
    model_embed: row.model_embed || "",
    model_rerank: row.model_rerank || "",
    kind: row.kind || "cloud",
    api_key: "",
  };
}

function payloadFromForm(v) {
  return {
    provider: (v.provider || "").trim() || "openai",
    base_url: (v.base_url || "").trim(),
    model_chat: (v.model_chat || "").trim(),
    model_vision: (v.model_vision || "").trim(),
    model_embed: (v.model_embed || "").trim(),
    model_rerank: (v.model_rerank || "").trim(),
    kind: v.kind || "cloud",
    api_key: (v.api_key || "").trim() || undefined,
  };
}

export function LlmKeyForm({
  load,
  save,
  test,
  reloadKey,
  saveLabel = "保存",
  keyHint = "控制台里复制的密钥。已保存的不会回显；只改地址或模型时可留空。",
}) {
  const [form] = Form.useForm();
  const [row, setRow] = useState(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const applyRow = (data) => {
    setRow(data || { configured: false });
    form.setFieldsValue(valuesFromRow(data));
  };

  const reload = () => {
    setErr("");
    return load()
      .then((r) => applyRow(r?.data || r))
      .catch((e) => setErr(formatError(e, "无法读取已保存的配置")));
  };

  useEffect(() => {
    reload();
  }, [reloadKey]);

  const applyPreset = (id) => {
    const p = LLM_PRESETS.find((x) => x.id === id);
    if (!p || p.id === "custom") {
      form.setFieldValue("preset", "custom");
      return;
    }
    form.setFieldsValue({
      preset: p.id,
      provider: p.provider,
      base_url: p.base_url,
      model_chat: p.model_chat,
      model_vision: p.model_vision,
      kind: p.kind,
    });
  };

  const configured = Boolean(row?.configured && row?.api_key_masked);

  if (row?.shared) {
    return (
      <div className="fs-llm-form">
        <div className="fs-llm-status">
          <Tag color="success">已保存</Tag>
          <span>密钥 {row.api_key_masked}</span>
          {row.model_chat ? <span className="fs-muted">对话 {row.model_chat}</span> : null}
          {row.base_url ? <span className="fs-muted">{row.base_url}</span> : null}
        </div>
        <p className="fs-muted">体验实验室使用已经配好的模型。提问、读文献、写作和检索都走它，密钥不在这里改。</p>
        <Button
          htmlType="button"
          disabled={testing}
          loading={testing}
          onClick={async () => {
            setTesting(true);
            try {
              const r = await test();
              const ping = reportPing(r.data);
              if (ping.ok) message.success(ping.text);
              else message.error(ping.text);
            } catch (e) {
              message.error(formatError(e, "测通失败"));
            } finally {
              setTesting(false);
            }
          }}
        >
          测一下能不能用
        </Button>
      </div>
    );
  }

  return (
    <div className="fs-llm-form">
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      <div className="fs-llm-status">
        {configured ? <Tag color="success">已保存</Tag> : <Tag>尚未配置</Tag>}
        {configured ? <span>密钥 {row.api_key_masked}</span> : <span>选服务商后填密钥即可</span>}
        {row?.model_chat ? <span className="fs-muted">对话 {row.model_chat}</span> : null}
        {row?.base_url ? <span className="fs-muted">{row.base_url}</span> : null}
      </div>
      <Form
        form={form}
        layout="vertical"
        onFinish={async (v) => {
          const body = payloadFromForm(v);
          if (!body.api_key && !configured) {
            message.error("第一次请填写 API 密钥");
            return;
          }
          if (!body.base_url) {
            message.error("请填写接口地址");
            return;
          }
          if (!body.model_chat) {
            message.error("请填写对话模型名");
            return;
          }
          setSaving(true);
          try {
            const r = await save(body);
            applyRow(r?.data || r);
            form.setFieldValue("api_key", "");
            message.success("已保存（密钥只显示后四位）");
          } catch (e) {
            message.error(formatError(e, "无法保存"));
          } finally {
            setSaving(false);
          }
        }}
      >
        <Form.Item name="preset" label="服务商" extra="选好后会自动填接口地址和模型名，仍可改。">
          <Select options={LLM_PRESETS.map((p) => ({ value: p.id, label: p.label }))} onChange={applyPreset} />
        </Form.Item>
        <Form.Item name="provider" hidden>
          <Input />
        </Form.Item>
        <Form.Item
          name="base_url"
          label="接口地址"
          extra="须是 OpenAI 兼容的 HTTP 地址，一般以 /v1 结尾。MiniMax 国内用 api.minimaxi.com。"
          rules={[{ required: true, message: "请填写接口地址" }]}
        >
          <Input placeholder="例如 https://api.minimaxi.com/v1" autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="model_chat"
          label="对话模型"
          extra="提炼方法、问这一页、多篇对比、写作大纲用这个名字。"
          rules={[{ required: true, message: "请填写对话模型名" }]}
        >
          <Input placeholder="例如 MiniMax-M3" autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="api_key"
          label="API 密钥"
          extra={configured ? `${keyHint} 当前已保存 ${row.api_key_masked}。` : keyHint}
          rules={configured ? [] : [{ required: true, message: "请填写 API 密钥" }]}
        >
          <Input.Password placeholder={configured ? "留空则保留已保存的密钥" : "从服务商控制台复制"} autoComplete="new-password" />
        </Form.Item>
        <Collapse ghost className="fs-llm-more" items={[
          {
            key: "more",
            label: "进阶（看图 / 知识库 / 内网）",
            children: (
              <>
                <Form.Item name="kind" label="部署位置" extra="云端走公网；实验室自己的 vLLM / Ollama 选内网。">
                  <Select
                    options={[
                      { value: "cloud", label: "云端（公网 API）" },
                      { value: "onprem", label: "内网（实验室网关）" },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  name="model_vision"
                  label="看图模型（可选）"
                  extra="图表反馈用。MiniMax-M3 本身能看图，可与对话模型相同。不填则复用对话模型。"
                >
                  <Input placeholder="可与对话模型相同" autoComplete="off" />
                </Form.Item>
                <Form.Item
                  name="model_embed"
                  label="向量模型（可选）"
                  extra="知识库检索用。不填则用普通搜索，不影响入库和公开源检索。"
                >
                  <Input placeholder="例如 text-embedding-3-small 或 bge-m3" autoComplete="off" />
                </Form.Item>
                <Form.Item
                  name="model_rerank"
                  label="重排序模型（可选）"
                  extra="文献推荐微调顺序。不填则按组内语料排序。"
                >
                  <Input placeholder="一般可留空" autoComplete="off" />
                </Form.Item>
              </>
            ),
          },
        ]} />
        <Space>
          <Button type="primary" htmlType="submit" loading={saving}>
            {saveLabel}
          </Button>
          <span className="fs-press-wrap" onClick={whenBlocked(!configured || testing, testing ? "正在测，请稍等" : "先保存，再测能不能用")}>
          <Button
            htmlType="button"
            disabled={!configured || testing}
            loading={testing}
            onClick={async () => {
              setTesting(true);
              try {
                const r = await test();
                const ping = reportPing(r.data);
                if (ping.ok) message.success(ping.text);
                else message.error(ping.text);
              } catch (e) {
                message.error(formatError(e, "测通失败"));
              } finally {
                setTesting(false);
              }
            }}
          >
            测一下能不能用
          </Button>
          </span>
        </Space>
        <p className="fs-hint" style={{ marginTop: 10 }}>
          测通检查的是上次点「保存」后的配置，改完请先保存再测。
        </p>
      </Form>
    </div>
  );
}
