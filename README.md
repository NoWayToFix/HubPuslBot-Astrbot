# HubPuslBot (AstrBot)

> Pusl = Push 和 Pull 在一个晚上喝醉了。

`hub-pusl` 是一个 AstrBot 插件，用于在群聊内与 GitHub 图片仓库互动：

- **Push**：把群里发送的图片（或回复消息中的图片）推送到 GitHub 仓库，并自动创建 Pull Request。
- **Pull**：从 GitHub 仓库随机拉取图片到群里，可随机抽取，也可按文件名精确拉取。

全程通过 GitHub API 完成，无需在本地 `git clone` 仓库。

## 安装

将本插件目录放入 AstrBot 的 `data/plugins/` 目录下，在 AstrBot 管理面板中启用即可。

## 配置

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `github_token` | `string` | 必填 | GitHub Personal Access Token，需具备 `repo` 权限。 |
| `github_repo` | `string` | 必填 | 上游仓库，格式为 `owner/repo`。 |
| `base_branch` | `string` | `main` | PR 的目标分支。 |
| `github_mirror` | `string` | 空 | 图片下载镜像前缀，例如 `https://gh-proxy.org/`，留空则直连 GitHub。 |
| `allowed_groups` | `string[]` | `[]` | 允许使用的群号列表，为空则允许所有群。 |
| `admin_users` | `string[]` | `[]` | 允许执行 push 的用户 ID，为空则允许所有人。 |
| `image_dir` | `string` | `images` | 图片在上游仓库中的目录。 |
| `max_file_size` | `number` | `20` | 允许推送的最大图片大小，单位 MB。 |

## 命令

### `/nwtf-push <标题>`

将随消息发送的图片（或回复消息中的图片）推送到上游仓库，并创建一个 Pull Request。

```
/nwtf-push 可爱小猫
```

注意事项：

- 标题会作为图片文件名，非法字符会被替换为下划线。
- 如果仓库中已存在同名文件，会提示更换标题。
- 插件会先 fork 上游仓库到自己的账号下，再创建分支、上传文件、提交 PR。

### `/nwtf-pull [图片名]`

从上游仓库拉取一张图片到群里。

```
# 随机拉取一张图片
/nwtf-pull

# 按文件名（不含扩展名）精确拉取
/nwtf-pull 可爱小猫
```

随机拉取时会记录每个群的已发送图片，尽量做到不重复；当所有图片都发送过后会自动重置记录。

## 使用前提

1. 准备一个 GitHub 仓库用于存放图片，并在仓库中创建配置的 `image_dir` 目录（例如 `images`）。
2. 生成一个具有 `repo` 权限的 [GitHub Personal Access Token](https://github.com/settings/tokens)。
3. 将 Token 填入插件配置，并设置正确的上游仓库 `owner/repo`。

## 许可证

AGPL-3.0