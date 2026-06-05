# templates/

PPT 母版 + 解析后的 schema。仓库**只追踪**两个空骨架（`*.example.pptx`），
真实模板（含字体、母版版式、配色）由每个安装自带，不入 git。

## 文件

| 文件 | 是否入库 | 说明 |
|------|---------|------|
| `lab_template.pptx` | 否 | 主模板，pipeline 实际使用 |
| `lab_template_demo.pptx` | 否 | demo 模板（演示页 / 备用） |
| `lab_template.meta.json` | 否 | 由 `scripts/parse_template.py` 从主模板解析得到的 layout schema |
| `lab_template.example.pptx` | 是 | 16:9 空 pptx，仅作占位与测试 fixture |
| `lab_template_demo.example.pptx` | 是 | 同上 |
| `*.backup.pptx` | 否 | 本地编辑前的人工备份 |

## 首次拿到仓库后怎么用

把你自己的 `.pptx` 重命名为 `lab_template.pptx` 放到本目录，然后跑：

```powershell
python scripts/parse_template.py templates/lab_template.pptx
```

会生成 `lab_template.meta.json`。pipeline 启动时会读取这两个文件。

如果只是想跑通构建 / 单测占位，把 `lab_template.example.pptx` 复制为
`lab_template.pptx` 即可（不会有真实排版，但路径解析能通过）。
