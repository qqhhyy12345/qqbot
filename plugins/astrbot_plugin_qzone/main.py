import asyncio
import base64
import hashlib
import io
import random
import time
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.agent.message import TextPart
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.provider.entities import ProviderRequest

from .core.autonomy import PublishInvite
from .core.campus_wall import CampusWall
from .core.config import PluginConfig
from .core.db import PostDB
from .core.llm_action import LLMAction
from .core.model import (
    Comment,
    Post,
    extract_and_replace_nickname,
    remove_em_tags,
)
from .core.qzone import QzoneAPI, QzoneParser, QzoneSession
from .core.scheduler import AutoComment, AutoPublish
from .core.sender import Sender
from .core.vision_server import VisionServer
from .core.service import PostService
from .core.utils import get_ats, get_image_urls, parse_range

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

try:
    from quart import jsonify as _quart_jsonify
    from quart import request as _quart_request
except Exception:
    _quart_jsonify = None
    _quart_request = None


class QzonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        # 配置
        self.cfg = PluginConfig(config, context)
        # 会话
        self.session = QzoneSession(self.cfg)
        # QQ空间
        self.qzone = QzoneAPI(self.session, self.cfg)
        # 数据库
        self.db = PostDB(self.cfg)
        # LLM模块（把本地图片转述喂进去，评说说也不用把图发给云端）
        self.llm = LLMAction(self.cfg, describe_images=self._describe_post_images)
        # 消息发送器
        self.sender = Sender(self.cfg)
        # 操作服务
        self.service = PostService(self.qzone, self.session, self.db, self.llm)
        # 表白墙
        self.campus_wall = CampusWall(self.cfg, self.service, self.db, self.sender)
        # 自动评论模块
        self.auto_comment: AutoComment | None = None
        # 自动发说说模块
        self.auto_publish: AutoPublish | None = None
        self._page_post_refs: dict[str, Post] = {}
        # 本地视觉：图片描述缓存 + 失败冷却截止时间
        self._vision_cache: dict[str, str] = {}
        self._vision_fail_until: float = 0.0
        # 本地视觉服务按需拉起，闲置 20 秒自动卸掉（不常驻显存）
        self._vision = VisionServer(
            exe=self.VISION_SERVER_EXE,
            model=self.VISION_MODEL_PATH,
            mmproj=self.VISION_MMPROJ_PATH,
            port=self.VISION_PORT,
            device=self.VISION_DEVICE,
            idle_timeout=self.VISION_IDLE_TIMEOUT,
            log_path=self.cfg.temp_dir / "llama-server.log",
        )
        # 聊天触发式自主发说说：不额外调模型，只在请求尾部偶尔提一句
        self._publish_invite = PublishInvite(
            self.cfg.data_dir / "autonomy_state.json",
            timezone=getattr(self.cfg, "timezone", None),
        )
        self._register_page_web_apis()

    async def initialize(self):
        """插件加载时触发"""
        await self.db.initialize()

        if not self.auto_comment and self.cfg.trigger.comment_cron:
            self.auto_comment = AutoComment(self.cfg, self.service, self.sender)

        if not self.auto_publish and self.cfg.trigger.publish_cron:
            self.auto_publish = AutoPublish(self.cfg, self.service, self.sender)

    def _register_page_web_apis(self) -> None:
        routes = (
            ("page/status", self.page_status, ["GET"], "Qzone dashboard status"),
            ("page/feed", self.page_feed, ["GET"], "Qzone dashboard feed"),
            ("page/detail", self.page_detail, ["GET"], "Qzone dashboard detail"),
            ("page/publish", self.page_publish, ["POST"], "Qzone dashboard publish"),
            ("page/like", self.page_like, ["POST"], "Qzone dashboard like"),
            ("page/comment", self.page_comment, ["POST"], "Qzone dashboard comment"),
            ("page/reply", self.page_reply, ["POST"], "Qzone dashboard reply"),
            ("page/delete", self.page_delete, ["POST"], "Qzone dashboard delete"),
            (
                "page/upload-media",
                self.page_upload_media,
                ["POST"],
                "Qzone dashboard upload media",
            ),
        )
        for endpoint, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/astrbot_plugin_qzone/{endpoint}",
                handler,
                methods,
                desc,
            )

    async def _page_response(self, payload: dict, status: int = 200):
        if _quart_jsonify is None:
            return payload
        response = _quart_jsonify(payload)
        response.status_code = status
        return response

    async def _page_json(self, callback):
        try:
            payload = await callback()
            status = 200
        except Exception as exc:
            logger.exception("qzone page api failed: %s", exc)
            payload = {
                "ok": False,
                "error": {
                    "message": str(exc) or "请求失败",
                },
            }
            status = 400
        return await self._page_response(payload, status)

    async def _page_query_params(self) -> dict:
        if _quart_request is None:
            return {}
        args = getattr(_quart_request, "args", {}) or {}
        try:
            return {str(key): value for key, value in args.items()}
        except Exception:
            return dict(args)

    async def _page_json_body(self) -> dict:
        if _quart_request is None:
            return {}
        try:
            data = await _quart_request.get_json(silent=True)
        except TypeError:
            data = await _quart_request.get_json()
        return data if isinstance(data, dict) else {}

    async def _build_page_status(self) -> dict:
        self._capture_page_client()
        try:
            uin = await self.session.get_uin()
            nickname = await self.session.get_nickname()
            bound = True
        except Exception:
            uin = 0
            nickname = ""
            bound = False
        return {
            "ok": True,
            "data": {
                "daemon": {"state": "ready" if bound else "offline"},
                "login": {
                    "bound": bound,
                    "uin": uin,
                    "nickname": nickname,
                    "avatar": "",
                },
                "limits": {
                    "feed": 10,
                    "images": 9,
                },
            },
        }

    def _remember_page_post(self, post: Post) -> str:
        post_id = f"{post.uin}:{post.tid}"
        self._page_post_refs[post_id] = post
        return post_id

    def _capture_page_client(self):
        if self.cfg.client is not None:
            return self.cfg.client
        try:
            platform = self.context.get_platform("aiocqhttp")
        except Exception:
            platform = None
        if platform is None:
            try:
                for candidate in self.context.platform_manager.platform_insts:
                    meta = candidate.meta()
                    if getattr(meta, "name", "") == "aiocqhttp":
                        platform = candidate
                        break
            except Exception:
                platform = None
        bot = getattr(platform, "bot", None) if platform is not None else None
        if bot is not None:
            self.cfg.client = bot
        return self.cfg.client

    def _page_post_payload(
        self,
        post: Post,
        *,
        include_comments: bool = False,
        self_uin: int = 0,
    ) -> dict:
        images = list(dict.fromkeys([*(post.images or []), *(post.videos or [])]))
        payload = {
            "id": self._remember_page_post(post),
            "author": {
                "uin": post.uin,
                "nickname": post.name or str(post.uin),
                "avatar": post.avatar_url or "",
            },
            "content": post.text or post.rt_con or "",
            "created_at": int(post.create_time or 0),
            "stats": {
                "likes": 0,
                "comments": len(post.comments or []),
            },
            "liked": False,
            "images": images,
            "can_delete": bool(self_uin and int(post.uin) == int(self_uin)),
        }
        if include_comments:
            payload["comments"] = [
                {
                    "id": str(comment.tid or index),
                    "author": {
                        "uin": comment.uin,
                        "nickname": comment.nickname or str(comment.uin),
                    },
                    "content": comment.content,
                    "created_at": int(comment.create_time or 0),
                    "can_reply": bool(comment.tid and comment.uin),
                }
                for index, comment in enumerate(post.comments or [], start=1)
            ]
        return payload

    def _require_page_post(self, post_id: str) -> Post:
        post = self._page_post_refs.get(str(post_id or "").strip())
        if not post:
            raise RuntimeError("说说引用已失效，请刷新页面后重试")
        return post

    async def page_status(self):
        return await self._page_json(self._build_page_status)

    async def page_feed(self):
        async def handler():
            self._capture_page_client()
            params = await self._page_query_params()
            scope = str(params.get("scope") or "friends")
            self_uin = await self.session.get_uin()
            target_id = None
            no_self = False
            if scope == "self":
                target_id = str(self_uin)
            elif scope == "profile":
                target_id = str(params.get("hostuin") or "").strip() or None
            else:
                no_self = False
            posts = await self.service.query_feeds(
                target_id=target_id,
                pos=0,
                num=min(max(int(params.get("limit") or 10), 1), 10),
                with_detail=False,
                no_self=no_self,
            )
            return {
                "ok": True,
                "data": {
                    "items": [
                        self._page_post_payload(post, self_uin=self_uin)
                        for post in posts
                    ],
                    "cursor": "",
                    "has_more": False,
                },
            }

        return await self._page_json(handler)

    async def page_detail(self):
        async def handler():
            self._capture_page_client()
            params = await self._page_query_params()
            post = self._require_page_post(params.get("id", ""))
            resp = await self.qzone.get_detail(post)
            if not resp.ok or not resp.data:
                raise RuntimeError(resp.message or "获取详情失败")
            parsed = QzoneParser.parse_feeds([resp.data])
            matched = parsed[0] if parsed else post
            self._remember_page_post(matched)
            return {
                "ok": True,
                "data": {
                    "post": self._page_post_payload(
                        matched,
                        include_comments=True,
                        self_uin=await self.session.get_uin(),
                    )
                },
            }

        return await self._page_json(handler)

    async def page_publish(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            media = body.get("media") or []
            images = []
            for item in media:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source") or "")
                if source.startswith("base64://"):
                    images.append(base64.b64decode(source.removeprefix("base64://")))
            post = await self.service.publish_post(
                text=str(body.get("content") or ""),
                images=images,
            )
            return {
                "ok": True,
                "data": {"post": self._page_post_payload(post)},
                "message": "说说已发布。",
            }

        return await self._page_json(handler)

    async def page_like(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            post = self._require_page_post(body.get("id", ""))
            resp = await self.qzone.like(post)
            if not resp.ok:
                raise RuntimeError(resp.message or "点赞失败")
            return {"ok": True, "data": {"liked": True, "verified": True}}

        return await self._page_json(handler)

    async def page_comment(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            post = self._require_page_post(body.get("id", ""))
            content = str(body.get("content") or "").strip()
            if not content:
                raise RuntimeError("评论内容不能为空")
            resp = await self.qzone.comment(post, content)
            if not resp.ok:
                raise RuntimeError(resp.message or "评论失败")
            comment = {
                "id": f"local-{len(post.comments) + 1}",
                "content": content,
                "author": {
                    "uin": await self.session.get_uin(),
                    "nickname": await self.session.get_nickname(),
                    "avatar": "",
                },
            }
            post.comments.append(
                Comment(
                    uin=comment["author"]["uin"],
                    nickname=comment["author"]["nickname"],
                    content=content,
                    create_time=0,
                    tid=0,
                    parent_tid=None,
                )
            )
            return {"ok": True, "data": {"comment": comment}, "message": "评论已发送。"}

        return await self._page_json(handler)

    async def page_reply(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            post = self._require_page_post(body.get("id", ""))
            content = str(body.get("content") or "").strip()
            comment_id = str(body.get("commentid") or "")
            if not content:
                raise RuntimeError("回复内容不能为空")
            target = next(
                (item for item in post.comments if str(item.tid) == comment_id), None
            )
            if target is None:
                raise RuntimeError("未找到要回复的评论")
            resp = await self.qzone.reply(post, target, content)
            if not resp.ok:
                raise RuntimeError(resp.message or "回复失败")
            reply = {
                "id": str(resp.data.get("tid") or ""),
                "content": content,
                "author": {
                    "uin": await self.session.get_uin(),
                    "nickname": await self.session.get_nickname(),
                    "avatar": "",
                },
            }
            post.comments.append(
                Comment(
                    uin=reply["author"]["uin"],
                    nickname=reply["author"]["nickname"],
                    content=content,
                    create_time=0,
                    tid=int(reply["id"]) if reply["id"].isdigit() else 0,
                    parent_tid=target.tid,
                )
            )
            return {"ok": True, "data": {"reply": reply}, "message": "回复已发送。"}

        return await self._page_json(handler)

    async def page_delete(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            post = self._require_page_post(body.get("id", ""))
            self_uin = await self.session.get_uin()
            if int(post.uin) != int(self_uin):
                raise RuntimeError("只能删除自己发布的说说")
            resp = await self.qzone.delete(str(post.tid or ""))
            if not resp.ok:
                raise RuntimeError(resp.message or "删除失败")
            return {"ok": True, "data": {}, "message": "说说已删除。"}

        return await self._page_json(handler)

    async def page_upload_media(self):
        async def handler():
            if _quart_request is None:
                raise RuntimeError("当前环境不支持上传")
            files = await _quart_request.files
            upload = files.get("file") or files.get("image") or files.get("media")
            if upload is None:
                raise RuntimeError("没有收到图片文件")
            data = await upload.read()
            return {
                "ok": True,
                "data": {
                    "media": {
                        "kind": "image",
                        "name": upload.filename or "image.jpg",
                        "source": f"base64://{base64.b64encode(data).decode('ascii')}",
                        "size": len(data),
                        "mime_type": getattr(upload, "content_type", "")
                        or "image/jpeg",
                    }
                },
            }

        return await self._page_json(handler)

    async def terminate(self):
        """插件卸载时"""
        if self.qzone:
            await self.qzone.close()
        if self.auto_comment:
            await self.auto_comment.terminate()
        if self.auto_publish:
            await self.auto_publish.terminate()
        # 别把 llama-server 留成孤儿进程占显存
        try:
            await self._vision.aclose()
        except Exception as exc:
            logger.debug(f"关闭本地视觉服务失败: {exc}")

    @filter.on_llm_request()
    async def invite_publish(self, event: AiocqhttpMessageEvent, req: ProviderRequest):
        """偶尔在聊天里提一句「想发说说随时发」。

        只追加到消息尾部，不动 system_prompt —— 后者是缓存前缀，
        改一次会让整段历史缓存失效（实测 96K token 全量重算）。
        没命中就什么都不做，零开销。
        """
        await self._publish_invite.maybe_invite(event, req)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def prob_read_feed(self, event: AiocqhttpMessageEvent):
        """监听消息"""
        if not self.cfg.client:
            self.cfg.client = event.bot
            logger.debug("QQ空间所需的 CQHttp 客户端已初始化")

        # 按概率触发点赞+评论
        sender_id = event.get_sender_id()
        if (
            not self.cfg.source.is_ignore_user(sender_id)
            and random.random() < self.cfg.trigger.read_prob
        ):
            target_id = event.get_sender_id()
            posts = await self.service.query_feeds(
                target_id=target_id, pos=0, num=1, no_self=True, no_commented=True
            )
            for post in posts:
                try:
                    await self.service.comment_posts(post, event=event)
                    if self.cfg.trigger.like_when_comment:
                        await self.service.like_posts(post)
                    await self.sender.send_post(
                        event,
                        post,
                        message="触发读说说",
                        send_admin=self.cfg.trigger.send_admin,
                    )
                except Exception as e:
                    logger.error(e)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看访客")
    async def view_visitor(self, event: AiocqhttpMessageEvent):
        """查看访客"""
        try:
            msg = await self.service.view_visitor()
            await self.sender.send_msg(event, msg)
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)

    async def _get_posts(
        self,
        event: AiocqhttpMessageEvent,
        *,
        target_id: str | None = None,
        with_detail: bool = False,
        no_commented=False,
        no_self=False,
    ) -> list[Post]:
        pos, num = parse_range(event)
        at_ids = get_ats(event)
        if not target_id:
            target_id = at_ids[0] if at_ids else None

        if target_id:
            self.cfg.remove_ignore_users(target_id)
        try:
            logger.debug(
                f"正在查询说说： {target_id, pos, num, with_detail, no_commented, no_self}"
            )
            posts = await self.service.query_feeds(
                target_id=target_id,
                pos=pos,
                num=num,
                with_detail=with_detail,
                no_commented=no_commented,
                no_self=no_self,
            )
            if not posts:
                await event.send(event.plain_result("查询结果为空"))
                event.stop_event()
            return posts
        except Exception as e:
            await event.send(event.plain_result(str(e)))
            logger.error(e)
            event.stop_event()
            return []

    @filter.command("看说说", alias={"查看说说"})
    async def view_feed(self, event: AiocqhttpMessageEvent):
        """
        看说说 <@群友> <序号>
        """
        posts = await self._get_posts(event, with_detail=True)
        for post in posts:
            await self.sender.send_post(event, post)

    @filter.command("评说说", alias={"评论说说", "读说说"})
    async def comment_feed(self, event: AiocqhttpMessageEvent):
        """评说说 <序号/范围>"""
        posts = await self._get_posts(event, no_commented=True, no_self=True)
        for post in posts:
            try:
                await self.service.comment_posts(post, event=event)
                msg = "已评论"
                if self.cfg.trigger.like_when_comment:
                    await self.service.like_posts(post)
                    msg += "并点赞"
                await self.sender.send_post(event, post, message=msg)
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)

    @filter.command("赞说说")
    async def like_feed(self, event: AiocqhttpMessageEvent):
        """赞说说 <序号/范围>"""
        posts = await self._get_posts(event)
        for post in posts:
            try:
                await self.service.like_posts(post)
                await self.sender.send_post(event, post, message="已点赞")
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_feed(self, event: AiocqhttpMessageEvent):
        """发说说 <内容> <图片>, 由用户指定内容"""
        text = event.message_str.partition(" ")[2]
        images = await get_image_urls(event)
        try:
            post = await self.service.publish_post(text=text, images=images)
            await self.sender.send_post(event, post, message="已发布")
            event.stop_event()
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("写说说", alias={"写稿"})
    async def write_feed(self, event: AiocqhttpMessageEvent):
        """写说说 <主题> <图片>, 由AI写完后管理员用‘通过稿件 ID’命令发布"""
        group_id = event.get_group_id()
        topic = event.message_str.partition(" ")[2]
        try:
            text = await self.llm.generate_post(
                group_id=group_id, topic=topic, event=event
            )
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)
            return
        images = await get_image_urls(event)
        if not text and not images:
            yield event.plain_result("说说生成失败")
            return
        self_id = event.get_self_id()
        post = Post(
            uin=int(self_id),
            text=text or "",
            images=images,
            status="pending",
        )
        await self.db.save(post)
        await self.sender.send_post(event, post)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删说说")
    async def delete_feed(self, event: AiocqhttpMessageEvent):
        """删说说 <序号>，不写序号则删最新一条"""
        posts = await self._get_posts(event, target_id=event.get_self_id())
        if not posts:
            return
        # parse_range 已在 _get_posts 里按「删说说 3」的末位数字收窄范围，
        # 这里只取第一条 —— 绝不批量删。
        # （旧实现是 for post in posts 全删，一条命令清空全部说说。）
        post = posts[0]
        if not post.tid:
            await event.send(event.plain_result("这条说说没有 tid，删不了"))
            return
        try:
            # 先把要删的内容发出来，让人知道删的是哪条，再删
            await self.sender.send_post(event, post, message="已删除这条说说")
            await self.service.delete_post(post)
        except Exception as e:
            await event.send(event.plain_result(f"删除失败：{e}"))
            logger.error(e)

    @filter.command("回评", alias={"回复评论"})
    async def reply_comment(
        self, event: AiocqhttpMessageEvent, post_id: int = -1, comment_index: int = -1
    ):
        """回评 <稿件ID> <评论序号>, 默认回复最后一条非己评论"""
        post = await self.db.get(post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        try:
            await self.service.reply_comment(post, index=comment_index, event=event)
            await self.sender.send_post(event, post, message="已回复评论")
        except Exception as e:
            await event.send(event.plain_result(str(e)))
            logger.error(e)

    @filter.command("投稿")
    async def contribute_post(self, event: AiocqhttpMessageEvent):
        """投稿 <内容> <图片>"""
        await self.campus_wall.contribute(event)

    @filter.command("匿名投稿")
    async def anon_contribute_post(self, event: AiocqhttpMessageEvent):
        """匿名投稿 <内容> <图片>"""
        await self.campus_wall.contribute(event, anon=True)

    @filter.command("撤稿")
    async def recall_post(self, event: AiocqhttpMessageEvent):
        """删除稿件 <稿件ID>"""
        async for msg in self.campus_wall.delete(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("看稿", alias={"查看稿件"})
    async def view_post(self, event: AiocqhttpMessageEvent):
        "查看稿件 <稿件ID>, 默认最新稿件"
        async for msg in self.campus_wall.view(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("过稿", alias={"通过稿件", "通过投稿"})
    async def approve_post(self, event: AiocqhttpMessageEvent):
        """通过稿件 <稿件ID>"""
        async for msg in self.campus_wall.approve(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("拒稿", alias={"拒绝稿件", "拒绝投稿"})
    async def reject_post(self, event: AiocqhttpMessageEvent):
        """拒绝稿件 <稿件ID> <原因>"""
        async for msg in self.campus_wall.reject(event):
            yield msg

    # ---- 本地视觉：按需拉起 llama-server 跑 Qwen2-VL-2B 转述图片 ----------
    # 原版只把图片 URL 拼进文本，模型只能看到一串链接。
    # 现在改成：本地模型看图 → 输出一句中文描述 → 只把描述发给云端。
    # 图片不进云端上下文，省 token；本地算力不花钱。
    # 服务只在看图那几秒存在，闲置 20 秒自动退出，不常驻显存（玩游戏不打架）。
    # 这条链路任何一步失败都退回纯文字，绝不影响回复、绝不阻塞。
    VISION_PORT = 1234
    VISION_DEVICE = "Vulkan0"  # 9060 XT；Vulkan1 是 9700X 核显，不用
    VISION_IDLE_TIMEOUT = 20.0
    VISION_MODEL = "qwen2-vl-2b-instruct"
    VISION_MAX_IMAGES = 2
    VISION_TIMEOUT = 30.0  # 冷启动由 ensure() 的 boot_timeout 兜，这里只管单次推理
    VISION_COOLDOWN = 120.0
    VISION_MAX_EDGE = 960
    VISION_JPEG_QUALITY = 80
    # llama-server 二进制与权重：都在 llama.cpp 的 Vulkan 后端目录 / 本地 models 下
    VISION_SERVER_EXE = Path(
        r"C:\Users\qhy\.lmstudio\extensions\backends"
        r"\llama.cpp-win-x86_64-vulkan-avx2-2.51.0\llama-server.exe"
    )
    VISION_MODEL_PATH = Path(
        r"D:\qqbot\models\qwen2vl-2b\Qwen2-VL-2B-Instruct-Q4_K_M.gguf"
    )
    VISION_MMPROJ_PATH = Path(
        r"D:\qqbot\models\qwen2vl-2b\mmproj-Qwen2-VL-2B-Instruct-f16.gguf"
    )
    VISION_PROMPT = (
        "用一句中文描述这张图片的内容。"
        "如果图里有文字，把文字如实念出来。"
        "只输出描述本身，不要寒暄，不要说明你在做什么。"
    )
    # 看说说时最多念几条评论给她（一条约 20~40 字，10 条约 200 token）
    VIEW_COMMENT_LIMIT = 10
    # 点赞人最多念几个名字（名字很短，6 个约 30 token）
    VIEW_LIKE_NAME_LIMIT = 6

    @staticmethod
    def _shrink_image(data: bytes) -> tuple[bytes, str]:
        """缩到长边 960 并重编码成 JPEG，省本地推理时间。

        动图或解码失败的原样返回，绝不因为压缩失败丢图。
        """
        try:
            from PIL import Image

            im = Image.open(io.BytesIO(data))
            if getattr(im, "is_animated", False):
                return data, "image/gif"
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            edge = QzonePlugin.VISION_MAX_EDGE
            if max(im.size) > edge:
                im.thumbnail((edge, edge), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(
                buf,
                format="JPEG",
                quality=QzonePlugin.VISION_JPEG_QUALITY,
                optimize=True,
            )
            out = buf.getvalue()
            if out and len(out) < len(data):
                return out, "image/jpeg"
        except Exception as exc:
            logger.debug(f"空间图片压缩失败，改用原图: {exc}")
        return data, "image/jpeg"

    async def _fetch_image(self, url: str) -> bytes | None:
        """下载图片。

        显式 trust_env=False：本机有全局代理，走代理连不上 QQ 图床。
        """
        try:
            connector = aiohttp.TCPConnector(ttl_dns_cache=300)
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                connector=connector,
                trust_env=False,
                headers={"User-Agent": _BROWSER_UA},
            ) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return await resp.read()
        except Exception as exc:
            logger.warning(f"空间图片下载失败（{type(exc).__name__}）: {url[:80]}")
            return None

    async def _describe_one(self, data: bytes, mime: str) -> str | None:
        """把一张图交给本地视觉模型，返回一句中文描述。失败返回 None。

        服务是懒启动的：第一次要看图时才拉起 llama-server（加载权重约几秒），
        每次调用都刷新闲置计时，超过 20 秒没人用就自动退出。
        """
        if not await self._vision.ensure():
            return None
        self._vision.touch()

        data_uri = f"data:{mime};base64," + base64.b64encode(data).decode()
        payload = {
            "model": self.VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "max_tokens": 160,
            "temperature": 0.2,
            "stream": False,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.VISION_TIMEOUT),
                trust_env=False,
            ) as session:
                async with session.post(self._vision.chat_url, json=payload) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:120]
                        logger.warning(f"本地视觉返回 HTTP {resp.status}: {body}")
                        return None
                    result = await resp.json()
        except asyncio.TimeoutError:
            logger.warning(f"本地视觉超时（{self.VISION_TIMEOUT:.0f}s），本轮跳过")
            return None
        except Exception as exc:
            logger.warning(f"本地视觉调用失败（{type(exc).__name__}）: {exc}")
            return None
        finally:
            self._vision.touch()

        try:
            text = result["choices"][0]["message"]["content"] or ""
        except Exception:
            return None
        text = text.strip()
        return text or None

    async def _describe_post_images(self, post: Post) -> tuple[list[str], int]:
        """转述说说里的图片，返回 (描述列表, 未展开的图片数)。

        单张失败只写一句占位，绝不抛错、绝不阻塞回复。
        """
        urls = [u for u in dict.fromkeys(post.images or []) if u]
        if not urls:
            return [], 0

        now = time.monotonic()
        if now < self._vision_fail_until:
            logger.debug(
                f"本地视觉冷却中，剩余 {int(self._vision_fail_until - now)}s"
            )
            return [], len(urls)

        descs: list[str] = []
        for url in urls:
            if len(descs) >= self.VISION_MAX_IMAGES:
                break

            key = hashlib.sha1(url.encode("utf-8")).hexdigest()
            if key in self._vision_cache:
                descs.append(self._vision_cache[key])
                continue

            raw = await self._fetch_image(url)
            if not raw:
                descs.append("（这张图没下载下来）")
                continue

            shrunk, mime = self._shrink_image(raw)
            desc = await self._describe_one(shrunk, mime)
            if not desc:
                # 本地服务不可用（例如游戏把显存占满），进入冷却期，
                # 避免接下来每次都白等一个超时
                self._vision_fail_until = time.monotonic() + self.VISION_COOLDOWN
                descs.append("（本地视觉这次没认出来）")
                continue

            if len(self._vision_cache) > 200:
                self._vision_cache.clear()
            self._vision_cache[key] = desc
            descs.append(desc)

        return descs, max(0, len(urls) - len(descs))

    def _format_comments(self, post: Post, limit: int | None = None) -> str:
        """把评论区渲染成她读得懂的文本。

        原来的问题是：评论只画在卡片图片上，模型收到的是纯正文，
        于是她以为这条说说没人评论。数据其实一直在 Post.comments 里。
        """
        comments = post.comments or []
        if not comments:
            return ""

        cap = self.VIEW_COMMENT_LIMIT if limit is None else limit
        lines = [f"\n【评论区 {len(comments)} 条】"]
        shown = 0
        for comment in comments:
            if shown >= cap:
                break
            body = remove_em_tags(
                extract_and_replace_nickname(comment.content or "")
            ).strip()
            if not body:
                continue
            name = remove_em_tags(comment.nickname or "").strip() or "某人"
            # 楼中楼缩进一层，跟主楼区分开
            mark = "  > " if comment.parent_tid else "- "
            lines.append(f"{mark}{name}：{body}")
            shown += 1

        if shown == 0:
            return ""
        left = len(comments) - shown
        if left > 0:
            lines.append(f"（另有 {left} 条未展开）")
        return "\n".join(lines)

    def _format_likes(self, info: dict | None) -> str:
        """把「谁赞了这条」渲染成一行文本。

        数据来自动态流，可能比说说列表晚一拍；拿不到就干脆不提，
        免得她以为没人赞而乱猜。
        """
        if not info:
            return ""
        count = int(info.get("count") or 0)
        names = info.get("names") or []
        if count <= 0 and not names:
            return ""

        head = f"\n【点赞 {max(count, len(names))} 人"
        if names:
            shown = names[: self.VIEW_LIKE_NAME_LIMIT]
            head += "：" + "、".join(shown)
            if len(names) > len(shown):
                head += f" 等 {max(count, len(names))} 人"
        head += "】"
        if info.get("self_liked"):
            head += "\n（这条她已经赞过）"
        return head

    @staticmethod
    def _owner_guard(event: AiocqhttpMessageEvent) -> str | None:
        """硬闸门：QQ 空间的操作只对主人开放（副人格/其他人一律拒绝）。"""
        if str(event.get_sender_id() or "") != "<OWNER_QQ>":
            return "QQ 空间的操作只对主人开放。"
        return None

    @filter.llm_tool()
    async def llm_view_feed(
        self,
        event: AiocqhttpMessageEvent,
        user_id: str | None = None,
        pos: int = 0,
        like: bool = False,
        reply: bool = False,
    ):
        """
        查看、点赞、评论某位用户QQ空间的某条说说、动态
        Args:
            user_id(string): 目标用户的QQ账号，必定为一串数字，如(12345678), 默认为当前用户QQ号
            pos(number): 要查询的说说序号, 默认为0表示最新
            like(boolean): 是否点赞
            reply(boolean): 是否评论
        """
        if (refused := self._owner_guard(event)) is not None:
            return refused
        try:
            user_id = user_id or event.get_sender_id()
            logger.debug(f"正在查询用户（{user_id}）的第 {pos} 条说说")

            try:
                posts = await self.service.query_feeds(
                    target_id=user_id,
                    pos=pos,
                    num=1,
                    with_detail=True,
                )
            except Exception as detail_exc:
                # 详情接口（emotion_cgi_msgdetail_v6）不稳定，实测常返回空 result。
                # 退回列表接口——它本来就带 replynum=100 + need_comment=1，
                # 评论一条不少，只是少了转发链之类的附加信息。
                logger.debug(f"说说详情获取失败，退回列表接口: {detail_exc}")
                posts = await self.service.query_feeds(
                    target_id=user_id,
                    pos=pos,
                    num=1,
                )

            if not posts:
                return "查询结果为空"

            post = posts[0]

            # 执行动作
            msg = ""

            if like and reply:
                await self.service.comment_posts(post, event=event)
                await self.service.like_posts(post)
                msg = "已评论并点赞"
            elif reply:
                await self.service.comment_posts(post, event=event)
                msg = "已评论"
            elif like:
                await self.service.like_posts(post)
                msg = "已点赞"

            # 发送展示
            await self.sender.send_post(event, post, message=msg)

            # 本地视觉转述图片，只把文字描述发给云端（图片不进上下文）
            descs, skipped = await self._describe_post_images(post)

            lines = [x for x in (msg, post.text) if x]
            text = "\n".join(lines).strip() or "(这条说说没有文字内容)"

            if post.images:
                text += f"\n【本说说共 {len(post.images)} 张图片】"
                for idx, desc in enumerate(descs, 1):
                    text += f"\n图{idx}：{desc}"
                if skipped:
                    text += f"\n（另有 {skipped} 张未展开）"

            # 评论区：接口本来就带回来了（replynum=100 + need_comment=1），
            # 但之前只发了卡片给她"看不见"——这里补进返回文本，她才能读到。
            block = self._format_comments(post)
            if block:
                text += block

            # 点赞：只能从好友动态流里拿（说说接口不返回赞），60 秒缓存
            try:
                like_map = await self.service.get_like_map()
                like_block = self._format_likes(like_map.get(str(post.tid)))
            except Exception as e:
                logger.debug(f"点赞信息附加失败（忽略）: {e}")
                like_block = ""
            if like_block:
                text += like_block

            return text

        except Exception as e:
            logger.error(e)
            return str(e)

    @filter.llm_tool()
    async def llm_delete_feed(
        self,
        event: AiocqhttpMessageEvent,
        pos: int = 0,
    ):
        """删掉 QQ 空间上你自己的一条说说。删了就找不回来了，动手前自己拿准。

        判断权在你：对方让你删、你也确认该删，就直接调；
        拿不准是哪一条、或不确定该不该删，就先 llm_view_feed 看一眼，
        或者反问对方一句，别闷头删。

        Args:
            pos(number): 要删第几条说说，0 表示最新那条
        """
        if (refused := self._owner_guard(event)) is not None:
            return refused
        self_id = str(event.get_self_id())
        try:
            posts = await self.service.query_feeds(
                target_id=self_id, pos=pos, num=1, with_detail=True
            )
            if not posts:
                return f"没有查到第 {pos} 条说说，pos 可能超出了范围。"
            post = posts[0]
            if not post.tid:
                return "这条说说没有 tid，删不了。"

            # 删之前把内容留在聊天里当个记录，方便事后回看删了什么
            await self.sender.send_post(event, post, message="已删除这条说说")
            await self.service.delete_post(post)
            text = (post.text or "").strip()
            return f"已删除第 {pos} 条说说：{text[:120] or '（纯图片）'}"
        except Exception as e:
            logger.error(e)
            return f"删除失败：{e}"

    @filter.llm_tool()
    async def llm_publish_feed(
        self,
        event: AiocqhttpMessageEvent,
        text: str = "",
        get_image: bool = True,
    ):
        """
        写一篇说说并发布到QQ空间
        Args:
            text(string): 要发布的说说内容
            get_image(boolean): 是否获取当前对话中的图片附加到说说里, 默认为True
        """
        if (refused := self._owner_guard(event)) is not None:
            return refused
        images = await get_image_urls(event) if get_image else []
        try:
            post = await self.service.publish_post(text=text, images=images)
            # 她真的发了，今天就不再提这茬
            self._publish_invite.mark_published()
            await self.sender.send_post(event, post, message="已发布")
            return "已发布说说到QQ空间: \n" + post.text + "\n" + "\n".join(post.images)
        except Exception as e:
            return str(e)
