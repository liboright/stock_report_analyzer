# search_sections 详细设计

## 1. 函数签名

```python
def search_sections(
    query: str,
    company: str,
    year: str | None = None,
    top_k: int = 5
) -> list[dict]:
    """
    基于自然语言检索匹配的年报章节

    参数:
        query: 自然语言查询，如 "公司主要业务是什么"、"营收情况"
        company: 公司名称，如 "宁德时代"
        year: 可选，限定报告年度，如不指定则搜索公司所有年份
        top_k: 返回最多几条结果，默认 5

    返回:
        list[dict]: [
            {
                "company": str,
                "year": str,
                "section_id": str,      # 如 "3.1.2"
                "title": str,
                "level": int,
                "match_score": float,   # 0-1 之间的匹配得分
                "file": str,
                "line_start": int,
                "line_end": int
            },
            ...
        ]
    """
```

## 2. 路径配置

| 配置项 | 值 |
|--------|-----|
| 索引缓存目录 | `{base}/md/{company}/output/navi/` |
| 索引文件名格式 | `{company}_{year}_index.json` |

## 3. 流程图

```
search_sections(query, company, year, top_k)
│
├─► 1. 确定搜索范围
│      ├─► 指定 year → 只加载该年索引
│      └─► 未指定 year → 加载公司所有年份索引
│
├─► 2. 加载索引缓存
│      ├─► 索引存在 → 解析 sections 展平成列表
│      └─► 索引不存在 → 抛出异常
│
├─► 3. Query 预处理
│      ├─► 分词（jieba）
│      ├─► 停用词过滤
│      └─► 同义词扩展
│
├─► 4. 多级匹配打分
│      ├─► 精确匹配查询词 → 得分 1.0
│      ├─► 查询词在标题中 → 得分 0.8
│      ├─► 同义词匹配 → 得分 0.6
│      └─► 部分字符串匹配 → 得分 0.3
│
├─► 5. 结果排序
│      └─► 按得分降序排列
│
└─► 6. 返回 top_k 个结果
```

## 4. Query 预处理

### 4.1 中文分词
```python
import jieba

query = "公司主要业务是什么"
tokens = jieba.cut(query)
# ['公司', '主要', '业务', '是', '什么']
```

### 4.2 停用词过滤
```python
STOPWORDS = {'的', '是', '了', '在', '和', '与', '或', '什么', '怎么', '如何', '吗', '呢'}

def remove_stopwords(tokens):
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]
```

### 4.3 同义词扩展
```python
SYNONYMS = {
    "业务": ["业务", "主要业务", "经营", "营业范围"],
    "营收": ["营收", "营业收入", "收入", "销售", "营业额"],
    "利润": ["利润", "净利润", "盈利", "收益"],
    "资产": ["资产", "总资产", "净资产"],
    "负债": ["负债", "总负债", "债务"],
    "股东": ["股东", "股权", "股份", "持有人"],
    "管理": ["管理", "管理层", "经营", "管理层讨论"],
    "财务": ["财务", "财务数据", "会计", "财务指标"],
    "行业": ["行业", "产业", "市场", "竞争"],
    "风险": ["风险", "风险因素", "不确定性"],
}
```

## 5. 匹配打分算法

```python
def calculate_match_score(query_tokens: list[str], title: str, section_id: str | None) -> float:
    """
    计算章节匹配得分

    参数:
        query_tokens: 处理后的查询词列表
        title: 章节标题
        section_id: 章节ID（可能为 None）

    返回:
        float: 得分 0-1 之间
    """
    title_lower = title.lower()
    score = 0.0

    for token in query_tokens:
        token_lower = token.lower()

        # 精确匹配查询词
        if token_lower == title_lower:
            score = max(score, 1.0)

        # 查询词在标题中
        elif token_lower in title_lower:
            score = max(score, 0.8)

        # 同义词匹配
        elif token_lower in SYNONYMS:
            for synonym in SYNONYMS[token_lower]:
                if synonym in title_lower:
                    score = max(score, 0.6)
                    break

        # 部分字符串匹配（至少2个字符）
        elif len(token_lower) >= 2:
            for i in range(len(token_lower) - 1):
                if token_lower[i:i+2] in title_lower:
                    score = max(score, 0.3)
                    break

    return score
```

## 6. 索引展平

由于索引是嵌套的树结构，检索前需要展平为列表：

```python
def flatten_sections(sections: list[dict]) -> list[dict]:
    """将嵌套的章节树展平为列表"""
    result = []

    def traverse(nodes):
        for node in nodes:
            result.append({
                "section_id": node.get("section_id"),
                "title": node["title"],
                "level": node["level"],
                "file": node["file"],
                "line_start": node["line_start"],
                "line_end": node.get("line_end"),
            })
            if node.get("subsections"):
                traverse(node["subsections"])

    traverse(sections)
    return result
```

## 7. 返回结果示例

```json
[
  {
    "company": "宁德时代",
    "year": "2024",
    "section_id": "3.2.1",
    "title": "1、主要业务",
    "level": 3,
    "match_score": 0.95,
    "file": "3_第三节 管理层讨论与分析.md",
    "line_start": 37,
    "line_end": 120
  },
  {
    "company": "宁德时代",
    "year": "2024",
    "section_id": "3.2",
    "title": "二、报告期内公司从事的主要业务",
    "level": 2,
    "match_score": 0.85,
    "file": "3_第三节 管理层讨论与分析.md",
    "line_start": 35,
    "line_end": 200
  }
]
```

## 8. 复用现有代码

| 函数 | 来源 | 用途 |
|------|------|------|
| 索引读取 | `annual_report_reader.core` | 读取并解析 JSON 缓存 |

## 9. 依赖

```python
jieba>=0.42.1  # 中文分词
```

## 10. 验证用例

| 用例 | 输入 | 预期输出 |
|------|------|----------|
| 基本检索 | query="营收情况", company="宁德时代" | 返回营收相关章节 |
| 指定年份 | query="业务", year="2024" | 只返回2024年报中的业务章节 |
| 多结果 | query="财务" | 返回多个财务相关章节，按得分排序 |
| 无结果 | query="不存在的内容" | 返回空列表 |
| 精确匹配 | query="一、报告期内公司所处行业情况" | 返回得分接近1的结果 |
| 同义词 | query="营收" | 能匹配到"营业收入"章节 |