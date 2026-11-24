# 避震项目文件（ShimTuner Shock Project）

桌面端「避震项目」面板默认从 `文档\ShimTunerProjects` 读写项目；也可用「打开项目」选择任意
`.json` / `.stjson` 文件。项目文件是普通 UTF-8 JSON：

```json
{
  "schema": "shimtuner-shock-project/1",
  "project": {
    "name": "项目名",
    "vehicle": "车型/编号",
    "position": "front | rear | other",
    "valve_type": "mid | base | both",
    "notes": "备注",
    "created_at": "...", "updated_at": "...",
    "reserved": { "对接测功机/PLM 的预留字段": "..." }
  },
  "schemes": {
    "shim_stacks": [
      { "id": "stack-1", "name": "舒适-2片",
        "layers": [ { "d_in_mm": 18, "d_out_mm": 42, "t_mm": 0.2 } ] }
    ],
    "restrictors": [
      { "id": "restr-1", "name": "标准 6×8×4 @16",
        "piston": { "n_ports": 6, "port_width_mm": 8,
                    "port_radial_mm": 4, "port_radius_mm": 16,
                    "bypass_d_mm": 0, "bypass_n": 0, "bypass_Cd": 0.62 } }
    ]
  },
  "active_schemes": { "shim_stack": "stack-1", "restrictor": "restr-1" },
  "case": { "schema": "shimtuner-flow-case/1", "mode": "rod",
            "input": { "...完整工况输入...": "" } }
}
```

## 读取规则

* `project` 中已识别字段写入项目面板（名称、车型、位置、阀类型、备注、时间）；
* `project.reserved` 原样读入内存并显示在项目徽标 tooltip，保存时原样写回；
* `project` 中未识别字段同样原样保留（不因保存丢失）；
* `schemes.shim_stacks` / `schemes.restrictors` 载入方案下拉框，`active_schemes` 选中对应方案；
* 切换方案只是把该方案的值套用到当前输入，随后正常重算；保存项目时会把当前输入同步回
  当前选中的方案。

示例：`示例-前减震.json`（两套阀片组 + 两套限流孔方案）。
