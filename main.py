#!/usr/bin/env python3
import asyncio
import base64
import json
import os
import re
import time
from typing import Any

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star, StarTools, register

SUPPORTED_EXTENSIONS = re.compile(r"\.(png|jpe?g|webp|gif|bmp)$", re.IGNORECASE)


def _extension_to_mime(ext: str) -> str:
    _mime_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
        "bmp": "image/bmp",
    }
    return _mime_map.get(ext, "image/png")


@register(
    "hub-pusl",
    "Yukino_fox",
    "通过 GitHub API 在群内推送图片并创建 PR，或随机拉取图片到群内，无需本地 clone 仓库。",
    "1.0.0",
    "https://github.com/NoWayToFix/HubPuslBot",
)
class HubPuslPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._http_session: aiohttp.ClientSession | None = None
        self._fork_owner: str = ""
        self._upstream_owner: str = ""
        self._upstream_repo: str = ""
        self._data_dir: str = ""
        self._history_path: str = ""

    async def initialize(self):
        repo = self.config.get("github_repo", "")
        parts = repo.split("/")
        if len(parts) != 2:
            logger.error(f"github_repo 格式错误：{repo}，应为 owner/repo")
            return
        self._upstream_owner, self._upstream_repo = parts

        self._data_dir = str(StarTools.get_data_dir("hub-pusl"))
        self._history_path = os.path.join(self._data_dir, "history.json")
        os.makedirs(self._data_dir, exist_ok=True)

        self._http_session = aiohttp.ClientSession()

        token = self.config.get("github_token", "")
        if not token:
            logger.warning("未配置 github_token，插件功能将不可用。")
            return

        try:
            headers = self._github_headers
            async with self._http_session.get(
                "https://api.github.com/user", headers=headers
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._fork_owner = data["login"]
                    logger.info(
                        f"插件已加载，上游仓库：{repo}，Token 用户：{self._fork_owner}，"
                        f"目标分支：{self.config.get('base_branch', 'main')}"
                    )
                else:
                    logger.error(f"获取 GitHub 用户信息失败：HTTP {resp.status}")
        except Exception as e:
            logger.error(f"获取 GitHub 用户信息失败：{e}")

    async def terminate(self):
        if self._http_session:
            await self._http_session.close()

    # ──── 属性 ──────────────────────────────────────────────────────

    @property
    def _github_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.get('github_token', '')}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @property
    def _upstream_api_base(self) -> str:
        return (
            f"https://api.github.com/repos/{self._upstream_owner}/{self._upstream_repo}"
        )

    @property
    def _fork_api_base(self) -> str:
        return f"https://api.github.com/repos/{self._fork_owner}/{self._upstream_repo}"

    def _build_download_url(self, path: str) -> str:
        mirror = self.config.get("github_mirror", "")
        branch = self.config.get("base_branch", "main")
        if mirror:
            return (
                f"{mirror}https://github.com/"
                f"{self._upstream_owner}/{self._upstream_repo}/blob/{branch}/{path}"
            )
        return (
            f"https://raw.githubusercontent.com/"
            f"{self._upstream_owner}/{self._upstream_repo}/{branch}/{path}"
        )

    # ──── 权限检查 ──────────────────────────────────────────────────

    def _is_group_allowed(self, event: AstrMessageEvent) -> bool:
        if event.message_obj.type != MessageType.GROUP_MESSAGE:
            return True
        allowed = self.config.get("allowed_groups", [])
        if not allowed:
            return True
        group_id = str(event.message_obj.group_id)
        if group_id not in allowed:
            logger.warning(f"群 {group_id} 不在允许列表中")
            return False
        return True

    def _is_user_allowed(self, event: AstrMessageEvent) -> bool:
        admin_users = self.config.get("admin_users", [])
        if not admin_users:
            return True
        user_id = event.get_sender_id()
        if user_id not in admin_users:
            logger.warning(f"用户 {user_id} 没有 push 权限")
            return False
        return True

    # ──── 图片工具 ──────────────────────────────────────────────────

    @staticmethod
    def _infer_extension(buffer: bytes) -> str:
        if len(buffer) < 2:
            return "png"
        if buffer[0] == 0x89 and buffer[1] == 0x50:
            return "png"
        if buffer[0] == 0xFF and buffer[1] == 0xD8:
            return "jpg"
        if len(buffer) >= 4 and buffer[:4].hex() == "52494646":
            return "webp"
        if len(buffer) >= 3 and buffer[:3].decode("ascii", errors="ignore") == "GIF":
            return "gif"
        if buffer[0] == 0x42 and buffer[1] == 0x4D:
            return "bmp"
        return "png"

    def _find_image_url(self, event: AstrMessageEvent) -> str | None:
        # 检查当前消息中的图片
        for comp in event.message_obj.message:
            if isinstance(comp, Comp.Image):
                return comp.url or comp.file

        # 检查回复/引用消息中的图片
        for comp in event.message_obj.message:
            if isinstance(comp, Comp.Reply):
                chain = getattr(comp, "chain", None) or getattr(comp, "message", None)
                if chain:
                    for reply_comp in chain:
                        if isinstance(reply_comp, Comp.Image):
                            return reply_comp.url or reply_comp.file

        return None

    @staticmethod
    def _sanitize_filename(title: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fa5\-]", "_", title)[:64]

    # ──── 历史记录 ──────────────────────────────────────────────────

    def _load_history(self) -> dict[str, list[str]]:
        if not os.path.exists(self._history_path):
            return {}
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_history(self, history: dict[str, list[str]]):
        os.makedirs(os.path.dirname(self._history_path), exist_ok=True)
        with open(self._history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    # ──── GitHub API 辅助 ──────────────────────────────────────────

    async def _github_get(self, url: str) -> Any:
        async with self._http_session.get(url, headers=self._github_headers) as resp:
            if resp.status >= 400:
                raise Exception(f"GitHub GET {url} 返回 {resp.status}")
            return await resp.json()

    async def _github_post(self, url: str, data: dict | None = None) -> Any:
        async with self._http_session.post(
            url, headers=self._github_headers, json=data or {}
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise Exception(f"GitHub POST {url} 返回 {resp.status}: {text}")
            return await resp.json()

    async def _github_patch(self, url: str, data: dict) -> Any:
        async with self._http_session.patch(
            url, headers=self._github_headers, json=data
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise Exception(f"GitHub PATCH {url} 返回 {resp.status}: {text}")
            return await resp.json()

    async def _github_put(self, url: str, data: dict) -> Any:
        async with self._http_session.put(
            url, headers=self._github_headers, json=data
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise Exception(f"GitHub PUT {url} 返回 {resp.status}: {text}")
            return await resp.json()

    async def _check_file_exists(self, path: str) -> bool:
        url = f"{self._upstream_api_base}/contents/{path}?ref={self.config.get('base_branch', 'main')}"
        async with self._http_session.get(url, headers=self._github_headers) as resp:
            if resp.status == 404:
                return False
            if resp.status >= 400:
                text = await resp.text()
                raise Exception(f"检查文件存在失败：HTTP {resp.status}: {text}")
            return True

    async def _ensure_fork(self) -> str:
        if not self._fork_owner:
            raise Exception("Token 用户信息未获取，请检查 Token 配置后重启插件。")

        try:
            repo_info = await self._github_get(self._fork_api_base)
            if repo_info.get("fork") and (
                repo_info.get("parent", {}).get("full_name")
                == f"{self._upstream_owner}/{self._upstream_repo}"
            ):
                logger.debug(f"Fork 已存在：{self._fork_owner}/{self._upstream_repo}")
                return repo_info.get("default_branch", "main")
            if not repo_info.get("fork") or (
                repo_info.get("parent", {}).get("full_name")
                != f"{self._upstream_owner}/{self._upstream_repo}"
            ):
                raise Exception(
                    f"用户 {self._fork_owner} 下已存在 {self._upstream_repo} 仓库"
                    f"但不是上游的 fork，请手动处理。"
                )
        except Exception as e:
            if "404" not in str(e) and "Fork" not in str(e):
                raise

        logger.info(
            f"Fork 不存在，正在创建："
            f"{self._upstream_owner}/{self._upstream_repo} → "
            f"{self._fork_owner}/{self._upstream_repo}"
        )
        await self._github_post(f"{self._upstream_api_base}/forks")

        logger.debug("等待 fork 创建完成...")
        for i in range(10):
            await asyncio.sleep(3)
            try:
                repo_info = await self._github_get(self._fork_api_base)
                if repo_info.get("fork"):
                    logger.info(
                        f"Fork 创建完成：{self._fork_owner}/{self._upstream_repo}，"
                        f"默认分支：{repo_info.get('default_branch', 'main')}"
                    )
                    return repo_info.get("default_branch", "main")
            except Exception:
                logger.debug("Fork 尚未就绪，继续等待...")
        raise Exception("Fork 创建超时，请稍后重试。")

    async def _sync_fork_branch(self, fork_default_branch: str) -> str:
        upstream_ref = await self._github_get(
            f"{self._upstream_api_base}/git/ref/heads/"
            f"{self.config.get('base_branch', 'main')}"
        )
        upstream_sha = upstream_ref["object"]["sha"]
        logger.debug(f"上游分支 SHA：{upstream_sha}")

        try:
            await self._github_patch(
                f"{self._fork_api_base}/git/refs/heads/{fork_default_branch}",
                {"sha": upstream_sha, "force": True},
            )
            logger.debug(f"Fork 分支 {fork_default_branch} 已同步")
        except Exception as e:
            logger.warning(f"同步 fork 分支失败，继续使用当前状态：{e}")

        return upstream_sha

    async def _create_branch_on_fork(self, branch: str, sha: str):
        await self._github_post(
            f"{self._fork_api_base}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )
        logger.debug(f"在 fork 上创建分支：{branch}")

    async def _create_file_on_fork(self, path: str, branch: str, buffer: bytes):
        await self._github_put(
            f"{self._fork_api_base}/contents/{path}",
            {
                "message": f"[HubPusl] add image {path.split('/')[-1]}",
                "content": base64.b64encode(buffer).decode("ascii"),
                "branch": branch,
            },
        )

    async def _create_pr(self, title: str, branch: str) -> str:
        resp = await self._github_post(
            f"{self._upstream_api_base}/pulls",
            {
                "title": f"[HubPusl] {title}",
                "head": f"{self._fork_owner}:{branch}",
                "base": self.config.get("base_branch", "main"),
                "body": f"Submitted by HubPusl bot for image `{title}`.",
            },
        )
        return resp["html_url"]

    # ──── 核心逻辑 ──────────────────────────────────────────────────

    async def _push_image(self, event: AstrMessageEvent, title: str) -> str:
        logger.debug(f"收到 push 请求，标题：{title}，用户：{event.get_sender_id()}")

        if not self._is_group_allowed(event):
            return "当前群不在允许列表中。"
        if not self._is_user_allowed(event):
            return "你没有权限执行 push 操作。"

        image_url = self._find_image_url(event)
        if not image_url:
            logger.warning(f"未找到图片，用户：{event.get_sender_id()}")
            return "未检测到图片，请随命令发送图片或引用带图片的消息。"

        logger.debug(f"下载图片：{image_url}")
        async with self._http_session.get(image_url) as resp:
            if resp.status != 200:
                return f"图片下载失败：HTTP {resp.status}"
            buffer = await resp.read()

        ext = self._infer_extension(buffer)
        size_mb = len(buffer) / 1024 / 1024
        logger.debug(f"图片大小：{size_mb:.2f} MB，扩展名：{ext}")

        max_size = self.config.get("max_file_size", 20)
        if size_mb > max_size:
            return f"图片大小 {size_mb:.2f} MB 超过限制 {max_size} MB。"

        safe_title = self._sanitize_filename(title)
        if not safe_title:
            return "标题无效，无法生成文件名。"

        filename = f"{safe_title}.{ext}"
        path = f"{self.config.get('image_dir', 'images')}/{filename}"
        logger.debug(f"准备上传文件：{path}")

        if await self._check_file_exists(path):
            logger.warning(f"文件已存在：{filename}")
            return f"文件 `{filename}` 已存在，请更换标题后再试。"

        fork_default_branch = await self._ensure_fork()
        latest_sha = await self._sync_fork_branch(fork_default_branch)
        branch = f"hub-pusl/{safe_title}-{int(time.time() * 1000)}"
        await self._create_branch_on_fork(branch, latest_sha)
        await self._create_file_on_fork(path, branch, buffer)
        pr_url = await self._create_pr(title, branch)

        logger.info(f"push 成功，PR：{pr_url}")
        return f"图片已推送，PR：{pr_url}"

    async def _pull_image(
        self, event: AstrMessageEvent, name: str | None = None
    ) -> str:
        logger.debug(f"收到 pull 请求，用户：{event.get_sender_id()}")

        if not self._is_group_allowed(event):
            return "当前群不在允许列表中。"

        image_dir = self.config.get("image_dir", "images")
        branch = self.config.get("base_branch", "main")
        url = f"{self._upstream_api_base}/contents/{image_dir}?ref={branch}"

        try:
            items = await self._github_get(url)
        except Exception as e:
            if "404" in str(e):
                return "仓库中暂无图片。"
            raise

        images = [
            item
            for item in items
            if item.get("type") == "file" and SUPPORTED_EXTENSIONS.search(item["name"])
        ]
        if not images:
            return "仓库中暂无图片。"

        if name:
            target = name.strip()
            match = None
            for img in images:
                base = re.sub(r"\.[^.]+$", "", img["name"])
                if base.lower() == target.lower():
                    match = img
                    break
            if not match:
                return f"未找到名为 `{target}` 的图片。"
            selected = match
            logger.info(f"指定拉取图片：{selected['name']}")
        else:
            # 随机选择
            group_id = str(event.message_obj.group_id or event.get_sender_id())
            history = self._load_history()
            history_set = set(history.get(group_id, []))
            candidates = [img for img in images if img["name"] not in history_set]
            if not candidates:
                logger.info(f"群 {group_id} 所有图片都已发送过，重置历史记录")
                history[group_id] = []
                candidates = images
            selected = candidates[int(time.time() * 1000) % len(candidates)]
            logger.info(f"随机选中图片：{selected['name']}")
            history.setdefault(group_id, []).append(selected["name"])
            self._save_history(history)

        # 下载图片并以 base64 内联返回（避免直连 GitHub raw 可能被屏蔽）
        download_url = self._build_download_url(selected["path"])
        logger.debug(f"下载图片：{download_url}")
        async with self._http_session.get(download_url) as resp:
            if resp.status != 200:
                return f"图片下载失败：HTTP {resp.status}"
            buffer = await resp.read()

        ext = self._infer_extension(buffer)
        mime = _extension_to_mime(ext)
        b64 = base64.b64encode(buffer).decode("ascii")
        data_uri = f"data:{mime};base64,{b64}"
        return f"{selected['name']}\n{data_uri}"

    # ──── 命令处理器 ────────────────────────────────────────────────

    @filter.command("nwtf-push")
    async def push_command(self, event: AstrMessageEvent):
        """推送图片到 Hub 仓库并创建 PR"""
        title = event.message_str.strip()
        if not title:
            yield event.plain_result("请提供图片标题，例如：/nwtf-push 可爱小猫")
            return

        try:
            result = await self._push_image(event, title)
            yield event.plain_result(result)
        except Exception as e:
            logger.error(f"push 命令执行失败：{e}")
            yield event.plain_result(f"推送失败：{e}")

    @filter.command("nwtf-pull")
    async def pull_command(self, event: AstrMessageEvent):
        """从 Hub 仓库拉取图片（不指定名字则随机）"""
        name = event.message_str.strip() or None

        try:
            result = await self._pull_image(event, name)
            # result 是 "文件名\n图片URL" 格式
            if "\n" in result:
                parts = result.split("\n", 1)
                yield event.chain_result(
                    [
                        Comp.Plain(f"{parts[0]}\n"),
                        Comp.Image.fromURL(parts[1]),
                    ]
                )
            else:
                yield event.plain_result(result)
        except Exception as e:
            logger.error(f"pull 命令执行失败：{e}")
            yield event.plain_result(f"拉取失败：{e}")
