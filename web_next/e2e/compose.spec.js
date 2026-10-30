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
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 20000 });
}

async function labIdOf(page) {
  return page.evaluate(async () => {
    const existing = localStorage.getItem("labId") || "";
    if (existing) return existing;
    const token = localStorage.getItem("access");
    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    const listed = await fetch("/api/v1/labs", { headers });
    const data = listed.ok ? await listed.json() : {};
    const first = (data.items || data.labs || data || []).find?.((x) => x.id || x.lab_id);
    if (first?.id || first?.lab_id) {
      const id = first.id || first.lab_id;
      localStorage.setItem("labId", id);
      return id;
    }
    const created = await fetch("/api/v1/labs", {
      method: "POST",
      headers,
      body: JSON.stringify({ name: "e2e实验室", code: `e2e-${Date.now().toString(36)}` }),
    });
    if (!created.ok) return "";
    const row = await created.json();
    if (row?.id) localStorage.setItem("labId", row.id);
    return row?.id || "";
  });
}

const modal = (page) => page.locator(".fs-create-modal").first();

async function closeDialog(page) {
  if (!(await modal(page).isVisible().catch(() => false))) return;
  const closer = page.locator(".fs-create-modal .ant-modal-close").first();
  if (await closer.count()) await closer.click();
  else await page.keyboard.press("Escape");
  await expect(modal(page)).toBeHidden({ timeout: 8000 });
}

async function expectCreateModal(page, buttonName, query = "compose=1") {
  await expect(page.getByRole("button", { name: buttonName }).first()).toBeVisible({ timeout: 15000 });
  await expect(modal(page)).toBeHidden();
  await page.getByRole("button", { name: buttonName }).first().click();
  await expect(modal(page)).toBeVisible();
  await expect(page).toHaveURL(new RegExp(query));
  await closeDialog(page);
}

test("列表页筛选不打开创建弹窗，主按钮和深链会打开", async ({ page }) => {
  test.setTimeout(180000);
  await login(page);
  const labId = await labIdOf(page);
  expect(labId).toBeTruthy();

  await page.goto(`/labs/${labId}/datasets?compose=1`);
  await expect(modal(page)).toBeVisible({ timeout: 15000 });
  await expect(page).toHaveURL(/compose=1/);
  await closeDialog(page);
  await expect(page).not.toHaveURL(/compose=1/);

  const composePages = [
    { href: `/labs/${labId}/datasets`, button: "登记数据集" },
    { href: `/labs/${labId}/manuscripts`, button: "创建手稿" },
    { href: `/labs/${labId}/runs`, button: "创建文献调研" },
    { href: `/labs/${labId}/reproduces`, button: "开始复现" },
    { href: `/labs/${labId}/directions`, button: "新建方向" },
    { href: `/labs/${labId}/tasks`, button: "添加任务" },
    { href: `/labs/${labId}/meetings`, button: "写纪要" },
    { href: `/labs/${labId}/members`, button: "邀请加入" },
  ];
  for (const row of composePages) {
    await page.evaluate((href) => {
      const links = [...document.querySelectorAll(`a[href="${href}"]`)];
      const visible = links.find((a) => a.offsetParent !== null);
      (visible || links[0])?.click();
    }, row.href);
    await expect(page).toHaveURL(new RegExp(row.href.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), { timeout: 15000 });
    if (row.after) await row.after();
    const filter = page.locator(".fs-filterbar .ant-select").first();
    if (await filter.count()) {
      await filter.click();
      await expect(modal(page)).toBeHidden();
      await page.keyboard.press("Escape");
    }
    await expectCreateModal(page, row.button, row.query || "compose=1");
  }
});
