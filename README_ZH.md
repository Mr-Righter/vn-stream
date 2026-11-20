<div align="center">
  <h1>VN Stream</h1>
  <p>在浏览器中随时随地串流游玩视觉小说</p>
  <p><a href="README.md">English</a> · <a href="README_ZH.md">简体中文</a></p>
  <p>
    <a href="https://github.com/Mr-Righter/vn-stream/releases/tag/v1.0.0"><img src="https://img.shields.io/badge/Release-v1.0.0-blue?style=flat-square" alt="Release v1.0.0" /></a>
    <a href="https://github.com/Mr-Righter/vn-stream/pkgs/container/vn-stream"><img src="https://img.shields.io/badge/GHCR-vn--stream-181717?style=flat-square&amp;logo=github" alt="GHCR" /></a>
    <a href="#快速开始"><img src="https://img.shields.io/badge/Linux-x86--64-555?style=flat-square&amp;logo=linux&amp;logoColor=white" alt="Linux x86-64" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="MIT" /></a>
  </p>
  <p>
    <a href="#支持的游戏引擎">支持的游戏引擎</a> ·
    <a href="#功能一览">功能一览</a> ·
    <a href="#快速开始">快速开始</a> ·
    <a href="#游戏运行与串流调优">游戏运行与串流调优</a>
  </p>
  <img src="web/public/branding/banner.png" alt="VN Stream 项目横幅" />
</div>

VN Stream 是一个自托管的视觉小说游戏库，支持 Ren'Py、ONScripter 和 Kirikiri。导入游戏后，VN Stream 在你的主机上运行游戏，并将画面和声音串流到浏览器。

## 支持的游戏引擎

| 引擎       | 内置版本与源码                                                                                                                                                                                                                      | 默认选项与兼容性                                                                                                                           |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Ren'Py     | 官方 SDK 7.5.3、7.8.2、8.0.3、8.1.3、8.2.3、8.3.2、8.3.7、8.4.1、8.5.3。[官方发布列表](https://www.renpy.org/release_list.html)                                                                                                     | 优先匹配游戏版本，其次推荐已安装的同一主版本中最接近的较新版本。                                                                           |
| ONScripter | [Yuri 0.7.7](https://github.com/YuriSizuku/OnscripterYuri)、[Jh 0.7.3](https://github.com/chf2000/ONScripter-Jh)、[zero 0.9.10-dev](https://github.com/zeromake/ONScripter-zero)、[NEW](https://github.com/timftw21/ONScripter-NEW) | 默认 Yuri。zero 适用于 GBK 或 Shift-JIS 项目，需要 AVX2。NEW 需要 UTF-8 文本入口 `0.txt` 或 `00.txt`，以及游戏自带的 `fonts/default.ttf`。 |
| Kirikiri   | [krkr-rs](https://github.com/wqlouis/krkr-rs)、[KrKr2-Next](https://github.com/reAAAq/KrKr2-Next)                                                                                                                                   | 默认 krkr-rs。兼容性取决于游戏插件和归档格式。内置的 KrKr2-Next 暂不支持依赖 `Motion.D3DAdaptor` 或 Live2D 的项目。                        |

## 功能一览

| 功能           | 说明                                                                                                                                                                 | 支持引擎                                        |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 存档管理与编辑 | 导入、下载、备份和恢复存档，支持本机签名、简单变量编辑和跨项目迁移。                                                                                                 | Ren'Py                                          |
| 素材库预览     | 浏览、预览和下载受支持的图片、音频和视频。                                                                                                                           | Ren'Py、ONScripter、Kirikiri                    |
| 本地游戏上传   | 可上传**单个游戏的完整根目录**，例如包含 `game/` 的 Ren'Py 游戏目录。支持 `.zip`、`.7z`、`.rar`、`.tar`、`.tar.gz`、`.tar.bz2` 和 `.tar.xz` 压缩包，以及兼容的 APK。 | Ren'Py、ONScripter、Kirikiri（APK 仅限 Ren'Py） |

## 快速开始

需要 Docker Compose v2。镜像为 `linux/amd64`。Linux 使用 Docker Engine，macOS 和 Windows 使用 Docker Desktop 的 Linux 容器。Apple Silicon 需启用 AMD64 模拟。[Docker Desktop 设置](https://docs.docker.com/desktop/settings-and-maintenance/settings/)

### 1. 准备游戏目录和 Compose 文件

**Linux 和 macOS**

```sh
mkdir -p vn-stream/games
cd vn-stream
chmod 777 games
curl -fL -o compose.yaml https://raw.githubusercontent.com/Mr-Righter/vn-stream/HEAD/compose.yaml
```

**Windows PowerShell**

```powershell
New-Item -ItemType Directory -Force -Path vn-stream, vn-stream/games
Set-Location vn-stream
curl.exe -fL -o compose.yaml https://raw.githubusercontent.com/Mr-Righter/vn-stream/HEAD/compose.yaml
```

启动前修改 `PASSWORD`。也可按需修改 `CUSTOM_USER` 和宿主机端口，或将 `source` 改为已有游戏目录。

```yaml
services:
  vn-stream:
    image: ghcr.io/mr-righter/vn-stream:v1.0.0
    platform: linux/amd64
    restart: unless-stopped
    shm_size: 1gb
    ports:
      - "127.0.0.1:8443:3000"
    environment:
      CUSTOM_USER: player
      PASSWORD: "change-this-password"
    volumes:
      - type: bind
        source: ./games
        target: /games
        bind:
          create_host_path: false
      - vn-stream-data:/data
      - vn-stream-config:/config

volumes:
  vn-stream-data:
    name: vn-stream-data
  vn-stream-config:
    name: vn-stream-config
```

### 2. 启动并打开游戏库

```sh
docker compose up -d
```

在运行 Docker 的设备上打开 <http://localhost:8443/library/> 并登录。通过**导入**添加游戏，也可将解压后的游戏按引擎放到 `games/` 下的 `renpy/`、`onscripter/` 或 `kirikiri/` 后刷新游戏库。

<details>
<summary>在 iOS 上全屏游玩</summary>

1. 在 iPhone 或 iPad 上用 Safari 打开设备可访问的 HTTPS 游戏库地址。
2. 轻点「共享」→「添加到主屏幕」。若看到「作为网页 App 打开」，将其开启，再轻点「添加」。[Apple 操作指南](https://support.apple.com/zh-cn/guide/iphone/iphea86e5236/27/ios/27)
3. 从主屏幕打开 **VN Stream** 图标并进入游戏。网页 App 会隐藏 Safari 的地址栏和工具栏。

</details>

## 游戏运行与串流调优

**游戏打不开**时，在原游戏项目的详情页选择同引擎的其他运行器，再检查游戏能否启动、存档和读档。ONScripter 的动画计时表现也可能因游戏和运行器而异。

**画面卡顿或网络较差**时，在 Selkies 侧边栏打开「视频设置」和统计，每次调整一项并观察效果。

| 设置           | 调整方法                                                                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 帧率           | 先设为 30 FPS，仍卡顿时再降到 24 或 20 FPS，减少每秒需要编码、传输和解码的画面。                                                                       |
| 视频 CRF       | 在 CRF 模式下，可以把 25 逐步调到 28–32。数值越高，通常越省带宽，画质也会降低。使用 CBR 时可降低视频码率。                                             |
| 编码器与解码器 | 黑屏或掉帧时，先尝试浏览器支持的 H.264。编码器决定服务端发送的视频格式，浏览器根据设备能力选择解码方式。可在统计中查看「解码器」显示的是硬件还是软件。 |

[Selkies](https://github.com/selkies-project/selkies) · [网页客户端设置](https://docs.linuxserver.io/selkies/user-guide/web-client/) · [串流故障排查](https://docs.linuxserver.io/selkies/user-guide/troubleshooting/)

## 许可证

[MIT 许可证](LICENSE)
