"""百度百科候选判断层（守门人）。

职责单一：给定「查询 + 候选词条列表」，剔掉明显不相关的候选。
**原则：宁可漏，不可错。**
    用户问「依安嘉措」，返回「嘉措」（西藏人名后缀）是**错**；
    返回「百科没收录」只是信息少一点，不是错误答案。
所以判断逻辑刻意保守——宁可漏掉正确项，也不把错的推给模型。

三条判据
--------
1. **硬性门槛**：词典页（XX（汉语汉字））、纯数字/版本号、单字词条 → 直接剔。
2. **强信 / 弱信分级**（拿归一化后的词条名和查询比）：
   - 强信：词条名 == 查询，或词条名**包含**查询的全部字符（超集）
           「珂莱塔」← 「珂莱塔·莫塔里」✓
   - 弱信：其余情况，含「词条名只是查询的子串」这种**残缺命中**
           「珂莱塔」← 「莱塔」✗（弱）
3. **整词型查询只认强信**：
   「依安嘉措」「崩坏星穹铁道」这类查询本身就是一个完整专名
   （短、无虚词、无标点），百科没有对应词条时，
   宁可直接回答「没收录」，也不把碎片「嘉措」当成答案。
   而「鸣潮珂莱塔这个角色怎么抽到需要什么材料」是自然语言问题，
   不适用这条——多个实体都是合法答案。

被剔除的候选会带上原因，方便排查时知道是哪条规则起的作用。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 归一化

# 归一化时丢掉所有标点和空白，只留实义字符。
# 这样「崩坏：星穹铁道」和「崩坏星穹铁道」会被认成同一个词。
_PUNCT_RE = re.compile(
    r"[\s,，。！？、；：\"'（）()\[\]【】<>《》〈〉~`!@#$%^&*+=|/\\.·・\-—…“”‘’]+"
)


def norm(s: str) -> str:
    """归一化：去掉标点空白，只留汉字/字母/数字。"""
    return _PUNCT_RE.sub("", s or "")


# ---------------------------------------------------------------- 词条名清洗

# 标题里「·」「：」后面如果是这类词，说明它是分区标签而不是词条本名的一部分
# （「影神图·图鉴」→ 本名是「影神图」；但「崩坏：星穹铁道」不能剥）
LABEL_TAILS = {
    "图鉴", "图集", "目录", "攻略", "资料", "视频", "直播", "壁纸", "头像",
    "表情包", "同人", "资讯", "新闻", "官网", "下载", "介绍", "评测",
    "怎么样", "是什么", "什么意思", "百科", "玩法", "技巧", "教程", "入坑",
}

# 纯数字/版本号当专名去查百科只会招来 .NET Framework 这类噪音
NUM_ONLY = re.compile(r"^[\d.]+[a-z]?$", re.I)

# 必应把「X（汉语汉字）_百度百科」这类字典型条目塞进结果，必须剔掉
DICT_TITLE_PAT = re.compile(
    r"[（(]\s*(?:汉语|漢字|HS\d|[\d一二三四五六七八九十]+画|汉字|字词|词条|字形|注音|读音)\s*[)）]"
    r"|_百度百科$"
    r"|汉语字典|汉典|漢典|词典|字典|汉语国学|汉语查|组词|部首|笔顺|拼音",
    re.I,
)

# 词典站点：zdic / zidian / dict.cn …… 这些站出的全是字词解释，不是词条
DICT_SITE_PAT = re.compile(
    r"zdic\.net|zidian\.|dict\.cn|hanyu|zhuanti\.com|hujiang\.com",
    re.I,
)


def is_dict_page(title: str, url: str) -> bool:
    """判断是不是「XX（汉语汉字）_百度百科」「XX - 漢典」这类字典型条目。"""
    if not title:
        return True
    if "baike.baidu.com" in url and re.search(r"[（(](?:汉语|HS\d)", title):
        return True
    if DICT_SITE_PAT.search(url or ""):
        return True
    if DICT_TITLE_PAT.search(title) and (
        "汉语" in title or "字典" in title or "组词" in title
        or "部首" in title or "笔顺" in title or "拼音" in title
        or "词典" in title or "汉典" in title or "漢典" in title
        or "汉字" in title or "漢字" in title
    ):
        return True
    return False


def title_core(title: str) -> str:
    """从标题里取出词条本名。

    百度百科的标题形如：
      「崩坏：星穹铁道（2023年米哈游开发的银河冒险策略游戏 ...」  ← 限定语被截断，没有右括号
      「珂莱塔·莫塔里_百度百科」
      「甘雨（游戏《原神》及其衍生作品中的角色）_百度百科」
    要把限定语、_百度百科 后缀都剥掉，只留本名。

    注意分隔符（·：:）不能乱剥——「崩坏：星穹铁道」「珂莱塔·莫塔里」都是本体，
    剥了就变成「崩坏」「珂莱塔」，反而错。所以只剥「标签式后缀」：
    图鉴 / 目录 / 攻略 / 壁纸 这类明显是分区名的尾巴。
    """
    t = (title or "").strip()
    # 去掉 _百度百科 后缀
    t = re.sub(r"_百度百科$", "", t)
    # 去掉「（...」开头到结尾的限定语（右括号可能缺失，必应摘要会截断）
    stripped = re.sub(r"[（(].*$", "", t).strip()
    if stripped:  # 别把整个标题剥空
        t = stripped
    # 去掉「·xxx」「:xxx」这类标签式后缀
    m = re.match(r"^(.+?)[·・:：](.+)$", t)
    if m:
        head, tail = m.group(1).strip(), m.group(2).strip()
        if tail in LABEL_TAILS or re.fullmatch(r"[\d\s.]+", tail):
            t = head
    return t.strip()


def is_dict_page(title: str, url: str) -> bool:
    """判断是不是「XX（汉语汉字）_百度百科」这类字典型条目。"""
    if not title:
        return True
    if "baike.baidu.com" in url and re.search(r"[（(](?:汉语|HS\d)", title):
        return True
    if DICT_TITLE_PAT.search(title) and (
        "汉语" in title or "字典" in title or "组词" in title
        or "部首" in title or "笔顺" in title or "拼音" in title
    ):
        return True
    return False


def has_meaning(core: str) -> bool:
    """词条本名里至少要有 2 个实义字（汉字/字母）。

    「穹」「鸣」这种单字词条当答案没意义——多半是分词碎片。
    """
    stripped = re.sub(r"[\d.\s\-_vV]+", "", core or "")
    return len(re.findall(r"[\u4e00-\u9fa5a-zA-Z]", stripped)) >= 2


# 标题里出现这些，说明它是转载页/栏目页，不是正经词条
SITE_NOISE = re.compile(
    r"wiki|百科|官网|官方|门户|攻略|论坛|社区|bilibili|哔哩|4399|taptap|"
    r"网易|腾讯|游民|多玩|17173|小米|抖音|b站|gamersky|3dmgame|贴吧|"
    r"知乎|豆瓣|应用宝|steam|哔哩哔哩",
    re.I,
)


def is_superset(nq: str, nc: str) -> bool:
    """词条名 nc 是不是查询 nq 的「名字延伸」。

    「珂莱塔」← 「珂莱塔·莫塔里」 ✓（多出的是名字的一部分）
    「影神图」← 「影神图 - 黑神话：悟空Wiki - BWIKI」 ✗（多出的是站点名）
    """
    if not nq or nq not in nc:
        return False
    rest = nc.replace(nq, "", 1)
    if not rest:
        return True  # 完全相等
    # 多出来的部分太长 → 是拼了别的东西，不是名字延伸
    if len(rest) > max(len(nq), 4):
        return False
    # 多出来的部分含站点/栏目关键词 → 转载页，不算超集
    if SITE_NOISE.search(rest):
        return False
    return True


# ---------------------------------------------------------------- 查询定性

# 查询里出现这些，说明它是自然语言问题，不是一个完整专名
QUERY_FILLERS = (
    # 疑问 / 指令
    "这个", "那个", "怎么", "如何", "什么", "哪些", "为什么", "哪里", "多少",
    "几个", "需要", "能否", "是否", "有没有", "是不是", "区别", "介绍",
    "详细", "推荐", "还是", "以及", "或者", "请问", "帮我", "查一下", "搜索",
    # 名词化的泛词
    "资料", "信息", "内容", "方法", "教程", "指南", "说明", "意思", "时候",
    "可以", "应该", "打算", "到底", "究竟", "具体", "所有", "东西", "问题",
    "情况", "时间", "地方", "故事", "人物", "角色", "版本", "攻略", "养成",
    "搭配", "材料", "抽到", "培养", "解锁", "是什么", "什么意思", "怎么样",
    # 单字虚词
    "的", "了", "吗", "呢", "啊", "呀", "吧", "和", "与", "或", "在", "是",
)


def is_term_query(query: str) -> bool:
    """查询本身是不是一个「完整专名」。

    判定：短（2~12 字）、无标点空白、不含疑问/泛词。
    这种查询下，碎片命中（查询的子串）不能作为答案。
    """
    s = (query or "").strip()
    if not (2 <= len(s) <= 12):
        return False
    if _PUNCT_RE.search(s):
        return False
    if any(f in s for f in QUERY_FILLERS):
        return False
    return bool(re.search(r"[\u4e00-\u9fa5a-zA-Z]", s))


# ---------------------------------------------------------------- 判定主体

def classify(query: str, title: str, url: str = "") -> tuple[str, str]:
    """把单个候选分成 strong / weak / reject。

    Returns:
        (等级, 原因)  等级 ∈ {"strong", "weak", "reject"}
    """
    core = title_core(title)
    nc = norm(core)
    nq = norm(query)

    if not nc:
        return "reject", "标题无实义"
    if is_dict_page(title, url):
        return "reject", "词典页"
    if NUM_ONLY.match(core):
        return "reject", "纯数字/版本号"
    if not has_meaning(core):
        return "reject", "实义字不足"

    if nq and nc == nq:
        # 词条名与查询完全一致
        return "strong", "完全匹配"
    if is_superset(nq, nc):
        # 词条名包含查询，且多出来的部分是名字的延伸
        return "strong", "包含查询全称"

    return "weak", "仅片段命中"


def judge_entries(
    query: str, entries: list[dict]
) -> tuple[list[dict], list[tuple[dict, str]]]:
    """判断层主入口：筛掉不可信的候选。

    Args:
        query: 用户的原始查询
        entries: 候选词条列表，每项至少要有 "title" 和 "url"

    Returns:
        (可信候选, [(被剔除的候选, 原因), ...])
    """
    strong: list[dict] = []
    weak: list[dict] = []
    rejected: list[tuple[dict, str]] = []

    for e in entries or []:
        level, why = classify(query, e.get("title") or "", e.get("url") or "")
        if level == "strong":
            strong.append(e)
        elif level == "weak":
            weak.append(e)
        else:
            rejected.append((e, why))

    term_like = is_term_query(query)

    if term_like:
        # 整词型查询：只认强信。碎片宁可不采用。
        for e in weak:
            rejected.append((e, "整词查询不采用片段命中"))
        return strong, rejected

    if strong:
        # 已经有更贴题的词条，弱信的一律让位
        for e in weak:
            rejected.append((e, "存在更贴题的词条"))
        return strong, rejected

    # 没有强信时保留弱信，交由后续打分排序
    return weak, rejected
