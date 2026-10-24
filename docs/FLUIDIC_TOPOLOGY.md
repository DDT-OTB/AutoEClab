# 注射泵入口选液与直接排液

当前确认的接法使用 `fluidic_topology=syringe_inlet_selector`：

```text
Water (channel 5) ------\
                         Inlet selector -> Syringe pump -> Cell
Electrolyte (channel 6)-/
Water pump 1 -------------------------------------------> Cell
Sulfuric acid pump 3 -----------------------------------> Cell
Cell -> Waste pump 2 -> One waste bottle
```

六通阀选择注射泵吸入的液体，不位于排液侧。通道 5 是水，通道 6 是电解液。注射泵内部 I/O 阀仍负责“入口吸入、出口注入”，与外部六通选液阀是不同部件。

## 编排

注射泵动作的 `fluid` 可以是 `water` 或 `electrolyte`。每次吸液前必须在流程中显式选择相符的入口；后端在实际吸液前再次回读六通阀状态和通道。流体名称与入口不匹配、没有显式选择或阀位回读不符时，不开始吸液。

```json
[
  {"device":"valve","operation":"switch_route","route":"water"},
  {"device":"syringe_c","operation":"inject","fluid":"water","volume_ml":1},
  {"device":"valve","operation":"switch_route","route":"electrolyte"},
  {"device":"syringe_c","operation":"inject","fluid":"electrolyte","volume_ml":1},
  {"device":"peristaltic_2","operation":"withdraw","fluid":"waste_liquid","volume_ml":2}
]
```

排液动作直接驱动泵 2，不打开或切换入口阀。排液的额外计时比例、流量标定、转向和转速继续来自原有模板。

启动前仍要求按需回零注射泵、标称排空 40 mL 并由管理员现场确认空池。新接法的前置排液 `route` 是 `waste`，不是阀的某个通道。所有液体的预计池内总量仍限制为 40 mL。

## 控制适配与兼容

新接法由 Pump Console 的拓扑编译器和固定执行工作进程执行。底层串口驱动、注射器容量与步数、泵转速及流量标定保持原样。直接排液使用原有计时/标定计算，独立执行，不再依赖废液切阀检查。

离线合成示例及旧废液阀测试使用 `legacy_waste_selector`。旧版 CLI 或旧方案把 5/6 号理解为废液出口，其编排不能直接用于新接法；新模式会拒绝 `waste_1`、`waste_2` 的阀动作，不会把它们误映射成进液来源。

## 迁移

更新实际控制配置前备份阀与泵模板、实验 JSON/Markdown 和私有功能块库。迁移只移除不再适用的废液切阀动作，并在注射泵动作前加入显式入口选择；液体、请求体积及液体动作顺序保持不变。CV 等其他实验参数保留原值。

已有旧前置块改为直接排液 `route=waste`。迁移后的所有方案先离线验证液路、容量及编译结果，再恢复网页硬件启动。改变路由或设备模板会使旧准备记录失效，必须重新准备，不能复用旧回零/排空确认。

## 界面

装置图突出“水/电解液 → 六通阀 → 注射泵 → 电解池”，纯水与硫酸泵使用简化图形，废液只绘制一条直排路径和一个废液瓶。点击电化学工作站，在设备详情中切换 CV 与时间—电流图；“数据管理”保留完整历史曲线与下载。
