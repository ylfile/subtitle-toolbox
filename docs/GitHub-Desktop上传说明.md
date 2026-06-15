# 用 GitHub Desktop 上传本仓库

## 操作步骤

1. 打开 **GitHub Desktop** → **File** → **Add local repository**
2. 选择文件夹：`e:\Chrome下载\final_tool`
3. 若提示「不是仓库」，选 **create a repository**（或沿用已有 `.git`）
4. 在网页创建空仓库：`zhangyao1989/YLFile-subtitle-toolbox`
5. Desktop：**Repository** → **Repository settings** → **Remote**，填 `https://github.com/zhangyao1989/YLFile-subtitle-toolbox.git`
6. 左侧勾选要提交的文件（见下方「会上传」），写说明后 **Commit**，再 **Push origin**

`.gitignore` 已配置好，**不要手动勾选**被忽略的 exe、dist、config.json 等。

---

## 会上传（源码与资源）

| 类型 | 文件/目录 |
|------|-----------|
| 程序 | `main.py`、`extract.py`、`crop.py`、`ass.py`、`rename_mkv_episodes.py`、`utils.py`、`config.py` |
| 配置模板 | `config.example.json` |
| 繁简转换 | `opencc\`（除 exe 外）、`share\opencc\` |
| 构建说明 | `build.bat`、`requirements.txt` |
| 文档 | `README.md`、`tools\README.txt`、`docs\` |
| 其他 | `.gitignore` |

## 不会上传（已在 .gitignore，保留在本地即可）

| 文件 | 原因 |
|------|------|
| `mkvmerge.exe`、`mkvextract.exe` | 体积大，用户自行下载，见 `tools/README.txt` |
| `opencc\opencc.exe` 等 | 同上 |
| `config.json` | 含你的路径与黑边数据，私密 |
| `dist\`、`build\`、`*.spec` | 打包临时文件 |
| `__pycache__\` | Python 缓存 |

## 给别人用 exe

打包后的 `dist\YLFile字幕工具箱.exe` **不要进 Git**。请在 GitHub 网页 **Releases** 里单独上传附件。

## 首次克隆后的用户

```bat
copy config.example.json config.json
```

并按 `tools\README.txt` 放置四个外部 exe。
