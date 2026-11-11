# Hookshot v2.1.0-CN — 简体中文菜单版

这是 Hookshot **v2.1.0** 的简体中文菜单发布，由 [`gx0803/Hookshot`](https://github.com/gx0803/Hookshot) 维护。

上游原作者 ([`NotRexed/Hookshot`](https://github.com/NotRexed/Hookshot)) 暂未纳入多语言支持（见 [issue #2303](https://github.com/NotRexed/Hookshot/issues/2303)），因此本 fork 以**源码级绘制时翻译（draw-time i18n）**的方式提供**可选**简体中文菜单，并随每次官方版本跟进发布 `X.Y.Z-CN`。

## 这个版本提供什么

- **可选**简体中文菜单：游戏内按 <kbd>F1</kbd> 打开菜单 → 最后一个「Language / 语言」标签页 → 在 `English` / `简体中文` 之间即时切换。
- 覆盖主菜单、Graphics / Gameplay / Controls / Debug 全部设置页，以及背包（Inventory）、传送（Teleport）二级子页，共 **520 条**译文。
- 译名取自游戏本体官方简体中文（romfs `CNzh` MSBT），**不机翻、不猜**；缺项手工补并按上游措辞沿用。
- 翻译只发生在绘制调用点，英文原文同时作为 ini 键与逻辑比较主语保持不变 ⇒ 不会破坏设置保存、金手指开关等底层逻辑。
- 中文菜单字体直接读取游戏自带的中文字体（`.bfttf`），**romfs 一字不改**，规避了替换 romfs 字体会导致 guest 字体系统崩溃的问题。

## 下载哪个

| 文件 | 用途 |
|---|---|
| `Hookshot-v2.1.0-CN-DLL-Patch.zip` | **已装好官方 v2.1.0 的玩家**：覆盖一个 DLL 即获得中文菜单（最简单，推荐的绝大多数人用这个）|
| `Source code (zip)` / `Source code (tar.gz)` | 想从源码自行构建中文版的开发者（tag = `v2.1.0-CN`，代码在 `feat/menu-i18n`）|
| `Hookshot-v2.1.0-Windows-x64.zip` + `HookshotBuilder.exe` | **全新安装**：官方原样启动器 / 构建器，从你自己的游戏文件构建；构建完成后再打上面的中文 DLL 补丁 |
| `hookshot-setup.txt` / `hookshot-linux.txt` / `SHA256SUMS` | 官方原样清单与校验和，用于全新安装时按需下载组件 |

**Linux 用户**：本 CN 版目前只提供 Windows 的 `hookshot_vk.dll` 补丁。官方 Linux AppImage（不含中文补丁）请从[上游 release](https://github.com/NotRexed/Hookshot/releases/latest) 下载；如需中文菜单，可按源码自行构建。

## 中文 DLL 补丁用法（Windows）

1. **完全退出游戏**。
2. 备份 `游戏目录/bin/hookshot_vk.dll` → `hookshot_vk.dll.bak`。
3. 把补丁包里的 `hookshot_vk.dll` 复制到 `游戏目录/bin/` 覆盖。
4. 启动游戏，F1 → 「Language / 语言」标签页 → 选「简体中文」；或在 `hookshot_options.ini` 加一行 `language=1`。
5. 中文菜单字体读取游戏自带 `.bfttf`，无需改 romfs。

详见补丁包内的 `INSTALL.txt`。

## 注意事项

- 本补丁**仅适用于官方 v2.1.0**。官方 Update 会用原版 DLL 覆盖它；升级后需重新下载对应的 `X.Y.Z-CN` 补丁（若上游尚未纳入多语言）。
- `hookshot_options.ini` 使用**英文键**（与官方原版一致）。若你此前用过把 ini 键改成中文的其它汉化 DLL，需先换回官方原版英文键 ini。
- 游戏内 Update 检查始终指向上游 `NotRexed/Hookshot`，**不会**自动更新到本 CN 版。
- 本 DLL 为 GPL-3.0 项目的衍生构建，完整源码见本 Release 的 `Source code` 资产，符合 GPL 的源码提供要求。
- Hookshot 为非官方粉丝项目，与 Nintendo 无关联；《塞尔达传说》《时之笛》为 Nintendo 商标。

## 相关链接

- 上游项目：https://github.com/NotRexed/Hookshot
- 多语言 issue：https://github.com/NotRexed/Hookshot/issues/2303
- 本 fork / 源码：https://github.com/gx0803/Hookshot
- 翻译表：`gpu/vk/menu_zh.json`（520 条），生成的 C++ 表：`gpu/vk/menu_i18n.h`
- 上游同步 SOP：`docs/upstream-sync.md`
