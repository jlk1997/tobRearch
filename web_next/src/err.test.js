import { strict as assert } from "node:assert";
import { test } from "node:test";
import { formatError, humanize } from "./err.js";

test("gateway html is not dumped as Bad Gateway", () => {
  const text = formatError(
    {
      response: {
        status: 502,
        data: "<html><title>502 Bad Gateway</title></html>",
        headers: { "content-type": "text/html" },
      },
    },
    "提炼方法未完成，请再点一次。不要把网关超时当分析成功。"
  );
  assert.equal(text.includes("<html>"), false);
  assert.match(text, /提炼方法未完成/);
});

test("axios timeout uses the analyze fallback", () => {
  const text = formatError(
    { code: "ECONNABORTED", message: "timeout of 270000ms exceeded" },
    "提炼方法未完成，请再点一次。不要把网关超时当分析成功。"
  );
  assert.match(text, /提炼方法未完成/);
});

test("humanize rewrites contract phrases people should not have to parse", () => {
  assert.equal(humanize("还没复现就写成成功"), "写了结果，组里还没跑过");
  assert.equal(humanize("综述没写到 2 篇"), "综述没提到 2 篇");
  assert.equal(humanize("工人未开。已入队不是失败。"), "还没开始跑。点恢复接着做。");
  assert.equal(humanize("等待实验算力"), "在等机器");
  assert.equal(humanize("尚无实测"), "还没跑出结果");
  assert.equal(
    humanize("未注入组内文献或数据集。图仍按单机问题运行。"),
    "还没带上组里的文献或数据。这次只按你写的问题查。"
  );
});

test("classified llm error still shows the model label", () => {
  const text = formatError({
    response: { status: 400, data: { detail: "密钥被供应商拒绝，请测通或更换 Key", error_class: "permission" } },
  });
  assert.match(text, /权限或密钥/);
});
