"""模型自主决策侧：执行骨架、纯契约、工具与业务能力通道。

依赖方向自外向内：`harness` → `skills`/`tools`/`files`/`memory`/`persona`/`llm`
→ `spec`/`prompts`。包初始化不放任何 import，避免包级循环导入。
"""
