# YLFile字幕工具箱

有料视界（YLFile）字幕组一体化工作流工具：**MKV 提取字幕 → 黑边测量 → 生成 ASS → 音轨处理 → 压制**。

- 微博：[YLFile](https://weibo.com)
- 官网：https://ylfile.com

## 功能

| 步骤 | 说明 |
|------|------|
| 规范 MKV/MP4 命名 | 统一重命名为 SxxExx.mkv / SxxExx.mp4 |
| 批量提取字幕 | 从 MKV 提取原版/中文字幕轨，繁体自动转简体 |
| 黑边测量 | 手动点击 + 批量自动检测，每部剧只需测一次 |
| 生成 ASS | 双语/仅中文字幕，自动定位黑边区域，含水印信息行 |
| 音频去多余音轨 | 按文件夹语言标识保留对应音轨，重封装为 _原音.mkv |

## 快速开始（用户）

1. 下载 `YLFile字幕工具箱.exe`
2. 双击运行
3. 按工作流程：**规范命名 → 提取字幕 → 测黑边 → 生成 ASS**

详细说明见 `使用说明.txt`。

## 源码运行

```bash
pip install -r requirements.txt
python main.py
```

需将 `mkvmerge.exe`、`mkvextract.exe` 放在程序根目录；`opencc/` 与 `share/opencc/` 已随仓库提供。

## 字幕目录约定

```
字幕根目录/
  剧名A/
    S01E01.mkv
    S01E01原版.srt
    S01E01中文版.srt
    S01E01双语-1080p.ass   # 生成后
  剧名B/
    ...
```

## 打包 exe

```bash
pip install pyinstaller
pyinstaller main.spec
```

## 许可

仅供学习交流；视频与字幕版权归原作者所有，请勿商用。

## 技术栈

- Python 3.12 / tkinter
- OpenCV（自动黑边检测）
- OpenCC（繁转简）
- mkvmerge / mkvextract（MKV 处理）
