"""search_web 改进（C 方案）+ 正文广告过滤（D 方案）的回归测试。

样本全部取自真实 Bing RSS 返回的坍缩结果与真实网页结构。
离线运行，不发网络请求。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PLUGIN = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_fetch_url\main.py")
SRC = PLUGIN.read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [!!] {name}  {extra}")


TREE = ast.parse(SRC)

WANTED_CONST = {
    "MIN_USABLE", "MAX_RETRIES", "RETRY_DELAY", "DICT_HOSTS",
    "DICT_TITLE_PAT", "BAIKE_ITEM_PAT", "NOISE_RE", "NOISE_LINE_PAT",
    "NOISE_LINE_MAX", "DROP_TAGS", "BREADCRUMB_PAT", "SEP_LINE_PAT",
    "MAX_LINK_RATIO",
}

NS: dict = {"re": re, "Any": object}
for _node in TREE.body:
    if isinstance(_node, ast.Assign):
        for _t in _node.targets:
            if isinstance(_t, ast.Name) and _t.id in WANTED_CONST:
                # 常量表达式里只用到 re.compile，限定命名空间求值
                _expr = ast.Expression(_node.value)
                ast.fix_missing_locations(_expr)
                NS[_t.id] = eval(compile(_expr, "<const>", "eval"), {"re": re}, {})

# 用 ast 精确切出函数源码
LINES = SRC.splitlines()


def grab(fn: str) -> str:
    for _node in TREE.body:
        if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _node.name == fn:
            return "\n".join(LINES[_node.lineno - 1:_node.end_lineno])
    raise RuntimeError(f"抠不出 {fn}")


for _fn in ("_is_dict_page", "_clean_results", "_relevant", "_enough",
            "_extract", "_score_block", "_block_stats", "_text_of",
            "_query_entities", "_load_jieba"):
    try:
        exec(grab(_fn), NS)
    except RuntimeError:
        pass

# _extract 依赖 bs4
from bs4 import BeautifulSoup  # noqa: E402

NS["BeautifulSoup"] = BeautifulSoup

# _query_entities 依赖百科通道的分词器
try:
    import sys as _sys

    _sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")
    from astrbot_plugin_fetch_url import baike as _baike_mod

    NS["baike"] = _baike_mod
    NS["_JIEBA"] = None
except Exception:  # noqa: BLE001
    NS["baike"] = None
    NS["_JIEBA"] = None

# ==================================================================
print("\n[1] 词典页识别 —— 样本来自真实 Bing RSS 坍缩结果")

DICT_SAMPLES = [
    ("鸣（汉语汉字）_百度百科", "https://baike.baidu.com/item/%E9%B8%A3/10395499"),
    ("《鸣》的拼音,鸣字的意思、组词、部首、笔画、笔顺 - 汉语查", "https://www.hgcha.com/zidian/be58baf2.html"),
    ("鸣的意思,鸣的解释,鸣的拼音,鸣的部首,鸣的笔顺-汉语国学", "https://www.hanyuguoxue.com/zidian/zi-40483"),
    ("鸣 míng - 汉典", "http://zdic.net/hans/%E9%B8%A3"),
    ("鸣怎么读_鸣的拼音 - 新华字典", "https://zidian.gushici.net/8/9e23.html"),
    ("鸣 míng - 漢典", "http://zdic.net/hant/%E9%B8%A3"),
    ("鸣字的含义和寓意 - 十大寓意解析 - 好名道", "https://www.haomingdao.com/zidian/%E9%B8%A3"),
    ("鸣的读音、意思、部首、笔顺及组词 - 新华字典", "https://www.ufanv.cn/zidian/%E9%B8%A3"),
    ("崩（汉语文字）_百度百科", "https://baike.baidu.com/item/%E5%B4%A9/81975"),
    ("原（汉语文字）_百度百科", "https://baike.baidu.com/item/%E5%8E%9F/81975"),
]
GOOD_SAMPLES = [
    ("《鸣潮》官方网站 - Wuthering Waves", "https://mc.kurogames.com/"),
    ("云·鸣潮 - Wuthering Waves", "https://mc.kurogames.com/cloud/index.html"),
    ("鸣潮角色 | V2.0珂莱塔培养攻略一图流 - TapTap", "https://www.taptap.cn/moment/123"),
    ("崩坏：星穹铁道角色图鉴 — 全角色Wiki、Build、技能与配队", "https://wiki.biligame.com/sr/角色图鉴"),
    ("甘雨 - 原神WIKI_BWIKI_哔哩哔哩", "https://wiki.biligame.com/ys/甘雨"),
    ("南京航空航天大学_百度百科", "https://baike.baidu.com/item/南京航空航天大学"),
    ("上海天气预报24小时详情_上海天气网", "https://www.weather.com.cn/weather/101020100.shtml"),
    ("【V3.0攻略】星穹铁道，全角色养成一图流攻略", "https://www.bilibili.com/video/BV1xx"),
    ("云·星穹铁道 - miHoYo", "https://sr.mihoyo.com/"),
]
for t, u in DICT_SAMPLES:
    check(f"词典页应剔除: {t[:34]}", NS["_is_dict_page"](t, u) is True)
for t, u in GOOD_SAMPLES:
    check(f"正常页应保留: {t[:34]}", NS["_is_dict_page"](t, u) is False)

# ==================================================================
print("\n[2] 结果清洗：剔词典 + URL 去重 + 保序")
mixed = [
    (*GOOD_SAMPLES[0], ""),
    (*DICT_SAMPLES[0], ""),
    (*GOOD_SAMPLES[1], ""),
    (*DICT_SAMPLES[3], ""),
    (*GOOD_SAMPLES[0], ""),          # 重复
    (*GOOD_SAMPLES[2], ""),
]
cleaned = NS["_clean_results"](mixed)
check("词典页被剔除", all(not NS["_is_dict_page"](t, u) for t, u, _ in cleaned))
check("重复 URL 去重", len({u for _, u, _ in cleaned}) == len(cleaned))
check("条数正确（3 条）", len(cleaned) == 3, f"got {len(cleaned)}")
check("保持首次出现顺序", cleaned[0][0] == GOOD_SAMPLES[0][0])

# ---- 百科通道返回 4 元组，不能让 _clean_results 崩 ----
# 回归 2026-10-23 20:31 的线上故障：baike_search 给 (标题,链接,摘要,from_api)，
# _clean_results 按 3 元组解包 → ValueError: too many values to unpack
baike_mixed = [
    ("鸣潮_百度百科", "https://baike.baidu.com/item/%E9%B8%A3%E6%BD%AE",
     "2024年广州库洛科技开发的游戏", True),
    (*DICT_SAMPLES[0], ""),
    ("《鸣潮》官方网站", "https://mc.kurogames.com/", "开放世界动作游戏", False),
]
try:
    cleaned4 = NS["_clean_results"](baike_mixed)
    check("4 元组（百科通道）不崩", True)
except ValueError as exc:
    check("4 元组（百科通道）不崩", False, f"got {exc}")
    cleaned4 = []
check("百科结果被保留", len(cleaned4) == 2, f"got {len(cleaned4)}")
check("百科结果仍为 3 元组", all(len(c) == 3 for c in cleaned4))
check("4 元组里词典页照样剔除",
      all(not NS["_is_dict_page"](c[0], c[1]) for c in cleaned4))
check("_enough 也能吃百科结果",
      NS["_enough"](cleaned4, ["鸣潮"]) is True)

# ---- 核心实体提取：中文长句没有空格，必须靠分词才能拿到实体 ----
_qe = NS.get("_query_entities")
if _qe is not None:
    ents = _qe("南京航空航天大学有哪些王牌专业")
    check("长句能提出完整校名", "南京航空航天大学" in ents, f"got {ents}")
    check("整句本身不当实体", "南京航空航天大学有哪些王牌专业" not in ents, f"got {ents}")
    check("被校名覆盖的碎片不出现", "南京" not in ents, f"got {ents}")
    ents2 = _qe("依央嘉措是什么地方")
    check("生僻专名也能提取", "依央嘉措" in ents2, f"got {ents2}")
    ents3 = _qe("鸣潮 珂莱塔")
    check("空格分词照常提取", "鸣潮" in ents3 and "珂莱塔" in ents3, f"got {ents3}")

# ---- _enough：偏题结果不能被判成够用 ----
offtopic = [
    ("南京市_百度百科", "https://baike.baidu.com/item/x", "南京"),
    ("南京必去景点大全 - 知乎", "https://zhuanlan.zhihu.com/p/1", "景点"),
    ("Nanjing - 南京市政府", "https://www.nanjing.gov.cn/", "市政府"),
    ("去了南京5次才整理出的攻略", "https://zhuanlan.zhihu.com/p/2", "攻略"),
]
check("偏题结果不算够用",
      NS["_enough"](offtopic, ["南京航空航天大学", "王牌专业"]) is False)
check("命中实体立刻算够用",
      NS["_enough"]([("南京航空航天大学", "https://x.com/", "简介")],
                   ["南京航空航天大学"]) is True)
check("空结果不够用", NS["_enough"]([], ["南京"]) is False)

# ==================================================================
print("\n[3] 查询拆分已移除")
check("源码里不再有 _split_queries 定义", "def _split_queries" not in SRC)
check("源码里不再有 _split_queries 调用", "_split_queries(" not in SRC)
check("MAX_SUBS 常量已删除", "MAX_SUBS" not in SRC)

# ==================================================================
print("\n[4] 相关度判定")
check("标题含关键词 → 相关", NS["_relevant"]("鸣潮角色 珂莱塔培养攻略", ["鸣潮", "珂莱塔"]) is True)
check("标题不含关键词 → 不相关", NS["_relevant"]("上海天气预报", ["鸣潮", "珂莱塔"]) is False)
check("单字符 token 被忽略", NS["_relevant"]("任意标题", ["鸣"]) is False)

# ==================================================================
print("\n[5] 正文广告/噪音过滤（方案 D）")
HTML = """
<html><head><title>测试文章 - 某站点</title></head>
<body>
<div class="header"><a href="#">首页</a><a href="#">分类</a></div>
<article>
  <p>这是正文的第一个段落，内容足够长，用来让 article 容器的段落总长度超过两百个字符的判定阈值，从而确保正文容器能被正确选中，而不会退回到整个 body 去做兜底处理。</p>
  <p>这是正文的第二个段落，同样是为了把总长度撑过阈值用的补充文字，确保这段话不会被误判成噪音而删掉，这样测试才能验证正文保留是否正常。</p>
  <div class="advert-banner">限时优惠 立即抢购</div>
  <div class="related-box">
    <a href="/a">推荐阅读一</a><a href="/b">推荐阅读二</a><a href="/c">推荐阅读三</a>
  </div>
  <p>广告</p>
  <p>相关阅读</p>
  <p>扫码下载APP</p>
  <p>广告收入占比虽然不高，但确实是公司收入结构里很重要的一部分。</p>
</article>
<div class="footer">Copyright 2026 某某网</div>
</body></html>
"""
title, text, ratio = NS["_extract"](HTML)
check("标题提取正确", "测试文章" in title, f"got {title!r}")
check("正文保留", "这是正文的第一个段落" in text)
check("正文第二段保留", "这是正文的第二个段落" in text)
check("广告容器被剔除", "立即抢购" not in text)
check("相关阅读列表被剔除", "推荐阅读一" not in text)
check("导航被剔除", "首页" not in text)
check("footer 被剔除", "Copyright" not in text)
check("独立行「广告」被剔除", not re.search(r"(?m)^广告$", text))
check("独立行「相关阅读」被剔除", not re.search(r"(?m)^相关阅读$", text))
check("独立行「扫码下载APP」被剔除", "扫码下载APP" not in text)
check("长正文行不被误伤（长度上限）",
      "广告收入占比虽然不高" in text)

# 回归：曾经把 app 加进 NOISE_RE，导致 <div id="app"> 整页被删
APP_HTML = """
<html><head><title>整页 app 包裹</title></head>
<body>
<div id="app" class="app-root">
  <div class="content">
    <article>
      <p>这是被 app 容器包裹的正文第一段，内容足够长，用来让 article 容器的段落总长度超过两百字阈值，确保它会被选为正文容器而不是被误删掉。</p>
      <p>这是第二段，同样是为了撑过两百字阈值而存在的补充文字，确保正文容器能被正确识别并保留下来，这样测试才能验证 app 这个 id 不会导致整页被清空。</p>
    </article>
  </div>
</div>
</body></html>
"""
_, app_text, _ = NS["_extract"](APP_HTML)
check("回归：id=app 的容器不会被整页删掉", len(app_text) > 100,
      f"got {len(app_text)} 字")
check("回归：app 容器里的正文还在", "被 app 容器包裹的正文" in app_text)

# ==================================================================
print("\n[5b] 链接密度惩罚 + 面包屑清洗 + 质量守卫")

# 侧栏链接密集，正文段落密集 —— 两者都有 <p>，不会被启发式删掉，
# 只能靠打分里的链接密度惩罚区分开。
DENSE_HTML = """
<html><head><title>侧栏测试</title></head>
<body>
<div class="wrap">
  <div class="sidebar">
    <p><a href="/1">新闻标题一</a></p>
    <p><a href="/2">新闻标题二</a></p>
    <p><a href="/3">新闻标题三</a></p>
    <p><a href="/4">新闻标题四</a></p>
    <p><a href="/5">新闻标题五</a></p>
  </div>
  <div class="main">
    <p>全国 &gt; 上海 &gt; 城区</p>
    <p>这是真正的正文段落第一段，内容足够长，需要写到超过两百字才会被计入段落权重，
所以这里再多写一些内容，确保它在与侧栏的竞争里能凭文字量胜出，同时链接占比也要足够低。</p>
    <p>这是真正的正文段落第二段，同样是为了把长度撑过阈值，验证新的打分逻辑
确实会选中正文这个容器，而不是链接密集的侧栏区块，这样测试才有意义。</p>
  </div>
</div>
</body></html>
"""
_, dense_text, dense_ratio = NS["_extract"](DENSE_HTML)
check("链接密度高时选中正文而非侧栏", "这是真正的正文段落第一段" in dense_text,
      f"got {dense_text[:80]!r}")
check("链接密集的侧栏被排除", "新闻标题一" not in dense_text)
check("面包屑被清洗", "全国 > 上海 > 城区" not in dense_text)
check("正文其余内容保留", "这是真正的正文段落第二段" in dense_text)
check("链接占比被算出来且不高", dense_ratio < 0.4, f"got {dense_ratio:.2%}")

# 段落全是链接、但每条链接文字很长（>500 字总量，绕过"相关阅读列表"启发式）
# → 这种页面才是质量守卫真正要拦的：看着像正文，其实全是链接
long_link = "这是一条很长的链接文字，用来把容器总字数撑过五百字，从而不会被启发式当成推荐列表删掉。" * 1
LINK_ONLY_HTML = f"""
<html><head><title>伪正文</title></head>
<body><div class="main">
  <p><a href="/1">{long_link}</a></p>
  <p><a href="/2">{long_link}</a></p>
  <p><a href="/3">{long_link}</a></p>
  <p><a href="/4">{long_link}</a></p>
</div></body></html>
"""
_, link_text, link_ratio = NS["_extract"](LINK_ONLY_HTML)
check("伪正文页的链接占比很高（触发质量守卫）", link_ratio > NS["MAX_LINK_RATIO"],
      f"got {link_ratio:.2%}")

# 面包屑正则本身
check("面包屑正则：全国 > 上海 > 城区",
      bool(NS["BREADCRUMB_PAT"].match("全国 > 上海 > 城区")))
check("面包屑正则：天气网 > 南京24小时天气",
      bool(NS["BREADCRUMB_PAT"].match("天气网 > 南京24小时天气")))
check("面包屑正则：普通正文不误判",
      not NS["BREADCRUMB_PAT"].match("这是正文里正常的一句话，不该被当面包屑删掉。"))

# ==================================================================
print("\n[6] 源码断言：死代码已清除、新逻辑已就位")
check("不再有 _parse_bing 定义", "_parse_bing" not in SRC)
check("不再请求 cn.bing.com", "cn.bing.com" not in SRC)
check("不再有 cookie 预热请求", 'session.get("https://cn.bing.com/")' not in SRC)
check("有词典页过滤调用", "_is_dict_page(" in SRC)
check("有条件重试", "MAX_RETRIES" in SRC)
check("不再有查询拆分调用", "_split_queries(" not in SRC)
check("有合并去重", "_clean_results(raw_all)" in SRC)
check("有相关度提前退出", "_relevant(" in SRC)
check("有行级噪音过滤", "NOISE_LINE_PAT.match" in SRC)
check("有链接密度惩罚", "_score_block" in SRC)
check("有质量守卫", "MAX_LINK_RATIO" in SRC and "link_ratio > MAX_LINK_RATIO" in SRC)
check("有面包屑清洗", "BREADCRUMB_PAT.match" in SRC)
check("有证书失败放宽重试", "ssl=False" in SRC and "_is_cert_error" in SRC)
check("提示词不再教模型对专名换短词",
      "换个更短的词重搜" not in SRC)

# ==================================================================
print("\n" + "=" * 62)
print(f"通过 {PASS} / {PASS + FAIL}")
if FAIL:
    print(f"失败 {FAIL} 项")
    sys.exit(1)
print("全部通过")
