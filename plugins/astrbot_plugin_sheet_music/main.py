"""曲谱工具 —— 报个歌名，去歌谱简谱网找谱图发过来。

只在简谱网内部找，不依赖任何外部搜索：站内搜索按单字模糊召回、一页 100 条，
结果里直接带标题和歌手，先按歌名打分；点了歌手名的再按歌手搜一遍；还不够就
打开歌手的作品页（`/g/xx/name.htm`，一个人的谱子全列在一页里）硬找。

候选页统一打开详情页、拿 <h1> 和「艺术家/歌手」复核，只有对得上才发——
宁可说没找到，也不发一张别人的谱。同名的《夜曲》有一堆，标着肖邦的那张
不是「周杰伦的夜曲」。

行为约定和 voice_tool / singing 一致：只回状态码，发完即删。
"""

from __future__ import annotations

import io
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx
from PIL import Image as PILImage

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star
from astrbot.core.utils.media_utils import get_astrbot_temp_path

try:  # AstrBot 4.x 主流路径
    from astrbot.core.message.message_event_result import MessageChain
except Exception:  # pragma: no cover - 版本回退
    from astrbot.api.all import MessageChain

SITE = "http://www.jianpu.cn"
SEARCH = SITE + "/search?q={}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = httpx.Timeout(15.0, connect=8.0)

MAX_CANDIDATES = 6  # 最多翻几个详情页
MAX_IMAGES = 4  # 一首谱最多发几页
STOP_AT = 85  # 标题已经以歌名开头，不用再看后面的候选
MIN_WIDTH = 300  # 再窄的多半是缩略图、广告条
MAX_WIDTH = 1600  # 谱子扫得再清楚，缩到这个宽度也够看

TEMP_ROOT = Path(get_astrbot_temp_path()) / "sheet_music"

# 目录名就是我们自己发的 uuid 前缀，只认这个形状，别的文件一概不碰
_WORK_NAME = re.compile(r"^[0-9a-f]{12}$")

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_REL_LINK = re.compile(r"<a[^>]+href=['\"]([^'\"]+\.htm)['\"][^>]*>(.*?)</a>", re.S | re.I)
_PU_LINK = re.compile(
    r"<a[^>]+href=['\"](/pu/\d+/\d+\.htm)['\"][^>]*>(.*?)</a>", re.S | re.I
)
_DIV = re.compile(r"<div[^>]+id=['\"]jianpu['\"][^>]*>(.*?)</div>", re.S | re.I)
_IMG = re.compile(r"<img[^>]+src=['\"]([^'\"]+)['\"]", re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
# 详情页「艺术家/歌手/词曲: <a href='/g/xx/name.htm'>周杰伦</a>」那一段
_INFO = re.compile(
    r"<span[^>]+class=['\"]info['\"][^>]*>(.*?)<span[^>]+id=['\"]replycounttop",
    re.S | re.I,
)
_ARTIST = re.compile(
    r"<span[^>]+class=['\"]info['\"][^>]*>.*?<a[^>]+href=['\"](/g/[^'\"]+\.htm)['\"]",
    re.S | re.I,
)

# 标题里的噪声：站点后缀、括注、「简谱/五线谱」这类谱种词
_SITE_TAIL = re.compile(r"[_\-—|]?\s*(歌谱简谱网|简谱歌谱网|简谱网|曲谱网)\s*$")
_BRACKET = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")
_KIND = re.compile(r"(五线谱|钢琴谱|吉他谱|电子琴谱|二胡谱|古筝谱|合唱谱|总谱|简谱|歌谱|曲谱|谱)")
_BLANK = re.compile(r"\s+")


def _norm(text: str) -> str:
    t = _SITE_TAIL.sub("", (text or "").strip())
    t = _BRACKET.sub("", t)
    t = _KIND.sub("", t)
    t = _BLANK.sub("", t)
    return t.strip("-_—·、,，.。")


def _singer_match(singer: str, want: str) -> bool | None:
    """页面上标了歌手就判对错；没标返回 None——无从判断，放行。"""
    s, w = _norm(singer), _norm(want)
    if not s:
        return None
    if not w:
        return True
    return w in s or s in w


def _score(song: str, title: str, singer: str = "", want: str = "") -> int:
    """歌名和谱子标题的贴合度：100 全等 / 85 开头 / 60 包含 / 0 无关。

    指定了歌手时，标注对得上的加 15 分压过同名不同曲；标着别人名字的直接作废。
    """
    q, t = _norm(song), _norm(title)
    if not q or not t:
        return 0
    if t == q:
        base = 100
    elif t.startswith(q):
        base = STOP_AT
    elif q in t:
        base = 60
    else:
        return 0
    if not want:
        return base
    hit = _singer_match(singer, want)
    if hit is False:
        return 0
    return base + 15 if hit else base


def _singer_of(html: str) -> str:
    """详情页头部那段「艺术家/歌手/词曲: <名字>」。没写就空字符串。"""
    m = _INFO.search(html)
    if not m:
        return ""
    block = _TAG.sub(" ", m.group(1)).replace("&nbsp;", " ")
    block = re.sub(r"^.*?[:：]", "", block, count=1)
    parts = block.split()
    return parts[0] if parts else ""


def _sweep_temp() -> None:
    """清掉上次进程被硬杀时没来得及删的临时目录。"""
    if not TEMP_ROOT.exists():
        return
    swept = 0
    for entry in TEMP_ROOT.iterdir():
        if entry.is_dir() and _WORK_NAME.match(entry.name):
            shutil.rmtree(entry, ignore_errors=True)
            swept += 1
    if swept:
        logger.info(f"[sheet] 清掉 {swept} 个上次残留的临时目录")


def _absolute_img(src: str) -> str:
    """图床挂了的老谱子指向 61.164.110.157:800，换成主站同一路径还能取到。"""
    src = src.strip()
    if not src.lower().startswith(("http", "//")):
        return SITE + "/" + src.lstrip("/")
    if "jianpu.cn" not in src:
        src = SITE + re.sub(r"^https?:/+[^/]+", "", src)
    return src


async def _site_search(
    client: httpx.AsyncClient, song: str
) -> list[tuple[str, str, str]]:
    """站内搜索：q 必须用 GB18030 编码，UTF-8 会一条都搜不出来。

    结果行是「序号 / 类别 / 标题 / 歌手」，歌手列能提前筛掉同名不同曲，
    省掉一次详情页请求。
    """
    try:
        r = await client.get(
            SEARCH.format(quote(song, encoding="gb18030")),
            headers={"Referer": SITE + "/"},
        )
        r.encoding = "gb18030"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[sheet] 站内搜索失败: {exc}")
        return []

    found = []
    for row in _ROW.findall(r.text):
        m = _REL_LINK.search(row)
        if not m:
            continue
        title = _TAG.sub("", m.group(2)).strip()
        if not title:
            continue
        cells = [_TAG.sub("", c).strip() for c in _CELL.findall(row)]
        singer = cells[3] if len(cells) > 3 else ""
        found.append((urljoin(SITE + "/search", m.group(1)), title, singer))
    return found


async def _artist_hits(
    client: httpx.AsyncClient,
    hints: list[tuple[str, str, str]],
    song: str,
    want: str,
) -> list[tuple[int, str, str]]:
    """从该歌手的任意一张谱子摸到作品页 `/g/xx/name.htm`，整页按歌名筛。

    作品页把他的谱子全列在一页里（周杰伦上千条），比站内那 100 条模糊结果靠谱得多。
    """
    artist = ""
    for url, _, _ in hints[:3]:
        try:
            r = await client.get(url)
            r.encoding = "gb18030"
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[sheet] 找歌手页失败 {url}: {exc}")
            continue
        m = _ARTIST.search(r.text)
        if m:
            artist = urljoin(SITE, m.group(1))
            break
    if not artist:
        return []

    try:
        r = await client.get(artist)
        r.encoding = "gb18030"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[sheet] 作品页打不开 {artist}: {exc}")
        return []

    scored = []
    for href, raw in _PU_LINK.findall(r.text):
        title = _TAG.sub("", raw).replace("&nbsp;", " ").strip()
        # 作品页上的谱子就是这位歌手的，歌手一栏按 want 计
        score = _score(song, title, want, want)
        if score:
            scored.append((score, urljoin(SITE, href), title))
    scored.sort(key=lambda c: -c[0])
    return scored[:3]


async def _open_detail(
    client: httpx.AsyncClient, url: str
) -> tuple[str, str, list[str]]:
    r = await client.get(url)
    r.encoding = "gb18030"
    text = r.text

    head = _H1.search(text)
    title = _TAG.sub("", head.group(1)).strip() if head else ""

    imgs: list[str] = []
    div = _DIV.search(text)
    if div:
        for src in _IMG.findall(div.group(1)):
            full = _absolute_img(src)
            if "/img/" in full and "dangdang" not in full and full not in imgs:
                imgs.append(full)
    return title, _singer_of(text), imgs


async def _pick(
    client: httpx.AsyncClient, song: str, want: str = ""
) -> tuple[str, str, str, list[str]] | None:
    """挑一首最贴合的谱子，返回 (标题, 歌手, 详情页, 图片地址)。"""
    rows = await _site_search(client, song)
    by_singer: list[tuple[str, str, str]] = []
    if want:
        by_singer = [
            row for row in await _site_search(client, want) if _singer_match(row[2], want)
        ]

    confirmed: list[tuple[int, str, str]] = []
    vague: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for url, title, singer in rows + by_singer:
        if url in seen:
            continue
        seen.add(url)
        score = _score(song, title, singer, want)
        if not score:
            continue
        bucket = confirmed if want and _singer_match(singer, want) else vague
        bucket.append((score, url, title))
    confirmed.sort(key=lambda c: -c[0])
    vague.sort(key=lambda c: -c[0])

    # 歌手对得上的先看；歌手栏空着的留到最后——同名的谱子一抓一大把，
    # 别让它们占满翻页名额
    candidates = [(url, title) for _, url, title in confirmed]
    if want and (not confirmed or confirmed[0][0] < STOP_AT):
        hits = await _artist_hits(client, by_singer, song, want)
        have = {url for _, url, _ in confirmed}
        candidates += [(url, title) for _, url, title in hits if url not in have]
    candidates += [(url, title) for _, url, title in vague]

    best: tuple[int, str, str, str, list[str]] | None = None
    tried: set[str] = set()
    for url, hint in candidates:
        if len(tried) >= MAX_CANDIDATES:
            break
        if url in tried:
            continue
        tried.add(url)
        try:
            title, singer, imgs = await _open_detail(client, url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[sheet] 详情页打不开 {url}: {exc}")
            continue
        if not imgs:
            continue
        score = _score(song, title or hint, singer, want)
        if best is None or score > best[0]:
            best = (score, title or hint, singer, url, imgs)
        # 歌名对上了、歌手也确认无误才算定案；歌手栏空着的还得往后看
        if score >= STOP_AT and (not want or _singer_match(singer, want)):
            break

    if not best or best[0] <= 0:
        return None
    # 点了名就只认对得上的：歌手栏空着的页面宁可当没找到，也不拿别人的谱冒充
    if want and _singer_match(best[2], want) is not True:
        logger.info(f"[sheet] 《{song}》只找到歌手不符或未标的谱子，放弃")
        return None
    return best[1], best[2], best[3], best[4]


async def _download(
    client: httpx.AsyncClient, imgs: list[str], page_url: str, work: Path
) -> list[Path]:
    saved: list[Path] = []
    for url in imgs:
        if len(saved) >= MAX_IMAGES:
            break
        try:
            r = await client.get(url, headers={"Referer": page_url})
            r.raise_for_status()
            im = PILImage.open(io.BytesIO(r.content))
            im.load()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[sheet] 取图失败 {url}: {exc}")
            continue
        if im.width < MIN_WIDTH:
            continue
        if im.width > MAX_WIDTH:
            dest = work / f"{len(saved)}.jpg"
            height = round(im.height * MAX_WIDTH / im.width)
            im.convert("RGB").resize((MAX_WIDTH, height), PILImage.LANCZOS).save(
                dest, "JPEG", quality=90
            )
        else:
            # 不动尺寸就别重编码：原图多是 100~200KB 的 JPEG，转成 PNG 反而涨三倍
            suffix = (im.format or "jpg").lower().replace("jpeg", "jpg")
            dest = work / f"{len(saved)}.{suffix}"
            dest.write_bytes(r.content)
        saved.append(dest)
    return saved


class SheetMusicPlugin(Star):
    """对方想看谱子的时候用，平时别主动发。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        try:
            _sweep_temp()
        except Exception as exc:  # noqa: BLE001 - 清理失败不能拖累插件加载
            logger.warning(f"[sheet] 清理残留失败: {exc}")

    @filter.llm_tool(name="find_sheet_music")
    async def find_sheet_music(
        self, event: AstrMessageEvent, song: str, singer: str = ""
    ) -> str:
        """去网上找一首歌的曲谱图片发出来。对方明确要看简谱、曲谱、谱子时用。

        返回 `已发送=1` 就是已经发出去了，不要再确认、不要再查、也别重发；
        返回 `未发送=0（原因）` 说明没找到，直接用文字说明。

        Args:
            song(string): 歌名，只写歌名本身，别带"简谱""曲谱"这类词
            singer(string): 歌手名，可留空。对方点名了谁唱的（比如"周杰伦的夜曲"）就填上，能筛掉同名不同曲的谱子
        """
        song = (song or "").strip()
        singer = (singer or "").strip()
        if not song:
            return "未发送=0（没有歌名）"

        work = TEMP_ROOT / uuid.uuid4().hex[:12]
        try:
            async with httpx.AsyncClient(
                headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
            ) as client:
                picked = await _pick(client, song, singer)
                if not picked:
                    who = f"（{singer}）" if singer else ""
                    return f"未发送=0（没找到《{song}》{who}的曲谱），改用文字说明。"
                title, found_singer, page_url, imgs = picked

                work.mkdir(parents=True, exist_ok=True)
                pages = await _download(client, imgs, page_url, work)

            if not pages:
                return f"未发送=0（《{title}》的谱图下载失败），改用文字。"

            await event.send(
                MessageChain([Image.fromFileSystem(str(p)) for p in pages])
            )
            logger.info(
                f"[sheet] 已发送《{title}》{found_singer or '未标歌手'} "
                f"{len(pages)} 张 {page_url}"
            )
            who = f"（{found_singer}）" if found_singer else ""
            return f"已发送=1（《{title}》{who}，{len(pages)} 张）"

        except Exception as exc:  # noqa: BLE001
            logger.error(f"[sheet] 找谱失败: {exc}")
            return f"未发送=0（{exc}），改用文字。"
        finally:
            shutil.rmtree(work, ignore_errors=True)
