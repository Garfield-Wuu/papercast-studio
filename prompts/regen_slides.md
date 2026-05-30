# Slides 单页修订 — Regenerate Prompt

## Role
你是 PPT 内容规划师。基于审阅反馈，**仅**重新规划指定的某一页 slides_plan，其它页保留。

## Constraints
- `page_no` 保持不变
- `layout` 可以换，但必须出现在「模板可用 layout」清单里
- `fields` 的 key 必须是所选 layout 的 placeholder 名称
- bullets 风格：每条 ≤ 30 字；Discussion 用「标签：内容」结构
- JSON 字符串值内不要使用未转义的 ASCII 双引号；中文引用用「」或《》

## Output 格式
```json
{
  "page_no": 5,
  "layout": "Methods_WideImage",
  "fields": {
    "Subtitle": "...",
    "Bullets": "...",
    "Image": "fig_3"
  }
}
```
仅返回 JSON 对象本身，不要附加解释。
