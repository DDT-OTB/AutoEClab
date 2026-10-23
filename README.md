# AutoEClab

React 实验编排前端与 Python 后端，包含装置示意图、设备状态监测、方案文件管理、英文 JSON 流程编排、CV 参数草稿和管理员账号管理。当前应用版本为 0.12.0，支持“实验 → 功能块 → 动作”两级编排、功能块 JSON 导入导出，以及管理员可编辑并持久保存的功能块模板库。前置回零、排空与人工确认由必需的首个功能块表达和执行。所有液体合计的预计池内存量上限为 40 mL，每次实验启动前必须排液并由管理员确认空池；包含注射泵时还须完成本次回零校验。公共示例默认关闭硬件执行。

仓库包含源码、测试、合成配置和部署模板。真实实验方案、测量与标定数据、用户账号、密码、证书、服务器地址和运行日志均不纳入版本控制。

## 本地启动

需要 Node.js 22.12 或更新版本，以及提供 `hashlib.scrypt` 的 Python 3.11 或更新版本。

```bash
git clone https://github.com/DDT-OTB/AutoEClab.git
cd AutoEClab
python3 scripts/init_local.py

cd frontend
npm ci
npm run build
cd ..

PYTHONPATH=backend python3 -m pump_console.auth add-user --username admin --role admin
PYTHONPATH=backend python3 -m pump_console.server
```

创建账号时在终端输入临时密码，浏览器打开 **http://127.0.0.1:8765/** 登录。首次登录必须修改密码。没有默认密码，也没有自行注册入口。

`scripts/init_local.py` 会从合成测试配置创建 `data/demo-lab` 和本机 `config/server.json`，再次运行时保留已有本机配置。两者均被 Git 忽略。演示环境不启用串口状态查询，不连接真实仪器。

前端开发时，在另一终端执行：

```bash
cd frontend
npm run dev
```

使用 Vite 输出的本机地址；`/api` 会转发到本机 Python 后端。

## 功能与目录

- 侧边栏以“实验运行”和“实验方案”为两个主要入口：运行页加载流程并监测，方案页合并文件管理与流程编辑；设计草稿和已载入运行流程分别保存于浏览器状态。
- 装置图以电解池为中心，绘制注液泵、排液泵、六通阀和预留的三电极测量连接；点击设备查看详情。
- 设备状态通过 SSE 自动更新，断线后重连与轮询；仅显式的管理员操作可发起白名单状态查询。
- 流程画布展示功能块，右侧编辑块内动作和参数；支持功能块复制、排序与单独导入导出，JSON 使用英文，界面可使用中文。
- 管理员可把当前功能块保存到模板库，也可新建、修改、重命名和删除模板；自定义内容与内置模板修改保存到忽略的 `data/function_blocks/`，不会改变已有实验副本。
- 实验方案以文件列表管理，支持新建、搜索筛选、查看、下载、复制、重命名和删除；同名说明文档同步管理，写入操作有版本检查和备份。
- 注入、抽出、切阀动作继承 JSON 模板；CV 测量动作支持配置和预演，尚不驱动工作站。
- 自动化使用后端固定的方案与设备参数快照；启动、停止均由后端检查管理员权限，逐步事件通过 SSE 更新装置图与设备状态，运行互斥且相同请求不会重复执行。
- 水、硫酸、电解液统一计入电解池容量预算；逐步显示预计存量，超限方案无法保存或启动。启动前独立排空使用显式选择的废液流路，并要求现场空池确认。
- 账号由管理员创建或重置，支持管理员与观测员；新建、重置和修改密码均有后端校验与会话撤销。

```text
backend/pump_console/       Python HTTP API、会话、配置编译与状态监测
frontend/src/              React 前端、装置 SVG、流程编辑与前端测试
config/                    公开示例模板，不含实际部署配置
deploy/                    Nginx、systemd 和 TLS 验证工具
scripts/                   本地初始化与提交内容检查
tests/fixtures/demo-lab/    合成配置和用于测试的控制器代码快照
docs/                      开发、校园网部署和 CV 编排说明
```

运行中的仪器控制工程由本机配置的 `lab_root` 指定。仓库的控制器快照用于配置编译、协议测试和本地演示，不包含真实标定或运行记录。

## 配置与部署

- `config/server.example.json`：本机演示模板，回环访问、强制登录、仪器查询关闭。
- `config/server.campus.example.json`：校园网 HTTPS 模板，使用文档示例地址。
- `config/action_templates/`：测量动作的参数模板。
- `config/function_blocks/`：可导入的功能块模板；`config/examples/demo_blocks.json` 提供完整两级实验示例。
- `data/function_blocks/`：本机模板库的自定义内容、内置模板覆盖和备份，始终忽略，不提交。
- `config/server.json`：本机实际配置，始终忽略，不提交。

功能块编排见 [使用与 JSON 说明](docs/FUNCTION_BLOCKS.md)，真实泵阀执行见 [自动化说明](docs/AUTOMATION.md)，实验方案管理见 [文件管理说明](docs/EXPERIMENT_FILES.md)，校园网部署见 [部署说明](docs/CAMPUS_ACCESS.md)，认证与角色见 [SECURITY.md](SECURITY.md)，CV 参数见 [编排说明](docs/CYCLIC_VOLTAMMETRY.md)。

必需前置功能块见 [前置准备说明](docs/PREFLIGHT_BLOCK.md)。新的页面组织与加载、编辑流程见 [工作区说明](docs/WORKSPACES.md)。界面不再提供设备模板查看入口；动作校验继续使用原有系统配置。

## 测试

```bash
python3 -m unittest discover -s tests -v
cd frontend
npm ci
npm test
npm run build
cd ..
python3 scripts/check_repository.py
```

测试使用临时账号、临时工作目录和合成设备配置。Linux 下可运行全部后端测试；依赖 Linux 进程锁的测试在其他系统上自动跳过。

## 提交内容

提交前运行 `python3 scripts/check_repository.py`。检查只报告文件、行号和问题类型，不打印匹配到的敏感内容。

实际账号数据库、密码、TLS 文件、内部地址、真实实验与标定数据、部署备份和构建产物都应保留在忽略目录中。仓库不包含这些文件，也不包含预设的可用登录账号。
