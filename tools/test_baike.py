"""百科通道单元测试。

分两部分：
  [1] 纯函数（分词、清洗、选择）—— 离线，快
  [2] 真实网络调用 —— 验证 openapi / site: 搜索 / wapbaike 三条通路
"""

import ast
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

PLUGIN_DIR = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_fetch_url")
SRC = (PLUGIN_DIR / "baike.py").read_text(encoding="utf-8")
JUDGE_SRC = (PLUGIN_DIR / "baike_judge.py").read_text(encoding="utf-8")

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


# ---------------- 抠出纯函数 ----------------
def grab(fn: str, src: str = SRC) -> str:
    m = re.search(rf"^(async )?def {fn}\(.*?(?=^(async )?def |^class |\Z)", src, re.M | re.S)
    if not m:
        raise RuntimeError(f"抠不出 {fn}")
    return m.group(0)


def load_module(src: str, names: list[str], ns: dict) -> dict:
    """把一个源码文件里的常量 + 指定函数加载进命名空间。"""
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    try:
                        e = ast.Expression(n.value)
                        ast.fix_missing_locations(e)
                        ns[t.id] = eval(compile(e, "<c>", "eval"), {"re": re}, {})
                    except Exception:
                        pass
    for fn in names:
        exec(grab(fn, src), ns)
    return ns


TREE = ast.parse(SRC)
NS: dict = {"re": re, "json": json}
from urllib.parse import quote, quote_plus  # noqa: E402

NS["quote"] = quote
NS["quote_plus"] = quote_plus
NS["aiohttp"] = aiohttp
NS["asyncio"] = asyncio

# ---- 判断层（baike_judge）先加载：baike.py 的 _core_name / _is_dict_entry 依赖它 ----
NS.update(load_module(
    JUDGE_SRC,
    ["norm", "title_core", "is_dict_page", "has_meaning", "is_superset",
     "is_term_query", "classify", "judge_entries"],
    {"re": re},
))

for n in TREE.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                try:
                    e = ast.Expression(n.value)
                    ast.fix_missing_locations(e)
                    NS[t.id] = eval(compile(e, "<c>", "eval"), {"re": re}, {})
                except Exception:
                    pass
for fn in ("_looks_like_term", "_strip_edges", "_segment", "_parse_openapi",
           "_unescape",
           "_card_to_text", "_is_dict_entry", "_pick_best", "_longest_text_block",
           "_extract_page_text",
           "_is_numberish", "_lookup_openapi", "_search_baike_site", "_parse_rss",
           "_core_name",
           "_fetch_baike_page"):
    exec(grab(fn), NS)

import jieba

jieba.setLogLevel(60)
NS["jieba_mod"] = jieba

looks = NS["_looks_like_term"]
strip_e = NS["_strip_edges"]
segment = NS["_segment"]
parse_openapi = NS["_parse_openapi"]
card_to_text = NS["_card_to_text"]
is_dict_entry = NS["_is_dict_entry"]
pick_best = NS["_pick_best"]
longest_block = NS["_longest_text_block"]
is_numberish = NS["_is_numberish"]
core_name = NS["_core_name"]

print("=" * 76)
print("[1] 纯函数测试")
print("=" * 76)

# ---- _looks_like_term ----
check("正常专名通过", looks("珂莱塔") is True)
check("单字不通过", looks("的") is False)
check("纯数字不通过", looks("2.0") is False)
check("版本号不通过", looks("1.4") is False)
check("含标点不通过", looks("甘雨，好") is False)
check("空串不通过", looks("") is False)
check("超长不通过", looks("x" * 30) is False)
check("停用词不通过", looks("什么") is False)
check("英文通过", looks("Genshin") is True)

# ---- _is_numberish：版本号不能当专名 ----
check("纯版本号算数值", is_numberish("2.0") is True)
check("纯数字算数值", is_numberish("123") is True)
check("带 V 的版本号算数值", is_numberish("V2") is True)
check("正常专名不算数值", is_numberish("崩坏星穹铁道") is False)
check("崩坏3 不算数值", is_numberish("崩坏3") is False)

# ---- _core_name：剥掉限定语 ----
check("剥掉_百度百科后缀",
      core_name("珂莱塔·莫塔里_百度百科") == "珂莱塔·莫塔里")
check("剥掉完整括号限定语",
      core_name("甘雨（游戏《原神》及其衍生作品中的角色）_百度百科") == "甘雨")
check("剥掉被截断的括号限定语",
      core_name("崩坏：星穹铁道（2023年米哈游开发的银河冒险策略游戏 ..") == "崩坏：星穹铁道")
check("保留含《》的本体",
      core_name("崩坏：星穹铁道") == "崩坏：星穹铁道")
check("短后缀被剥掉", core_name("影神图·图鉴") == "影神图")

# ---- _strip_edges ----
check("剥掉尾部「里」", strip_e("原神里") == "原神")
check("剥掉首部「在」", strip_e("在鸣潮") == "鸣潮")
check("剥到底只剩2字就停", strip_e("鸣潮") == "鸣潮")
check("不误伤正常的词", strip_e("影神图") == "影神图")

# ---- _segment：核心是保住完整专名 ----
terms = segment("崩坏星穹铁道2.0版本新角色有哪些", jieba)
check("长句能提出专名", len(terms) > 0, f"got {terms}")
check("提出「崩坏星穹铁道」或「星穹铁道」",
      "崩坏星穹铁道" in terms or "星穹铁道" in terms, f"got {terms}")
check("不提出纯数字版本号", "2.0" not in terms, f"got {terms}")
check("不提出「角色」这类泛词", "角色" not in terms, f"got {terms}")

terms2 = segment("鸣潮珂莱塔这个角色怎么抽到需要什么材料", jieba)
check("生僻专名场景也有候选", len(terms2) > 0, f"got {terms2}")
check("不提出「角色」", "角色" not in terms2, f"got {terms2}")
check("不提出「材料」", "材料" not in terms2, f"got {terms2}")

# 切不出结果的场景（全是停用词）应该返回空，不硬拼
check("无实义词时返回空", segment("这个角色怎么样", jieba) is not None)

# ---- _parse_openapi ----
d = parse_openapi('{"id":1,"key":"x","desc":"游戏《鸣潮》及其衍生作品中的角色","title":"珂莱塔·莫塔里","card":[]}')
check("解析正常 JSON", d is not None and d["title"] == "珂莱塔·莫塔里")
check("拿到简介", d and d["desc"].startswith("游戏《鸣潮》"))
check("空 JSON 返回 None", parse_openapi("{}") is None)
check("空串返回 None", parse_openapi("") is None)
check("无 title 返回 None", parse_openapi('{"desc":"x"}') is None)
# 截断的 JSON 要能救
trunc = '{"id":1,"desc":"简介","title":"测试词条","card":[{"name":"属性","value":["甲","乙"]}]'
d2 = parse_openapi(trunc)
check("截断 JSON 也能救回 title", d2 is not None and d2["title"] == "测试词条", f"got {d2}")

# ---- _card_to_text ----
cards = card_to_text([
    {"name": "中文名", "value": ["珂莱塔·莫塔里"]},
    {"name": "别名", "value": "<a href='/x'>欧泊</a>"},
    {"name": "空字段", "value": []},
])
check("card 转文本", len(cards) == 2, f"got {cards}")
check("card 剥掉 HTML 标签", cards[1][1] == "欧泊", f"got {cards}")
check("card 跳过空字段", all(c[0] != "空字段" for c in cards))

# ---- _is_dict_entry ----
check("字典型百科条目被识别",
      is_dict_entry("鸣（汉语汉字）_百度百科", "https://baike.baidu.com/item/x") is True)
check("正常百科条目不被误伤",
      is_dict_entry("珂莱塔·莫塔里_百度百科", "https://baike.baidu.com/item/x") is False)
check("游戏角色百科条目不误伤",
      is_dict_entry("甘雨（游戏《原神》及其衍生作品中的角色）_百度百科", "https://baike.baidu.com/item/x") is False)
check("汉语字典站被识别",
      is_dict_entry("鸣的拼音_汉语字典", "https://www.zdic.net/hans/%E9%B8%A3") is True)
check("「XX（汉字）」单字词典页被识别",
      is_dict_entry("莱（汉字）_百度百科", "https://baike.baidu.com/item/x") is True)
check("漢典站点被识别",
      is_dict_entry("莱 lái - 漢典", "https://www.zdic.net/hans/%E8%8E%B1") is True)
check("正常词条不被站点规则误伤",
      is_dict_entry("珂莱塔·莫塔里_百度百科", "https://baike.baidu.com/item/x") is False)

# ---- _pick_best ----
entries = [
    {"title": "甘雨（天津人民艺术剧院演员）", "desc": "天津人民艺术剧院演员", "card": []},
    {"title": "甘雨（游戏《原神》及其衍生作品中的角色）", "desc": "游戏《原神》及其衍生作品中的角色", "card": []},
]
best = pick_best("原神里甘雨这个角色到底怎么培养", entries)
check("挑中游戏角色那条", best and "原神" in best["title"], f"got {best}")

entries2 = [
    {"title": "2.0", "desc": "98°组合发行的专辑", "card": []},
    {"title": "崩坏：星穹铁道", "desc": "2023年米哈游开发的银河冒险策略游戏", "card": []},
]
best2 = pick_best("崩坏星穹铁道2.0版本新角色有哪些", entries2)
check("挑中游戏那条而非版本号", best2 and "星穹铁道" in best2["title"], f"got {best2}")

check("空列表返回 None", pick_best("x", []) is None)

# ---- _longest_text_block ----
check("能取到长文本块", len(longest_block("<p>" + "内容" * 200 + "</p>")) > 100)
check("短文本不误取", longest_block("<p>短</p>") == "")
check("JSON 里的正文能取到",
      len(longest_block('{"desc":"' + "正文内容" * 100 + '"}')) > 100)

# ---- _extract_page_text：按 data-tag="paragraph" 抽正文 ----
extract_page = NS["_extract_page_text"]

# 真实结构的片段：段落 div 里嵌着参考文献角标 div
REAL_PARA_HTML = (
    '<html><head><meta name="description" content="珂莱塔是鸣潮角色，'
    '莫塔里家族二小姐。" /></head><body>'
    '<div class="index_pageContent">'
    '<div data-tag="paragraph" data-para-index="0" class="index_para__70wAX">'
    '珂莱塔·莫塔里，游戏《<span class="index_link__7u5s9">鸣潮</span>》中的角色'
    '<div class="index_refSup__l9CFR"><span>2</span>'
    '<i data-refid="uyqIOI9tYFbG"></i></div>。'
    '莫塔里家族的二小姐。'
    '<span class="index_discussionTag__o7Bvl" data-note-id="1298385">讨论</span>'
    '一颗宝石的绽放。'
    '</div>'
    '<div data-tag="paragraph" data-para-index="1" class="index_para__70wAX">'
    '第二段落：珂莱塔是黎那汐塔地区莫塔里家族没落旁支的后裔。'
    '</div>'
    '</div></body></html>'
)

body, meta = extract_page(REAL_PARA_HTML)
check("正文段落被提取", "莫塔里家族的二小姐" in body, f"got {body[:120]}")
check("第二段落也在", "黎那汐塔地区" in body, f"got {body[:120]}")
check("meta 简介被提取", "莫塔里家族二小姐" in meta, f"got {meta[:80]}")
check("参考文献角标被剔除", "<span>2</span>" not in body and "uyqIOI9tYFbG" not in body,
      f"got {body[:160]}")
check("讨论标签被剔除", "讨论" not in body, f"got {body[:160]}")
check("角标数字残留已清理", "角色。" in body, f"got {body[:160]}")
check("标点旁多余空格已清理", "《鸣潮》" in body, f"got {body[:160]}")
check("正文长度合理", 60 < len(body) < 400, f"got {len(body)}")

# 空壳页面：没有段落也没有 meta → 两个都空
b2, m2 = extract_page("<html><body><div>导航</div></body></html>")
check("空壳页面正文为空", b2 == "", f"got {b2[:80]}")
check("空壳页面 meta 为空", m2 == "", f"got {m2[:80]}")

# 有 meta 没段落的情况（半成品页）
b3, m3 = extract_page('<html><head><meta name="description" content="半成品的简介" />'
                      '</head><body></body></html>')
check("半成品页能拿到 meta", m3 == "半成品的简介", f"got {m3}")
check("半成品页正文为空", b3 == "")

# ---- script 里的 JS 不能被当成正文 ----
# 回归 2026-10-23 20:58：百科页里有一段设置根字号的 JS，
# 老逻辑用「>纯文本<」匹配，把它当成了"正文"喂给模型。
JS_HTML = (
    "<html><head><style>.a{color:red}</style></head><body>"
    "<script>(function() { const designWidth = 414; const baseFontSize = 100; "
    "function setRootFontSize() { const viewportWidth = window.innerWidth || "
    "document.documentElement.clientWidth; const rootFontSize = "
    "(Math.min(viewportWidth, 600) / designWidth) * baseFontSize; "
    "document.documentElement.style.fontSize = rootFontSize + 'px'; } "
    "setRootFontSize(); window.addEventListener('resize', setRootFontSize); })();"
    "</script></body></html>"
)
b4, m4 = extract_page(JS_HTML)
check("JS 代码不会被当成正文", "designWidth" not in b4 and "setRootFontSize" not in b4,
      f"got {b4[:120]}")
check("JS 页面正文基本为空", len(b4) < 60, f"got {len(b4)}")

print()
print("=" * 76)
print("[1b] 判断层（baike_judge）测试")
print("=" * 76)

norm = NS["norm"]
title_core_fn = NS["title_core"]
is_term_query = NS["is_term_query"]
classify = NS["classify"]
judge_entries = NS["judge_entries"]

# ---- norm：归一化去掉标点 ----
check("norm 去标点", norm("崩坏：星穹铁道") == "崩坏星穹铁道", f"got {norm('崩坏：星穹铁道')}")
check("norm 去空格", norm("珂莱塔 莫塔里") == "珂莱塔莫塔里")
check("norm 保留数字", norm("崩坏3") == "崩坏3")

# ---- title_core 与 baike._core_name 一致 ----
check("title_core 与 _core_name 一致",
      title_core_fn("崩坏：星穹铁道（2023年米哈游开发的银河冒险策略游戏 ..")
      == core_name("崩坏：星穹铁道（2023年米哈游开发的银河冒险策略游戏 .."))
check("title_core 剥 _百度百科", title_core_fn("珂莱塔·莫塔里_百度百科") == "珂莱塔·莫塔里")

# ---- is_term_query：查询本身是不是完整专名 ----
check("整词查询判为专名", is_term_query("依安嘉措") is True)
check("整词查询判为专名", is_term_query("崩坏星穹铁道") is True)
check("整词查询判为专名", is_term_query("珂莱塔") is True)
check("自然语言问题不算专名",
      is_term_query("鸣潮珂莱塔这个角色怎么抽到需要什么材料") is False)
check("自然语言问题不算专名", is_term_query("原神里甘雨这个角色到底怎么培养") is False)
check("含虚词不算专名", is_term_query("原神里的甘雨") is False)
check("带标点不算专名", is_term_query("崩坏：星穹铁道") is False)
check("太长不算专名", is_term_query("崩坏星穹铁道2.0版本新角色有哪些") is False)

# ---- classify：强信 / 弱信 / 拒 ----
lv, why = classify("珂莱塔", "珂莱塔·莫塔里_百度百科", "https://baike.baidu.com/item/x")
check("超集词条判为强信", lv == "strong", f"got {lv}/{why}")
lv, why = classify("珂莱塔", "莱塔_百度百科", "https://baike.baidu.com/item/x")
check("子串命中判为弱信", lv == "weak", f"got {lv}/{why}")
lv, why = classify("依安嘉措", "嘉措_百度百科", "https://baike.baidu.com/item/x")
check("碎片命中判为弱信", lv == "weak", f"got {lv}/{why}")
lv, why = classify("崩坏星穹铁道", "2.0_百度百科", "https://baike.baidu.com/item/x")
check("版本号被拒", lv == "reject", f"got {lv}/{why}")
lv, why = classify("鸣", "鸣（汉语汉字）_百度百科", "https://baike.baidu.com/item/x")
check("词典页被拒", lv == "reject", f"got {lv}/{why}")
lv, why = classify("崩坏星穹铁道", "穹（游戏《崩坏：星穹铁道》中的男主角）_百度百科",
                   "https://baike.baidu.com/item/x")
check("单字词条被拒", lv == "reject", f"got {lv}/{why}")

# ---- judge_entries：整词查询不采用碎片 ----
acc, rej = judge_entries("依安嘉措", [
    {"title": "嘉措_百度百科", "url": "https://baike.baidu.com/item/a", "desc": "西藏人名的后缀"},
    {"title": "依安县_百度百科", "url": "https://baike.baidu.com/item/b", "desc": "黑龙江省齐齐哈尔市辖县"},
])
check("整词查询剔掉碎片候选", len(acc) == 0, f"got {[e['title'] for e in acc]}")
check("整词查询剔掉碎片有原因", len(rej) == 2, f"got {rej}")

acc, rej = judge_entries("珂莱塔", [
    {"title": "莱塔_百度百科", "url": "https://baike.baidu.com/item/a", "desc": "游戏《无期迷途》的禁闭者"},
    {"title": "珂莱塔·莫塔里_百度百科", "url": "https://baike.baidu.com/item/b", "desc": "游戏《鸣潮》及其衍生作品中的角色"},
])
check("整词查询只留超集词条",
      len(acc) == 1 and "莫塔里" in acc[0]["title"], f"got {[e['title'] for e in acc]}")

acc, rej = judge_entries("崩坏星穹铁道", [
    {"title": "穹（游戏《崩坏：星穹铁道》中的男主角）_百度百科", "url": "https://baike.baidu.com/item/a", "desc": "游戏角色"},
    {"title": "崩坏：星穹铁道", "url": "https://baike.baidu.com/item/b", "desc": "2023年米哈游开发的银河冒险策略游戏"},
])
check("整词查询剔掉分词碎片",
      len(acc) == 1 and "星穹铁道" in acc[0]["title"], f"got {[e['title'] for e in acc]}")

# ---- judge_entries：自然语言问题保留多个实体 ----
acc, rej = judge_entries("鸣潮珂莱塔这个角色怎么抽到需要什么材料", [
    {"title": "鸣潮_百度百科", "url": "https://baike.baidu.com/item/a", "desc": "2024年广州库洛科技有限公司开发的开放世界动作类游戏"},
    {"title": "珂莱塔·莫塔里_百度百科", "url": "https://baike.baidu.com/item/b", "desc": "游戏《鸣潮》及其衍生作品中的角色"},
])
check("自然语言问题保留多个候选", len(acc) == 2, f"got {[e['title'] for e in acc]}")

# ---- judge_entries：有强信时弱信让位 ----
acc, rej = judge_entries("原神里甘雨这个角色到底怎么培养", [
    {"title": "原神_百度百科", "url": "https://baike.baidu.com/item/a", "desc": "2020年米哈游开发的开放世界冒险游戏"},
    {"title": "甘雨（游戏《原神》及其衍生作品中的角色）_百度百科", "url": "https://baike.baidu.com/item/b", "desc": "游戏《原神》及其衍生作品中的角色"},
])
check("自然语言问题不强信时保留弱信", len(acc) == 2, f"got {[e['title'] for e in acc]}")

acc, rej = judge_entries("影神图", [
    {"title": "影神图（游戏《黑神话：悟空》中的图鉴系统）_百度百科", "url": "https://baike.baidu.com/item/a", "desc": "游戏《黑神话：悟空》中的图鉴系统"},
    {"title": "影神图 - 黑神话：悟空Wiki - BWIKI", "url": "https://wiki.biligame.com/x", "desc": "影神图相当于图鉴"},
])
check("有强信时弱信让位", len(acc) == 1 and "百度百科" in acc[0]["title"],
      f"got {[e['title'] for e in acc]}")

# ---- judge_entries：空输入 ----
acc, rej = judge_entries("x", [])
check("空候选返回空", acc == [] and rej == [])

# ---- is_superset：超集必须是「名字延伸」，不是拼了站点名 ----
is_superset = NS["is_superset"]
check("名字延伸算超集", is_superset("珂莱塔", "珂莱塔莫塔里") is True)
check("完全相等算超集", is_superset("影神图", "影神图") is True)
check("站点名不算超集",
      is_superset("影神图", "影神图黑神话悟空WikiBWIKI") is False)
check("剩余太长不算超集",
      is_superset("崩坏", "崩坏星穹铁道官方网站") is False)
check("普通后缀算超集", is_superset("崩坏", "崩坏星穹铁道") is True)
check("无包含关系不算超集", is_superset("原神", "崩") is False)

print()
print("=" * 76)
print("[2] 真实网络测试")
print("=" * 76)

BAIKE = NS  # 复用命名空间
exec(grab("baike_search"), NS)
exec(grab("baike_lookup"), NS)
baike_search = NS["baike_search"]
baike_lookup = NS["baike_lookup"]


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        print("\n[2a] baike_search —— 百科通道搜索")
        for q in ["鸣潮珂莱塔这个角色怎么抽到需要什么材料",
                  "原神里甘雨这个角色到底怎么培养",
                  "崩坏星穹铁道2.0版本新角色有哪些"]:
            res = await baike_search(s, q, jieba_mod=jieba, count=10)
            d_cnt = sum(1 for t, u, _d, _a in res if is_dict_entry(t, u))
            print(f"  「{q}」→ {len(res)} 条（词典页 {d_cnt}）")
            for t, u, sn, fa in res[:4]:
                print(f"      {'×' if is_dict_entry(t,u) else '√'} {'[API]' if fa else '     '} {t[:52]}")
                print(f"         {sn[:78]}")
            check(f"「{q[:12]}」有结果", len(res) > 0)
            check(f"「{q[:12]}」词典页被过滤", d_cnt == 0, f"got {d_cnt}")
            await asyncio.sleep(1.0)

        print("\n[2b] baike_lookup —— 完整查询")
        for q in ["珂莱塔", "崩坏星穹铁道", "影神图"]:
            text, entries = await baike_lookup(q, jieba_mod=jieba, include_body=False)
            print(f"\n  ── 「{q}」")
            print("  " + text[:600].replace("\n", "\n  "))
            check(f"「{q}」拿到词条", len(entries) > 0)
            await asyncio.sleep(1.0)

        print("\n[2c] baike_lookup —— 不存在的词")
        text, entries = await baike_lookup("依安嘉措", jieba_mod=jieba, include_body=False)
        print(f"  {text[:200]}")
        check("不存在的词如实说没收录", "没有找到" in text or len(entries) == 0)

        print("\n[2d] baike_lookup —— 含正文")
        text, _ = await baike_lookup("崩坏星穹铁道", jieba_mod=jieba, include_body=True)
        print(f"  输出 {len(text)} 字，含'正文'段: {'正文' in text}")
        check("正文段存在", "正文" in text or "简介" in text)


asyncio.run(main())

print()
print("=" * 76)
print(f"结果: {PASS} 通过 / {FAIL} 失败")
print("=" * 76)
if FAIL:
    sys.exit(1)
