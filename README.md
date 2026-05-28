# YLFile字幕工具箱

有料视界（YLFile）字幕组一体化工作流工具：**MKV 提取字幕 → 黑边测量 → 生成 ASS → FFmpeg 硬字幕压制**。

- 微博：[YLFile](https://weibo.com)
- 官网：https://ylfile.com

## 功能

| 步骤 | 说明 |
|------|------|
| 批量提取字幕 | 从 MKV 提取原版/中文字幕轨，繁转简 |
| 黑边测量 | 每部剧测一次样例视频，写入 `config.json` |
| 生成 ASS | 双语/仅中文布局，含水印信息行 |
| 批量压制 | H.264/H.265，CPU 或 NVIDIA/AMD/Intel GPU |

## 环境要求

- Windows 10/11
- Python 3.10+（源码运行或打包）
- 外部工具见 [tools/README.txt](tools/README.txt)

## 快速开始（源码）

```bash
pip install -r requirements.txt
copy config.example.json config.json
python main.py
```

将 `ffmpeg.exe`、`ffprobe.exe`、`mkvmerge.exe`、`mkvextract.exe` 放在程序根目录（与 `main.py` 同级）。`opencc` 与 `share/opencc` 已随仓库提供。

## 打包 exe

```bash
pip install pyinstaller
build.bat
```

生成 `dist\YLFile字幕工具箱.exe`。首次使用请将 `config.example.json` 复制为 `config.json`。

## 目录结构

```
├── main.py              # 程序入口
├── extract.py / crop.py / ass.py / encode.py
├── config.py / config.example.json
├── opencc/ / share/opencc/
├── build.bat
└── tools/README.txt     # 外部 exe 下载说明
```

## 字幕目录约定

```
字幕根目录/
  剧名A/
    S01E01.mkv
    S01E01原版.srt
    S01E01中文版.srt
    S01E01双语.ass      # 生成后
  剧名B/
    ...
```

黑边测量后，剧集文件夹名须与 `config.json` 中的键名一致。

## 许可

仅供学习交流；视频与字幕版权归原作者所有，请勿商用。

## 发布到 GitHub

维护者 GitHub：**[@zhangyao1989](https://github.com/zhangyao1989)**  
建议仓库：**[zhangyao1989/YLFile-subtitle-toolbox](https://github.com/zhangyao1989/YLFile-subtitle-toolbox)**

安装 [Git](https://git-scm.com/) 后，可双击运行 `publish-github.bat`，或在程序目录执行：

```bash
git init
git add .
git commit -m "Initial release: YLFile字幕工具箱"
```

在 GitHub 新建空仓库（建议名：`YLFile-subtitle-toolbox`），然后：

```bash
git remote add origin https://github.com/zhangyao1989/YLFile-subtitle-toolbox.git
git branch -M main
git push -u origin main
```

`dist\` 与 `*.exe` 已在 `.gitignore` 中排除（体积过大）。可将 `dist\YLFile字幕工具箱.exe` 作为 **Releases** 附件上传，供用户直接下载。

## 说明

- 硬字幕烧录主要占用 CPU；GPU 仅用于视频编码阶段。
- 部分片源压制后可能出现音画不同步，属已知限制，建议使用 CPU + CRF 或自行校验成片。
