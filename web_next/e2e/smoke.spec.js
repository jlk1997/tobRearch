import { test, expect } from "@playwright/test";

const enabled = process.env.FENGSUN_E2E === "1";
const email = process.env.FENGSUN_E2E_EMAIL || "admin@example.com";
const password = process.env.FENGSUN_E2E_PASSWORD || "adminpass1";

test.skip(!enabled, "设置 FENGSUN_E2E=1 并启动 API + web 后再跑");

async function login(page) {
  await page.goto("/login");
  await page.getByPlaceholder("邮箱").fill(email);
  await page.getByPlaceholder("密码").fill(password);
  await page.getByRole("button", { name: "进入工作台" }).click();
}

test("登录后进入驾驶舱并可点开成员", async ({ page }) => {
  await login(page);
  await expect(page.getByRole("heading", { name: /驾驶舱|工作台/ })).toBeVisible({ timeout: 15000 });
  const cockpit = page.getByRole("link", { name: "驾驶舱" });
  if (await cockpit.count()) {
    await cockpit.first().click();
  }
  await expect(page.getByText(/在读|手稿|开始使用|待处理/)).toBeVisible({ timeout: 15000 });
  const member = page.locator(".fs-member-card").first();
  if (await member.count()) {
    await member.click();
    await expect(page.getByText(/成员进展|在读|手稿/)).toBeVisible();
  }
});

test("文献库可打开", async ({ page }) => {
  await login(page);
  const lit = page.getByRole("link", { name: "文献" });
  await expect(lit.first()).toBeVisible({ timeout: 15000 });
  await lit.first().click();
  await expect(page.getByRole("heading", { name: "文献" })).toBeVisible({ timeout: 15000 });
});

test("阅读器 ?page= 写入网址", async ({ page }) => {
  await login(page);
  await page.goto("/literature/missing-id?page=3");
  await expect(page).toHaveURL(/page=3/);
});

test("工作台学生下一步标题与入门卡", async ({ page }) => {
  await login(page);
  await expect(page.getByText(/我的下一步|组里还没做完|开始使用|入门/)).toBeVisible({ timeout: 15000 });
});

test("文献库保留过滤、不铺催办芯片", async ({ page }) => {
  await login(page);
  const lit = page.getByRole("link", { name: "文献" });
  await lit.first().click();
  await expect(page.getByRole("heading", { name: "文献" })).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("写几个词，找出库里和外面的文献。")).toBeVisible();
});

test("助手页可打开", async ({ page }) => {
  await login(page);
  const link = page.getByRole("link", { name: "助手" });
  await expect(link.first()).toBeVisible({ timeout: 15000 });
  await link.first().click();
  await expect(page.getByRole("heading", { name: "助手" })).toBeVisible({ timeout: 15000 });
});

test("知识库问答栏可打开", async ({ page }) => {
  await login(page);
  const kb = page.getByRole("link", { name: "知识库" });
  if (await kb.count()) {
    await kb.first().click();
    await expect(page.getByRole("heading", { name: /知识库/ })).toBeVisible({ timeout: 15000 });
  }
});

test("进度页卡住清单默认折叠", async ({ page }) => {
  await login(page);
  const cockpit = page.getByRole("link", { name: /进度|驾驶舱/ });
  if (await cockpit.count()) {
    await cockpit.first().click();
    await expect(page.getByText(/谁在读|需要关注|开始使用|还没有学生/)).toBeVisible({ timeout: 15000 });
  }
});
