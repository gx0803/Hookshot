# Hookshot Builder 预更新面板（Windows）

一个**本地网页面板**，帮你在点官方 Builder 的 **Update** 之前，先把那批体积很大、GitHub 直连很慢的构建组件**预先下载好并校验**。之后官方 `setup.c` 发现缓存已存在且 SHA-256 匹配，会整段跳过下载，Update 秒过。

这个工具是 [`gx0803/Hookshot`](https://github.com/gx0803/Hookshot) fork 随 `X.Y.Z-CN` Release 附送的便利工具，**不是官方组件**，也不替代官方 Builder。

## 它做什么 / 不做什么

做三件事，且只做这三件事：

1. **只读**本机版本：`versions\current.txt` / `previous.txt`，以及游戏目录的 `hookshot_version.txt`（仅用于显示）。
2. 从上游 `NotRexed/Hookshot` 的最新 release 取 `hookshot-setup.txt`，严格照官方 `setup.c` 的 `part_folder()` / `reuse_part()` 规则算出「官方会复用 / 已缓存 / 需要下载」的差集。
3. 把「需要下载」的 part 用并发分段下载 + SHA-256 校验，原子地放到官方 `downloads\<sha256>.zip`，让 `setup.c:637` 直接跳过。

**绝不**：写游戏目录、启动 Builder、替你点 Update、改 `hookshot_options.ini` / `hookshot_env.txt` / 任何 DLL 或 exe。唯一写入是 Builder 的 `downloads\` 与 `predl\`，以及本目录的 `prep-update.log` / `last-check.json`。

## 平台

**仅支持 Windows** 的 `hookshot-setup.txt`。官方 Linux 用 `hookshot-linux.txt`，它的 part 行是 `<sha> <size> <dest> <url>` 并带 `tree` / `file` 指令，**格式不同**；面板检测到会明确报错，不会静默把差集算成 0。Linux 用户请照官方方式更新。

## 用法

最简单：双击 **`start.bat`**，会自动选 Python、打开 `http://127.0.0.1:8806/`，浏览器里点「检测版本更新」→「开始预下载」；全部就绪后，**由你手动**去点官方 Builder 的 Update 或直接启动游戏触发编译。关窗口即停（或在黑窗口按 Ctrl+C）。

也可以命令行：

```
python server.py               # 启动面板
python server.py --selftest    # 只自检（不下载、不起服务），排查路径问题用
```

`--selftest` 会打印探测到的路径来源和当前差集，是排查「为什么显示未找到目录」的第一步。

## 路径怎么找（重要）

工具**不写死任何盘符或用户名**，按以下优先级自动定位 HookshotBuilder 数据根（含 `versions\` 与 `downloads\`）：

1. 命令行 `--builder-dir <path>`
2. 环境变量 `HOOKSHOT_BUILDER_DIR`
3. 本目录 `config.json` 的 `builder_dir` 字段
4. 自动探测 `%LOCALAPPDATA%\HookshotBuilder`，再退到 `~/.local/share/HookshotBuilder`

判据是 `<数据根>\versions\current.txt` 是否存在；找到了才认定为有效目录。游戏目录同理（`HOOKSHOT_GAME_DIR` / `config.json` 的 `game_dir` / 常见 `~/Games/Hookshot`），**探测不到只影响「游戏本体版本」那一栏的显示，不影响差集与预下载**。

如果你的 Builder 数据放在自建位置（例如把 `%LOCALAPPDATA%\HookshotBuilder\versions` 做了 junction 指向 D 盘），推荐用 junction 保持默认探测即可；面板会把真实指向一并显示在「路径与安全边界」里。否则复制 `config.example.json` 为 `config.json` 并填 `builder_dir`：

```json
{
  "builder_dir": "D:\\你的数据根\\HookshotBuilder",
  "game_dir": "D:\\你的游戏目录\\The Legend of Zelda Ocarina of Time"
}
```

> `config.json` 含本机路径，属个人配置，**不要提交/分享**。它和运行时产生的 `prep-update.log` / `last-check.json` 都已在 `.gitignore` 中忽略。

## 判定规则（与官方一致，可核对）

| 状态 | 含义 |
|---|---|
| `noop` | 本机已是该版本，官方不会触发更新，无需预下载 |
| `reuse` | `reuse_part()` 命中：从旧版本 `MoveFileW` 复用，不下载 |
| `cached` | `downloads\<sha>.zip` 已存在且 SHA-256 匹配，`setup.c:637` 跳过 |
| `need` | 需下载；面板会把它放进预下载队列 |

注意：`app` / `dotnet` 两类没有独立文件夹、永远不进 `parts.txt`，**每次更新都要重下**；这是官方行为，不是本工具的缺陷。

上游若新增了组件，面板每次「检测」都会重新拉最新 `hookshot-setup.txt` 并**全量重算**差集，part 数量增减会自动反映到列表与进度，无需改本工具。

## 依赖与许可

- 仅需 **Python 3**（或系统里有 `python`；`start.bat` 也会回退去找 Builder 自带 `tools\python\python.exe`）和 **curl**（Windows 10+ 自带）。标准库之外无依赖。
- 代码来自 [`gx0803/Hookshot`](https://github.com/gx0803/Hookshot)，遵循上游 **GPL-3.0-or-later**；详见仓库根 `LICENSE`。这是对官方 `builder/setup/setup.c` 下载缓存机制的本地辅助实现，与 Nintendo 无关。
