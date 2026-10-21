# 循环伏安法编排草稿

当前支持在图形化流程中添加循环伏安法动作，编辑参数、生成英文 JSON、离线预演并保存同名 Markdown。
电化学工作站尚未接入，当前不会设置电位、开启电解池、采集电流或生成实测曲线。

## 编辑方式

1. 在左侧点击“循环伏安法”，将测量动作加入当前流程。
2. 点击任意流程节点，右侧“动作参数”页会显示该动作的表单。
3. 泵只在右侧编辑注入或抽出体积；切换阀在右侧选择目标流路。
4. 测量动作在右侧编辑 CV 参数。画布只显示摘要，可复制、排序和删除。
5. “流程摘要”与“JSON”页仍可查看全流程信息。保存接口会校验全部参数。

参数默认值来自 config/action_templates/cyclic_voltammetry.json。
合成参数示例见 config/examples/demo_cv.json；该文件仅用于开发和参数说明，不包含实际实验数据。

## CV 参数与单位

| JSON 字段 | 含义 | 界面单位 | JSON 单位 |
| --- | --- | --- | --- |
| initial_potential_v | 起始电位 | V | V |
| upper_potential_v | 上限电位 | V | V |
| lower_potential_v | 下限电位 | V | V |
| final_potential_v | 终止电位 | V | V |
| initial_scan_direction | 首先扫描到上限或下限 | positive / negative | 英文标识 |
| scan_rate_v_s | 扫描速率 | mV/s | V/s |
| potential_step_v | 采样电位间隔 | mV | V |
| cycles | 完整扫描循环数 | 圈 | 整数 |
| quiet_time_s | 扫描前静置时间 | s | s |
| current_range_a | 固定电流量程，null 表示自动量程 | µA | A |
| reference_electrode | 电位参照 | 英文标识 | 英文标识 |

例如界面的 100 mV/s 对应 JSON 的 0.1 V/s，1 mV 对应 0.001 V。
参比电极默认 unspecified，表示待确认。默认电位与速率仅为界面编排示例，不是针对某种体系的实验建议。

## 电位程序与预计时间

正向先到上限，再到下限；负向则先到下限，再到上限。随后按圈数在两界限之间往返，最后到终止电位。
起始与终止电位必须位于上下限之间，下限必须小于上限。
扫描速率、采样间隔和固定量程须为正数，静置时间不能为负，圈数须为 1 至 10000 的整数。

预计时间是扫描路程除以扫描速率，再加一次扫描前静置。
该估算不包含通讯、工作站自身额外等待，也不考虑尚未确定型号的采样率和量程限制。
参数面板中的折线表示设定的电位随时间变化；它不是实测电流—电位曲线。超过三圈时仅展示前三圈示意。

## 执行边界

测量动作使用 device=electrochemical_station、operation=measure 和 technique=cyclic_voltammetry。
它属于 Pump Console 的编排模板，不加入真实泵阀的 config/templates，也不加入串口状态刷新列表。

含测量动作的预演计划固定 ready_for_hardware=false，并在 unresolved 中列出工作站未接入。
旧版 lab_automation 控制器会在配置展开阶段拒绝未知测量设备，整条流程不会因忽略测量步骤而提前驱动泵。
现有不包含测量动作的方案继续使用原编译器。网页硬件动作接口仍被禁用。

工作站型号、连接协议、真实电位范围、采样能力及量程适配需在后续接入时确认，当前版本未实现工作站驱动。

## 参数参考

通用 CV 字段参考 [PalmSens 的 CV 参数说明](https://dev.palmsens.com/python/latest/_attachments/reference/methods/cyclic_voltammetry/)。
本项目采用自己的编排字段，不依赖该 SDK，也不声明与任一工作站型号直接兼容。
