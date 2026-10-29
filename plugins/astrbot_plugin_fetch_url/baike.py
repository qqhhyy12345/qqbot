"""百度百科专用检索通道。

背景
----
必应 RSS 对中文长查询会「单字化」：只认真检索第一个 token，遇到它不认识的
专名就把词拆成单字去查词典页。实测：
    「鸣潮 珂莱塔 养成攻略」 → 满屏「鸣（汉语汉字）_百度百科」
    「黑神话悟空影神图…」   → 「黑（汉语文字）_百度百科」
而百科类查询（游戏角色、影视、音乐、梗）的答案**本来就在百科里**，
所以对这类查询直接走百科通道，比跟必应的分词较劲划算得多。

三层结构
--------
L1 词条名校正（百度百科开放 API）
    GET https://baike.baidu.com/api/openapi/BaikeLemmaCardApi
        ?scope=103&format=json&appid=379020&bk_key=<词>&bk_length=600
    → 纯 JSON，给出**正式词条名** + 一句话简介 + 结构化 card 字段。
    它的价值是「校正」：分词给不出的名字它能给
    （「珂莱塔」→ 正式名「珂莱塔·莫塔里」，简介里还带「鸣潮」上下文）。
    实测 100% 稳定，bk_length 参数无效（正文长度由服务端定）。

L2 必应百科限定（`site:baike.baidu.com <词>`）
    site: 前缀会**占掉必应的首 token 位置**，让查询词正常参与检索，
    这是绕过中文单字化的关键。
    但加第二个词就会重新坍缩（实测「site:… 珂莱塔 鸣潮」→ 满屏「珂（汉语汉字）」），
    所以**一次只能带一个词**。

L3 百科正文（wapbaike.baidu.com）
    wapbaike.baidu.com/item/<词> 能返回 200（桌面版 item 页是 403），
    但正文极不稳定：同一 URL 抓 4 次，有 3 次只返回 1~2KB 的空壳。
    → 必须重试，且只在拿到足够正文时才用。

已知边界
--------
- 百科没有的词（依安嘉措、大二普）→ openapi 返回 {}，这条路救不了
- 词条名有歧义（甘雨 → 天津人艺演员，而游戏角色是另一条）→
  靠简介里的作品名做筛选，但无法完全消除
- 百科是「资料库」不是「攻略站」，养成/配队这类问题仍需配合必应找攻略帖
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote, quote_plus

import aiohttp

from .baike_judge import (
    is_dict_page,
    judge_entries,
    title_core,
)

# ---------------------------------------------------------------- 常量

BAIKE_API = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
BAIKE_OPENAPI_APPID = "379020"
BAIKE_WAP = "https://wapbaike.baidu.com/item/{}"
BAIKE_SEARCH_SITE = "baike.baidu.com"

TIMEOUT = 20
MAX_BYTES = 2 * 1024 * 1024

# 词典页判定（词典标题正则、词典站点、正文清洗用的通用工具）
# 已统一移到 baike_judge.py，这里不再重复定义。

# 百科「有词条但不是你要的那个」的信号：简介是这类词条基本是通用词
GENERIC_DESC = (
    "汉语词汇", "汉语词语", "汉语字典", "指示代词", "代词", "汉语",
    "常用词", "汉语成语", "姓氏", "地名", "自然现象", "科学术语",
)

# 分词时直接丢掉的词：查这些没有百科价值
STOP_TERMS = {
    "什么", "哪些", "怎么", "如何", "为什么", "哪里", "多少", "几个",
    "角色", "版本", "攻略", "养成", "搭配", "推荐", "资料", "信息",
    "内容", "方法", "教程", "指南", "说明", "介绍", "意思", "时候",
    "结果", "问题", "东西", "情况", "时间", "地方", "所有", "材料",
    "故事", "具体", "详细", "完整", "一起", "自己", "现在", "收集",
    "这个", "那个", "我们", "你们", "他们", "可以", "需要", "知道",
    "有没有", "是不是", "怎么样", "为什么", "告诉我", "介绍一下",
}

# 纯数字/版本号当专名去查百科只会招来 .NET Framework 这类噪音
_NUM_ONLY = re.compile(r"^[\d.]+[a-z]?$", re.I)

# 标题里「·」「：」后面如果是这类词，说明它是分区标签而不是词条本名的一部分
# （「影神图·图鉴」→ 本名是「影神图」；但「崩坏：星穹铁道」不能剥）
LABEL_TAILS = {
    "图鉴", "图集", "目录", "攻略", "资料", "视频", "直播", "壁纸", "头像",
    "表情包", "同人", "资讯", "新闻", "官网", "下载", "介绍", "评测", "攻略",
    "怎么样", "是什么", "什么意思", "百科", "玩法", "技巧", "教程", "入坑",
}

# 判定一个词是否「像专名」：长度 + 不含虚词标点
_PUNCT = re.compile(r"[\s,，。！？、；：\"'（）()\[\]【】<>《》~`!@#$%^&*+=|/\\]+")


def _looks_like_term(w: str) -> bool:
    if not w or len(w) < 2 or len(w) > 20:
        return False
    if _NUM_ONLY.match(w):
        return False
    if w in STOP_TERMS:
        return False
    if _PUNCT.search(w):
        return False
    # 至少要有一个 CJK 或字母
    return bool(re.search(r"[\u4e00-\u9fa5a-zA-Z]", w))


def _is_numberish(w: str) -> bool:
    """含数字的词当专名去查百科基本是错的（「2.0」「3.0」会招来 .NET Framework）。

    但「崩坏3」「V2.0」这种整体带数字的专名要放过，
    所以判据是：去掉数字和常见符号后，剩下的实义字太少就算数。
    """
    core = re.sub(r"[\d.\s\-_vV]+", "", w)
    if not core:
        return True
    # 至少要有 2 个汉字/字母才算实义
    return len(re.findall(r"[\u4e00-\u9fa5a-zA-Z]", core)) < 2


def _strip_edges(w: str) -> str:
    """剥掉词首尾的方位/结构助词：「原神里」→「原神」。"""
    junk = ("里", "中", "上", "下", "的", "了", "是", "在", "和", "与", "或",
            "个", "这", "那", "吗", "呢", "啊", "呀", "吧")
    changed = True
    while changed and len(w) > 2:
        changed = False
        if w[0] in junk:
            w = w[1:]
            changed = True
        if w[-1] in junk:
            w = w[:-1]
            changed = True
    return w


# ---------------------------------------------------------------- 分词

def _segment(query: str, jieba_mod, limit: int = 5) -> list[str]:
    """从查询里提取候选专名。

    关键点一：**合并连续实义词**，而不是逐词切。
    jieba 会把「崩坏星穹铁道」切成 崩坏/星穹/铁道，单独搜「崩坏」毫无意义；
    合并后得到「崩坏星穹铁道」才是能用的专名。

    关键点二：**合并相邻碎片**。
    jieba 也不认识「珂莱塔」（切成 珂/莱塔）和「依安嘉措」（切成 依安/嘉措）。
    但这些碎片在查询里是**紧挨着**的，拼回去才是完整的词。

    返回按重要度排序的候选词。
    """
    q = query.strip()
    if not q:
        return []

    base: list[tuple[str, int, int]] = []  # (词, 起点, 终点)

    def add(w: str, start: int, end: int) -> None:
        w = _strip_edges(w.strip())
        if not w or _is_numberish(w):
            # 「2.0」「3.0」查百科只会招来 .NET Framework 之类
            return
        if not _looks_like_term(w):
            return
        for old, s, e in base:
            if old == w and s == start:
                return
        base.append((w, start, end))

    # ① 搜索引擎模式的长词（jieba 词典里有的完整专名）
    try:
        for w in jieba_mod.cut_for_search(q):
            w = w.strip()
            if not w:
                continue
            idx = q.find(w)
            if idx >= 0:
                add(w, idx, idx + len(w))
    except Exception:  # noqa: BLE001
        pass

    # ② 连续实义词合并
    try:
        import jieba.posseg as pseg

        buf: list[str] = []
        buf_start = 0
        for w, flag in pseg.cut(q):
            w = w.strip()
            if not w:
                continue
            pos = q.find(w, buf_start if buf else 0)
            if pos < 0:
                pos = q.find(w)
            if flag in {"n", "ns", "nr", "nt", "nz", "vn", "l", "eng"}:
                if not buf:
                    buf_start = pos if pos >= 0 else buf_start
                buf.append(w)
            else:
                if buf:
                    merged = "".join(buf)
                    s = q.find(merged)
                    add(merged, s if s >= 0 else buf_start,
                        (s + len(merged)) if s >= 0 else buf_start + len(merged))
                    buf = []
        if buf:
            merged = "".join(buf)
            s = q.find(merged)
            add(merged, s if s >= 0 else buf_start,
                (s + len(merged)) if s >= 0 else buf_start + len(merged))
    except Exception:  # noqa: BLE001
        pass

    # ③ 合并相邻碎片：「珂」+「莱塔」→「珂莱塔」
    merged_frags: list[tuple[str, int, int]] = []
    ordered = sorted(base, key=lambda x: (x[1], x[2]))
    for i in range(len(ordered) - 1):
        w1, s1, e1 = ordered[i]
        w2, s2, e2 = ordered[i + 1]
        if s2 <= e1 and s2 >= s1:  # 两段在查询里紧挨着或重叠
            joined = q[s1:e2]
            if len(joined) >= 3 and joined not in [x[0] for x in base]:
                merged_frags.append((joined, s1, e2))
    base.extend(merged_frags)

    # ④ 与原查询有交集
    base = [(w, s, e) for w, s, e in base if set(w) & set(q)]

    # ⑤ 排序：长的优先（专名比泛词长）
    base.sort(key=lambda x: (-(x[2] - x[1]), x[1]))

    # 去重（保留最长的）
    out: list[str] = []
    for w, _, _ in base:
        if w not in out:
            out.append(w)
    return out[:limit]


def _card_to_text(card: list) -> list[tuple[str, str]]:
    """把 card 字段数组转成 (字段名, 值) 列表，并清掉 HTML 标签。"""
    out: list[tuple[str, str]] = []
    for c in card or []:
        name = str(c.get("name") or "").strip()
        val = c.get("value")
        if isinstance(val, list):
            text = " / ".join(str(v) for v in val if v)
        else:
            text = str(val or "")
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if name and text:
            out.append((name, text))
    return out


def _parse_openapi(raw: str) -> dict | None:
    """解析开放 API 的返回。返回 None 表示没这个词条。

    注意两点：
    - 偶尔会因为网络只读到一半导致 JSON 截断，用 raw_decode 能救回大部分情况
    - 百度的 JSON 里中文是 \\uXXXX 转义，json 库会自动解码；
      但截断回退路径用的是正则，必须手动解码
    """
    raw = raw.strip()
    if not raw or raw == "{}":
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw)
    except Exception:  # noqa: BLE001
        m_t = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        m_d = re.search(r'"desc"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if not m_t:
            return None
        obj = {"title": m_t.group(1), "desc": m_d.group(1) if m_d else ""}
    if not isinstance(obj, dict):
        return None
    title = _unescape(str(obj.get("title") or "")).strip()
    if not title:
        return None
    desc = _unescape(str(obj.get("desc") or "")).strip()
    card = _card_to_text(obj.get("card") or [])
    return {"title": title, "desc": desc, "card": card,
            "url": f"https://baike.baidu.com/item/{quote(title)}"}


def _unescape(s: str) -> str:
    """把 JSON 里的 \\uXXXX 转义解码成真正的字符。"""
    if "\\u" not in s:
        return s
    try:
        return json.loads(f'"{s}"') if "\\" in s else s
    except Exception:  # noqa: BLE001
        return re.sub(
            r"\\u([0-9a-fA-F]{4})",
            lambda m: chr(int(m.group(1), 16)),
            s,
        )


async def _lookup_openapi(
    session: aiohttp.ClientSession, term: str
) -> dict | None:
    """L1：查百度百科开放 API，拿正式词条名 + 简介 + 结构化字段。"""
    url = (
        f"{BAIKE_API}?scope=103&format=json"
        f"&appid={BAIKE_OPENAPI_APPID}&bk_key={quote(term)}&bk_length=600"
    )
    try:
        async with session.get(url) as resp:
            if resp.status >= 400:
                return None
            raw = (await resp.content.read(MAX_BYTES)).decode(
                resp.charset or "utf-8", errors="replace"
            )
    except Exception:  # noqa: BLE001
        return None
    return _parse_openapi(raw)


# ---------------------------------------------------------------- L2 必应百科限定

async def _search_baike_site(
    session: aiohttp.ClientSession, term: str, count: int = 10
) -> list[tuple[str, str, str]]:
    """L2：用 `site:baike.baidu.com <词>` 搜必应。

    site: 前缀会占掉必应的首 token 位置，让查询词正常参与检索——
    这是绕开中文单字化的关键。
    **只能带一个词**，加第二个词就会重新坍缩。
    """
    q = f"site:{BAIKE_SEARCH_SITE} {term}"
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    try:
        async with session.get(url) as resp:
            if resp.status >= 400:
                return []
            raw = (await resp.content.read(MAX_BYTES)).decode(
                resp.charset or "utf-8", errors="replace"
            )
    except Exception:  # noqa: BLE001
        return []
    return _parse_rss(raw, count)


def _parse_rss(xml_text: str, limit: int) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for item in re.findall(r"<item>(.*?)</item>", xml_text, re.S):
        def g(tag: str) -> str:
            m = re.search(
                rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", item, re.S
            )
            return m.group(1).strip() if m else ""
        title, link, desc = g("title"), g("link"), g("description")
        if title and link:
            out.append((title, link, desc))
        if len(out) >= limit:
            break
    return out


def _is_dict_entry(title: str, url: str) -> bool:
    """判断是不是「XX（汉语汉字）_百度百科」这类字典型条目。"""
    return is_dict_page(title, url)


# ---------------------------------------------------------------- L3 百科正文

# 百度百科 wap 版的正文段落自带标记，class 名是 CSS Modules 哈希（每次可能变），
# 但这个属性是稳定的 —— 用它定位正文，不依赖 class。
_PARA_ATTR = {"data-tag": "paragraph"}

# 段落里要剔掉的噪音：参考文献角标（上标数字）、讨论标签
# ⚠️ bs4 的 select() 只接受**一个**选择器字符串，逗号分隔可以，
#    但传多个参数时第二个会被当成 namespaces（dict），直接抛 AttributeError。
_JUNK_SELECTOR = ", ".join(
    ("[class*='refSup']", "[class*='discussionTag']", "i[data-refid]")
)


def _longest_text_block(html: str, min_len: int = 200) -> str:
    """从 HTML 里找最长的纯文本块（兜底用）。

    ⚠️ **必须先剥掉 script/style**：
    wapbaike 页面里有一大段设置根字号的 JS，用 `>([^<>]{N,})<` 匹配纯文本节点时，
    它比真正的正文还长，会被当成"正文"喂给模型（实测抓到 461 字符的 JS 代码）。
    """
    best = ""
    cleaned = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.S | re.I
    )
    # ① DOM 里的文本节点
    for m in re.finditer(r">([^<>]{%d,})<" % min_len, cleaned):
        t = m.group(1).strip()
        if len(t) > len(best):
            best = t
    # ② JSON 字符串里的正文
    if len(best) < min_len:
        for m in re.finditer(r'"(?:desc|content|abstract|summary)"\s*:\s*"([^"]{%d,})"'
                             % min_len, cleaned):
            t = m.group(1)
            t = re.sub(r"\\u([0-9a-fA-F]{4})",
                       lambda x: chr(int(x.group(1), 16)), t)
            t = t.replace("\\n", "\n").replace('\\"', '"')
            if len(t) > len(best):
                best = t
    return best


def _extract_page_text(html: str, min_len: int = 200) -> tuple[str, str]:
    """从百科页面里抽出（正文段落, meta 简介）。

    ⚠️ **不要用「叶子容器取最长文本块」那套来找正文**——
    百度百科的正文段落是 `<div data-tag="paragraph">`，但它内部嵌着
    参考文献角标 `<div class="index_refSup__...">`，
    按"元素内部还有 div 就整个丢掉"的规则，段落会被自己的角标判死。
    实测：27KB 的完整页面，老逻辑只提取出 39 字符；
    改用 data-tag 定位，同一个页面能拿到 5 段 / 509 字符。

    meta description 是完整简介，**100% 稳定拿到**（143~330 字符），
    比正文段落稳得多，所以一并抽出来。
    """
    meta = ""
    paras: list[str] = []
    # meta 简介用正则取（快），不走 bs4 —— 大词条页面 300KB+，
    # 每多一次 bs4 解析就多几百毫秒，而 meta 只要第一个成功页面就够。
    m = re.search(
        r'<meta[^>]*name="description"[^>]*content="([^"]*)"', html
    ) or re.search(
        r'<meta[^>]*content="([^"]*)"[^>]*name="description"', html
    )
    if m:
        meta = m.group(1).strip()

    if "paragraph" in html:
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            for div in soup.find_all(attrs=_PARA_ATTR):
                # 先剔角标和讨论标签，再取文本。
                # 清理失败也要把段落取出来——不能因为清洗出错就丢掉整段正文。
                try:
                    for junk in div.select(_JUNK_SELECTOR):
                        junk.decompose()
                except Exception:  # noqa: BLE001
                    pass
                t = re.sub(r"\s+", " ", div.get_text(" ", strip=True))
                # decompose 掉角标后会留下多余空格（「角色 。」「《 鸣潮 》」），清掉
                t = re.sub(r"\s+([，。、；：！？）」』】》,.!?;:])", r"\1", t)
                t = re.sub(r"([（「『【《])\s+", r"\1", t)
                if t:
                    paras.append(t)
        except Exception:  # noqa: BLE001
            pass

    body = "\n".join(paras)
    if len(body) < min_len:
        fallback = _longest_text_block(html, min_len)
        if len(fallback) > len(body):
            body = fallback
    return body, meta


async def _fetch_baike_page(
    session: aiohttp.ClientSession,
    title: str,
    waves: int = 4,
    per_wave: int = 3,
    min_body: int = 250,
) -> tuple[str, str]:
    """L3：抓百科词条正文 + meta 简介。

    wapbaike 的返回**极不稳定**：同一 URL 连续请求，页面大小在
    3KB ~ 317KB 之间乱跳，只有约 15~20% 的次数是带正文段落的完整页。
    实测加随机 query 参数没用（不是 CDN 缓存问题，是服务端随机返回不同渲染程度）。

    所以用**小并发 + 多轮**而不是串行重试、更不是猛并发：
      - 串行 7 次 = 7 个 RTT，且第一次可能只命中"只有摘要段"的半成品
      - 并发 5 个 = 1 个 RTT，但实测**会被限流**：
        同一 URL 一次打 5 个请求，百度的 WAF 会返回错误页
        （连 meta description 都没有，正文更是空的）
      - 实测「并发3个 × 4轮 + 轮次间随机延时」最稳
    实测：影神图 1521 字、珂莱塔 609 字、原神 882 字，甘雨需第二轮。
    """
    url = BAIKE_WAP.format(quote(_core_name(title) or title))
    best_body, best_meta = "", ""

    async def _get() -> str:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    return ""
                return (await resp.content.read(MAX_BYTES)).decode(
                    resp.charset or "utf-8", errors="replace"
                )
        except Exception:  # noqa: BLE001
            return ""

    for wave in range(waves):
        htmls = await asyncio.gather(*[_get() for _ in range(per_wave)])
        for html in htmls:
            if not html:
                continue
            body, meta = _extract_page_text(html)
            if len(body) > len(best_body):
                best_body = body
            if len(meta) > len(best_meta):
                best_meta = meta
        if len(best_body) >= min_body:
            break
        if wave < waves - 1:
            # 随机延时，避开「同一 URL 高频请求」的限流判定
            await asyncio.sleep(0.5 + (wave % 3) * 0.35)
    return best_body, best_meta


# ---------------------------------------------------------------- 主流程

def _core_name(title: str) -> str:
    """从标题里取出词条本名（实现见 baike_judge.title_core）。"""
    return title_core(title)


def _pick_best(
    query: str, entries: list[dict]
) -> dict | None:
    """从多个百科词条里挑最贴题的一个。

    先过**判断层**（baike_judge）剔掉不可信的候选，再在剩下的里打分排序。

    判断层挡掉的典型错答：
    - 问「珂莱塔」→ 选中「莱塔」（无期迷途角色，恰好是查询的子串）
    - 问「崩坏星穹铁道」→ 选中「穹（…男主角）」（分词碎片）
    - 问「依安嘉措」→ 选中「嘉措」（西藏人名后缀）

    打分要点：
    - **字面出现在查询里不等于相关**。「崩坏星穹铁道2.0版本新角色有哪些」里的
      「2.0」字面就在查询中，但用户问的是游戏不是版本号。
      所以先判「这个词条本身是不是有实义」，再谈相关度。
    - **来源权威性优先**：百度百科的正式词条 > 第三方站点转载。
      实测同一个词，网易云游戏的转载页会和百科正条同时命中，
      必须让百科排前面。
    - 简介与查询的字符交集只作微弱参考（按比例给分，不按个数），
      否则长查询天然占便宜。
    """
    if not entries:
        return None
    q = query.strip()

    # ---- 判断层：先剔掉明显不对的候选 ----
    trusted, rejected = judge_entries(q, entries)
    if not trusted:
        return None

    scored: list[tuple[int, dict]] = []
    for e in trusted:
        title = e["title"]
        desc = e.get("desc") or ""
        url = e.get("url") or ""
        score = 0

        # ── 前置门槛：词条本身要有实义 ──
        core = _core_name(title)
        if _NUM_ONLY.match(core):
            # 纯数字/版本号（2.0、3.0、V2）当答案毫无意义
            scored.append((-100, e))
            continue
        core_chars = len(
            re.findall(r"[\u4e00-\u9fa5a-zA-Z]", re.sub(r"[\d.\s\-_vV]+", "", core))
        )
        if core_chars < 2:
            score -= 40

        # ── 来源权威性 ──
        if "baike.baidu.com" in url:
            score += 45
        if e.get("from_api"):
            # 开放 API 返回的是百科认定的正式词条
            score += 25

        # ── 相关度 ──
        if core and core in q:
            score += 100
        elif title in q:
            score += 90

        # 简介是通用词 → 大幅降权
        if any(g in desc for g in GENERIC_DESC):
            score -= 60

        # 简介带作品名（游戏/影视/歌）→ 相关
        if re.search(r"[《》〈〉]|游戏|角色|作品|专辑|电影|电视剧|动漫", desc):
            score += 25

        # 字符交集：按比例给分，不按个数（避免长查询天然占便宜）
        if desc and q:
            shared = len(set(q) & set(desc))
            score += int(shared / max(len(set(q)), 1) * 12)

        # 简介里直接出现查询里的关键词 → 相关
        for kw in re.findall(r"[\u4e00-\u9fa5]{2,}", q):
            if kw in desc:
                score += 8
                break

        scored.append((score, e))

    scored.sort(key=lambda x: -x[0])
    if scored[0][0] <= 0:
        return None
    return scored[0][1]


async def baike_search(
    session: aiohttp.ClientSession,
    query: str,
    jieba_mod=None,
    count: int = 10,
    max_terms: int = 3,
) -> list[tuple[str, str, str, bool]]:
    """百科通道的搜索版：返回 (标题, 网址, 摘要, 是否来自开放API)。

    给 search_web 当兜底用——必应结果差的时候，用百科结果补上。

    两条来源：
      ① `site:baike.baidu.com <专名>` 的必应结果（摘要比开放 API 丰富）
      ② 开放 API 命中的正式词条（简介 + 结构化字段拼成摘要）
    """
    q = (query or "").strip()
    if not q:
        return []

    terms = _segment(q, jieba_mod) if jieba_mod else []
    if _looks_like_term(q) and q not in terms:
        terms.insert(0, q)

    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def add(title: str, url: str, desc: str, from_api: bool = False) -> None:
        if not title or not url or url in seen:
            return
        if _is_dict_entry(title, url):
            return
        seen.add(url)
        out.append((title, url, desc, from_api))

    # ① 开放 API：拿正式词条名 + 结构化信息
    for term in terms[:max_terms]:
        info = await _lookup_openapi(session, term)
        if not info:
            continue
        desc = info.get("desc") or ""
        card = info.get("card") or []
        if card:
            extra = "；".join(f"{n}：{v}" for n, v in card[:6])
            desc = f"{desc}（{extra}）" if desc else f"（{extra}）"
        add(info["title"], info["url"], desc[:400], from_api=True)
        await asyncio.sleep(0.4)

    # ② 必应百科限定：摘要更丰富
    for term in terms[:max_terms]:
        for title, link, desc in await _search_baike_site(session, term, count):
            add(title, link, desc)
        await asyncio.sleep(0.4)

    return out


async def baike_lookup(
    query: str,
    jieba_mod=None,
    include_body: bool = True,
    max_body_chars: int = 3000,
) -> tuple[str, list[dict]]:
    """查百度百科。

    Returns:
        (给模型看的文本, 结构化词条列表)
    """
    q = (query or "").strip()
    if not q:
        return "没给要查的词。", []

    terms = _segment(q, jieba_mod) if jieba_mod else []
    # 查询本身就是专名时也要试（分词可能失败）
    if _looks_like_term(q) and q not in terms:
        terms.insert(0, q)

    entries: list[dict] = []
    seen_urls: set[str] = set()

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=TIMEOUT)
    ) as session:
        # ---- L1：开放 API 拿正式词条名 + 结构化字段 ----
        for term in terms[:4]:
            info = await _lookup_openapi(session, term)
            if not info:
                continue
            if info["url"] in seen_urls:
                continue
            seen_urls.add(info["url"])
            info["matched_term"] = term
            info["from_api"] = True
            entries.append(info)
            await asyncio.sleep(0.4)

        # ---- L2：必应百科限定，拿更丰富的摘要 ----
        for term in terms[:3]:
            for title, link, desc in await _search_baike_site(session, term):
                if _is_dict_entry(title, link):
                    continue
                if link in seen_urls:
                    continue
                seen_urls.add(link)
                entries.append({
                    "title": title,
                    "desc": desc,
                    "card": [],
                    "url": link,
                    "matched_term": term,
                    "from_search": True,
                })
            await asyncio.sleep(0.4)

    # ---- 挑出最贴题的词条 ----
    primary = _pick_best(q, entries)
    if primary is None:
        return (
            f"百度百科里没有找到「{q}」这个词条。"
            "（百科只收录有一定知名度的词条，冷门专名可能没有。）"
        ), []

    lines = [f"百度百科「{primary['title']}」", ""]
    if primary.get("desc"):
        lines.append(f"简介：{primary['desc']}")
        lines.append("")

    card = primary.get("card") or []
    if card:
        lines.append("结构化信息：")
        for name, value in card[:14]:
            lines.append(f"  {name}：{value[:160]}")
        lines.append("")

    # ---- L3：正文（可选）----
    if include_body:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=TIMEOUT)
        ) as session:
            body, page_meta = await _fetch_baike_page(session, primary["title"])
        # meta 简介比正文段落稳得多（100% 拿到），正文没抓到时至少还有它
        if page_meta and page_meta != primary.get("desc"):
            lines.append("百科页简介：")
            lines.append(page_meta[:max_body_chars])
            lines.append("")
        if body:
            lines.append("正文摘录：")
            lines.append(body[:max_body_chars])
        else:
            lines.append(
                "（正文没抓到——百度的正文页时快时慢，同一个链接重试多次也常返回空壳。"
                "上面的简介和结构化信息已经够用了。）"
            )

    lines.append("")
    lines.append(f"词条链接：{primary.get('url') or ''}")

    # 附上其它相关词条（跳过词典页和明显的无关项）
    others = [
        e for e in entries
        if e is not primary
        and not _is_dict_entry(e.get("title") or "", e.get("url") or "")
        and not any(g in (e.get("desc") or "") for g in GENERIC_DESC)
    ]
    if others:
        lines.append("")
        lines.append("百科里还有这些相关条目：")
        for e in others[:5]:
            d = e.get("desc") or ""
            lines.append(f"  - {e['title']}：{d[:80]}")

    return "\n".join(lines), entries
