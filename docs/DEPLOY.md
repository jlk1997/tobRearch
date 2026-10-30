# 蜂算科研部署手册

产品名：**蜂算科研**。OpenLens AI 是底层发现式引擎，不出现在用户主界面标题。

## 本机开发

1. Python 3.12+（开发机可用 3.14）。仓库根目录准备 `config.toml`（可由 `config.minimal.toml` 复制，勿提交密钥）。
2. 安装平台依赖：

```
pip install -r platform/requirements.txt
pip install -e .
```

3. 环境变量可参考 `deploy/.env.example`。管理员密码须便于记忆且含字母和数字。

**存储纪律：** 完整实验室以 **Postgres 16 + pgvector** 为准（见下方 Compose）。本机 SQLite 只适合单人开发，知识库会走 hash 向量回退，并发抢单也会退化。不要把 SQLite 文件库当多实验室生产默认。

可选 `REDIS_URL`：只换 `claim_next` 的排队实现（先 pop 再 SQL 兜底），槽位与 `awaiting_gpu` 语义不变。未设置时表轮询仍可用，CI 不强依赖 Redis。`GET /health` 含产品版本（当前 0.2.0）、工人 last tick / lag、`db` / 可选 `redis`，以及响应头 `X-Request-Id`。数据库不可用时返回 503。Compose 里 Postgres 与 api 带 readiness。嵌入式工人与独立 `python -m fengsuan.app.worker` 用 `file_root/worker.lock` 互斥，语义仍是单槽抢单。CI 额外执行 `web_next` `npm ci && npm run build`。可选：`FENGSUN_NIGHTLY=1 pytest -m nightly` 验证工人把 stub 图收成 succeeded；`FENGSUN_E2E=1` 加 Playwright（登录→驾驶舱、文献上次页），不是 TypeScript 重写。
4. 启动 API（先 `pip install -e .`，把 `fengsuan` 装进当前环境；包名必须是 `fengsuan`，避免遮蔽标准库 `platform`）：

```
uvicorn fengsuan.app.main:app --reload --port 8000
```

5. 前端：

```
cd web_next
npm install
npm run dev
```

浏览器打开 Vite 提示的地址。可用种子管理员登录（默认 `admin@example.com` / `changeme1`，或环境变量 `ADMIN_EMAIL` / `ADMIN_PASSWORD`），也可走 `/apply` 申请开通机构后由运营审批。邀请链接形如 `/invite/{token}`。

Windows PowerShell 示例：

```
uvicorn fengsuan.app.main:app --reload --port 8000
```

另开终端启动前端（`cd web_next; npm run dev`）。知识库索引 / 发现式 / 复现需要后台工人时，再开一个终端：

```
python -m fengsuan.app.worker
```

可选 `FENGSUN_AUTO_APPROVE_ORG=1`：申请机构时自动通过（仅本机单人开发，默认关闭以便手测审批队列）。

若本机已有旧版 `data/fengsuan.db`，重启 API 时会自动补齐 `orgs` 入驻列与通知 `payload_json` 等缺列。也可手动升级：在仓库根目录执行 `alembic -c platform/alembic.ini upgrade head`（须在已安装 `fengsuan` 的 conda 环境里）。SQLite 不适合多实验室生产；删库重建亦可：`create_all` 会按新模型建表。

API 进程默认会自带工人（`FENGSUN_WORKER` 未设则为开）。若你单独跑 worker，给 API 设 `FENGSUN_WORKER=0`，避免双跑。

## 完整本地验证

自动化（与 CI 相同，仓库根目录）：

```
pip install -r platform/requirements.txt pytest
pip install -e .
$env:PYTHONPATH=".;platform"
python -m pytest platform/tests tests/unit/test_windows_copytree.py -q
```

或只跑平台套件：`cd platform; python -m pytest tests -q`。

然后启动 API + 前端，用 `ADMIN_EMAIL` 登录，按 [用户手册手测剧本](USER.md) 与 [管理员手册手测剧本](ADMIN.md) 走通：文献阅读 ↔ 语言笔记、手稿 ↔ 语言写回、驾驶舱 / 周报深链、复现等 GPU。无真实大模型 Key 时，自动化已用 stub；手测润色/方法卡需在「个人 Key」或管理页配置兼容接口。

回归测试（cmd）：

```
set PYTHONPATH=.;platform
python -m pytest platform/tests tests/unit/test_windows_copytree.py -q
```

## Docker Compose

在仓库根目录：

```
cd deploy
copy .env.example .env
docker compose up -d --build
```

与现有站点并存（无域名、只开 IP）时，**不要**用上面这份会占用宿主机 80 / 8000 / 5432 的 Compose。改用：

```
cd deploy
docker compose -f docker-compose.cloud.yml up -d --build
```

它只对外映射 **18080**，Postgres 与 API 留在 Docker 网桥内。宿主机 Nginx 的 80 与 `/api` 不动。访问 `http://<公网IP>:18080`。

没有备案域名时，可以用 ngrok 起一条向外的隧道，得到 `https://……ngrok-free.app`。在服务器的 `deploy/.env` 写入 `NGROK_AUTHTOKEN`（不要提交）。要网址固定，再在 ngrok 后台领一个免费域名，写入 `NGROK_DOMAIN`。然后单独执行 `docker compose -f docker-compose.cloud.yml --profile tunnel up -d ngrok`。没令牌不要开这个 profile。隧道只转发 `web`，数据库端口不暴露。`release.py` 不会启动它。免费档可能先出现一页 Visit Site；流量经国外节点，国内打开可能比 IP 慢，原来的 `:18080` 仍可用。UFW 与腾讯云安全组都要放行 18080；不要改 `bittune.conf`。云端 API 设 `FENGSUN_WORKER=0`（不在 API 进程里再开工人），侧车 `worker` 写 `file_root/worker.tick`；`/health` 与入队文案看这份心跳，而不是只看环境变量。长作业期间侧车另有 5 秒心跳，避免把「正在跑」误报成工人未开。`FENGSUN_SEED_GUIDE_LAB=1` 会在空库写入可删除的引导实验室。发现式真正跑图还要平台模型 Key。云端 `Dockerfile.api` 会再装 `platform/requirements-engine.txt` 与 `modules/file1agent`（不装 OpenHands，不改图边）。缺依赖时入队后诚实失败，预检 `engine_ok=false`，不得把 ModuleNotFound 写成模型坏了。实验算力关闭时默认暂停于文献综述。

服务：

- `postgres`：PostgreSQL 16 + pgvector（**完整实验室默认**）
- `api`：FastAPI，`http://localhost:8000`
- `worker`：消费 pending 的发现式 / 复现 / 知识库索引；GROBID 就绪后会重试扫描件并重切关联 KB chunk。可选 `REDIS_URL`，无则 SQL `claim_next`
- `web`：Nginx 反代前端与 `/api`

本机单人开发仍可用 SQLite（`DATABASE_URL=sqlite+pysqlite:///./data/fengsuan.db`），不要用于多实验室。

可选 GROBID（CI 不强依赖）：

```
# deploy/.env 中设置
GROBID_URL=http://grobid:8070
docker compose --profile grobid up -d --build
```

无 `GROBID_URL` 时 PDF 仍用 pypdf；扫描件标记 `unusable`，不生成假方法卡。OCR 为尽力而为（需本机 Tesseract / pdf2image）。

首次启动会写入可删除的引导实验室与管理员账号（`ADMIN_EMAIL` / `ADMIN_PASSWORD`）。

备份：

```
python scripts/backup.py
```

默认写入 `data/backups/<utc>/`。生产请改 `SECRET_KEY`、数据库密码，并限制 CORS（`FRONTEND_ORIGIN`）。

云上每日备份（`python deploy/_cloud_backup_setup.py` 装一次即可）：

- 每天 3:30 `pg_dump -Fc` 导出到 `/opt/fengsuan/backups/db/`，留 7 天；PDF 等文件目录同步到 `/opt/fengsuan/backups/files/`。日志在 `backups/backup.log`。
- 向量随库导出；丢了也不用慌，块上 `embed_model` 标成 `hash` 后后台会按原文重算。
- 恢复：`docker exec -i fengsuan-postgres-1 pg_restore -U fengsuan -d fengsuan --clean --if-exists < fengsuan-YYYYMMDD.dump`，再把 `files/` 拷回数据卷。
- 备份和正式数据在同一台机器上，只防误删不防整机故障；要防整机故障，把 `backups/` 再同步到对象存储。

## 几千篇论文的容量与验收

- 容量检查（只读）：`python deploy/_cloud_scale_capacity.py`，看内存、磁盘、库大小、块数、向量索引。GROBID 占约 3 GB 内存。
- 压力测试：`python deploy/_cloud_scale_stress.py`，在临时库 `fengsuan_stress` 里造 5000 篇 / 约 20 万块，测入库、建索引、检索 p50/p95（目标 95% 在 1.5 秒内），再把评测集跑一遍，跑完删库，不碰正式库。2026-09-26 在现有机器上测过：库大约 1.9 GB，向量索引大约 521 MB，检索一半 0.12 秒、95% 在 0.21 秒内，30 道标准题全对。
- 评测集：`fengsuan.app.services.scale_eval`，10 篇带标准答案的论文、30 道题（找人、引用、原文细节、综述、统计）。本地由 `platform/tests/test_scale_eval.py` 跑；改检索、切块、认人或路由后分数掉了就别上线。
- 分期发布：先 `python deploy/_cloud_backup_setup.py` 备份，再 `python deploy/release.py --services api,worker,web,grobid`，然后跑容量检查确认 HNSW / trigram 索引在、老块已回填 `lab_id`、GROBID `isalive` 为 true。

## GPU

仅管理员配置 `off / internal / external`。实验室与用户不能改拓扑。`off` 且任务需要 GPU 时，复现进入 `awaiting_gpu`，不得伪造指标。

## 不要做的事

- 不启用 Sci-Hub，不绕过付费墙。
- 不把用户 / 实验室 API Key 明文写入日志。
- 不删除 `webui/`（引擎调试控制台）。
- 不改 `openlens_ai/agents/` 流水线语义（仅允许新增 `paper_reproducer/`）。
