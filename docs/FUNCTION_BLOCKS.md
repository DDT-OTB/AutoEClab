# 功能块实验编排

实验采用两级顺序结构：**实验 → 功能块 → 动作**。主画布按功能块展示流程；选择功能块后，在右侧编辑名称、说明和内部动作。点击动作进入参数编辑，点击返回按钮回到动作列表。

## 网页操作

1. 进入侧边栏“实验方案”，在文件管理中新建方案，或切到“流程编辑”，填写英文方案 ID 和描述。默认“实验编排”会在开头加入必需的前置准备功能块。选择“调试编排”可使用相同的编辑器组合多个功能块和动作，不强制前置准备。
2. 在左侧“功能块”中添加模板或空白功能块，也可通过“导入功能块 JSON”追加本机文件。
3. 选择画布中的功能块，在右侧点击“添加动作”，再从左侧“动作”中选择泵、切换阀或 CV 测量。
4. 在右侧点击具体动作编辑体积、流路或测量参数。上下按钮调整块内动作顺序。
5. 画布支持移动、拖动、复制、删除整个功能块；复制会分配新的块 ID，内部参数独立。
6. “导出功能块 JSON”仅导出当前功能块，便于复用；底部“下载 JSON”导出完整实验。
7. “校验与预演”检查整个实验，再保存方案。需要执行时点击“载入运行”，在独立运行页确认启动。任何导入、编辑、校验、保存或载入操作均不会自动启动设备。

已移除水洗、注入电解液、排至废液 1 和排至废液 2 四个默认模板。功能块库保留管理员自定义内容；也可添加空白块、导入 JSON，并保存为可复用模板。历史示例仅保留在测试夹具中，不加入组件库。导入的功能块作为独立副本追加到当前实验，保存实验时随方案一起保存；只有明确点击模板保存按钮，才会加入可复用模板库。

## 保存和编辑功能块模板

这部分操作仅向管理员开放，后端也会验证会话角色、CSRF 和请求保护头。

- **保存当前功能块**：在画布中选中自定义功能块，右侧点击“保存到功能块库”。检查模板 ID、说明和动作，然后点击“保存模板”。保存后立即出现在左侧功能块列表，刷新页面和重启服务后仍可使用。
- **编辑已有模板**：点击左侧“管理功能块模板”，或模板旁的铅笔按钮。选择模板，编辑说明、动作顺序及各动作参数，再保存。
- **新建模板**：在管理窗口点击“新建模板”，添加动作并逐个编辑参数后保存，无需先导入 JSON 文件。
- **同名处理**：从实验保存的功能块与库内模板同名时，可改 ID 另存，或明确点击“更新此同名模板”。不会自动覆盖已有模板。
- **重命名和删除**：编辑已有模板的 ID 后点击“重命名并保存”；删除按钮只删除库内模板，已有实验和运行快照中的副本继续保留。

模板内容与实验实例相互独立。修改模板只影响以后从库中添加的功能块；不会自动改写已编排、已保存或已准备运行的实验。模板管理窗口有独立草稿，编辑模板也不会替换当前实验的编排内容。

实际自定义内容、对内置模板的修改和删除状态保存在应用数据目录 `data/function_blocks/library.json`，操作前备份位于同目录的 `backups/`。数据目录权限为 0700、文件权限为 0600；这些真实实验配置不会提交到 Git，后续代码部署也应保留。版本检查会拒绝过时的覆盖或删除请求；若提示冲突，刷新模板库并重新选择模板查看最新内容，再决定如何修改。

模板保存会校验动作参数，但功能块可能依赖前一块的流路或液量状态。因此模板保存不代替完整实验的容量、流路和启动前校验，也不会发送设备动作。当前入口阀位于注射泵之前，通道 5 为水、6 为电解液；排液泵直接通向废液瓶，排液不需要切换入口阀。旧液路方案必须重新审核。

## 单个功能块文件

单块文件使用 `schema_version: 1` 与 `kind: "function_block"`。`steps` 使用现有动作模板的字段；转速、标定和设备通讯参数继续由设备 JSON 模板提供。

```json
{
  "schema_version": 1,
  "kind": "function_block",
  "id": "water_rinse",
  "description": "Inject water and drain directly to waste",
  "steps": [
    {"device": "peristaltic_1", "operation": "inject", "fluid": "water", "volume_ml": 10},
    {"device": "peristaltic_2", "operation": "withdraw", "fluid": "waste_liquid", "volume_ml": 12}
  ]
}
```

## 完整实验文件

新实验使用 `schema_version: 3`，顶层为 `blocks`，每块内为 `steps`。块实例只需要 `id`、`description`、`steps`，不重复单块文件的版本与类型字段。

```json
{
  "schema_version": 3,
  "id": "two_block_demo",
  "description": "Synthetic water injection and drainage",
  "notes": ["Review routing and volumes before use."],
  "blocks": [
    {
      "type": "preflight",
      "id": "experiment_preparation",
      "description": "Mandatory experiment preparation",
      "steps": [
        {"device": "syringe_c", "operation": "home_if_used"},
        {"device": "cell", "operation": "drain_to_waste", "route": "waste"},
        {"device": "cell", "operation": "confirm_empty"}
      ]
    },
    {
      "id": "add_water",
      "description": "Inject water",
      "steps": [
        {"device": "peristaltic_1", "operation": "inject", "fluid": "water", "volume_ml": 10}
      ]
    },
    {
      "id": "remove_water",
      "description": "Drain directly to waste",
      "steps": [
            {"device": "peristaltic_2", "operation": "withdraw", "fluid": "waste_liquid", "volume_ml": 12}
      ]
    }
  ]
}
```

完整的示例在 `config/examples/demo_blocks.json`：包含旧液路的水洗与电解液示例，仅用于兼容性测试。实际使用必须按当前入口阀与直接排液构型更新；此文件不会自动复制到真实实验目录或执行。

每个正式实验（默认模式或 `mode: "experiment"`）必须以一个 `type: "preflight"` 功能块开始，详见 [前置准备说明](PREFLIGHT_BLOCK.md)。该块内部步骤固定，只有废液流路可配置；不能用名称相同的普通块替代。

同一实验的块 ID 须唯一；同一块内显式提供的动作 ID 须唯一。不同块可复用局部动作 ID。后端展开时为每个执行动作生成 `block_001_step_001` 形式的稳定 ID。实验最多 128 块、1024 个动作；导入文件最多 256 KB。不支持嵌套功能块、文件引用、循环或条件分支。

旧版方案载入网页时会补齐前置准备，并将原有动作放入 `imported_actions` 功能块；原文件在手动保存前保持原样，保存后转为版本 3。后端不会直接接受缺少前置准备的正式实验。调试方案的模式标记和初始液量会在导入、保存和载入运行时保留，不自动补入前置块。旧版 API 格式须提供 `preflight` 功能块字段。不要直接用旧控制器 CLI 读取版本 3 文件，网页后端会统一展开并通过原有控制器编译与执行。

## 全局校验与执行

- 后端按功能块顺序展开所有动作，再统一校验泵阀模板和流路；上一个块的入口选择可由后续块继续使用；注射泵转移前回读入口通道。排液直接进入废液瓶，不操作入口阀。
- 水、硫酸、电解液合计的逐步预计池内存量不超过 40 mL。功能块边界不重置液量预算，最终排液也不能抵消此前超限。
- 正式实验的启动前准备作为第一功能块执行：按需回零、排空、人工确认空池；普通排液块不能替代它。
- 使用注射泵的实验仍须通过本次回零校验。仅管理员可准备和启动自动化，执行使用不可变快照。
- CV 测量动作可以放入任何功能块并编辑参数；工作站尚未接入时，包含测量的整个实验都不能执行。
- 运行状态仍按实际动作推送；画布显示当前功能块与块内动作序号，已修改或过期的编排不显示匹配运行高亮。

`GET /api/function-blocks` 需已登录，返回合并后的内置与自定义模板；`blocks` 为单块 JSON，`entries` 附带版本和来源。`GET /api/function-blocks/{id}` 返回单个版本化模板。

管理员创建模板使用 `POST /api/function-blocks`，请求为 `{"block": {...}}`。更新或重命名使用 `POST /api/function-blocks/{original_id}`，请求为 `{"block": {...}, "expected_version": "..."}`。删除使用同一路径的 `DELETE`，请求为 `{"expected_version": "..."}`。写入操作记录管理员审计，成功结果始终标记 `hardware_executed=false`。

预演、方案文件管理和准备运行继续使用原有 API，同时接受实验版本 2 和 3。预演结果的 `blocks` 提供每块的全局动作范围；`plan.steps` 仍是通过现有控制器校验的展开动作。


## 调试编排

在“实验方案 → 流程编辑”顶部选择“调试编排”。支持与实验相同的多个功能块、多个动作、复制、排序、JSON 导入导出、保存和模板复用。切换到调试会移除自动加入的前置块，保留普通块；切回实验会重新补入必需的前置准备。

未选择前置块时填写现场的“初始池内液量”。它是人工声明值，不是传感器读数；后端从此数值开始累计所有液体，仍禁止超过 40 mL。不要将有液体的电解池声明为空。可从左侧自行加入前置准备；一旦加入，完整的回零、排空与空池确认仍按该块执行。调试流程中只有这一个前置块，且须位于首位；可删除以恢复无前置准备的调试。

以下为两个功能块的调试示例，仅在预演时不发出设备指令：

```json
{
  "schema_version": 3,
  "mode": "debug",
  "initial_cell_volume_ml": 10,
  "id": "multi_component_debug",
  "description": "Explicit multi-block component debugging",
  "blocks": [
    {
      "id": "water_component",
      "description": "Inject water",
      "steps": [
        {"device": "peristaltic_1", "operation": "inject", "fluid": "water", "volume_ml": 1}
      ]
    },
    {
      "id": "drain_component",
      "description": "Drain directly to waste",
      "steps": [
        {"device": "peristaltic_2", "operation": "withdraw", "fluid": "waste_liquid", "volume_ml": 1}
      ]
    }
  ]
}
```

单独调试注射泵时可明确添加“注射泵回零”动作，JSON 为 `{"device":"syringe_c","operation":"initialize"}`，不接受体积或任意串口指令。运行确认会额外要求确认机械回零的排液条件。只有该动作会发送完整 `ZR`；转移动作遇到错误码 7 不自动回零，也不发送裸 `R`。回零会排出注射器中已有液体，其未知体积不能由预算推断，须现场确认排液位置与余量。

注射泵调试若没有编排切阀，允许使用当前入口，但吸液前必须回读其实际通道与所选液体相符，不匹配即停止。切换液体时请显式编排入口选择。正式实验继续要求明确的入口选择和本次前置校验。

调试准备使用原 `POST /api/execution/prepare`，返回 `operation: "debug_sequence"`。启动使用 `POST /api/execution/debug-start`，请求包含固定的 `run_id`、`version`、本次 `confirm_setup` 和 `confirm_homing_discharge` 布尔确认。管理员权限、CSRF、版本检查、共享设备锁、控制器预检和停止清理与正式实验一致；普通 `/start` 不能跳过调试确认。正式实验也不能使用调试启动接口绕过前置准备。

CV 调试可仅含一个测量动作，此时不会自动打开泵阀或执行回零、排空。曲线仍显示在工作站状态详情，数据记录带 `provenance.execution_mode: "debug_sequence"`。调试回零不生成正式实验的前置证明，后续正式实验仍需重新准备。
