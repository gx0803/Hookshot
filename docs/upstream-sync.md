# 上游长期同步与中文常驻 SOP

本仓库是 `NotRexed/Hookshot` 的 fork。中文以**源码级 draw-time 翻译**实现：英文原文一字不动（它们同时是 ini 键和 `strcmp` 比较主语），只在绘制调用点包 `menu_i18n::tr()`。因此中文是**编译进 DLL 的**，不是事后打补丁——这是本方案相对旧「二进制 `.cntext` 汉化」的本质区别，后者每次升级都要重算 PE 偏移、重定向数百个引用点，易错且脆弱。

本文目标：上游每发一版，按固定流程把新代码同步进来、保住中文、产出可直接使用的 DLL，且**不依赖对话上下文**即可复现。

## 分支结构（谁负责什么）

| 分支 | 用途 | 是否提 PR |
|---|---|---|
| `feat/menu-i18n` | 唯一承载 i18n 功能改动（建自 `v2.1.0` tag，单个提交），**保持不动**作为纯净 PR 来源 | 是：给上游提 PR |
| `chore/upstream-sync` | `feat/menu-i18n` + 本目录同步工具与本文档。**日常同步在这个分支做**（工具就在同一工作树磁盘上） | 否 |
| `main` | fork 的 upstream 镜像，不直接改 | 否 |

**为什么同步在 `chore/upstream-sync` 上做**：它的父提交就是 `feat/menu-i18n`，既含全部 i18n 代码、又含同步工具。`sync-upstream.py` 动手前会检查「工具是否在当前工作树磁盘上」——若在裸 `feat/menu-i18n` 上跑，那里没有 `scripts/sync/`，脚本直接拒绝。这样可避免 rebase 冲突暂停时发现解决器不在、却误把带冲突标记的文件 `git add` 提交上去（**这是真实踩过并已修的坑**）。

**提 PR 时**：`chore/upstream-sync` rebase 到目标上游后，只把那个 `feat(menu):` 提交 cherry-pick 到一个从 `main` 新切的 PR 分支，工具与本文档天然不进 PR（上游 CONTRIBUTING 要求一 PR 一主题）。`feat/menu-i18n` 始终停在原始功能提交，是这条 cherry-pick 的稳定来源。

**冲突铁律**：任何冲突必须经 `resolve-rebase-conflicts.py --check` 逐字节确认是「已登记的纯插入」、且解决后文件**零冲突标记**，才允许 `git add && git rebase --continue`。遇到未登记冲突，编排器自动 `git rebase --abort` 回到同步前状态，绝不半途提交带标记的文件。手工操作时同样遵守，并**逐条命令看退出码，不要用管道把失败吞掉**。

## 核心回路（六步）

```
①  fetch upstream main
②  在 chore/upstream-sync 分支 rebase（i18n 提交整块挪到新 main 之上）
       └ 冲突只可能是「上游在我们挂钩处插入了新代码」→ 用登记好的脚本保留两侧
       └ 未登记冲突 / 解决后有标记 → 自动 abort，回到同步前状态
③  audit-menu-drift.py：列出上游新增、中文表缺失的菜单串
④  补译：从游戏官方 zh-Hans 资源取译名，写入 menu_zh.json（唯一人工步骤）
⑤  gen_menu_i18n.py 重生成表头 + RX_VK_ONLY=1 重编译 vk 层（约 46 秒）
⑥  switch-fork-i18n.py 把新 DLL 装回游戏目录 + 校验
```

除第 ④ 步外全部可自动化/脚本化。第 ④ 步是唯一需要人的地方，且只在**上游改了菜单文案**时才会产生新串——多数升级这一步是空的。

## 绝对路径与工具链（本机）

| 角色 | 路径 |
|---|---|
| fork 仓库 | `D:\businessProjects\Hookshot\gx0803\Hookshot` |
| 上游 remote | `https://github.com/NotRexed/Hookshot.git`（本地 ref `refs/remotes/upstream/main`） |
| Builder 工具链数据 | `D:\games\NS2\HookshotBuilder-data\versions\v2.1.0\HookshotBuilder\tools\` |
| 编译工作目录 | `D:\games\NS2\Hookshot\build-fork-i18n3` |
| 游戏目录 | `D:\games\NS2\Hookshot\games\The Legend of Zelda Ocarina of Time\` |
| 官方简中译名来源 | `D:\games\NS2\Hookshot\menu-cn\translations.json`（romfs `CNzh` MSBT 提取，440 条） |
| 游戏 DLL 安装/体检工具 | `D:\games\NS2\Hookshot\v2.1-review\switch-fork-i18n.py` |

## 逐步命令

所有命令在 fork 仓库根目录执行，Python 用 Builder 自带解释器：
`PY="D:\games\NS2\HookshotBuilder-data\versions\v2.1.0\HookshotBuilder\tools\python\python.exe"`

### ① + ② 同步代码

```bash
cd /d D:\businessProjects\Hookshot\gx0803\Hookshot
git checkout chore/upstream-sync  # 关键：必须在这个分支（工具才在磁盘上），别在裸 feat 上做
git status                         # 必须干净，否则先 commit/stash
git fetch --no-tags https://github.com/NotRexed/Hookshot.git main:refs/remotes/upstream/main
git log --oneline HEAD..refs/remotes/upstream/main | wc -l   # 上游领先多少提交
git rebase refs/remotes/upstream/main
```

**强烈建议用编排器**（默认只读报告，加 `--apply` 才真正 fetch+rebase）。它会在动手前检查工具是否齐全、工作树是否干净，rebase 冲突时**逐条严格判退出码**、已登记冲突自动保留两侧、未登记冲突自动 abort，并在进入 build 前停下：

```bash
%PY% -I -B scripts/sync/sync-upstream.py --check     # 只体检，零改动
%PY% -I -B scripts/sync/sync-upstream.py --apply     # fetch + rebase + 冲突检测，停在编译前
```

> 手工执行 `resolve-rebase-conflicts.py` 时，**不要用 `| sed`/`| head` 包裹后再靠 `$?` 判成败**（管道会吞掉真实退出码，曾因此把带冲突标记的文件误提交）。直接运行并看它自己打印的 `OK`/`ABORTED`，或在 shell 里 `set -o pipefail`。`--check` 通过、`--apply` 后 `grep -c '<<<<<<<' gpu/vk/vk_menu.cpp` 必须为 `0`，才 `git add && git rebase --continue`。

### ② b. 若有冲突

`vk_menu.h`、`menu_i18n.h`、`menu_zh.json`、`vk_game_font.h`、`vk_imgui.cpp` 历次 rebase 均自动合并（上游基本不动这些）。冲突几乎只在 `vk_menu.cpp`，且是**纯插入**（上游在我们挂钩的同一位置插了新函数/新初始化）。

```bash
git diff --name-only --diff-filter=U                  # 列出冲突文件
%PY% -I -B scripts/sync/resolve-rebase-conflicts.py --dump gpu/vk/vk_menu.cpp
                                                       # 看两侧原文（JSON 转义）
%PY% -I -B scripts/sync/resolve-rebase-conflicts.py --check gpu/vk/vk_menu.cpp
                                                       # 是否都已登记且逐字节匹配
%PY% -I -B scripts/sync/resolve-rebase-conflicts.py gpu/vk/vk_menu.cpp
                                                       # 应用：两侧都保留，写回 LF
git add gpu/vk/vk_menu.cpp && git rebase --continue
```

**新冲突怎么处理**：脚本对任何未登记或内容变了的冲突一律中止、绝不写文件。确认它确实是「两处独立插入」后，把 `--dump` 打出的两侧原文追加进 `scripts/sync/known-conflicts.json` 的 `conflicts` 数组（中文用 `\uXXXX`），再跑一遍即可。若是「同一行的两种修改」，**必须人工判断取舍，不要塞进登记表**。

### ③ 漂移检测（每次必做）

```bash
%PY% -I -B scripts/sync/audit-menu-drift.py            # 人类可读报告
%PY% -I -B scripts/sync/audit-menu-drift.py --check    # 有未翻译新串就 exit 1（CI 门禁）
```

脚本抽取 `rows[]` 行名/help（含 C++ 相邻字面量拼接）与 `tx_*` 档位值，与 `menu_zh.json` 求差集；数字、按键 token、品牌名、printf 格式串由 `scripts/sync/drift-whitelist.json` 过滤。**它只报告，绝不机翻。**

### ④ 补译（唯一人工步骤，来源纪律）

- **译名只能来自游戏自带的官方简中资源**：`D:\games\NS2\Hookshot\menu-cn\translations.json`（romfs `CNzh.Product.110.sarc.zs` 与 `USen` 按 MSBT file+label 对齐提取），以及新版本 romfs 里对应的 `CNzh`。
- **不机翻、不凭空猜**。游戏里没有对应词的（如 Hookshot 自造的 UI 术语），保留英文或由人显式定名，并在表格备注。
- 品牌/协议名（HDR / DLSS / V-Sync / NVIDIA / Discord…）、纯数字档位、按键 token 一律不翻，放白名单。
- 写入 `gpu/vk/menu_zh.json` 的 `entries` 数组；ASCII 键、UTF-8 中文、值非空、无 identity（en==zh）、printf 占位符 en/zh 数量顺序一致（防 `snprintf` 读错 varargs）。
- 若上游改写了已有 help（不是新增），`--check` 会以旧 key 命中而漏报，需人工对照 `git diff` 检查变动过的 help 串。

### ⑤ 重生成表头 + 重编译

```bash
%PY% -I -B scripts/gen_menu_i18n.py --check           # 确认表头与表一致；改过表则先不带 --check 重生成
%PY% -I -B scripts/gen_menu_i18n.py
%PY% -I -B tests/test_menu_i18n.py                    # 6 项全绿
```

只重编 vk 层（约 46 秒，不碰 470 MB 的 `hookshot.exe`）：

```bash
export EMSDK="D:/games/NS2/HookshotBuilder-data/versions/v2.1.0/HookshotBuilder/tools/emsdk"
export HOOKSHOT_MINGW="D:/games/NS2/HookshotBuilder-data/versions/v2.1.0/HookshotBuilder/tools/llvm-mingw"
export RX_CPM="D:/games/NS2/HookshotBuilder-data/versions/v2.1.0/HookshotBuilder/tools/cpm"
export OOT_WORK="D:/games/NS2/Hookshot/build-fork-i18n3"
export RX_VK_ONLY=1
%PY% -I nx2/build.py --standalone --jobs 10
# 产物：build-fork-i18n3/sa/build/hookshot_vk.dll
```

> 三个 `export` 缺一不可：缺 `RX_CPM` 找不到 Vulkan/SPIR-V/VMA 头会直接退出；缺 `EMSDK`/`HOOKSHOT_MINGW` 用错编译器报头文件找不到。`RX_VK_ONLY=1` 把 jobs 换成 vk_jobs，跳过 guest 代码（否则全量要几小时）。`--standalone` 会顺带重链 exe，但**只取 DLL，绝不覆盖 `bin\hookshot.exe`**。

### ⑥ 装回并校验（碰游戏目录，改前先确认游戏已关）

```bash
%PY% D:\games\NS2\Hookshot\v2.1-review\switch-fork-i18n.py --check
%PY% D:\games\NS2\Hookshot\v2.1-review\switch-fork-i18n.py --apply --lang 1 --fork-dll "D:\games\NS2\Hookshot\build-fork-i18n3\sa\build\hookshot_vk.dll"
```

工具会：核对游戏进程未运行 → 备份当前 DLL/ini/env → 装新 fork DLL → 写**等价英文键 ini**（fork 的 `ini_key()` 只认英文行名）→ 逐 SHA 校验。当前本地常驻态：`hookshot_vk.dll` = `d2c7f248…`（stage2）、96 个纯英文键 ini、`language=1`（中文启动）、env 三项原样（`HOS_ASPECT=2.3889` / `HOS_OOT_CHEATS=1` / `HOS_INPUT_LIVE=cheat-live.txt`）。

## 关键不变量（改代码时别破坏）

1. **英文原文是逻辑键**：`ini_key()`、`is_motion_row()`、`cheat_value()=="On"` 等比较的是运行时英文串，绝不能让翻译污染它们。draw-time `tr()` 保证这一点，所以旧二进制汉化那套「开关高亮恒停关」的回归**结构上不可能重现**。
2. **romfs 字体一个字节都不动**：把 CFF/TrueType 字体塞进 `.bfotf` 会让 guest 字体库崩溃（`hookshot.exe+0x83a055c`，已多次实证）。正解是 host 侧回退到游戏自带的 `.bfttf`：`vk_game_font.h` 依次试 `.bfotf.zs`→`.bfttf.zs`，`vk_imgui.cpp` 用 `FZYASHJW_Zhun`/`FZYASHJW_Cu`（含 Latin，英文照显）。上游这两文件历次 0 改动，冲突风险极低。
3. **宽度缓存按语言失效**：`name_w`/`card_w` 缓存除 scale 外还要带 `menu_i18n::language` 维度，否则中英切换后字形宽度变会裁字（中文「共 4 页」比英文「Page 1 of 5」窄，同屏多塞项）。
4. **printf 格式串成对翻译**：`Quantity %d`→`数量 %d` 等，`%d` 数量顺序必须一致。

## Builder 升级后如何恢复中文（每次 Update 必做）

官方 `HookshotBuilder.exe` 的 Update 会**成套覆盖 `hookshot.exe` + `hookshot_vk.dll`**，fork 的 Language tab 与源码中文会丢。恢复流程：

1. 确认新版本对应的上游 tag/commit，把 `chore/upstream-sync` rebase 到它（走上面六步）。
2. 用新版本工具链重编 vk 层（工具链路径里的 `v2.1.0` 换成新版本目录）。
3. `switch-fork-i18n.py --fork-dll <新DLL>` 重铺，体检 SHA / 字体回退串 / 新译文串。
4. 真机进游戏确认。

> 这一步「把中文 DLL 换回去」是当前路线**唯一无法消除的机械动作**，因为 Builder 是官方 closed box、构建用的是官方源码，不会用本 fork。它已从旧方案的「重算偏移打补丁」降级为「换一个已编好的文件」，可脚本化、可代劳。

## 何时能真正零追加操作

若上游接受 i18n（issue #2303 → PR），中文进入官方源码，Builder 下载的 DLL 自带中文，上表「恢复中文」整段都不需要——**这才是彻底零操作的唯一路径**。因此：

- **短期**：本 SOP + 工具保底，fork 自维护。
- **并行**：盯 issue #2303；作者一旦松口，从 `chore/upstream-sync` 把 `feat(menu):` 那个提交 cherry-pick 到从 `main` 新切的 PR 分支（工具与 SOP 不进 PR），按 CONTRIBUTING 要求（英文、Conventional Commit、附中英截图、CI 通过、AI 辅助署名）提上去。`feat/menu-i18n` 不参与 rebase，始终保持这个可分发的干净提交。

## 回滚

- DLL 异常：用 `switch-fork-i18n.py --restore` 从 `v2.1-review\fork-preview-backup\` 逐 SHA 还原；或游戏目录同目录的 `.swap-<stamp>` / `.bak-v210-*` 备份。
- rebase 出错：`git rebase --abort` 回到 rebase 前；工具全程对游戏目录只写一次、写前备份。
- 想彻底切回官方原版：用 `v2.1-review\switch-state.py --state vanilla`（旧二进制路线的体检/切换工具，仅回退时用）。

## 如何约定「跟进上游更新」（触发词）

用户只要说下面任意一种，即视为下发一次完整同步任务，助手直接按本 SOP 执行，不必再逐条确认：

- 「Hookshot 更新到 X.Y.Z 了，请跟进」
- 「跟进 Hookshot 上游」/「官方出新版本，同步中文」

一次跟进 = 以下整条链（每个写动作前备份、验证）：

1. `git fetch upstream`，确认上游新 tag/commit。
2. 在 `chore/upstream-sync` 跑 `scripts/sync/sync-upstream.py --apply`（rebase + 已知冲突自动解 + 门禁）。
3. 跑 `scripts/sync/audit-menu-drift.py`，把新出现的待译串从**官方 zh-Hans 资源**补进 `gpu/vk/menu_zh.json`（不机翻、不猜）。
4. `scripts/gen_menu_i18n.py` 重生成 `menu_i18n.h`；`RX_VK_ONLY=1` 重编 vk 层得到中文 `hookshot_vk.dll`。
5. **先请用户关闭游戏**，再用 `switch-fork-i18n.py --apply --lang 1 --fork-dll <新DLL>` 装回并体检。
6. 打 `X.Y.Z-CN` tag + 发布 Release（见下节），把该版中文 DLL 补丁包换上去。

两个前置条件需用户配合：**关游戏**（换 DLL 前）、**一次 gh 登录**（仅建 issue/PR 用；tag/Release 可用 Git 凭据里的 token 走 API，无需 gh）。

## 每次跟进要同步发一个 `X.Y.Z-CN` Release

若上游原作者仍不纳入多语言（issue #2303 无回应），每合并一个官方版本 X.Y.Z，就在本 fork 发一个同号 `X.Y.Z-CN`：

- **tag**：annotated tag，指向该版中文 i18n 源码所在的 commit（`feat/menu-i18n` 对应提交）。例如 `v2.1.0-CN` → `9e8c389`（上游 `v2.1.0` `8ef8461` + `feat(menu)`）。
- **Release**：非 draft、非 prerelease、标为 **Latest**；正文给「下载哪个 / 中文菜单用法 / 加速更新 / 注意事项」。
- **资产 = 官方 7 项照搬 + CN 3 项 = 网页共 10 项**（GitHub 自动的 `Source code` zip/tar.gz 2 项 + 上传 8 项）：
  - 官方 7 项照搬原样：`HookshotBuilder.exe`、`Hookshot-vX.Y.Z-Windows-x64.zip`、`hookshot-setup.txt`、`hookshot-linux.txt`、`SHA256SUMS`，及 GitHub 自动生成的 `Source code` zip/tar.gz。
  - CN 差异 3 项：
    1. **中文 DLL 补丁** `Hookshot-X.Y.Z-CN-DLL-Patch.zip`（`hookshot_vk.dll` + `INSTALL.txt`）。官方 release 本就不含 470 MB 游戏 exe（Builder 在玩家本机从游戏文件构建），能分发的中文载体只有①源码 tag（draw-time i18n）②这个 DLL 补丁；它是「追加中文支持的可执行文件」。
    2. **预更新面板** `Hookshot-CN-PrepUpdate.zip`（本地下载加速工具，见下节；Windows-only）。
    3. `README-X.Y.Z-CN.md`（完整中文说明，随资产上传）。
  - **不镜像 231 MB 的 Linux AppImage**：它不含中文、且是 Linux 构建器，硬塞进 CN Release 只会误导；在正文给官方直达链接。
- **SHA256SUMS** 重算，覆盖本 Release **实际上传的 8 项**（不含自身、不含 GitHub 自动源码归档）。新增/替换资产后必须重跑 `make-release-meta.py`。
- 发布脚本与产物在 `D:\games\NS2\Hookshot\v2.1-review\release-assets\`（`make-patch.py` / `make-prepupdate-zip.py` / `make-release-meta.py` / `gh-release.py`），token 从 Git 凭据运行时读取、不落盘。GitHub 不允许覆盖同名资产：更新 `SHA256SUMS` 要先 DELETE 旧资产再上传新的（`gh-release.py` 的 `upload` 只跳过同名重复，不覆盖）。

## Update 下载加速：为什么不改官方链路，继续用网页工具

`v2.1-review` 那个带 Update 按钮的启动器，下载慢的 ~500 MB 组件来自 `hookshot-setup.txt` 里写死的 `github.com/NotRexed/Hookshot/releases/download/builder-parts/*.zip`（外加 `mstorsjo/llvm-mingw`）。下载 URL 写死在官方源码里，且每个 part 带 GitHub API 的 SHA-256 摘要校验，改任何字节都会校验失败。

改源重编译可行但**不采用**，原因：

1. 要改 `builder/HookshotBuilder/Updater.cs`、`host/hos_update.c`、`builder/setup/setup.c` 等多处写死的 `NotRexed/Hookshot`，并重编译分发 `HookshotBuilder.exe`；
2. 官方每次改这块代码，本 fork 的 rebase 就得重打补丁，维护成本远超收益；
3. 更致命：得先有一个长期稳定、真能加速的第三方 GitHub 镜像域名——不可控的外部依赖，不能替用户拍板。

**既定方案**：继续用预更新面板。它读官方清单算出差集，把 `<sha256>.zip` 预下载进官方 `downloads` 目录；`setup.c` 发现文件已存在且 SHA-256 匹配就整段跳过下载（`setup.c:637`），于是 Update 按钮秒过。零改源码、零 rebase 冲突，且 `app`/`dotnet` 等无独立目录的 part 每次仍需重下这点也已写明。若用户日后指定了一个稳定加速域名，再按本节评估是否做代码级改源。

**工具已通用化并开源（2026-11-11）**，现在有两个来源，逻辑同源：

| 来源 | 路径 | 面向 |
|---|---|---|
| 维护者本机版 | `D:\games\NS2\Hookshot\prep-update\`（旧路径，写死本机，仍可用） | 高工本地 |
| **对外发布版** | fork `main` 分支 `tools/prep-update/`，随每个 `X.Y.Z-CN` Release 打包为 `Hookshot-CN-PrepUpdate.zip` | 所有 CN 版用户 |

对外版消除了所有硬编码路径（数据根按 `HOOKSHOT_BUILDER_DIR` 环境变量 > `config.json` > 自动探测 `%LOCALAPPDATA%\HookshotBuilder` 定位；`GAME` 探测不到只影响版本显示、不影响差集），`start.bat` 用 `%~dp0` 相对定位，`curl` 的 `--ssl-no-revoke` 只在 Windows 加。**仅支持 Windows**：官方 `hookshot-linux.txt` 是 `part <sha> <size> <dest> <url>` + `tree`/`file` 指令（无 `frm/to` 字段），格式不同，`parse_setup()` 检测到会硬报错，绝不静默算出 0 个 part。

- 源码改动只落 `main` 分支，**不进 `feat/menu-i18n`**（PR 源保持纯净），也不影响上游 rebase。
- 打包脚本 `make-prepupdate-zip.py` 用白名单，排除 `prep-update.log` / `last-check.json` / `config.json` / `__pycache__` 等运行时与个人产物。
- 面板核心（差集/复用/下载/校验）与本机版逐项一致：本机 `--selftest` 实测官方清单 8 part、升级前差集 4 需下载 / 4 复用 / 147.8 MB；内存注入第 9 个新 part 自动归入 `need`、`need+1`，证明上游新增组件会被准确识别。
