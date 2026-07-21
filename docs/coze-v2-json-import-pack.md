# Coze V2 节点 JSON 导入包

## 使用方法

在当前节点右侧点击“JSON 导入”，一次只粘贴当前节点对应的 JSON。这个入口只批量创建当前节点的变量，不会自动创建节点、连线、Prompt 或插件配置。

普通 JSON 无法可靠表达 Coze 的图片类型。以下开始节点字段导入后必须手动改类型：

- `product_images`：`Array<Image/File>`，必填。
- `competitor_images`：`Array<Image/File>`，可选。

代码节点不提供 `Image/File` 输出类型，因此选图和生图结果统一输出 URL String。该 URL 可绑定到接受 URL 的生图插件，但不得直接绑定到要求 `Image` 的 AI 视觉理解输入。

完整 Prompt 和节点连线见 [Coze V2 搭建指南](plans/2026-07-19-coze-shopee-v2-workflow-implementation.md)。

---

## 1. 开始节点：输入

```json
{
  "product_name": "示例商品名称",
  "product_images": [],
  "points": "真实商品资料、卖点、规格、结构数量、包装内容和使用限制",
  "competitor_images": [],
  "platform": "Shopee",
  "zhandian": "SG"
}
```

导入后设置：`product_name`、`product_images`、`points`、`platform`、`zhandian` 必填；`competitor_images` 非必填。

## 2. 单图商品理解：普通文本输入

```json
{
  "product_name": "示例商品名称",
  "points": "真实商品资料"
}
```

另外在“视觉理解输入”添加：

```text
current_image: Image/File
```

绑定：`current_image = 逐图商品证据提取.current_item`。

## 3. 多图汇总与主商品判定：输入

```json
{
  "product_name": "示例商品名称",
  "points": "真实商品资料",
  "image_evidence_list": [
    "单图分析 JSON 字符串"
  ]
}
```

`image_evidence_list` 类型应为 `Array<String>`。

## 4. 解析商品判定 JSON：输入

```json
{
  "raw": "多图汇总节点输出的 JSON 字符串"
}
```

## 5. 解析商品判定 JSON：输出

```json
{
  "decision": "continue",
  "confidence": 90,
  "needs_input_reason": "",
  "product_profile": "{}",
  "identity_lock": "商品身份锁",
  "source_image_index": 0,
  "supporting_image_indexes": [1, 2],
  "standardization_mode": "reuse",
  "standardization_reason": "存在完整清晰商品图"
}
```

确认 `confidence`、`source_image_index` 为 Integer/Number，`supporting_image_indexes` 为 `Array<Integer>`，其余为 String。互补角度索引不得包含主图索引，最多 3 项，只选择同一主外观/SKU 的清晰实物图。

## 6. 选择标准图源：输入

```json
{
  "images": [],
  "index": 0
}
```

`images` 手动设为 `Array<Image/File>`；`index` 为 Integer。

## 7. 选择标准图源：输出

```json
{
  "source_image_url": "https://example.com/product.jpg",
  "source_image_urls": [
    "https://example.com/product.jpg"
  ]
}
```

当前 Coze 代码节点不能声明 `Image` 输出，所以这里不要创建 `source_image: Object` 冒充图片。`source_image_url` 保持 `String`，`source_image_urls` 设为 `Array<String>`，仅供复用和生成式参考图分支使用。

`按索引抠图` 是后来加入的实验批处理，曾只保留商品的一个组件；生产版本 `v0.0.12` 保持该节点断开，不把其 `Array<Image>` 输出绑定到标准图生成节点。当前 `cutout` 与 `semantic_extract` 都使用选中的 `source_image_urls` 进入标准商品图生成，只有 `reuse` 直接复用原图。

批处理前添加 `组装抠图 Prompt`，只输入 `product_name` 和 `identity_lock`，输出 `cutout_prompt: String`。Prompt 必须要求在重复商品、多视角、多款式或其他物体同时存在时只保留一个最大、最清晰、最完整且匹配身份锁的商品实例；禁止包含白底棚拍、改变视角、修复结构或语义重建指令。cutout 的提示词绑定 `cutout_prompt`，不得绑定 `组装标准商品图 Prompt.std_image_prompt`。

## 7A. 复用标准图 URL：输入输出

该代码节点只连在 `标准图策略.如果（standardization_mode = reuse）` 分支。

```json
{
  "source_image_url": "https://example.com/product.jpg"
}
```

输入 `source_image_url: String` 绑定 `选择标准图源.source_image_url`；输出设为 `image_url: String`。

```javascript
async function main({ params }) {
  return {
    image_url: String(params.source_image_url || "")
  };
}
```

## 7B. 组装标准商品图 Prompt：输入输出

该代码节点连在 `标准图策略.否则` 分支，接受 `cutout` 或 `semantic_extract`。其中 `cutout` 表示从选中原图中隔离完整商品主体，`semantic_extract` 表示受身份锁约束地整理/重建标准参考图；两者都必须保持商品身份和完整关键结构。

```json
{
  "product_name": "示例商品名称",
  "mode": "semantic_extract",
  "identity_lock": "商品身份锁",
  "points": "真实商品资料"
}
```

输出设为 `std_image_prompt: String`（16 个字符）。完整代码见搭建指南 Task 4 Step 3。生图插件的 `prompt` 绑定该输出，`image_urls` 绑定 `选择标准图源.source_image_urls`，`asyn` 设为 `true`（插件运行示例定义为同步等待结果）。

## 7C. 标准商品图 URL：变量聚合

```text
聚合策略 = 返回每个分组中第一个非空的值
Group1 = 复用标准图 URL.image_url
       + 标准商品图生成.data.url
```

`标准商品图生成` 与详情图插件使用相同的长时任务配置：`asyn=true`（同步等待）、节点超时 10 分钟、自动重试 0。只设置详情图节点会让 `semantic_extract` 分支仍在标准图生成满 3 分钟后报 `plugin timeout`。

后续的 `standard_product_image_url` 统一绑定 `标准商品图 URL.Group1`。不需要“提取标准图 URL”节点。

## 7D. 组装详情图参考图数组

输入 `standard_url: String = 标准商品图 URL.Group1`、`product_images = 开始.product_images`、`supporting_indexes: Array<Integer> = 解析商品判定 JSON.supporting_image_indexes`；输出 `image_urls: Array<String>`。数组顺序固定为标准主图在前，随后是最多 3 张同一主外观/SKU 的互补角度原图。

```javascript
function toUrl(value) {
  if (typeof value === "string") return value.trim();
  return String(value?.url || value?.image_url || value?.file_url || value?.uri || value?.file?.url || "").trim();
}

async function main({ params }) {
  const standardUrl = String(params.standard_url || "").trim();
  if (!standardUrl) throw new Error("标准商品图 URL 为空");
  const images = Array.isArray(params.product_images) ? params.product_images : [];
  const indexes = Array.isArray(params.supporting_indexes) ? params.supporting_indexes : [];
  const supportingUrls = indexes
    .map(Number)
    .filter(index => Number.isInteger(index) && index >= 0 && index < images.length)
    .map(index => toUrl(images[index]))
    .filter(Boolean);
  return { image_urls: [...new Set([standardUrl, ...supportingUrls])].slice(0, 4) };
}
```

详情图插件的 `image_urls` 绑定该节点输出，`asyn` 设为 `true`（同步等待），节点超时时间设为 10 分钟，自动重试设为 `0`。该数组只能作为批处理体内引用的普通上游变量，不得作为详情图批处理的第二个输入列表。`asyn=true` 不会自动覆盖插件节点默认 3 分钟超时；默认值会导致外部任务仍运行但父工作流已经失败。

## 8. 标准商品图身份检查：第一版跳过

Coze AI 节点的视觉理解输入要求 `Image`，当前生图插件只输出 `data.url: String`，两者不能直接绑定。第一版不创建该 AI 节点和其后的通过条件，直接进入后续流程。

## 9. 竞品视觉策略提取：文本输入

```json
{
  "product_profile": "商品档案 JSON 字符串",
  "points": "真实商品资料"
}
```

另外在视觉理解输入添加：`current_competitor_image: Image/File`，绑定竞品批处理的 `current_item`。

## 10. 竞品策略汇总：输入

```json
{
  "competitor_analysis_list": [
    "竞品逐图分析 JSON 字符串"
  ],
  "product_profile": "商品档案 JSON 字符串",
  "points": "真实商品资料"
}
```

## 11. Shopee 详情页设计 V2：输入

```json
{
  "product_name": "示例商品名称",
  "product_profile": "商品档案 JSON 字符串",
  "identity_lock": "商品身份锁",
  "points": "真实商品资料",
  "zhandian": "SG",
  "competitor_strategy": "可为空的竞品抽象策略"
}
```

## 12. 解析设计数组：输入

```json
{
  "raw": "详情页设计节点输出的 JSON 数组字符串"
}
```

## 13. 解析设计数组：输出

```json
{
  "design_list": [
    "第一张完整中文设计",
    "第二张完整中文设计",
    "第三张完整中文设计",
    "第四张完整中文设计",
    "第五张完整中文设计",
    "第六张完整中文设计"
  ]
}
```

`design_list` 必须为 `Array<String>`。

## 14. 本地化与生图 Prompt 编译：输入

```json
{
  "design": "批处理当前一条设计",
  "identity_lock": "商品身份锁",
  "product_profile": "商品档案 JSON 字符串",
  "zhandian": "SG"
}
```

`design` 必须绑定批处理 `current_item`，不能绑定详情页设计节点的整组输出。

## 15. 单图商品一致性检查：第一版跳过

详情图插件同样只输出 URL String，第一版不创建该视觉 AI 节点。批处理直接汇总生图插件的真实 URL 字段。

## 16. 分支结果汇合与唯一结束节点

Coze 工作流只保留一个结束节点。不要在 needs_input 和 completed 分支各建一个结束，也不要把“输出”节点当作第二个结束。

```text
是否需要补资料.是 → 组装待补资料结果.result ─┐
详情图批处理完成 → 组装生成完成结果.result ─┘
                                   → 最终结果对象.Group1
                                   → 拆分最终结果
                                   → 结束（唯一）
```

`最终结果对象.Group1` 为 `Object`，聚合策略是“返回第一个非空值”。完整代码见搭建指南 Task 8。

唯一结束节点输出：

```json
{
  "status": "completed|needs_input|failed",
  "standard_product_image_url": "https://example.com/standard.png",
  "competitor_summary": "未启用竞品分析",
  "result_image_urls": [
    "https://example.com/detail-01.png"
  ],
  "message": "生成完成或需要补充的资料说明"
}
```

`result_image_urls` 类型为 `Array<String>`，其余四项为 `String`。

`拆分最终结果` 的代码节点输出变量不要直接命名为 `standard_product_image_url`。Coze 当前会把超过 20 个字符的代码节点输出名截断，导致声明名与代码返回键不一致。该节点使用短字段 `standard_image_url: String`，代码读取对象中的 `result.standard_product_image_url` 并返回 `standard_image_url`；唯一结束节点仍保留对外字段 `standard_product_image_url`，其值绑定 `拆分最终结果.standard_image_url`。

2026-07-19 后续编辑发布曾把该输出重新变成被截断的 `standard_product_ima`，导致 ID 6、ID 7、ID 8 虽然执行成功并回写 Listing 图片，标准商品图仍为空。发布前必须在节点输出列表和结束节点绑定处各复核一次短字段，不能只检查代码正文。

2026-07-19 的 ID 12 回归又发现一种同类错配：节点输出列表和结束节点虽然都使用 `standard_image_url`，代码正文却返回 `standard_product_image_url`。Coze 会丢弃这个未声明字段。固定写法是代码读取 `result.standard_product_image_url`，返回键必须为 `standard_image_url`；纯代码单测通过后已发布为 `v0.0.11`。

2026-07-19 的真实异步运行显示，Coze 运行记录 API 可能在这五个字段之外再返回一层大写 `Output` 字符串，并同时带 `node_status`。结束节点仍保持上述五字段契约；中转层必须先解析 `Output` 内的 JSON，再读取 `result_image_urls` 等字段，不能只检查 API 返回对象的顶层。中转兼容已部署并通过回收验证；同一次执行的 7 张 Listing 图片已成功写回飞书。

## 最快搭建顺序

1. 先用第 1 段导入开始节点。
2. 创建“逐图商品证据提取”批处理，在批处理体加入“单图商品理解”。
3. 用第 2–5 段快速建立商品理解和解析变量。
4. 用第 6–8 段建立两路标准商品图路线：`reuse` 复用原图，否则走标准商品图生成；实验抠图保持断开。
5. 首次不接竞品，先跳过第 9–10 段。
6. 用第 11–15 段跑通一条详情图。
7. 确认生图插件真实图片 URL 字段后，按第 16 节汇合分支并配置唯一结束节点。
8. 单商品跑通后再补竞品分支和完整 6–8 张批处理。
