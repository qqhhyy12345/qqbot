"""网页正文抓取 —— 给模型一个读网页的工具。

只做一件事：抓 URL、去噪、出正文。所有安全边界都在代码里，不依赖提示词。
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import quote_plus, urlparse

import aiohttp
from bs4 import BeautifulSoup

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from . import baike

BING_RSS = "https://www.bing.com/search"
MAX_RESULTS = 10

# ---- 检索策略参数 ----
# Bing 对中文查询只认真实检索第一个 token，专名会被单字化，
# 于是返回「鸣」「原」「崩」这类单字的字典条目占满结果位（中文坍缩）。
# 下面这套参数专门对付它。
MIN_USABLE = 3          # 过滤后至少要有这么多条，才认为这次搜索够用
MAX_RETRIES = 2         # 同一查询最多重试几轮（坍缩是概率性的，重试有时能撞到正常那次）
RETRY_DELAY = 0.7       # 重试间隔（秒）
MAX_LINK_RATIO = 0.40   # 正文里链接占比超过这个值，说明选错容器了，宁可说不可靠

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 20
MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS_LIMIT = 6000
ALLOWED_TYPES = ("text/html", "text/plain", "application/xhtml+xml")

DROP_TAGS = (
    "script", "style", "noscript", "nav", "footer", "header",
    "aside", "form", "iframe", "svg", "button", "input",
    "ins", "object", "embed", "applet", "canvas", "picture",
    "source", "track", "map", "audio", "video",
)

# 导航、广告、分享栏这类容器的 class / id 特征
# 注意：这里的词必须是"独占 token"才匹配（前后必须是 - _ 或首尾），
# 像 app 这种太常见的词绝不能加——CSDN 整页就是 <div id="app"> 包着的，
# 加进去会把整篇文章连根拔掉（实测 3575 字 → 0 字）。
NOISE_RE = re.compile(
    r"(?:^|[-_])(nav|navbar|menu|sidebar|breadcrumb|crumb|footer|header|topbar|"
    r"toolbar|copyright|share|related|recommend|comment|advert|ads?|promo|banner|"
    r"cookie|popup|modal|mask|overlay|sponsor|subscribe|newsletter|"
    r"download-?(btn|button|app|link)?|qrcode|qr-?code|"
    r"float-?(left|right)|fixed-?(top|bottom|bar|panel)|"
    r"pager|pagination|tag-?list)(?:$|[-_])",
    re.I,
)

# 正文里以独立行出现的噪音（广告位、"相关阅读"、"扫码下载"这类）
NOISE_LINE_PAT = re.compile(
    r"^\s*(?:"
    r"广告|商业推广|推广|广告合作|赞助|"
    r"相关阅读|相关推荐|相关资讯|猜你喜欢|热门推荐|推荐阅读|大家都在看|"
    r"扫码|扫描二维码|下载APP|下载客户端|立即下载|手机版|"
    r"关注公众号|扫码关注|更多精彩|当前位置|"
    r"点赞|收藏|分享|举报|"
    r"Copyright|©|版权所有|备案号|隐私政策|用户协议|免责声明|"
    r"Previous|Next|Home|More"
    r")",
    re.I,
)
# 噪音行都很短；正文句子更长，用长度上限兜住，避免误伤正文
NOISE_LINE_MAX = 12

# 面包屑：「全国 > 上海 > 城区」「天气网 > 南京24小时天气」这类分隔路径
# 每段都短、整行也短，与正文句子区分得开
BREADCRUMB_PAT = re.compile(
    r"^\s*\S{1,14}\s*(?:[>|｜»])\s*\S{1,14}"
    r"(?:\s*(?:[>|｜»])\s*\S{1,14})*\s*$"
)
# 单独成行的分隔符。页面的面包屑常被拆成「全国」「>」「上海」「>」「城区」五行，
# 整行匹配不到，只能先把纯分隔符行剔掉。
SEP_LINE_PAT = re.compile(r"^\s*[>|｜»·]\s*$")

# ---- 词典页识别（中文坍缩的核心） ----
# Bing 把「鸣潮」当成不认识的专名，单字化成「鸣」，
# 于是返回「鸣（汉语汉字）_百度百科」「鸣 míng - 汉典」这类词条，把结果位占满。
DICT_HOSTS = (
    "hgcha.com", "hanyuguoxue.com", "zdic.net", "zidian.gushici.net",
    "haomingdao.com", "ufanv.cn", "hydcd.com", "dict.cn", "hujiang.com",
    "youdao.com", "chazidian.com", "zidian365.com", "hujiang.com",
)
DICT_TITLE_PAT = re.compile(
    r"汉语字典|汉典|漢典|新华字典|汉语国学|汉语字库|字海|字词大全|"
    r"组词|部首|笔顺|五笔|四角号码|仓颉码|郑码|区位码|电报码|"
    r"读音|拼音.{0,6}(?:意思|解释)|字的意思|的意思，|的意思_",
    re.I,
)
BAIKE_ITEM_PAT = re.compile(r"baike\.baidu\.com/item/([^/?#]+)", re.I)


def _bad_ip(host: str) -> bool:
    """回环、私网、链路本地、保留地址一律拒绝。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _check_url(url: str) -> str | None:
    """返回错误说明；None 表示可以抓。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return "网址解析不了。"
    if parsed.scheme not in ("http", "https"):
        return "只支持 http 和 https。"
    host = parsed.hostname
    if not host:
        return "网址里没有域名。"
    if _bad_ip(host):
        return "这个地址指向本机或内网，不能访问。"
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
    except Exception:
        return f"域名解析失败：{host}"
    for info in infos:
        addr = info[4][0]
        if _bad_ip(addr):
            return "这个域名解析到了内网地址，不能访问。"
    return None


def _block_stats(cand) -> tuple[int, int]:
    """返回 (容器文字总长, 链接文字总长)。

    口径必须统一：打分和质量守卫都靠这两个数，
    分子分母用不同的文本算，守卫就会误伤（实测把中国天气网挡掉了）。
    """
    text = cand.get_text(" ", strip=True)
    link_len = sum(len(a.get_text(strip=True)) for a in cand.find_all("a"))
    return len(text), link_len


def _score_block(cand) -> float:
    """给候选容器打分：文字越多越好，链接越多越扣分。

    早期版本只看 <p> 的文字总长，结果会选中"包住一切的根容器"——
    实测 tianqi.com 的当日天气页选中了链接占比 85% 的外层 div，
    抓回来全是「西双版纳怎么玩」「如何破解 wifi」，真正的温度一个没有。
    加上链接密度惩罚后，同一个页面选中了链接占比 2% 的天气盒，
    抓回来是「20℃ 晴 15~21℃ 湿度 64%」。
    """
    tlen, link_len = _block_stats(cand)
    if tlen < 50:
        return -1.0
    ratio = link_len / tlen if tlen else 1.0
    if ratio > 0.45:
        # 链接占比过高 → 这是导航/推荐区，不是正文。
        # 阈值定 0.45 而不是 0.55：weather.com.cn 的 `con today` 容器
        # 清掉导航后链接占比正好 54%，用 0.55 会漏进来，
        # 而它的得分反而比真正的 7 天预报容器高。
        return -1.0
    p_len = sum(len(p.get_text(strip=True)) for p in cand.find_all("p"))
    # 段落文字权重高，散落在 span/li 里的散文权重低一些
    score = p_len + (tlen - p_len) * 0.3
    return score * (1 - ratio)


def _extract(html_text: str) -> tuple[str, str, float]:
    """抽正文。返回 (标题, 正文, 链接占比)。

    链接占比给调用方做质量守卫用：一篇正文的链接占比应该很低，
    占比过高说明选错了容器，抓回来的是导航而不是文章。
    """
    soup = BeautifulSoup(html_text, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(DROP_TAGS):
        tag.decompose()

    # 先摘掉导航、广告、分享栏这类容器
    noisy = []
    seen_noisy: set[int] = set()

    def mark(tag) -> None:
        if id(tag) not in seen_noisy:
            seen_noisy.add(id(tag))
            noisy.append(tag)

    for tag in soup.find_all(True):
        # 注意：本模块的 filter 是 AstrBot 装饰器，不能用内置 filter()
        parts = []
        klass = tag.get("class")
        if klass:
            parts.append(" ".join(klass) if isinstance(klass, (list, tuple)) else str(klass))
        if tag.get("id"):
            parts.append(str(tag.get("id")))
        ident = " ".join(parts)
        if ident and NOISE_RE.search(ident):
            mark(tag)
            continue
        if tag.name in ("body", "html"):
            continue
        # 启发式判定只作用于「叶子容器」。
        # 父容器往往也满足"一堆链接"，一旦被删就会连正文一起带走——
        # 实测把 CSDN 的 3709 字正文删成了 0 字。
        if tag.find(["div", "section", "article", "ul", "ol", "table", "p", "blockquote"]):
            continue
        # 疑似「相关阅读 / 推荐位」列表：一堆链接，几乎没有正文
        links = tag.find_all("a")
        if len(links) >= 3:
            own = len(tag.get_text(strip=True))
            link_chars = sum(len(a.get_text(strip=True)) for a in links)
            if own < 500 and link_chars >= own * 0.6:
                mark(tag)
                continue
        # 疑似广告图位：一张图 + 极少文字
        if tag.find("img") and len(tag.get_text(strip=True)) < 30:
            mark(tag)
    for tag in noisy:
        tag.decompose()

    # 挑正文容器：文字多、链接少的那个
    best, best_score = None, 0.0
    for cand in soup.find_all(["article", "main", "div", "section"]):
        s = _score_block(cand)
        if s > best_score:
            best, best_score = cand, s
    root = best if best is not None else (soup.find("article") or soup.body or soup)

    text = root.get_text("\n", strip=True)
    text = re.sub(r"[ \t\u3000]{2,}", " ", text)
    # 剔掉以独立行出现的噪音：「广告」「相关阅读」「扫码下载APP」这类
    lines = [
        ln for ln in text.split("\n")
        if not (NOISE_LINE_PAT.match(ln) and len(ln.strip()) <= NOISE_LINE_MAX)
        and not BREADCRUMB_PAT.match(ln)
        and not SEP_LINE_PAT.match(ln)
    ]
    text = "\n".join(lines)
    text = re.sub(r"\n{2,}", "\n", text).strip()

    tlen, link_len = _block_stats(root)
    ratio = link_len / tlen if tlen else 0.0
    return title, text, ratio


def _parse_rss(xml_text: str, limit: int) -> list[tuple[str, str, str]]:
    """解析必应 RSS：稳定、无 cookie、不受反爬影响。"""
    import html as _html

    out: list[tuple[str, str, str]] = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.S):
        def pick(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
            return _html.unescape(m.group(1)).strip() if m else ""

        link = pick("link")
        title = pick("title")
        if not title or not link.startswith("http"):
            continue
        snippet = re.sub(r"<[^>]+>", " ", pick("description"))
        snippet = re.sub(r"\s+", " ", snippet).strip()
        out.append((title, link, snippet))
        if len(out) >= limit:
            break
    return out


def _is_dict_page(title: str, url: str) -> bool:
    """这条结果是不是「被当成词条查出来的字典页」。

    Bing 中文坍缩时，会返回大量「鸣（汉语汉字）_百度百科」「鸣 míng - 汉典」
    这类单字条目，把真正的攻略页挤掉。这里把它们识别出来剔除。

    判定用「标题特征 + 词典站域名 + 百科单字词条」三条，任一命中即算。
    """
    t = (title or "").strip()
    u = (url or "").strip()
    if not t:
        return False
    # 1) 标题里出现词典站的典型特征词
    if DICT_TITLE_PAT.search(t):
        return True
    # 2) 百度百科词条：单字词条 = 坍缩，多字词条 = 正常内容
    m = BAIKE_ITEM_PAT.search(u)
    if m:
        from urllib.parse import unquote

        if len(unquote(m.group(1))) == 1:
            return True
    # 3) 已知词典站域名
    low = u.lower()
    if any(h in low for h in DICT_HOSTS):
        return True
    return False


def _clean_results(results: list[tuple]) -> list[tuple[str, str, str]]:
    """剔除词典页 + 按 URL 去重，保留首次出现的顺序。

    ⚠️ 元组长度**不固定**，不能做定长解包：
      - 必应 RSS 给 (标题, 链接, 摘要)
      - 百度百科通道给 (标题, 链接, 摘要, 是否来自开放API)
    统一按下标取前三个。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for item in results:
        t, u, s = item[0], item[1], item[2]
        if not t or not u:
            continue
        if u in seen:
            continue
        seen.add(u)
        if _is_dict_page(t, u):
            continue
        out.append((t, u, s))
    return out


def _query_entities(query: str) -> list[str]:
    """从查询里提取核心实体，用于相关度判定。

    **为什么不能直接用 `query.split()`**：中文长句没有空格，
    split 下来只有一整句，拿整句去标题里匹配 = 永远匹配不上，
    于是相关度判据形同虚设 —— 搜「南航有哪些王牌专业」会拿
    「南京市百科」「去南京玩攻略」这种一堆偏题结果判定为"够用"，
    检索提前结束，正确答案根本没机会出现。

    这里复用百科通道的分词器（`baike._segment`）：它会合并连续实义词
    和相邻碎片，能把「南京航空航天大学有哪些王牌专业」提出
    「南京航空航天大学」，把「依央嘉措是什么地方」提出「依央嘉措」。
    """
    # 整句本身不当实体——它不可能出现在标题里，而且它包含所有子实体，
    # 留着会把真正有用的「南京航空航天大学」当成"被整句包含"而丢掉。
    toks: list[str] = [
        t for t in query.split() if len(t) >= 2 and t != query.strip()
    ]
    jm = _load_jieba()
    if jm:
        try:
            toks += [
                t for t in baike._segment(query, jm, limit=8) if len(t) >= 2
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[fetch_url] 实体提取失败: {exc}")

    # 去掉首尾虚词（「依央嘉措是」→「依央嘉措」）
    cleaned: list[str] = []
    for t in toks:
        t = t.strip("的是了吗呢啊呀吧在和与或这那个")
        if len(t) >= 2 and t not in cleaned:
            cleaned.append(t)

    # 长的优先
    cleaned.sort(key=len, reverse=True)
    # 被更长实体连续包含的短实体丢掉
    # （「南京」被「南京航空航天大学」覆盖，「航空」也是它的片段）
    out: list[str] = []
    for t in cleaned:
        if any(t != o and t in o for o in out):
            continue
        out.append(t)
    return out


def _relevant(title: str, entities: list[str]) -> bool:
    """标题里出现了查询里的核心实体 → 认为这条是有效命中。

    有些查询 Bing 只能给出 1 条结果，但那条恰恰是对的（比如直接给出攻略帖），
    这时不该再重试——所以判断够不够用，不能只看条数，还得看相关度。
    """
    low = (title or "").lower()
    return any(e.lower() in low for e in entities if len(e) >= 2)


def _enough(
    results: list[tuple], entities: list[str]
) -> bool:
    """判断当前结果够不够用，不够才值得再花请求。

    三条判据，从宽到严：
    1. 有一条标题直接命中查询核心实体（哪怕只有 1 条，那也可能正是答案）
    2. 条数达标 **且** 来自两个以上不同站点
       （说明找到了不同角度的内容，而不是同一个官网反复出现）

    ⚠️ 判据 2 必须带「多来源」条件。原来只看条数，导致
    「南京航空航天大学有哪些王牌专业」拿到 3 条偏题结果
    （南京市百科 + 南京旅游攻略 + 南京大学介绍）就被判定"够用"，
    检索提前收尾、百科通道根本不触发，用户看到的是一堆不相干的东西。
    """
    if not results:
        return False
    # 命中优先：有一条标题直接对上查询实体，立刻算够用
    if any(_relevant(r[0], entities) for r in results):
        return True
    hosts = set()
    for r in results:
        try:
            hosts.add(urlparse(r[1]).netloc.lower())
        except Exception:  # noqa: BLE001
            continue
    # 一条都没命中 → 几乎可以确定是偏题（「南航」给南京市百科 + 南京旅游攻略）。
    # 这时不能收尾，要继续往下走（百科通道），把正确结果捞出来。
    # 兜底给得很宽（条数翻倍 + 三个以上来源），只为了放过
    # 「结果确实对、但标题里没出现查询实体」这种少数情况。
    return len(results) >= MIN_USABLE * 2 and len(hosts) >= 3


class _FetchError(Exception):
    """抓取过程中的可预期错误，消息可以直接给用户看。"""


def _is_cert_error(exc: Exception) -> bool:
    """判断是不是证书链校验失败。

    实测 www.nmc.cn 报 `CERTIFICATE_VERIFY_FAILED: self-signed certificate in
    certificate chain`——中间证书链被企业代理或杀软替换了。
    只有这一类错误才值得放宽重试，其他错误照常报错。
    """
    name = type(exc).__name__.lower()
    msg = f"{name}: {exc}".lower()
    return (
        "certificate_verify_failed" in msg
        or "certificate verify failed" in msg
        or "self-signed certificate" in msg
        or "sslcerterror" in name
        or "sslerror" in name
    )


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> tuple[str, str]:
    """抓一个页面，返回 (html 文本, 最终地址)。"""
    async with session.get(url, allow_redirects=True) as resp:
        # 跳转后的地址也要查一遍，否则可以用 302 绕过
        final = str(resp.url)
        if final != url:
            err = await _check_url(final)
            if err:
                raise _FetchError(f"跳转到了不能访问的地址：{err}")

        ctype = (resp.headers.get("Content-Type") or "").lower()
        if ctype and not any(t in ctype for t in ALLOWED_TYPES):
            raise _FetchError(
                f"这个地址返回的是 {ctype.split(';')[0]}，不是网页，读不了。"
            )

        raw = await resp.content.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise _FetchError("页面超过 2 MB，太大了，没读。")
        if resp.status >= 400:
            raise _FetchError(f"页面返回 {resp.status}，读不到内容。")

        charset = resp.charset or "utf-8"
        try:
            html_text = raw.decode(charset, errors="replace")
        except LookupError:
            html_text = raw.decode("utf-8", errors="replace")
        return html_text, final


async def _fetch_rss(
    session: aiohttp.ClientSession, query: str, count: int
) -> list[tuple[str, str, str]]:
    """请求一次必应 RSS。必应对 RSS 不做反爬，稳定且不用 cookie。"""
    url = f"{BING_RSS}?q={quote_plus(query)}&format=rss&count={count}"
    async with session.get(url) as resp:
        if resp.status >= 400:
            return []
        raw = await resp.content.read(MAX_BYTES)
        return _parse_rss(
            raw.decode(resp.charset or "utf-8", errors="replace"), count
        )


def _load_jieba():
    """加载 jieba 分词库。装了才用，没装也不影响主流程。

    jieba 已经装在 AstrBot 的 venv 里（0.42.1），但仍要做成可选依赖——
    万一以后环境变了，搜索功能不能因此挂掉。
    """
    global _JIEBA
    if _JIEBA is not None:
        return _JIEBA
    try:
        import jieba

        jieba.setLogLevel(60)
        _JIEBA = jieba
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[fetch_url] jieba 不可用，百科通道降级: {exc}")
        _JIEBA = False
    return _JIEBA or None


_JIEBA = None


class FetchUrlPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)

    @filter.llm_tool(name="search_web")
    async def search_web(
        self, event: AstrMessageEvent, query: str, count: int = 5
    ) -> str:
        """用必应搜索网页，返回带标题、网址和摘要的结果。这是你唯一能用的网页搜索：想知道网上的事实、新闻、价格、天气、资料，先搜一遍再回答，别凭记忆编。搜完如果只是摘要不够，再对感兴趣的链接用 fetch_url 读正文。

        搜索技巧（搜得准全靠这个）：
        - 查询写 2~4 个关键词，不要写成整句话。「鸣潮 珂莱塔 养成攻略」这种长串，
          必应只会认真检索第一个词，后面的全丢掉。
        - **必应对中文会拆词**：碰到它不认识的专名（人名、地名、角色名）会把词拆成
          单字，搜出来变成完全不相干的东西。
          这时结果里会混进一堆不相干的条目——**直接忽略它们看剩下的**，
          不要照着答，也不要反复换词试。
        - **搜游戏角色、影视、音乐、专有名词时，改用 baike_lookup**：百科对专名的
          收录最全，给的还是结构化资料（属性、别名、所属作品）。
        - 搜不到就如实说没查到，不要照着记忆编。

        Args:
            query(string): 搜索关键词，建议 2~4 个词
            count(int): 要几条结果，默认 5，最多 10
        """
        query = (query or "").strip()
        if not query:
            return "关键词是空的。"
        try:
            count = int(count)
        except Exception:
            count = 5
        count = max(1, min(count, MAX_RESULTS))

        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        raw_all: list[tuple] = []
        tried: list[str] = []
        # 相关度判定用「核心实体」而不是整句——中文长句没有空格，
        # 拿整句去标题里匹配永远匹配不上，相关度判据会形同虚设。
        entities = _query_entities(query)
        try:
            async with aiohttp.ClientSession(
                timeout=timeout, headers={"User-Agent": UA}
            ) as session:
                # 阶段 1：原查询 + 条件重试。
                # 必应的中文坍缩是概率性的——同一查询有时返回满屏词典页，
                # 有时又完全正常。重试能救回「不稳定」的那类。
                for attempt in range(MAX_RETRIES + 1):
                    got = await _fetch_rss(session, query, count)
                    tried.append(query)
                    raw_all.extend(got)
                    cleaned = _clean_results(raw_all)
                    # 够用了就收工（命中查询实体，或条数多且来源分散）
                    if _enough(cleaned, entities):
                        break
                    # 一条都没有 → 重试也没意义，直接交给百科通道
                    if not got:
                        break
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY)

                # 阶段 2：必应对上 → 走百科通道。
                # 必应只认真检索第一个 token，遇到中文专名就单字化，这是它自己的软肋；
                # 百科对专名的收录最全，用开放 API + site: 限定能绕开单字化。
                # 不做查询拆分——拆出来的子查询照样被必应拆词，收益不稳定，
                # 判断交给百科通道就够了。
                if not _enough(_clean_results(raw_all), entities):
                    try:
                        jieba_mod = _load_jieba()
                        got = await baike.baike_search(
                            session, query, jieba_mod=jieba_mod, count=count
                        )
                        if got:
                            raw_all.extend(got)
                            tried.append("百度百科通道")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[search_web] 百科通道失败: {exc}")
        except asyncio.TimeoutError:
            return "搜索超时了，稍后再试。"
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[search_web] 请求失败 {query}: {exc}")
            return "搜索请求发不出去，可能是网络问题。"

        results = _clean_results(raw_all)
        dropped = len(raw_all) - len(results)
        if not results:
            logger.info(f"[search_web] {query} -> 0 条（试过 {len(tried)} 次）")
            return (
                f"没搜到「{query}」的可靠结果"
                f"（试过 {'、'.join(dict.fromkeys(tried))}）。"
                "如果这是个人名、地名、游戏角色这类专名，"
                "可以改用 baike_lookup 查百度百科；确实查不到就如实说没查到，"
                "不要照着印象编。"
            )

        # 命中查询实体的排前面。必应坍缩时前几条往往全是噪音
        # （搜「南京航空航天大学」前六条全是南京市百科和南京旅游攻略），
        # 不排序的话模型顺着第一条读，答案就跑偏了。
        results.sort(key=lambda r: 0 if _relevant(r[0], entities) else 1)

        logger.info(
            f"[search_web] {query} -> {len(results)} 条"
            f"（过滤词典页 {dropped} 条，试过 {len(tried)} 次）"
        )
        lines = [
            f"搜索「{query}」，共 {len(results)} 条（排在最前的是最相关的）：",
            "",
        ]
        for i, (title, href, snippet) in enumerate(results, 1):
            lines.append(f"{i}. {title}")
            lines.append(f"   {href}")
            if snippet:
                lines.append(f"   {snippet[:300]}")
        lines.append("")
        lines.append("这些只是摘要。需要原文就对某条链接用 fetch_url。")
        lines.append(
            "如果前几条和查询明显对不上题（必应把专名拆成了单字），"
            "改用 baike_lookup 查百度百科更准。"
        )
        return "\n".join(lines)

    @filter.llm_tool(name="baike_lookup")
    async def baike_lookup(
        self, event: AstrMessageEvent, term: str, with_body: bool = True
    ) -> str:
        """查百度百科。问「XX 是什么」「XX 是谁」「XX 的资料/设定」这类问题时优先用这个，比网页搜索准得多。

        必应对中文长查询会拆词，搜游戏角色、影视、音乐、专有名词时结果经常是
        「XX（汉语汉字）_百度百科」这种字典型条目，根本对不上题。百度百科正好相反——
        它对专名的收录最全，而且给的是结构化资料（属性表、别名、配音、所属作品…）。

        用法：直接传要查的名词，比如「珂莱塔」「崩坏星穹铁道」「影神图」。
        传整句话也能用，它会自动挑出里面的专名。

        拿不到就说没收录，别编。

        Args:
            term(string): 要查的词或名字
            with_body(bool): 是否抓取正文摘录，默认 True；只要简介和属性表就传 False，更快
        """
        term = (term or "").strip()
        if not term:
            return "没给要查的词。"

        jieba_mod = _load_jieba()

        try:
            text, _entries = await baike.baike_lookup(
                term, jieba_mod=jieba_mod, include_body=bool(with_body)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[baike_lookup] 查询失败 {term}: {exc}")
            return "百科查询出错了，稍后再试。"

        logger.info(f"[baike_lookup] {term} -> {len(text)} 字")
        return text

    @filter.llm_tool(name="fetch_url")
    async def fetch_url(
        self, event: AstrMessageEvent, url: str, max_chars: int = 3000
    ) -> str:
        """抓一个网页并读出正文。搜索拿到的只是摘要，想认真回答一个问题，就用这个把原文读一遍。读不到就如实说读不到，不要照着摘要编。

        读完发现抓回来的是满屏导航、广告、推荐链接（这在天气站、门户站特别常见），
        说明这个页面的正文提不出来——那就直接用搜索结果里的摘要回答，不要拿垃圾凑。

        Args:
            url(string): 要读的网页地址，必须是 http 或 https 开头
            max_chars(int): 正文最多返回多少字，默认 3000，上限 6000
        """
        url = (url or "").strip()
        if not url:
            return "没给网址。"
        try:
            max_chars = int(max_chars)
        except Exception:
            max_chars = 3000
        max_chars = max(300, min(max_chars, MAX_CHARS_LIMIT))

        err = await _check_url(url)
        if err:
            return err

        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        html_text = ""
        final = url
        try:
            try:
                async with aiohttp.ClientSession(
                    timeout=timeout, headers={"User-Agent": UA}
                ) as session:
                    html_text, final = await _fetch_html(session, url)
            except Exception as exc:  # noqa: BLE001
                if not _is_cert_error(exc):
                    raise
                # 证书链验不过：多半是中间证书被代理/杀软替换了。
                # 只在这一种情况下放宽重试，不是无条件降级。
                logger.warning(
                    f"[fetch_url] {url} 证书校验失败，放宽后重试一次：{exc}"
                )
                async with aiohttp.ClientSession(
                    timeout=timeout,
                    headers={"User-Agent": UA},
                    connector=aiohttp.TCPConnector(ssl=False),
                ) as session:
                    html_text, final = await _fetch_html(session, url)
        except _FetchError as exc:
            return str(exc)
        except asyncio.TimeoutError:
            return "读取超时了，这个站可能太慢。"
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[fetch_url] 抓取失败 {url}: {exc}")
            return "这个网页打不开。"

        title, text, link_ratio = _extract(html_text)
        if not text or len(text) < 30:
            return "页面里没抽到正文，可能是要靠脚本渲染的站，或者是空页面。"
        if link_ratio > MAX_LINK_RATIO:
            # 链接占比过高 = 选错了容器，抓回来的是导航而不是文章。
            # 宁可明说不可靠，也不要把垃圾喂给模型——
            # 实测这种情况下 RSS 摘要里的信息反而比正文有用。
            logger.info(
                f"[fetch_url] {url} 正文提取不可靠（链接占比 {link_ratio:.0%}）"
            )
            return (
                f"这个页面结构混乱，正文提取不可靠（{link_ratio:.0%} 都是链接）。"
                "别用这条结果回答——要么换个来源，要么就直接用搜索结果里的摘要。"
            )

        truncated = len(text) > max_chars
        body = text[:max_chars]
        head = f"标题：{title}\n地址：{final}\n\n" if title else f"地址：{final}\n\n"
        tail = f"\n\n（正文共 {len(text)} 字，这里只给了前 {max_chars} 字）" if truncated else ""
        logger.info(f"[fetch_url] {url} -> {len(text)} 字（链接占比 {link_ratio:.0%}）")
        return head + body + tail
