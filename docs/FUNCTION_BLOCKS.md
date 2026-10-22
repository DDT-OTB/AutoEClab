# 功能块实验编排

实验采用两级顺序结构：**实验 → 功能块 → 动作**。主画布按功能块展示流程；选择功能块后，在右侧编辑名称、说明和内部动作。点击动作进入参数编辑，点击返回按钮回到动作列表。

## 网页操作

1. 新建实验，填写英文方案 ID 和描述。
2. 在左侧“功能块”中添加模板或空白功能块，也可通过“导入功能块 JSON”追加本机文件。
3. 选择画布中的功能块，在右侧点击“添加动作”，再从左侧“动作”中选择泵、切换阀或 CV 测量。
4. 在右侧点击具体动作编辑体积、流路或测量参数。上下按钮调整块内动作顺序。
5. 画布支持移动、拖动、复制、删除整个功能块；复制会分配新的块 ID，内部参数独立。
6. “导出功能块 JSON”仅导出当前功能块，便于复用；底部“下载 JSON”导出完整实验。
7. “校验与预演”检查整个实验，再保存方案。任何导入、编辑、校验或保存操作均不会自动启动设备。

功能块模板来自应用的 `config/function_blocks/`，提供水洗、废液 1 排液、废液 2 排液和注入电解液的示例。示例参数需要按实验要求审核。导入的功能块作为独立副本追加到当前实验，保存实验时随方案一起保存；导入不会修改服务器上的模板库。以后可导出这个副本再次使用。

## 单个功能块文件

单块文件使用 `schema_version: 1` 与 `kind: "function_block"`。`steps` 使用现有动作模板的字段；转速、标定和设备通讯参数继续由设备 JSON 模板提供。

```json
{
  "schema_version": 1,
  "kind": "function_block",
  "id": "water_rinse",
  "description": "Inject water and drain to waste 1",
  "steps": [
    {"device": "peristaltic_1", "operation": "inject", "fluid": "water", "volume_ml": 10},
    {"device": "valve", "operation": "switch_route", "route": "waste_1"},
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
      "id": "add_water",
      "description": "Inject water",
      "steps": [
        {"device": "peristaltic_1", "operation": "inject", "fluid": "water", "volume_ml": 10}
      ]
    },
    {
      "id": "remove_water",
      "description": "Drain to waste 1",
      "steps": [
        {"device": "valve", "operation": "switch_route", "route": "waste_1"},
        {"device": "peristaltic_2", "operation": "withdraw", "fluid": "waste_liquid", "volume_ml": 12}
      ]
    }
  ]
}
```

完整的示例在 `config/examples/demo_blocks.json`：水洗 → 注入电解液 → 废液 2 排液 → 水洗。此文件为合成方案，不会自动复制到真实实验目录或执行。

同一实验的块 ID 须唯一；同一块内显式提供的动作 ID 须唯一。不同块可复用局部动作 ID。后端展开时为每个执行动作生成 `block_001_step_001` 形式的稳定 ID。实验最多 128 块、1024 个动作；导入文件最多 256 KB。不支持嵌套功能块、文件引用、循环或条件分支。

旧版 `schema_version: 2` 方案仍可由后端读取、校验和执行。网页载入时把原有动作整体放入 `imported_actions` 功能块，原文件在手动保存前保持原样；保存后转为版本 3。不要直接用旧控制器 CLI 读取版本 3 文件，网页后端会统一展开并通过原有控制器编译与执行。

## 全局校验与执行

- 后端按功能块顺序展开所有动作，再统一校验泵阀模板和流路；上一个块的废液流路可由后续块继续使用，排液前仍必须有明确切阀动作。
- 水、硫酸、电解液合计的逐步预计池内存量不超过 40 mL。功能块边界不重置液量预算，最终排液也不能抵消此前超限。
- 每次真实启动前仍须独立执行启动排空，并由管理员确认空池；方案中的排液块不替代这项准备。
- 使用注射泵的实验仍须通过本次回零校验。仅管理员可准备和启动自动化，执行使用不可变快照。
- CV 测量动作可以放入任何功能块并编辑参数；工作站尚未接入时，包含测量的整个实验都不能执行。
- 运行状态仍按实际动作推送；画布显示当前功能块与块内动作序号，已修改或过期的编排不显示匹配运行高亮。

`GET /api/function-blocks` 需已登录，返回公开模板。预演、方案文件管理和准备运行继续使用原有 API，同时接受版本 2 和 3。预演结果的 `blocks` 提供每块的全局动作范围；`plan.steps` 仍是通过现有控制器校验的展开动作。
