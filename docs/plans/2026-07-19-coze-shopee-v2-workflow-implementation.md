# Coze Shopee Listing 通用出图 V2 搭建指南

按任务顺序逐项搭建并测试；每一步通过后再连接下一步，避免错误进入付费生图节点。

**Goal:** 在 Coze 新建并手动验收一条通用 Shopee Listing 图片工作流，能够处理 1–10 张形态不一的自家商品图、可选竞品图和八个站点，输出 6–8 张 1:1 图片及结构化结果，为后续飞书联动提供稳定接口。

**Architecture:** V2 不修改旧流程。先逐图提取证据，再汇总判断主商品和标准商品图策略；商品不明确时提前结束，明确时生成或复用标准商品参考图。竞品只转成文字策略。详情页设计输出数组，批处理每轮只编译一条本地化生图 Prompt、生成一张图并汇总 URL。当前生图插件只输出 URL String，第一版不接需要 Image 类型的 AI 视觉质检节点。

**Tech Stack:** Coze 工作流、视觉大模型节点、条件节点、批处理节点、JavaScript 代码节点、现有支持参考图的生图插件。

> 生产覆盖说明（2026-07-20）：`v0.0.12` 已按用户确认收敛为两路——`reuse` 直接复用选中的干净原图，`cutout`/`semantic_extract` 进入 `组装标准商品图 Prompt → 标准商品图生成`。下文非生成式 `按索引抠图` 方案保留为实验设计参考，但当前画布保持断开，不是生产连线。

领导汇报用的节点价值、当前完成度和下一阶段边界见 [Shopee Listing 自动出图 V2 领导汇报摘要](../coze-v2-leadership-brief.md)。

## Global Constraints

- 第一版平台固定为 `Shopee`，站点只允许 `SG/MY/TH/VN/PH/ID/TW/BR`。
- 最终只输出 6–8 张 1:1 Shopee Listing 图片；标准商品图是内部参考，不计入产出图数量。
- `points` 是功能、材料、性能、认证、效果、包装与营销声明的唯一事实来源。
- 商品图片可用于识别外观身份；从图片观察到的数量只能作为外观一致性证据，不能自动写成营销声明。
- 竞品图片不得传入标准商品图或最终生图节点。
- 付费生图节点每轮 `n=1`、重试次数 `0`；一致性失败只记录，不自动再次付费。
- `标准商品图生成` 与 `详情图生成` 两个插件节点超时时间都设为 10 分钟；插件字段 `asyn=true` 表示同步等待，但不会自动延长节点默认 3 分钟超时。
- Coze AI 节点的“视觉理解输入”要求真实 `Image`类型；当前生图插件的 `data.url` 是 `String`，不得直接绑定并忽略类型警告。
- API Key、App Secret 和访问令牌不得进入 Coze 普通输入变量或 Prompt。
- 在 Coze 五类手动样例全部通过前，不连接飞书 HTTP 动作，不启用批量生产。

---

## 一、第一版最终节点地图（无竞品）

```text
开始
→ 批处理：逐图商品证据提取
→ 多图汇总与主商品判定
→ 代码：解析商品判定 JSON
→ 条件：是否需要补资料
   ├─ 是 → 代码：组装待补资料结果 ─┐
   └─ 否 → 代码：选择标准图源
            → 条件：标准图策略
               ├─ 如果 reuse → 代码：复用标准图 URL ─────────────────┐
               └─ 否则（cutout / semantic_extract）
                          → 代码：组装标准商品图 Prompt
                          → 标准商品图生成插件 ──────────────────────┘
                         → 变量聚合：标准商品图 URL
                         → 代码：组装详情图参考图数组
            → Shopee 详情页设计
                     → 代码：解析设计数组
                      → 批处理：逐张详情图
                         → 本地化与生图 Prompt 编译
                         → 详情图生成插件
                     → 代码：组装生成完成结果 ─┘
→ 变量聚合：最终结果对象
→ 代码：拆分最终结果
→ 结束（唯一）
```

旧的“图片格式转换”节点不迁移。V2 中只有标准商品图策略需要时才执行参考图清理；设计数组解析与图片处理是两件完全不同的事。当前生产版中，只有 `reuse` 跳过生成；`cutout` 与 `semantic_extract` 都进入标准图生成，并接受生成模型无法绝对保证精确重复部件数量的风险。非生成式抠图仍是后续可验证方向，不在当前生产主线。

---

### Task 1: 新建 V2 与开始节点

**Coze 对象：**
- Create: 工作流 `Shopee Listing 通用出图 V2`
- Configure: `开始`

**Interfaces:**
- Consumes: 运营手动测试输入；后续由飞书中转服务传入相同字段。
- Produces: 六个稳定工作流变量。

- [ ] **Step 1: 新建空白工作流**

不要复制旧工作流。名称填写：

```text
Shopee Listing 通用出图 V2
```

- [ ] **Step 2: 配置开始节点**

| 变量 | 类型 | 必填 | 手动测试值 |
| --- | --- | --- | --- |
| `product_name` | String | 是 | 明确到具体商品或款式 |
| `product_images` | Array<Image/File>；界面若叫“图片列表/文件列表”选对应类型 | 是 | 1–10 张 |
| `points` | String | 是 | 商品事实、卖点、规格、结构数量、包装与限制 |
| `competitor_images` | Array<Image/File> | 否 | 0–10 张 |
| `platform` | String | 是 | 默认 `Shopee` |
| `zhandian` | String | 是 | SG/MY/TH/VN/PH/ID/TW/BR |

不要创建 `yuyan`、`cp_tu`、模型名称、分辨率或 API Key 输入。

- [ ] **Step 3: 保存并做输入自检**

验收：开始节点只显示以上六个变量；`product_images` 和 `competitor_images` 能同时接收多张图片；空竞品列表不会报错。

---

### Task 2: 逐图商品证据提取

**Coze 对象：**
- Create: 批处理 `逐图商品证据提取`
- Inside batch Create: AI 节点 `单图商品理解`

**Interfaces:**
- Consumes: `开始.product_images`、`开始.product_name`、`开始.points`。
- Produces: `image_evidence_list: Array<String>`，每项为一张自家图片的 JSON 证据。

- [ ] **Step 1: 配置批处理**

```text
输入列表 = 开始.product_images
当前项 = 一张 Image/File
并发数 = 1（首次测试）
```

- [ ] **Step 2: 配置单图商品理解节点输入**

```text
product_name = 开始.product_name
points = 开始.points
current_image（视觉理解输入）= 批处理.current_item
```

- [ ] **Step 3: 粘贴单图商品理解系统 Prompt**

```text
# 角色
你是跨境电商商品视觉证据分析师。你只分析当前这一张自家商品图片，目标是帮助后续节点从多张混杂图片中锁定同一个主商品。你不做营销策划，不生成图片，不把包装、说明书、线材或人物误判为主商品。

# 输入
运营填写的产品名称：{{product_name}}
运营填写的真实商品资料：{{points}}
当前图片：通过视觉输入提供

# 分析原则
1. 以 product_name 定位目标商品；points 用于确认商品事实与明确结构。
2. 图片可能是白底商品图、场景图、人物使用图、局部细节、商品与包装混拍、说明书、配件、多个款式或完全无关图片。
3. 包装盒上的照片和文字只能作为辅助证据，不能代替真实商品实物。
4. 区分“商品主体”“包装”“说明书”“线材/配件”“人物/手”“背景道具”“其他商品”。
5. 可以记录图片中实际观察到的颜色、外形、部件位置、Logo、接口和结构数量，但观察值必须带置信度；它们只用于外观一致性，不得自动升级为营销文案。
6. points 明确写出的数量、排列、颜色、接口、配件和不可改变结构优先级最高。图片与 points 冲突时记录冲突，不自行裁决。
7. 看不清、被遮挡或无法确定时输出 unknown/null，不猜测。

# 输出
只输出一个合法 JSON 对象，不要 Markdown、解释或前后缀：
{
  "image_role": "clean_product|mixed_product_package|detail|usage_scene|package_only|manual_only|accessory_only|multiple_variants|unrelated|unclear",
  "contains_target_product": true,
  "target_is_physical_product": true,
  "target_visibility": 0,
  "target_complete": true,
  "reference_quality": 0,
  "background_complexity": "low|medium|high",
  "observed_identity": {
    "category": "",
    "dominant_colors": [],
    "overall_shape": "",
    "materials_visually_observed": [],
    "logos_or_markings": [],
    "controls_ports_connectors": [],
    "distinctive_parts": [],
    "count_observations": [
      {"part": "", "count": null, "confidence": 0}
    ]
  },
  "non_target_objects": [],
  "text_or_package_clues": [],
  "conflicts_with_points": [],
  "recommended_use": "reuse|cutout_source|semantic_extract_source|evidence_only|reject",
  "reason": ""
}

所有 0–100 分数字段必须是整数。没有内容的数组输出 []；不适用的文本输出空字符串；不确定数量输出 null。
```

- [ ] **Step 4: 配置批处理输出**

```text
image_evidence_list = 每轮 单图商品理解.output 的数组
```

验收：上传“商品+包装盒+说明书+线材”混拍图时，主商品、包装、说明书和配件分别识别；不会把包装盒当成每张详情图必须出现的元素。

---

### Task 3: 多图汇总、主商品判定与 JSON 解析

**Coze 对象：**
- Create: AI `多图汇总与主商品判定`
- Create: Code `解析商品判定 JSON`
- Create: Condition `是否需要补资料`

**Interfaces:**
- Consumes: `image_evidence_list`、`product_name`、`points`。
- Produces: `decision`、`confidence`、`needs_input_reason`、`product_profile`、`identity_lock`、`source_image_index`、`supporting_image_indexes`、`standardization_mode`。

- [ ] **Step 1: 配置汇总节点输入**

```text
product_name = 开始.product_name
points = 开始.points
image_evidence_list = 逐图商品证据提取.image_evidence_list
```

- [ ] **Step 2: 粘贴多图汇总系统 Prompt**

```text
# 角色
你是跨境电商商品身份归并与参考图策略专家。你接收产品名称、真实商品资料和多张图片的逐图证据，需要从异构图片中识别目标商品家族，合并互补证据，选择本次生成使用的主外观版本和标准商品图源。

# 输入
产品名称：{{product_name}}
真实商品资料：{{points}}
逐图证据 JSON 数组：{{image_evidence_list}}

# 判断框架
1. 商品身份不变量：用于判断是否属于同一商品家族，包括核心品类、主要用途、主体结构、工作或开合机制、关键部件拓扑、装配关系和功能关系。
2. 商品可变属性：可能随外观版本、规格选项、展示状态或拍摄条件变化。可变属性差异本身不能证明是不同商品家族。
3. 互补证据：不同图片可能只展示商品的一部分信息。只要身份不变量相容，应合并为同一商品家族的补充证据，不要求任何单张图片独立展示全部结构。
4. 真实冲突：只有核心用途、主体结构、工作机制、关键部件拓扑或装配关系互相排斥，才视为商品身份冲突。

# 证据处理规则
1. points 明确写出的功能、材料、性能、认证、效果、规格、包装和营销声明是唯一可宣传事实。
2. product_name 用于定位目标商品，但可能是俗称、上位类目或简写；只要核心品类和用途相符，可依据高置信度图片证据补充更具体的类别描述。
3. 按逐图证据索引逐项评估，优先使用清晰、完整、确实包含真实目标商品且 reference_quality 较高的证据。
4. 包装、说明书和文字线索只作辅助；真实商品实物证据优先。
5. 高置信度证据可以相互补充；低置信度、模糊或被遮挡的观察不得推翻多个高置信度一致观察。
6. 不得因可变属性、拍摄视角、展示状态、环境或构图差异直接拆分商品家族。
7. 不得把不同外观版本拼接成现实中不存在的混合商品。
8. 图片观察只用于身份和外观保持，不得自动升级为消费者营销文案。

# 商品家族与主外观选择
1. 先依据商品身份不变量对有效实物证据归并，确定是否存在一个与 product_name 和 points 相符的目标商品家族。
2. 只要能够确认目标商品家族并找到至少一张可用实物图，就应继续处理；不要求所有图片完全一致，也不要求单一外观版本具备完整视图。
3. product_name 或 points 明确指定具体外观、型号或规格时，优先选择匹配证据。
4. 没有明确指定时，选择 reference_quality、完整度和可见度综合最高的有效实物图所代表的外观版本；同等条件选择索引更小的图片。
5. 其他属于同一商品家族的图片可用于补充共有结构，但主外观的颜色、纹理、标记和装饰只能来自选中的主图证据。
6. source_image_index 必须指向选中的主外观实物图。
7. supporting_image_indexes 用于选择同一主外观版本的互补角度图。只收录能补充正面、背面、侧面、顶部、底部或关键细节且清晰可靠的实物图；排除包装图、说明书、模糊图、重复视角、其他商品和其他颜色/纹理/型号 SKU。不得包含 source_image_index，最多 3 项。

# 身份锁规则
1. identity_lock 先写商品家族共有的不变量，再写选中主外观的可见属性。
2. 共有结构可以综合同一商品家族的多张互补证据。
3. 主外观属性只能依据 source_image_index 对应图片及与其明确一致的证据，不得混入其他外观版本的可变属性。
4. points 明确写出的关键数量和结构必须保留。
5. points 未说明的数量或结构只在图片证据清晰且置信度足够时记录；无法确认时省略，不猜测。
6. identity_lock 必须明确禁止改变核心结构、增减关键部件、错位装配或混合不同外观版本。

# confidence 规则
confidence 衡量“能否确认目标商品家族、选择主外观并建立可靠身份锁”，不衡量图片之间的表面一致程度。
- 90–100：商品家族、主外观和关键身份均清晰可靠。
- 80–89：能够可靠继续，存在非关键视图或细节缺失。
- 60–79：目标家族大概率正确，但关键身份仍有实质歧义。
- 0–59：缺少有效实物证据，或存在无法消解的商品家族冲突。

# 必须进入 needs_input 的情况
- 只有包装印刷图，没有清楚实物。
- 所有实物严重模糊、遮挡或裁切，无法锁定整体结构。
- 有效证据形成多个身份不变量互相排斥的商品家族，且依据 product_name 和 points 无法确定目标。
- 图片内容与 product_name 和 points 的核心品类或用途明显不符。
- 输入明确指定了具体版本，但有效实物证据中不存在匹配对象。
- 关键身份存在真实冲突，无法通过证据质量、互补关系或明确输入消解。

除以上情况外，优先输出 continue，不要因非关键差异过度拒绝。

# 标准图策略
- reuse：存在完整、清楚、背景干净、无多余商品且适合作为参考图的实物图。
- cutout：主商品完整清楚，但与包装、说明书、配件、人物或背景混在一起，可以直接抠出。
- semantic_extract：商品家族和主外观可确认，但没有可直接复用或简单分离的干净完整图片，需要依据主外观和互补结构证据重建参考图。
- needs_input：无法可靠确认。

# 输出
只输出一个合法 JSON 对象：
{
  "decision": "continue|needs_input",
  "confidence": 0,
  "needs_input_reason": "",
  "product_profile": {
    "category": "",
    "appearance": "",
    "shared_structure": [],
    "primary_variant": "",
    "other_variants": [],
    "verified_claims": [],
    "verified_specs": [],
    "usage_relationship": "",
    "included_items": [],
    "restrictions": []
  },
  "identity_lock": "用一段完整中文描述商品家族身份不变量、选中主外观的可见属性和不可改变结构；禁止混入其他外观版本的可变属性；不确定内容不要写入",
  "source_image_index": 0,
  "supporting_image_indexes": [],
  "standardization_mode": "reuse|cutout|semantic_extract|needs_input",
  "standardization_reason": ""
}

source_image_index 和 supporting_image_indexes 使用从 0 开始的逐图证据数组索引。逐图证据数组每一项对应原始上传图片的同位置索引。confidence 为 0–100 整数。decision=needs_input 时 source_image_index=-1、supporting_image_indexes=[]、standardization_mode=needs_input；decision=continue 时 identity_lock 不得为空且 source_image_index 必须有效。只输出 JSON，不要输出分析、Markdown 或代码围栏。
```

- [ ] **Step 3: 配置解析商品判定 JSON 代码节点**

输入：`raw: String = 多图汇总与主商品判定.output`

输出字段：

```text
decision: String
confidence: Integer
needs_input_reason: String
product_profile: String
identity_lock: String
source_image_index: Integer
supporting_image_indexes: Array<Integer>
standardization_mode: String
standardization_reason: String
```

JavaScript：

```javascript
async function main({ params }) {
  const text = String(params.raw || "").trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "");
  const data = JSON.parse(text);
  const sourceIndex = Number.isInteger(data.source_image_index)
    ? data.source_image_index
    : -1;
  const supportingIndexes = Array.isArray(data.supporting_image_indexes)
    ? [...new Set(data.supporting_image_indexes
        .map(Number)
        .filter(index => Number.isInteger(index) && index >= 0 && index !== sourceIndex))]
        .slice(0, 3)
    : [];
  return {
    decision: String(data.decision || "needs_input"),
    confidence: Number(data.confidence || 0),
    needs_input_reason: String(data.needs_input_reason || ""),
    product_profile: JSON.stringify(data.product_profile || {}, null, 2),
    identity_lock: String(data.identity_lock || ""),
    source_image_index: sourceIndex,
    supporting_image_indexes: supportingIndexes,
    standardization_mode: String(data.standardization_mode || "needs_input"),
    standardization_reason: String(data.standardization_reason || "")
  };
}
```

- [ ] **Step 4: 配置主商品是否明确条件**

`如果` 分支的四个条件使用“且”连接：

```text
decision 等于 continue
且 confidence 大于等于 80
且 source_image_index 大于等于 0
且 identity_lock 不为空
```

Coze 右侧固定字符串值直接填写 `continue`，不要填写 `"continue"`，否则引号会成为字符串内容并导致条件不相等。`如果` 分支进入标准图和生图流程；`否则` 分支连接 Task 8 的 `组装待补资料结果`，再与成功分支汇合后进入唯一的结束节点。

---

### Task 4: 标准商品图与图源选择

**Coze 对象：**
- Create: Code `选择标准图源`
- Create: Condition `标准图策略`
- Create: Condition `是否普通抠图`
- Create: Code `复用标准图 URL`
- Create: Code `组装抠图 Prompt`
- Create: Batch `按索引抠图`
- Create inside batch: Condition `是否选中图` + non-generative cutout plugin `标准商品抠图`
- Create: Code `提取抠图 URL`
- Create: Code `组装标准商品图 Prompt`
- Create: Image plugin `标准商品图生成`
- Create: Variable aggregate `标准商品图 URL`
- Create: Code `组装详情图参考图数组`

**Interfaces:**
- Consumes: `product_images`、`source_image_index`、`standardization_mode`、`identity_lock`。
- Produces: `standard_product_image_url: String`。

- [ ] **Step 1: 选择源图代码节点**

输入：

```text
images = 开始.product_images
index = 解析商品判定 JSON.source_image_index
```

输出：`source_image_url: String`、`source_image_urls: Array<String>`

当前 Coze 代码节点的输出类型列表没有 `Image`，因此这个节点只负责为 `reuse` 和 `semantic_extract` 分支提供 URL。不要新增 `source_image: Object` 并绑定到要求 `Image` 的插件输入；`Object` 不会可靠转换成 `Image`。

JavaScript：

```javascript
async function main({ params }) {
  const images = Array.isArray(params.images) ? params.images : [];
  const index = Number(params.index);
  if (!Number.isInteger(index) || index < 0 || index >= images.length) {
    throw new Error("source_image_index 超出 product_images 范围");
  }

  const selected = images[index];
  const url = typeof selected === "string"
    ? selected
    : selected?.url ||
      selected?.image_url ||
      selected?.file_url ||
      selected?.uri ||
      selected?.file?.url ||
      "";

  if (!url) {
    throw new Error(
      "未找到图片 URL，可用字段：" +
      Object.keys(selected || {}).join(",")
    );
  }

  return {
    source_image_url: url,
    source_image_urls: [url]
  };
}
```

- [ ] **Step 2: 配置标准图策略条件**

```text
如果：解析商品判定 JSON.standardization_mode == "reuse"
否则：承接 cutout 或 semantic_extract，连接“组装标准商品图 Prompt”
```

“如果”后添加代码节点 `复用标准图 URL`。该节点只在 reuse 分支执行，将已合格原图的 URL 原样传给聚合节点。

输入：

```text
source_image_url: String = 选择标准图源.source_image_url
```

输出：`image_url: String`

```javascript
async function main({ params }) {
  return {
    image_url: String(params.source_image_url || "")
  };
}
```

以下 `cutout` 批处理方案是实验参考，`v0.0.12` 不连入生产主线。它不能经过代码节点选择图片，因为代码节点无法输出 `Image`；如果后续重新启用，应先单独验证完整主体、索引和 URL 收口。

输入：

```text
product_name = 开始.product_name
identity_lock = 解析商品判定 JSON.identity_lock
```

输出：`cutout_prompt: String`

```javascript
async function main({ params }) {
  const productName = String(params.product_name || "target product").trim();
  const identityLock = String(params.identity_lock || "").trim();

  return {
    cutout_prompt: `Isolate exactly one complete instance of the target product: ${productName}.

Identity reference: ${identityLock}

If the image contains duplicates, multiple views, variants, packaging, accessories, people, hands, props, text, or other objects, keep only the single largest, clearest, most complete product instance that matches the identity reference. Remove everything else.

Preserve the selected instance's exact visible pixels, silhouette, component count, arrangement, proportions, colors, controls, ports, logos, and markings. Do not redraw, repair, extend, merge, add, remove, or invent any product part. Return only the selected product with a transparent background.`
  };
}
```

删除该节点原有的 `mode` 和 `points` 输入；它们与主体分割无关。原 `组装标准商品图 Prompt` 保留在 `semantic_extract` 分支，不与 cutout 共用。

然后添加批处理节点 `按索引抠图`：

```text
批处理输入 images = 开始.product_images（Array<Image>）
并行运行数量 = 1
批处理次数上限 = 10
```

批处理体内添加条件节点 `是否选中图`：

```text
如果：批处理内置 index == 解析商品判定 JSON.source_image_index
```

只在“如果”分支运行 `标准商品抠图`：

```text
上传图 = item (in images): Image
输出图模式 = 透明背景图
提示词 = 组装抠图 Prompt.cutout_prompt
```

这样图片从原始 `Array<Image>` 的当前项原生进入插件，不经过 `Object` 或 String→Image 强制转换。插件必须只删除背景，不改变商品结构、角度、颜色、部件数量和相对位置。

批处理输出：

```text
cutout_images: Array<Image> = 标准商品抠图.data
```

批处理后添加代码节点 `提取抠图 URL`。抠图插件的 `data` 虽在画布上标记为 `Image`，官方定义说明其运行值通常是公开可访问 URL；该节点只做 Image→URL 的单向收口，不再把 URL 转回 Image。

输入：`images = 按索引抠图.cutout_images`

输出：`image_url: String`

```javascript
function findUrl(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    for (const item of value) {
      const url = findUrl(item);
      if (url) return url;
    }
    return "";
  }
  return value.url ||
    value.image_url ||
    value.file_url ||
    value.uri ||
    findUrl(value.data) ||
    "";
}

async function main({ params }) {
  const imageUrl = findUrl(params.images);
  if (!/^https?:\/\//i.test(imageUrl)) {
    throw new Error("抠图结果中未找到可访问的图片 URL");
  }
  return { image_url: imageUrl };
}
```

- [ ] **Step 3: 组装标准商品图 Prompt**

`标准图策略.否则` 后添加代码节点 `组装标准商品图 Prompt`。该分支允许 `cutout` 或 `semantic_extract`；生图插件的 Prompt 字段只绑定该节点的完整字符串输出。

输入：

```text
product_name = 开始.product_name
mode = 解析商品判定 JSON.standardization_mode
identity_lock = 解析商品判定 JSON.identity_lock
points = 开始.points
```

输出：`std_image_prompt: String`（16 个字符，符合 Coze 变量名最多 20 个字符的限制）

```javascript
async function main({ params }) {
  const productName = String(params.product_name || "").trim();
  const mode = String(params.mode || "").trim();
  const identityLock = String(params.identity_lock || "").trim();
  const points = String(params.points || "").trim();

  if (!["cutout", "semantic_extract"].includes(mode)) {
    throw new Error(`不支持的标准图处理模式：${mode}`);
  }

  const task = mode === "cutout"
    ? "Perform product subject isolation. Preserve one complete real product from the selected reference while removing packaging, people, props and environmental clutter. Do not crop to one component or invent structure."
    : "Perform identity-constrained semantic extraction. Use the primary appearance in the reference image to reconstruct one clean, complete, structurally verifiable product reference while preserving product identity. Restore only areas supported by the immutable identity constraints and verified product facts. Do not invent structure, accessories, or appearance.";

  return {
    std_image_prompt: `Edit the reference image only. Do not redesign the product.

Target product name: ${productName}
Processing mode: ${mode}
Immutable product identity constraints: ${identityLock}
Verified product facts: ${points}

Task: ${task}

Keep an accessory only when the verified product facts explicitly identify it as a fixed part of the product. Never add accessories.

Create one clean internal product reference image for downstream detail-image generation. Keep the reference image's existing product pose and camera angle whenever it is usable. Show the product complete, centered, unobstructed, uncropped, and clearly outlined. Use a pure white or very light neutral-gray background, soft commercial studio lighting, and realistic materials.

Do not change the product color, logo, shape, ports, controls, component count, arrangement, relative position, or proportions. Do not fabricate unseen structure. Do not render titles, captions, icons, borders, people, or decorative scenes.`
  };
}
```

- [ ] **Step 4: 配置标准商品图生成插件**

```text
提示词/prompt = 组装标准商品图 Prompt.std_image_prompt
参考图/image_urls = 选择标准图源.source_image_urls
asyn = true（插件运行示例定义为同步等待结果）
整体执行超时 = 600 秒
重试次数 = 0
尺寸 = 1:1 或插件最接近的方形尺寸
分辨率 = 首次测试使用 1k
```

该生成插件服务 `cutout` 与 `semantic_extract`；`reuse` 不进入。标准商品图仅作内部参考，不计入最终 6–8 张详情图。2026-07-19 的 ID 7 实际执行确认，若该节点仍使用默认 3 分钟超时，会在外部任务尚未返回 URL 时报 `plugin timeout`；只修改详情图节点不够。

- [ ] **Step 5: 配置标准商品图 URL 聚合**

添加变量聚合节点 `标准商品图 URL`，并把复用与生成两条分支都连到它。

```text
聚合策略 = 返回每个分组中第一个非空的值
Group1 第 1 个值 = 复用标准图 URL.image_url
Group1 第 2 个值 = 标准商品图生成.data.url
```

后续节点中的 `standard_product_image_url` 统一绑定 `标准商品图 URL.Group1`。2026-07-19 的 ID 12 回归确认，选择图源、复用 URL 和聚合绑定都能得到值；本次空值不是插件异步结果未就绪，而是最后的拆分代码返回键与节点短输出定义不一致。

在聚合节点后添加代码节点 `组装详情图参考图数组`，将标准主图与最多 3 张同一主外观版本的互补角度图组成参考图数组。不要把全部上传图片无筛选地传给生图模型。

```text
输入 standard_url: String = 标准商品图 URL.Group1
输入 product_images = 开始.product_images
输入 supporting_indexes: Array<Integer> = 解析商品判定 JSON.supporting_image_indexes
输出 image_urls: Array<String>
```

```javascript
function toUrl(value) {
  if (typeof value === "string") return value.trim();
  return String(
    value?.url ||
    value?.image_url ||
    value?.file_url ||
    value?.uri ||
    value?.file?.url ||
    ""
  ).trim();
}

async function main({ params }) {
  const standardUrl = String(params.standard_url || "").trim();
  if (!standardUrl) throw new Error("标准商品图 URL 为空");

  const images = Array.isArray(params.product_images)
    ? params.product_images
    : [];
  const indexes = Array.isArray(params.supporting_indexes)
    ? params.supporting_indexes
    : [];

  const supportingUrls = indexes
    .map(Number)
    .filter(index => Number.isInteger(index) && index >= 0 && index < images.length)
    .map(index => toUrl(images[index]))
    .filter(Boolean);

  return {
    image_urls: [...new Set([standardUrl, ...supportingUrls])].slice(0, 4)
  };
}
```

详情图批处理的输入列表仍然只能是 `design_list`。`image_urls` 在批处理体内直接引用这个节点的完整数组，不要把它配置成第二个批处理输入列表，否则 Coze 会按最短数组长度执行，重新只生成一张详情图。

完整连线：

```text
标准图策略.如果 → 复用标准图 URL → 标准商品图 URL
标准图策略.否则 → 组装标准商品图 Prompt → 标准商品图生成 → 标准商品图 URL
```

- [ ] **Step 6: 跳过 AI 标准图视觉质检**

当前 `标准商品图生成.data.url` 和 `标准商品图 URL.Group1` 都是 `String`，而 AI 节点的视觉理解输入要求 `Image`。第一版不创建 `标准商品图身份检查`和 `身份检查是否通过`，直接将 `标准商品图 URL` 连到后续竞品分支或详情页设计。单商品联调时人工打开标准图 URL 抽查商品身份。

只有后续增加“URL 转 Image”节点、换成直接输出 `Image`的生图插件，或改用原生支持读取公网 URL 的视觉 API 时，再恢复自动质检。

---

### Task 5: 可选竞品策略分析

> 第一版暂不搭建本 Task。`标准商品图 URL` 直接连接 `Shopee 详情页设计 V2`。设计节点第一版不创建 `competitor_strategy` 输入，也删除系统/用户 Prompt 中的竞品策略输入行与竞品规则；无竞品主流程跑通后再按本节追加整个分支。

**Coze 对象：**
- Create: Condition `是否有竞品图`
- Create: Batch `逐张竞品分析`
- Inside batch Create: AI `竞品视觉策略提取`
- Create: AI `竞品策略汇总`

**Interfaces:**
- Consumes: `competitor_images`、`product_profile`、`points`。
- Produces: `competitor_strategy: String`；没有竞品时输出空字符串。

- [ ] **Step 1: 配置竞品条件**

```text
有竞品：competitor_images 的长度 > 0
无竞品：为空或长度 = 0
```

- [ ] **Step 2: 配置逐张竞品分析批处理**

输入列表：`开始.competitor_images`；视觉输入绑定 `current_item`；并发首次设为 1。

系统 Prompt：

```text
你是跨境电商竞品详情图策略分析师。分析当前竞品图片的销售表达方法，只提取可复用的抽象策略：信息层级、画面职责、构图类型、镜头景别、商品占比、人物与商品关系、场景选择、文案密度、卖点证明方法、配色节奏和整套页面顺序线索。

严禁复制或描述可用于复刻的竞品品牌名、Logo、具体文案、独特产品外观、包装设计、人物身份、商标元素或受保护视觉资产。严禁把竞品图作为我方商品外观参考。当前我方商品资料仅用于判断哪些策略可能适用：{{product_profile}}；{{points}}。

只输出一个合法 JSON：
{"visual_roles":[],"composition_patterns":[],"selling_methods":[],"scene_patterns":[],"copy_density":"","useful_ideas":[],"ideas_to_avoid":[]}
```

- [ ] **Step 3: 配置竞品策略汇总**

输入 `competitor_analysis_list` 为批处理输出数组。

系统 Prompt：

```text
把竞品逐图分析汇总为一段只针对我方商品的抽象视觉策略。删除重复项、品牌、Logo、竞品原文和竞品外观描述；只保留可复用的页面职责、构图、场景、证明卖点的方法、信息密度和节奏。所有策略必须受我方 points 事实边界约束，不能因为竞品展示了某功能就假设我方也有。

只输出中文纯文本，250–500 字；没有可靠可用策略时输出空字符串。
```

---

### Task 6: Shopee 详情页设计与数组解析

**Coze 对象：**
- Create: AI `Shopee 详情页设计 V2`
- Create: Code `解析设计数组`

**Interfaces:**
- Consumes: `product_name`、`product_profile`、`identity_lock`、`points`、`zhandian`。
- Produces: `design_list: Array<String>`，6–8 条，每条是一张图的完整中文设计稿但不是最终生图 Prompt。

第一版无竞品主流程只消费 `product_name`、`product_profile`、`identity_lock`、`points`、`zhandian`；`competitor_strategy` 是主流程成功后的追加输入。

- [ ] **Step 1: 配置设计节点输入**

```text
product_name = 开始.product_name
product_profile = 解析商品判定 JSON.product_profile
identity_lock = 解析商品判定 JSON.identity_lock
points = 开始.points
zhandian = 开始.zhandian
```

- [ ] **Step 2: 粘贴详情页设计系统 Prompt**

```text
# 角色与任务
你是资深 Shopee 跨境电商 Listing 视觉转化策划师，负责 SG、MY、TH、VN、PH、ID、TW、BR 八个买家市场。你要为当前真实商品设计 6–8 张 1:1 方形 Listing 图片，每张承担不同购买决策任务，并能在下游被编译为一条完整生图 Prompt。

你要积极设计商品怎么拍、如何使用人物或真实环境、怎样用可见证据表达卖点、怎样让整套有节奏和销售力；不能只写限制清单，也不能套用某个固定品类的功能、档位、参数或场景。

# 输入
商品名称：{{product_name}}
商品档案：{{product_profile}}
商品身份锁：{{identity_lock}}
唯一营销事实来源：{{points}}
Shopee 站点：{{zhandian}}

# 八站正向视觉映射
SG：现代都市公寓、HDB 风格住宅或整洁办公环境；浅灰、米白、冷调浅木；多元成年消费者；高效、克制、精致。
MY：现代热带公寓或排屋；暖灰、米白、浅咖、绿植与自然日光；多元成年消费者；舒适、亲切、轻奢但不浮夸。
TH：温暖明亮现代住宅或城市生活空间；暖白、浅木、浅草绿、奶油黄；自然成年消费者；轻盈、亲和、有生活感。
VN：紧凑整洁的现代城市公寓、家庭工作区或通勤生活；暖白、浅木、柔和米灰；清透、实用、年轻。
PH：明亮通风现代热带住宅或轻户外空间；纯白、浅青、暖米、浅橙；自然成年消费者；友好、阳光、轻快。
ID：温暖实用的现代住宅、工作区或收纳空间；暖浅灰、奶油白、浅木；自然得体的成年消费者；可靠、大众、温暖。
TW：现代小户型、公寓、书桌或城市生活空间；莫兰迪灰、奶白、浅木、雾霾绿；自然成年消费者；细腻、安静、有质感。
BR：明亮现代公寓、阳台、庭院或相关轻户外环境；燕麦白、暖米灰、浅木，低饱和绿/珊瑚/蓝点缀；多元成年消费者；亲和、有活力但不杂乱。

不要加入国旗、地标、民族服饰、宗教符号、刻板面孔、无关招牌或文化道具。

# 商品与事实规则
1. points 是功能、材料、性能、认证、效果、包装、配件和消费者文案的唯一来源。
2. identity_lock 必须在每一张设计中执行；不能增加、减少、复制、合并或改变商品关键部件、数量、排列、颜色、Logo、接口和相对位置。
3. 商品档案用于理解使用关系和外观，但不能把图片观察自动强化为营销承诺。
4. 包装盒、说明书和配件只在 points 明确需要展示“包装内容/配件清单”时出现；不得因为输入图中有包装就让每张图都出现包装。

# 6–8 张递进结构
本流程中的第一张承担 Shopee 商品主图/首图作用，全部输出统一为 1:1。第一张可根据品类和真实用途采用干净商品英雄图或真人使用场景。

必须包含六个互不重复的销售任务：
1. 第一眼价值：商品是什么、最核心且已证实的价值是什么；采用高质量商品英雄视角或自然使用场景。对必须穿戴、接触身体、手持或由人物操作才能理解用途的商品，优先采用正确真人使用英雄图。
2. 核心收益：选择 points 中最重要的一个卖点，用商品状态或真实可见证据解释。
3. 事实证明：使用结构、细节、操作状态、组合关系或实物信息证明另一个卖点。
4. 使用理解：根据真实商品动态选择人物、手部、安装、摆放、收纳、携带或操作过程，让消费者看懂关系和尺度。
5. 细节信任：展示影响购买判断的做工、纹理、开合、连接、内部空间、边角、配件关系或其他已确认细节。
6. 场景体验与收尾：在目标站点自然生活、工作、出行或使用环境中展示拥有商品后的真实状态。

只有 points 明确提供对应事实时才增加：
7. 规格/适配/选型/护理。
8. 包装内容/配件清单/下单确认/使用提醒。

资料不足就输出六张，不为凑数量虚构功能；同一卖点不得换句话重复。

# 人物与场景
人物不是禁止项，也不是每张必需。根据商品真实使用关系、人物是否帮助理解，以及使用风险动态决定：
- 商品需要穿戴、手持、携带、接触身体、涂抹、操作或人物尺度才能理解时，6–8 张中安排 2–3 张真人或手部使用场景，前两张至少一张；第一张允许直接采用真人使用英雄图。
- 人物不能帮助理解用途、尺度、佩戴、操作或使用结果时，不为追求氛围强行加人。
- 对危险、受管制、具有刺激性、需要专业资质或特殊防护条件的商品，不生成轻松生活化人物使用场景。只有 points 明确提供可核验的安全使用条件和防护要求时，才可制作相应规范操作画面；否则采用无人商品展示、包装信息或合规细节图。
- 只有外观、结构、细节、规格或包装说明图才可明确要求无人。不得为了构图简洁把必须由人物演示的商品改成桌面摆件。

人物必须为自然成年消费者，动作和商品接触关系正确，商品仍是主角且关键结构无遮挡。其他商品根据真实用途选择人物、真实空间、安装位置、桌面、收纳、组合或尺度关系。相邻两张至少改变场景、机位、景别、商品朝向、人物姿态和信息结构中的三项。

# 文案与合规
每张只规划一个短主标题、一个可选副标题和最多三个短标注。先以中文写文案源，后续节点负责母语本地化。文案只能来自 points，不得生成最高级、绝对化、医疗治疗、安全保证、官方认证、效果保证、价格折扣或售后承诺，除非 points 提供了可核验且平台允许的事实。不得制作误导性前后对比。

# 每项设计字符串必须包含
- 消费者文案源：主标题、副标题、短标注。
- 画面目标：这一张让消费者理解什么。
- 商品呈现：完整/局部、角度、朝向、动作和身份锁执行方式。
- 人物与场景：人物/手部/无人、动作、环境、道具和本地化氛围。
- 构图：1:1、景别、主体占比、文字区域、信息层级和与相邻图片的差异。
- 视觉风格：真实商业摄影、电商精修、光线、材质、整套统一规则。
- 本图防错：只写与当前图有关的结构、遮挡、文字或场景风险。

字符串中不得出现“第几屏、引流屏、功能转化屏、场景体验屏、信任促单屏、模块、内部用途、销售目标、screen、module、layout”等会污染最终图片的内部标签。不要要求图片显示这些词。

# 输出格式
只输出一个合法 JSON 字符串数组，包含 6–8 个非空字符串。数组顺序就是详情页顺序。每个字符串 350–700 个中文字符，必须是一张图的完整具体设计，不是标题列表或抽象分析。不要 Markdown、不要外层对象、不要说明文字；正确转义字符串中的引号、换行和反斜杠。
```

- [ ] **Step 3: 配置设计数组解析代码节点**

输入：`raw = Shopee 详情页设计 V2.output`

输出：`design_list: Array<String>`

JavaScript：

```javascript
async function main({ params }) {
  const text = String(params.raw || "").trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "");
  const list = JSON.parse(text);
  if (!Array.isArray(list) || list.length < 6 || list.length > 8) {
    throw new Error("详情页设计必须是 6–8 项 JSON 数组");
  }
  const clean = list.map((item, i) => {
    if (typeof item !== "string" || !item.trim()) {
      throw new Error(`第 ${i + 1} 项不是非空字符串`);
    }
    return item.trim();
  });
  return { design_list: clean };
}
```

验收：批处理输入必须绑定 `解析设计数组.design_list`，绝不能绑定设计节点的整段 JSON 文本。

---

### Task 7: 批量本地化生图与 URL 汇总

**Coze 对象：**
- Create: Batch `逐张详情图生成`
- Inside batch Create: AI `本地化与生图 Prompt 编译`
- Inside batch Create: Image plugin `详情图生成`

**Interfaces:**
- Consumes: `design_list`、`identity_lock`、`standard_product_image_url`、`zhandian`。
- Produces: `result_image_urls: Array<String>`。

- [ ] **Step 1: 配置批处理**

```text
输入列表 = 解析设计数组.design_list
current_item 类型 = String
首次联调最大执行次数 = 1
首次联调并发数 = 1
单张联调已经通过后：批处理次数上限 = 8，并发数仍为 1；输入有 6 项就执行 6 次，输入有 8 项就执行 8 次
```

- [ ] **Step 2: 配置本地化与生图 Prompt 编译输入**

```text
design = 批处理.current_item
identity_lock = 解析商品判定 JSON.identity_lock
product_profile = 解析商品判定 JSON.product_profile
zhandian = 开始.zhandian
```

- [ ] **Step 3: 粘贴本地化与生图 Prompt 编译系统 Prompt**

```text
# 角色
你是 Shopee 八站母语级电商文案编辑与 AI image-generation prompt compiler。你接收一张中文详情图设计稿，将消费者可见文案改写为目标站点自然母语，并把所有画面控制信息编译成一条可直接交给国际生图模型的英文 Prompt。你不是逐字翻译器，也不重新发明商品、卖点或结构。

# 输入
当前单张设计：{{design}}
商品身份锁：{{identity_lock}}
商品档案：{{product_profile}}
站点：{{zhandian}}

# 站点语言
SG：自然简洁的新加坡电商英语。
MY：自然 Bahasa Malaysia，不使用印尼语地区词。
TH：自然现代泰语，词序符合泰语购物表达。
VN：带完整声调的自然越南语。
PH：自然友好的菲律宾电商英语，不自行混入 Taglish。
ID：自然标准 Bahasa Indonesia，不使用马来西亚马来语地区词。
TW：台湾繁体中文与当地常用商品表达，不出现简体或大陆地区用语。
BR：巴西葡萄牙语 pt-BR，不使用欧洲葡萄牙语表达。

# 编译任务
1. 从设计稿中提取一个主标题、一个可选副标题和最多三个短标注。根据目标站点进行母语级改写，而不是机械直译；优先使用当地消费者熟悉、简短、手机端易读的商品表达。
2. 保持品牌、型号、数字、单位、限定条件和宣传强度；不得补充输入没有的功能、容量、效果、认证、保证或促销承诺。
3. 输出前静默检查目标语言的语法、拼写、词序、地区词汇、大小写、标点和自然度；发现生硬直译时主动缩短并改写，但不得改变事实。不要输出检查过程。
4. 将画面目标、商品呈现、人物与场景、构图、镜头、光线、配色、材质、摄影风格和本图防错全部写成清晰、自然、具体的英文生图指令。除最终需要画在图片上的目标站点文案、真实品牌名和型号外，输出不得出现中文或其他说明语言。
5. 将 identity_lock 和 product_profile 中与当前画面有关的事实准确转换为英文约束，不得弱化、遗漏或自行扩写。标准商品参考图是唯一外观基准；不能增加、减少、复制、合并、错位或改变关键结构、数量、颜色、标记和比例。
   - 对 identity_lock 或 product_profile 中所有明确的部件数量，必须在英文 Prompt 中写成“exactly + 数量 + 明确部件名称”，并紧接一句“no extra, missing, merged or duplicated components”。不得提及任何其他候选数量。
   - 对围绕主体重复排列、容易重复生成的数量关键部件，还必须描述一对一连接拓扑：主体具有准确数量的连接位，每个连接位只连接一个对应部件，不得从主体背后、遮挡区或不存在的连接位额外伸出部件。最终英文可使用：Each component must originate from exactly one visible attachment point on the main body, with one component per attachment point and no hidden extra attachment points.
   - 当前画面需要完整展示精确数量结构时，选择能让各部件彼此分离、便于目视计数的正面、俯视或三分之四机位，避免因严重遮挡、极端透视或重叠造成重复部件。
   - 不得为了“所有端点都可见”而违反自然遮挡或补画额外部件。数量核对优先于端点全显；人物、头发、手部和极端透视不能造成部件根部难以计数。
6. product_profile 中的 usage_relationship 是商品真实使用关系的强约束。先判断当前 design 属于真实使用场景还是商品静态展示：
   - 真实使用场景必须准确描述商品与人物、身体部位、手、承载面、安装位置或配套物体之间经资料确认的佩戴、接触、握持、安装、悬挂、收纳、放置、朝向和受力关系。需要人物或身体部位才能解释用途时必须出现，不能为了画面简洁改成桌面摆拍。
   - 商品静态展示只能用于外观、结构、细节或规格说明。应采用参考图支持的中性展示姿态、合理平放、悬浮或支撑方式；不得让任何非承重、非支撑用途的功能部件充当未经证实的底座或支撑结构，也不得把商品表现成可自行站立的生物、机器人、家具或装饰摆件。
   - 当前 design 与 usage_relationship、identity_lock 或 product_profile 冲突时，必须静默纠正 design，以已验证商品事实为准。参考图只锁定商品外观和结构，不代表参考图中的悬空或摆放姿态就是商品使用方式。
   - 若 usage_relationship 为空或证据不足，只能采用不会暗示新用途的中性商品展示，禁止根据商品形状猜测使用方式。
   - 对危险、受管制、具有刺激性、需要专业资质或特殊防护条件的商品，不得自动增加轻松生活化人物使用场景；points 未提供可核验的安全条件和防护要求时，改为无人中性展示。
7. 输出英文 Prompt 时必须明确写出 verified real-world usage relationship、商品朝向、接触点和禁止的错误摆放方式。对真实使用场景必须包含：Show the product in its verified real-world use position and contact relationship. Do not depict it as a freestanding object unless the verified product facts explicitly say it is freestanding.
8. 固定 1:1 方形画布、画面铺满画布，采用真实商业产品摄影和成熟 Shopee Listing 视觉设计。商品必须完整清楚，关键结构无遮挡；场景、人物和道具必须服务于当前卖点，不能抢主体。
9. 只允许图片显示最终本地化后的消费者文案，以及参考商品本身真实存在的品牌/型号标记。不得显示中文设计说明、站点代码、语言名称、序号、页面类型、内部标签、字段名或 Prompt 指令。
10. 在英文 Prompt 中明确写出：Only render the quoted localized copy below; do not render the labels Headline, Subheadline or Callouts, and do not add any other text。随后只列出实际需要显示的目标站点文字；原设计没有的副标题或标注不得强行补齐。
11. 相邻图片的设计差异已经由上游提供。在不违反商品真实身份、结构和使用关系的前提下执行当前 design；不要把当前图片改成通用白底图，也不要复制上一张的场景、构图或文字结构。

# 英文 Prompt 固定组织顺序
1. 第一段先写参考图优先级、商品身份、外观结构和所有精确部件数量；不得先写场景。
2. 第二段写已验证的真实使用关系、商品朝向、接触点、人物或支撑关系；纠正 design 中与商品事实冲突的摆放方式。
3. 第三段再写场景、构图、机位、主体占比、光线、材质和视觉风格。
4. 第四段列出唯一允许显示的本地化消费者文案。
5. 最后一段用一句简短英文再次强调精确数量、不得复制部件、不得改变结构和不得采用错误使用姿态；不要堆叠同义否定词。

# 输出格式
只输出一条完整纯文本 Prompt，不输出 JSON、Markdown、分析、翻译说明或前后解释。整条 Prompt 的画面描述、摄影指令、商品约束和禁止事项必须使用英文；唯一允许出现的非英文内容是需要实际显示在图片上的目标站点文案和参考商品原有的真实品牌/型号。
```

2026-07-19 运行版本核对补充：`zhandian` 变量绑定本身正常。浏览器运行页只渲染富文本编辑器当前可见区域，不能据此判断后续系统提示词被删除；用户提供的 432 行完整原文确认八站映射、商品身份、文字渲染和输出格式规则都存在。为降低 TH/VN 等非英语站点仍输出英语的风险，完整原文已进一步加入目标语言硬映射和输出前语言闸门。发布但不试运行；回归时先检查本地化节点输出语言，再判断问题位于本地化模型还是生图模型。

- [ ] **Step 4: 配置详情图生成插件**

```text
prompt = 本地化与生图 Prompt 编译.output
参考图/image_urls = 组装详情图参考图数组.image_urls
asyn = true（插件运行示例定义为同步等待结果）
size = 1:1
resolution = 1k（首次测试）
超时时间 = 10 分钟
重试次数 = 0
```

不要传入 `product_images` 整组、`competitor_images`、设计 JSON 数组或 AI 的 reasoning_content。

2026-07-19 首次真实 E2E 验证了该配置的必要性：批处理输入为 6 项、并发为 1；第一轮详情图约 51 秒成功，第二轮在插件节点精确运行 3 分钟后报 `[720712053] plugin timeout`，剩余 4 轮未启动。节点现已设为 600 秒并发布。外部生图任务可能在父工作流失败后继续运行，因此确认遗留任务结束并再次取得用户付费确认前不得重跑。

- [ ] **Step 5: 先确认真实图片 URL 输出路径**

只执行一轮，展开插件实际输出；找到真正的图片 URL 字段后再绑定批处理汇总。不要根据旧 HTTP 接口猜测 `data` 内部路径。

- [ ] **Step 6: 汇总详情图 URL**

当前详情图插件同样输出 URL String，不创建需要 `Image` 视觉输入的 `单图商品一致性检查`。批处理每轮直接保存生图插件的真实 URL 字段，并按原顺序汇总为 `result_image_urls: Array<String>`。首次联调只跑一张并人工查看结果。

对具有明确重复部件数量的商品，人工抽查必须逐一核对部件根部、部件总数和一对一连接关系；提示词约束不能保证生成模型百分之百遵守精确计数。需要自动拦截时，必须先增加可靠的 URL→Image 转换能力，或改用原生接受公网图片 URL 的视觉质检 API/插件，再将不合格图片标记为失败或人工复核。

---

### Task 8: 分支结果汇合、单一结束与手动验收

**Coze 对象：**
- Create: Code `组装待补资料结果`
- Create: Code `组装生成完成结果`
- Create: Variable aggregate `最终结果对象`
- Create: Code `拆分最终结果`
- Use existing: End `结束`（全工作流唯一）

**Interfaces:**
- Consumes: 所有分支结果。
- Produces: 后续线上中转服务和飞书固定使用的输出契约。

不使用“输出”节点代替结束节点。两条分支先各自输出一个完整 Object，再聚合为一个 Object，最后拆成五个稳定字段交给唯一结束节点。

- [ ] **Step 1: 组装待补资料结果**

`result: Object`。输入 `reason: String = 解析商品判定 JSON.needs_input_reason`。

```javascript
async function main({ params }) {
  return {
    result: {
      status: "needs_input",
      standard_product_image_url: "",
      competitor_summary: "未启用竞品分析",
      result_image_urls: [],
      message: String(params.reason || "无法可靠确认主商品，请补充清晰单品图或明确具体款式")
    }
  };
}
```

- [ ] **Step 2: 组装生成完成结果**

输入：

```text
standard_product_image_url: String = 标准商品图 URL.Group1
result_image_urls: Array<String> = 逐张详情图生成.result_image_urls
```

输出：`result: Object`

```javascript
async function main({ params }) {
  return {
    result: {
      status: "completed",
      standard_product_image_url: String(params.standard_product_image_url || ""),
      competitor_summary: "未启用竞品分析",
      result_image_urls: Array.isArray(params.result_image_urls) ? params.result_image_urls : [],
      message: "生成完成"
    }
  };
}
```

- [ ] **Step 3: 聚合并拆分最终结果**

`最终结果对象` 使用“返回每个分组中第一个非空的值”：

```text
Group1 第 1 个值 = 组装待补资料结果.result
Group1 第 2 个值 = 组装生成完成结果.result
```

`Group1` 类型为 `Object`。随后连接 `拆分最终结果`：输入 `result: Object = 最终结果对象.Group1`；输出 `status: String`、`standard_image_url: String`、`competitor_summary: String`、`result_image_urls: Array<String>`、`message: String`。不要把该代码节点输出直接命名为 `standard_product_image_url`：Coze 当前会把超过 20 个字符的代码节点输出名截断，造成声明名与返回键不一致。

```javascript
async function main({ params }) {
  const result = params.result || {};
  return {
    status: String(result.status || "failed"),
    standard_image_url: String(result.standard_product_image_url || ""),
    competitor_summary: String(result.competitor_summary || "未启用竞品分析"),
    result_image_urls: Array.isArray(result.result_image_urls) ? result.result_image_urls : [],
    message: String(result.message || "")
  };
}
```

- [ ] **Step 4: 配置唯一结束节点**

结束节点除标准图外的四个输出变量绑定 `拆分最终结果` 的同名输出；结束节点对外字段 `standard_product_image_url` 绑定 `拆分最终结果.standard_image_url`。删除详情图后面误加的“输出”节点，两条分支都只经过结果聚合后进入这一个结束节点。

2026-07-19 真实异步执行补充：Coze 运行记录 API 最终返回的 `output` 外层可能包含大写 `Output` 和 `node_status`，其中 `Output` 是本节五字段对象的 JSON 字符串。中转层必须先解包并解析 `Output`，再读取 `status`、`standard_product_image_url` 和 `result_image_urls`。结束节点仍保持五字段契约，不再新增第二套结果节点。ID 12 回归确认必须同时满足三处一致：拆分代码返回 `standard_image_url`、节点输出定义为 `standard_image_url`、结束节点对外 `standard_product_image_url` 绑定该短字段。该修复已发布为 `v0.0.11`。

- [ ] **Step 5: 五类样例按顺序测试**

1. 单张干净白底商品图：策略应为 reuse，最终生成 6–8 张。
2. 商品、包装盒、说明书和配件混拍：能选对商品，包装不会出现在每张图。
3. 多张不同角度同一商品：identity_lock 汇总一致，关键结构和数量不漂移。
4. 人物正在使用商品：人物图只用于理解正确关系，标准图移除人物，详情页根据商品动态安排人物。
5. 同商品家族的多个颜色/花纹子 SKU 分别提供不同角度：应继续并选择主 SKU；只有混入核心结构或用途实质不同的多个商品家族且无法定位目标时，才 status=needs_input。

- [ ] **Step 6: 验收成本保护**

每类样例先运行到“解析设计数组”为止；确认商品判定和 6–8 条设计正确后，才把批处理最大执行次数设为 1 生成一张。第一张通过后才跑完整 6–8 张。

---

## 二、飞书和线上中转服务的后续接口边界

本计划只完成 Coze V2。通过 Task 8 后在用户现有线上服务器部署轻量中转服务，固定沿用以下契约，不再改变 Coze 输入输出：

```text
飞书按钮
→ 状态改为待生成
→ POST 线上中转服务 HTTPS 地址，正文只传 record_id
→ 线上服务用飞书 API 读取当前行与附件
→ 上传图片给 Coze 并调用已发布的 V2
→ 下载结果并上传回飞书
→ 回写标准商品图、竞品摘要、Listing 产出图、状态和处理说明
```

不再使用本机临时 HTTPS 隧道，直接在用户已有线上服务器联调和运行。飞书自动化 HTTP 请求中不存飞书 App Secret 或 Coze Token。

---

## 三、自查结果

- 六个开始输入与飞书字段一一对应，语言由站点映射，不存在独立 yuyan。
- 商品理解按图片逐张处理，不依赖视觉节点一次接收多图的能力。
- 商品图清理按策略执行，旧“图片格式转换”不会无条件污染流程。
- 竞品图在文字策略汇总后停止，无法进入标准图和生图插件。
- 详情设计数组经过代码解析；批处理每轮只接收一个字符串，避免整组数组塞入 prompt。
- 内部页面职责不进入最终 Prompt；本地化节点同时负责母语校对与 Prompt 编译。
- 标准图和详情图付费节点均不自动重试；主商品不明确时提前结束。
- 标准图与详情图两个付费插件节点超时均为 10 分钟，不使用默认 3 分钟阈值。
- 输出契约满足后续线上中转和飞书回写。
- 无 TODO、TBD 或未定义变量。

---

## 四、AI 节点用户 Prompt 与通用解析补充

Coze AI 节点同时配置“系统提示词”和“用户提示词”。系统提示词使用各 Task 已给出的完整内容，用户提示词使用以下文本；变量必须通过界面变量选择器插入，不要只输入看起来相同的普通文字。

### 单图商品理解：用户 Prompt

```text
请分析当前这一张自家商品图片。

产品名称：{{product_name}}
真实商品资料：{{points}}
当前图片：{{current_image}}

严格按照系统规定的 JSON 结构输出，不要输出解释、Markdown 或代码围栏。
```

`{{current_image}}` 必须通过 Coze 变量选择器插入用户 Prompt。只在“视觉理解输入”区域绑定图片、但不在用户 Prompt 中插入该变量，模型实际消息可能不携带图片。

### 多图汇总与主商品判定：用户 Prompt

```text
请综合以下逐图证据，完成目标商品家族归并、互补证据合并、主外观选择和标准商品参考图策略判断。请依据商品身份不变量判断一致性，不要把可变属性、展示状态或拍摄差异直接当作商品身份冲突。

产品名称：{{product_name}}
真实商品资料：{{points}}
逐图证据：{{image_evidence_list}}

严格按照系统规定输出一个合法 JSON 对象。
```

### 竞品视觉策略提取：用户 Prompt

```text
请分析当前这一张竞品图片的抽象销售和视觉策略。

我方商品档案：{{product_profile}}
我方真实商品资料：{{points}}
当前竞品图：{{current_competitor_image}}

不得复制竞品品牌、Logo、原文、包装和产品外观，只输出系统规定的 JSON。
```

### 竞品策略汇总：用户 Prompt

```text
请将以下逐张竞品分析汇总为可用于我方 Shopee 详情页设计的抽象策略。

逐张分析：{{competitor_analysis_list}}
我方商品档案：{{product_profile}}
我方真实商品资料：{{points}}

没有竞品分析时输出空字符串；否则只输出中文策略纯文本。
```

### Shopee 详情页设计 V2：用户 Prompt

```text
请为当前商品设计 6–8 张 Shopee 1:1 Listing 图片。

商品名称：{{product_name}}
商品档案（其中 usage_relationship 是强制物理使用关系）：{{product_profile}}
商品身份锁：{{identity_lock}}
真实商品资料：{{points}}
站点：{{zhandian}}

只输出系统规定的合法 JSON 字符串数组，不要输出说明或 Markdown。
```

### 本地化与生图 Prompt 编译：用户 Prompt

```text
请将当前单张中文设计编译为目标站点可直接生图的一条完整 Prompt。画面描述和控制指令全部使用英文；只有实际显示在图片上的消费者文案使用目标站点母语。

当前设计：{{design}}
商品身份锁：{{identity_lock}}
商品档案：{{product_profile}}
站点：{{zhandian}}

只输出一条纯文本生图 Prompt，不输出翻译过程、分析、JSON 或 Markdown。
```

### 生图 URL 输出绑定

当前标准商品图插件已经直接输出 `data.url: String`，因此不要添加 `提取标准图 URL` 节点，直接绑定 `标准商品图生成.data.url`。

详情图插件如果同样直接输出 `data.url: String`，也直接绑定；只有插件实际返回结构中没有可直接选择的 URL 字段时，才添加下面的通用提取代码作为兼容层。

兼容层输入：`raw_data: Object/String = 对应生图插件.data`。

兼容层输出：`image_url: String`、`image_urls: Array<String>`。

```javascript
async function main({ params }) {
  let data = params.raw_data;
  if (typeof data === "string") {
    const text = data.trim();
    try { data = JSON.parse(text); } catch (_) { data = text; }
  }

  const urls = [];
  const visit = (value) => {
    if (typeof value === "string") {
      if (/^https?:\/\//i.test(value)) urls.push(value);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (value && typeof value === "object") {
      ["image_url", "url", "urls", "images", "output", "result", "data"].forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(value, key)) visit(value[key]);
      });
    }
  };

  visit(data);
  const unique = [...new Set(urls)];
  if (!unique.length) throw new Error("生图插件输出中未找到图片 URL");
  return { image_url: unique[0], image_urls: unique };
}
```

不要同时保留直接 URL 绑定和提取代码两套重复适配。
