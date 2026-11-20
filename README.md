# 食堂备餐需求预估

季节需求预测的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。

## 过敏餐配餐
在结果复核页批准某日份数后，进入「过敏餐配餐」页建立当日批次：

1. 从已批准分析结果的每日建议份数锁定批次总数。
2. 登记忌口组合（过敏原代码 + 份数）与菜品登记份数；组合合计必须等于当日份数，差额自动记为普通餐。
3. 配餐按「忌口 ∩ 菜品过敏原 = ∅」做组合-菜品容量匹配。标签不明、停用或过敏原版本与批次不一致的菜品暂停分配。
4. 能全部配齐才生成为每一份餐的标签与用料依据；配不齐整批进入「待补」，不会下发半批，也不会漏餐。
5. 整批下发前核对配餐版本与每份标签指纹；替料后旧版本下发直接拒绝。
6. 替料只挂起受影响且未出餐的份并优先改配到替入菜品；已出餐与未受影响份保留原标签，新份另行生成下发单。
7. 批次处于「配餐中」视为中断；恢复后按原登记份数重配，登记容量只读，重试不会重复占用菜品。
8. 页面的「逐份核对用料依据」可按组合、状态、份号/菜品检索每一份的忌口、菜品标签版本、过敏原与分配规则。

菜品标签（过敏原与版本）在目录区维护；标签更新后若与已配餐份不一致，下发会被阻止并挂起受影响份，需要重新配餐。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18213；后端文档 http://localhost:19213/docs；数据库宿主端口 17713。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；MySQL 8.0；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。
测试：`cd backend && python -m pytest tests -q`（使用临时 SQLite 与临时产物目录，不触碰 Compose 数据）。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=meal-seasonal；FRONTEND_PORT=18213；BACKEND_PORT=19213；DB_PORT=17713；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1014/，源码不含日志/截图/测试报告。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
