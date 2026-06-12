# Report Database Backend

A 股年报「搜索+解析+深度报告」Web 系统的 FastAPI 后端。

## 快速开始

```bash
cd D:/Quant/report_database/backend

# 1) 安装依赖（conda 环境）
pip install -e ".[dev]"

# 2) 配置 .env（拷贝模板后填入 API Key）
cp .env.example .env
# 编辑 .env：填 ANTHROPIC_API_KEY / MINERU_API_KEY

# 3) 启动
uvicorn app.main:app --reload --port 8000

# 4) 打开 API 文档
# http://127.0.0.1:8000/docs
```

## 目录结构

详见 `docs/architecture.md`（项目根 docs 目录）。

## 关键设计

- 单进程单 worker，BackgroundTasks 串行执行
- SQLite 持久化（无 Redis / Celery）
- SSE 推送任务进度（`/tasks/{id}/stream`）
- 复用外部代码：`deep-research-report/shared/tools/`、`report_gen/parser/`、`scripts/split_section3.py`，**不复制代码**

## 阶段进度

- [x] **M1.1** 目录与配置
- [ ] **M1.2** 数据库与 ORM
- [ ] **M1.3** Service 层（搜索/上传/下载）
- [ ] **M1.4** 路由层与 main.py
- [ ] **M1.5** pytest + 端到端验证
