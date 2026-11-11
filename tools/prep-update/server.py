# -*- coding: utf-8 -*-
"""Hookshot Builder 预更新面板（本地服务，只监听 127.0.0.1）

做三件事，且只做这三件事：
  1. 只读本机版本：versions\\current.txt / previous.txt / 游戏 hookshot_version.txt
  2. 从官方 release 取 hookshot-setup.txt，严格按 setup.c 的 part_folder()/reuse_part()
     规则算出「官方会复用 / 已缓存 / 需要下载」的差集
  3. 把「需要下载」的 part 用并发 Range 分段下载 + SHA256 校验，原子放到
     downloads\\<sha256>.zip，让官方 setup.c:637 直接跳过下载

安全边界（重要）：
  只写 Builder 的 downloads 与 predl 目录，以及本目录下的 prep-update.log / last-check.json。
  绝不写游戏目录，绝不启动 Builder，绝不点 Update，绝不改 ini/env/DLL/exe。

路径配置（按优先级从高到低，均为可选项，开箱默认探测）：
  1. 环境变量 HOOKSHOT_BUILDER_DIR  -> HookshotBuilder 数据根目录（含 versions\\ 与 downloads\\）
  2. 本目录 config.json 的 builder_dir 字段
  3. 自动探测 %LOCALAPPDATA%\\HookshotBuilder（Windows）/ ~/.local/share/HookshotBuilder（Linux）
  游戏目录同理：HOOKSHOT_GAME_DIR / config.json 的 game_dir / 常见位置探测；
  探测不到只影响「游戏本体版本」的显示，不影响差集与预下载。

平台：仅支持 Windows 的 hookshot-setup.txt（官方 Linux 用 hookshot-linux.txt，
其 part 行是 <sha> <size> <dest> <url> + tree/file 指令、无 frm/to，格式不同）。
检测到非 Windows 清单会明确报错，不会静默算出 0 个 part。

用法：
  start.bat                             启动面板（Windows，自动选 Python 并开浏览器）
  python server.py                      启动面板
  python server.py --selftest           只自检，不下载、不起服务
  python server.py --selftest --builder-dir <path> --game-dir <path>   显式覆盖路径
"""
import concurrent.futures
import copy
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 中文 Windows 控制台是 cp936；start.bat 按 GBK 规则生成且不再加 -X utf8。
# 兜底：万一日志里混进该代码页编不出的字符，替换掉而不是抛 UnicodeEncodeError 中断服务。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, OSError, ValueError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "prep-update.log")
LAST = os.path.join(HERE, "last-check.json")
CONFIG = os.path.join(HERE, "config.json")

REPO = "NotRexed/Hookshot"
SETUP_ASSET = "hookshot-setup.txt"
# setup.c:308 part_folder() 的 own[]：只有这几类有独立文件夹，才写进 parts.txt 并可复用
OWN = ("python", "emsdk", "cpm", "third_party", "wgpu-native")
NSEG = 8
PORT0 = 8806


# ---------------------------------------------------------------- 路径探测
# 优先级：命令行 --xxx-dir > 环境变量 > config.json > 自动探测。
# 全部可选；探测失败不会中断，只会在面板里显示「未探测到」，并提示用户配置。

def _cli(name):
    """读 --builder-dir / --game-dir 这类命令行覆盖值。"""
    argv = sys.argv
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else ""


def _load_config():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_builder_dir(p):
    """Builder 数据根的标志：其下有 versions\\ 且能找到 current.txt。"""
    return bool(p) and os.path.isfile(os.path.join(p, "versions", "current.txt"))


def _is_game_dir(p):
    """游戏目录的标志：其下有 hookshot_version.txt。"""
    return bool(p) and os.path.isfile(os.path.join(p, "hookshot_version.txt"))


def _explicit_dir(kind):
    """返回第一个「显式指定」来源 (src, path)。命令行 > 环境变量 > config。
    显式来源一旦给出就锁定：即使校验失败也不回退到自动探测，避免「我明明指定了
    A，工具却偷偷用了 B」这种 dangerous 行为。"""
    cli = _cli("--%s-dir" % kind)
    if cli:
        return "命令行 --%s-dir" % kind, cli
    env = os.environ.get("HOOKSHOT_%s_DIR" % kind.upper(), "").strip()
    if env:
        return "环境变量 HOOKSHOT_%s_DIR" % kind.upper(), env
    cfg = _load_config().get("%s_dir" % kind, "")
    if cfg:
        return "config.json %s_dir" % kind, cfg
    return None


def _probe_builder_dir():
    local = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    explicit = _explicit_dir("builder")
    if explicit:
        src, p = explicit
        p = os.path.abspath(p)
        return p, src if _is_builder_dir(p) else src + "（未找到 current.txt，请核对路径）"
    # 无显式指定：走自动探测链（官方 setup 默认放 %LOCALAPPDATA%\HookshotBuilder）
    cands = []
    if local:
        cands.append(("自动探测 %LOCALAPPDATA%", os.path.join(local, "HookshotBuilder")))
    cands.append(("自动探测 Linux XDG", os.path.join(home, ".local", "share", "HookshotBuilder")))
    for src, p in cands:
        if _is_builder_dir(p):
            return os.path.abspath(p), src
    for src, p in cands:
        if p:
            return os.path.abspath(p), src + "（未找到 current.txt，请核对路径）"
    return os.path.join(local or home, "HookshotBuilder"), "默认（未探测到有效目录）"


def _probe_game_dir():
    explicit = _explicit_dir("game")
    if explicit:
        src, p = explicit
        p = os.path.abspath(p)
        return p, src if _is_game_dir(p) else src + "（未找到 hookshot_version.txt，仅影响版本显示）"
    home = os.path.expanduser("~")
    for src, p in (("常见位置 ~/Games/Hookshot", os.path.join(home, "Games", "Hookshot")),):
        if _is_game_dir(p):
            return os.path.abspath(p), src
    return "", "未探测到（只影响游戏版本显示，不影响预下载；可用 HOOKSHOT_GAME_DIR 指定）"


BASE, BASE_SRC = _probe_builder_dir()
GAME, GAME_SRC = _probe_game_dir()
VERSIONS = os.path.join(BASE, "versions")
DOWNLOADS = os.path.join(BASE, "downloads")
TMP = os.path.join(BASE, "predl")
# versions_real：若 BASE 是 junction/symlink（用户自建 D 盘数据本体），解析真实指向便于面板展示；
# 解析失败就退回 BASE 本身，绝不写死任何盘符。
try:
    VERSIONS_REAL = os.path.realpath(VERSIONS)
except OSError:
    VERSIONS_REAL = VERSIONS

LOCK = threading.RLock()
STATE = {
    "phase": "idle",          # idle / checking / ready / downloading / done / error
    "message": "",
    "plan": None,
    "stop": False,
    "log": [],
    "started_at": None,
    "finished_at": None,
}


# ---------------------------------------------------------------- 基础工具

def log(msg):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
    with LOCK:
        STATE["log"].append(line)
        if len(STATE["log"]) > 300:
            del STATE["log"][:-300]
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def read_small(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def curl(args, timeout=60):
    """统一走 curl：--noproxy 绕开残留代理；--ssl-no-revoke 只在 Windows schannel 下需要。"""
    cmd = ["curl", "-sS", "-L", "--noproxy", "*"]
    if platform.system() == "Windows":
        # schannel 吊销检查在部分网络会卡死；Linux/macOS 的 curl 无此选项，加了会报错。
        cmd.append("--ssl-no-revoke")
    cmd += ["-H", "User-Agent: hookshot-prep-update"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0, r.stdout, (r.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return False, b"", "curl 超时"
    except OSError as e:
        return False, b"", str(e)


def http_bytes(url, accept=None, timeout=40):
    args = ["--max-time", str(timeout)]
    if accept:
        args += ["-H", "Accept: " + accept]
    args.append(url)
    return curl(args, timeout + 10)


def vtuple(s):
    n = [int(x) for x in re.findall(r"\d+", s or "")]
    return tuple(n) if n else (0,)


def human(n):
    return "%.1f MB" % (n / 1048576.0) if n >= 1048576 else "%.1f KB" % (n / 1024.0)


# ---------------------------------------------------------------- 本机状态

def game_version():
    t = read_small(os.path.join(GAME, "hookshot_version.txt"))
    for ln in t.splitlines():
        if ln.startswith("version="):
            return ln.split("=", 1)[1].strip()
    return ""


def busy_processes():
    """只读探测编译/游戏进程是否在运行；非 Windows 无 tasklist 时安全降级为 False。"""
    found = {}
    try:
        if platform.system() == "Windows":
            r = subprocess.run(["tasklist", "/NH", "/FO", "CSV"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=15)
            out = r.stdout.lower()
            pairs = (("clang.exe", "clang"), ("clang-23.exe", "clang23"),
                     ("hookshotbuilder.exe", "builder"), ("hookshot.exe", "game"))
            for exe, key in pairs:
                found[key] = ('"%s"' % exe) in out
        else:
            r = subprocess.run(["ps", "-eo", "comm="], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=15)
            out = r.stdout.lower()
            for exe, key in (("clang", "clang"), ("hookshotbuilder", "builder"),
                             ("hookshot", "game")):
                found[key] = any(ln.strip() == exe for ln in out.splitlines())
            found["clang23"] = False
    except (OSError, subprocess.SubprocessError):
        out = ""
        for key in ("clang", "clang23", "builder", "game"):
            found[key] = False
    found["building"] = found.get("clang") or found.get("clang23")
    return found


def local_status():
    cached = []
    if os.path.isdir(DOWNLOADS):
        for n in os.listdir(DOWNLOADS):
            if n.lower().endswith(".zip"):
                p = os.path.join(DOWNLOADS, n)
                try:
                    cached.append({"name": n, "size": os.path.getsize(p)})
                except OSError:
                    pass
    return {
        "builder_current": read_small(os.path.join(VERSIONS, "current.txt")),
        "builder_previous": read_small(os.path.join(VERSIONS, "previous.txt")),
        "game_version": game_version(),
        "versions_path": VERSIONS,
        "versions_real": VERSIONS_REAL,
        "downloads_path": DOWNLOADS,
        "predl_path": TMP,
        "game_dir": GAME or "（未配置）",
        "builder_dir": BASE,
        "builder_src": BASE_SRC,
        "game_src": GAME_SRC,
        "config_path": CONFIG,
        "config_exists": os.path.isfile(CONFIG),
        "platform": platform.system(),
        "builder_found": _is_builder_dir(BASE),
        "downloads_exists": os.path.isdir(DOWNLOADS),
        "cached_zips": cached,
        "busy": busy_processes(),
        "phase": STATE["phase"],
        "message": STATE["message"],
    }


# ---------------------------------------------------------------- 官方清单

def release_json(tag):
    suffix = ("tags/" + tag) if tag else "latest"
    url = "https://api.github.com/repos/%s/releases/%s" % (REPO, suffix)
    ok, b, err = http_bytes(url, "application/vnd.github+json", 30)
    if not ok:
        raise RuntimeError("GitHub API 取 release 失败 (%s): %s" % (suffix, err[-200:]))
    try:
        return json.loads(b.decode("utf-8", "replace"))
    except ValueError:
        raise RuntimeError("GitHub API 返回的不是 JSON（可能被限流）")


def asset_of(rel, name):
    for a in rel.get("assets", []):
        if a.get("name") == name:
            return a
    return None


def fetch_setup_text(rel, tag):
    """照抄官方 setup.c：先 release 直连，失败则 API asset 的 octet-stream 通道。"""
    a = asset_of(rel, SETUP_ASSET)
    if not a:
        raise RuntimeError("release %s 里没有 %s" % (rel.get("tag_name"), SETUP_ASSET))

    # 通道 A：API asset（官方 api_link_for + octet-stream），本机实测最快且最稳
    ok, b, err = http_bytes(a.get("url", ""), "application/octet-stream", 40)
    if ok and b.startswith(b"tag "):
        log("清单来源: GitHub API asset (octet-stream)")
        return b.decode("utf-8", "replace")
    log("API asset 通道不可用，改走 release 直连: %s" % err[-120:])

    # 通道 B：browser_download_url
    bd = a.get("browser_download_url")
    if not bd:
        rtag = tag or rel.get("tag_name") or "latest"
        bd = "https://github.com/%s/releases/download/%s/%s" % (REPO, rtag, SETUP_ASSET)
    ok, b, err = http_bytes(bd, None, 40)
    if ok and b.startswith(b"tag "):
        log("清单来源: release 直连")
        return b.decode("utf-8", "replace")
    raise RuntimeError("两条通道都取不到 %s: %s" % (SETUP_ASSET, err[-200:]))


def parse_setup(txt):
    tag, run, parts = "", "", []
    part_lines = 0           # 见到多少个 part 指令行
    skipped_parts = 0        # 其中因字段格式不符被跳过的
    has_tree_file = False    # Linux 清单特有指令
    for raw in txt.splitlines():
        line = raw.strip()
        if line.startswith("tag "):
            tag = line[4:].split()[0]
        elif line.startswith("run "):
            run = line[4:].split()[0]
        elif line == "tree" or line.startswith("file ") or line.startswith("tree "):
            has_tree_file = True
        elif line.startswith("part "):
            part_lines += 1
            f = line.split()
            if len(f) < 7:
                # Windows 清单 part 恒为 7 段（part sha size dest frm to url）；
                # Linux 清单 part 只有 4~5 段（part sha size dest url）。
                skipped_parts += 1
                continue
            sha = f[1]
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                skipped_parts += 1
                continue
            try:
                size = int(f[2])
            except ValueError:
                skipped_parts += 1
                continue
            url = f[6]
            name = url.rsplit("/", 1)[-1].rsplit(".zip", 1)[0]
            name = re.sub(r"-[0-9a-f]{12}$", "", name)
            parts.append({
                "sha": sha, "size": size, "dest": f[3], "frm": f[4],
                "to": f[5], "url": url, "name": name,
            })
    # 平台硬校验：绝不能把「格式对不上」静默算成「0 个 part 要下载」，否则用户会
    # 误以为全部就绪。Linux 的 hookshot-linux.txt 明确拒绝并给出可操作提示。
    if has_tree_file or (part_lines and len(parts) == 0):
        raise RuntimeError(
            "这份清单不是 Windows 的 hookshot-setup.txt：检测到 tree/file 指令，"
            "或 %d 个 part 行全部不符合 Windows 的 7 段格式（官方 Linux 的 "
            "hookshot-linux.txt 是 4 段 + tree/file，格式不同）。本面板只支持 Windows "
            "构建器；请改用官方 Windows release 里的 hookshot-setup.txt。" % part_lines)
    if not tag or not parts:
        raise RuntimeError("hookshot-setup.txt 解析失败（缺 tag 或无可识别的 part）")
    return {"tag": tag, "run": run, "parts": parts}


def part_folder(p):
    """复刻 setup.c:302 part_folder()。返回相对目录；None = 该 part 永远不可复用。"""
    if p["to"] != "-" and p["dest"] != ".":
        return os.path.join(p["dest"].replace("/", "\\"), p["to"])
    base = p["url"].rsplit("/", 1)[-1]
    for n in OWN:
        if base.startswith("HookshotBuilder-%s-" % n):
            return os.path.join("HookshotBuilder", "tools", n)
    return None


def old_hashes(tag):
    t = read_small(os.path.join(VERSIONS, tag, "parts.txt"))
    return set(x.strip() for x in t.splitlines()
               if re.fullmatch(r"[0-9a-f]{64}", x.strip()))


def compute_plan(setup, current_tag, hashes=None, folder_tag=None):
    """严格按官方两条规则算差集：
       reuse_part(): part_folder() 非 None + sha 在旧 parts.txt + 旧目录存在 + 版本不同
       setup.c:637 : downloads\\<sha>.zip 存在且 SHA256 匹配则跳过下载

    folder_tag 只供 --selftest 模拟升级前场景使用：升级成功后旧版本的 tools\\*
    已被官方 MoveFileW 搬进新版本，旧目录只剩 parts.txt，不能再拿它做目录检查。
    """
    if hashes is None:
        hashes = old_hashes(current_tag)
    if folder_tag is None:
        folder_tag = current_tag
    out = []
    for p in setup["parts"]:
        rel = part_folder(p)
        reusable = rel is not None
        folder_ok = reusable and os.path.isdir(os.path.join(VERSIONS, folder_tag, rel))
        in_old = p["sha"] in hashes
        zp = os.path.join(DOWNLOADS, p["sha"] + ".zip")
        cached = False
        if os.path.isfile(zp):
            try:
                cached = sha256_file(zp) == p["sha"]
            except OSError:
                cached = False

        if setup["tag"] == current_tag:
            st = "noop"
            why = "本机已是该版本，官方不会触发更新，无需预下载"
        elif reusable and in_old and folder_ok:
            st = "reuse"
            why = "官方 reuse_part() 从 %s 直接 MoveFile 复用，不下载" % current_tag
        elif cached:
            st = "cached"
            why = "downloads 里已有且 SHA256 匹配，setup.c:637 会跳过下载"
        else:
            st = "need"
            if not reusable:
                why = "app/dotnet 类没有独立文件夹，官方每次更新都重下"
            elif not in_old:
                why = "hash 不在 %s\\parts.txt，需重下" % current_tag
            elif not folder_ok:
                why = "旧版本对应目录不存在，MoveFile 复用不了，需重下"
            else:
                why = "需重下"
        out.append(dict(p, state=st, reason=why, reusable=reusable,
                        folder=rel, bytes_done=0, error=""))

    need = [x for x in out if x["state"] == "need"]
    return {
        "tag": setup["tag"],
        "run": setup["run"],
        "current": current_tag,
        "has_update": vtuple(setup["tag"]) > vtuple(current_tag),
        "same_version": setup["tag"] == current_tag,
        "parts": out,
        "old_parts_count": len(hashes),
        "need_count": len(need),
        "need_bytes": sum(x["size"] for x in need),
        "reuse_count": sum(1 for x in out if x["state"] == "reuse"),
        "cached_count": sum(1 for x in out if x["state"] == "cached"),
        "skipped_count": sum(1 for x in out if x["state"] == "noop"),
        "total_bytes": sum(x["size"] for x in out),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "release_tag": None,
        "published_at": None,
        "asset_api": {},
    }


def do_check(tag=""):
    with LOCK:
        STATE["phase"] = "checking"
        STATE["message"] = "正在读取官方 release..."
    log("检测更新: %s" % (tag or "(latest stable)"))
    try:
        rel = release_json(tag)
        txt = fetch_setup_text(rel, tag)
        setup = parse_setup(txt)
        cur = read_small(os.path.join(VERSIONS, "current.txt"))
        plan = compute_plan(setup, cur)
        plan["release_tag"] = rel.get("tag_name")
        plan["published_at"] = rel.get("published_at")

        # 顺带缓存 builder-parts release 的 asset -> API url 映射，供下载失败时备用
        amap = {}
        try:
            pr = release_json("builder-parts")
            for a in pr.get("assets", []):
                amap[a.get("name", "")] = a.get("url", "")
        except Exception as e:
            log("builder-parts asset 映射不可用（不影响主流程）: %s" % e)
        plan["asset_api"] = {
            p["sha"]: amap.get(p["url"].rsplit("/", 1)[-1], "")
            for p in setup["parts"]
        }

        with LOCK:
            STATE["plan"] = plan
            STATE["phase"] = "ready"
            STATE["message"] = ("发现新版本 %s" % plan["tag"]) if plan["has_update"] else ("已是最新版本 %s" % plan["tag"])
        log("差集: 需下载 %d 个 / %s，可复用 %d 个，已缓存 %d 个（旧 parts.txt %d 行）" %
            (plan["need_count"], human(plan["need_bytes"]),
             plan["reuse_count"], plan["cached_count"], plan["old_parts_count"]))
        try:
            with open(LAST, "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in plan.items() if k != "asset_api"},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return plan
    except Exception as e:
        with LOCK:
            STATE["phase"] = "error"
            STATE["message"] = str(e)
        log("检测失败: %s" % e)
        raise


# ---------------------------------------------------------------- 预下载

def set_part(sha, **kw):
    with LOCK:
        plan = STATE.get("plan")
        if not plan:
            return
        for p in plan["parts"]:
            if p["sha"] == sha:
                p.update(kw)
                return


def seg_path(sha, i):
    return os.path.join(TMP, "%s.%d.seg" % (sha[:12], i))


def fetch_seg(job):
    url, a, b, path = job
    ok, _, err = curl(["-r", "%d-%d" % (a, b), "-o", path,
                       "--max-time", "240", "--retry", "3", "--retry-delay", "2", url], 300)
    if not ok:
        raise RuntimeError("curl %d-%d: %s" % (a, b, err[-200:]))
    if not os.path.isfile(path):
        raise RuntimeError("分段文件没写出: %s" % path)
    return path


def live_bytes(sha):
    total = 0
    for i in range(NSEG):
        try:
            total += os.path.getsize(seg_path(sha, i))
        except OSError:
            pass
    return total


def download_part(p, api_url=""):
    sha, size, name = p["sha"], p["size"], p["name"]
    set_part(sha, state="downloading", bytes_done=0, error="",
             reason="正在并发 %d 段下载" % NSEG)
    step = (size + NSEG - 1) // NSEG
    jobs, paths = [], []
    for i in range(NSEG):
        a, b = i * step, min(size - 1, (i + 1) * step - 1)
        if a >= size:
            break
        path = seg_path(sha, i)
        try:
            os.remove(path)
        except OSError:
            pass
        jobs.append((p["url"], a, b, path))
        paths.append(path)

    whole_ok = False
    whole = os.path.join(TMP, sha[:12] + ".whole")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            list(ex.map(fetch_seg, jobs))
    except Exception as e:
        log("  %s 分段失败（%s），改单连接整文件重试" % (name, e))
        url = api_url or p["url"]
        try:
            os.remove(whole)
        except OSError:
            pass
        ok, _, err = curl(["-o", whole, "--max-time", "600", "--retry", "2", url], 660)
        if not ok:
            raise RuntimeError("整文件重试也失败: %s" % err[-200:])
        whole_ok = True

    merged = whole if whole_ok else os.path.join(TMP, sha[:12] + ".zip")
    if not whole_ok:
        with open(merged, "wb") as w:
            for path in paths:
                with open(path, "rb") as f:
                    w.write(f.read())

    got = os.path.getsize(merged)
    digest = sha256_file(merged)
    if got != size or digest != sha:
        raise RuntimeError("校验失败: %d/%d B, sha=%s" % (got, size, digest[:16]))

    os.makedirs(DOWNLOADS, exist_ok=True)
    dst = os.path.join(DOWNLOADS, sha + ".zip")
    os.replace(merged, dst)          # 同卷原子移动，setup 不会看到半截 zip
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass
    if whole_ok:
        try:
            os.remove(whole)
        except OSError:
            pass
    set_part(sha, state="cached", bytes_done=size, error="",
             reason="已下载并校验，setup.c:637 会跳过下载")
    log("  %s OK  %d B  sha 匹配 -> %s" % (name, got, dst))


def download_worker(plan):
    os.makedirs(TMP, exist_ok=True)
    os.makedirs(DOWNLOADS, exist_ok=True)
    targets = [p for p in plan["parts"] if p["state"] == "need"]
    with LOCK:
        STATE["phase"] = "downloading"
        STATE["started_at"] = time.strftime("%H:%M:%S")
        STATE["finished_at"] = None
        STATE["message"] = "预下载 %d 个 part / %s" % (len(targets), human(sum(x["size"] for x in targets)))
    log("开始预下载 %d 个 part，共 %s" % (len(targets), human(sum(x["size"] for x in targets))))

    failed = 0
    for i, p in enumerate(targets, 1):
        if STATE["stop"]:
            log("收到停止请求，中断下载")
            break
        log("[%d/%d] %s  %s" % (i, len(targets), p["name"], human(p["size"])))
        try:
            download_part(p, plan.get("asset_api", {}).get(p["sha"], ""))
        except Exception as e:
            failed += 1
            set_part(p["sha"], state="error", error=str(e), reason=str(e))
            log("  失败: %s" % e)

    with LOCK:
        STATE["phase"] = "done" if failed == 0 and not STATE["stop"] else "error"
        STATE["finished_at"] = time.strftime("%H:%M:%S")
        done = sum(1 for p in STATE["plan"]["parts"] if p["state"] in ("cached", "reuse"))
        total = len(STATE["plan"]["parts"])
        STATE["message"] = ("全部就绪：%d/%d 个 part 已可跳过下载" % (done, total)) if failed == 0 \
            else ("%d 个 part 失败，可重试" % failed)
    log("预下载结束: %s" % STATE["message"])


def start_download():
    with LOCK:
        if STATE["phase"] == "downloading":
            return False, "已经在下载中"
        plan = STATE.get("plan")
        if not plan:
            return False, "请先点「检测版本更新」"
        if plan["need_count"] == 0:
            return False, "没有需要下载的部分（已是最新或全部已缓存）"
        STATE["stop"] = False
        t = threading.Thread(target=download_worker, args=(copy.deepcopy(plan),), daemon=True)
    # worker 里改的是 deepcopy，进度必须写回主线程持有的 plan：用 sha 定位 set_part
    t.start()
    return True, "已启动"


# ---------------------------------------------------------------- HTTP

def snapshot():
    with LOCK:
        plan = copy.deepcopy(STATE["plan"]) if STATE["plan"] else None
        if plan and STATE["phase"] == "downloading":
            for p in plan["parts"]:
                if p["state"] == "downloading":
                    p["bytes_done"] = min(live_bytes(p["sha"]), p["size"])
        need_total = sum(x["size"] for x in plan["parts"] if x["state"] == "need") if plan else 0
        # 进度按「本轮需要下载的字节」算；已缓存/复用的部分直接算完成
        all_bytes = sum(x["size"] for x in plan["parts"]) if plan else 0
        done_bytes = 0
        if plan:
            for p in plan["parts"]:
                if p["state"] in ("reuse", "cached", "noop"):
                    done_bytes += p["size"]
                elif p["state"] == "downloading":
                    done_bytes += p["bytes_done"]
        pct = (done_bytes * 100.0 / all_bytes) if all_bytes else 0
        finished = bool(plan) and not plan["same_version"] and \
            all(p["state"] in ("reuse", "cached") for p in plan["parts"])
        return {
            "phase": STATE["phase"],
            "message": STATE["message"],
            "plan": plan,
            "pct": round(pct, 1),
            "done_bytes": done_bytes,
            "all_bytes": all_bytes,
            "need_total_bytes": need_total,
            "finished": finished,
            "started_at": STATE["started_at"],
            "finished_at": STATE["finished_at"],
            "log": STATE["log"][-120:],
            "status": local_status(),
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                self._send(500, str(e).encode("utf-8"), "text/plain; charset=utf-8")
        elif path == "/api/status":
            self.json(local_status())
        elif path == "/api/progress":
            self.json(snapshot())
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            body = {}
        try:
            if path == "/api/check":
                do_check((body.get("tag") or "").strip())
                self.json({"ok": True})
            elif path == "/api/download":
                ok, msg = start_download()
                self.json({"ok": ok, "message": msg})
            elif path == "/api/cancel":
                with LOCK:
                    STATE["stop"] = True
                log("已请求停止（当前分段结束后生效）")
                self.json({"ok": True})
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as e:
            self.json({"ok": False, "error": str(e)}, 500)


def serve():
    port = PORT0
    srv = None
    for p in range(PORT0, PORT0 + 8):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        print("端口 %d~%d 都被占用，无法启动面板" % (PORT0, PORT0 + 7))
        return 1
    url = "http://127.0.0.1:%d/" % port
    print("=" * 70)
    print(" Hookshot Builder 预更新面板")
    print(" " + url)
    print(" 平台  : %s" % platform.system())
    print(" 数据根: %s  [%s]" % (BASE, BASE_SRC))
    print(" 只写  : %s" % DOWNLOADS)
    print(" 游戏  : %s  [%s]" % (GAME or "未配置（不影响预下载）", GAME_SRC))
    if not _is_builder_dir(BASE):
        print(" [!] 未找到 %s\\versions\\current.txt" % BASE)
        print("     请用 HOOKSHOT_BUILDER_DIR 环境变量，或在本目录建 config.json 指定 builder_dir。")
    print(" 关闭  : 在本窗口按 Ctrl+C")
    print("=" * 70)
    log("面板启动 %s" % url)
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("面板关闭")
    return 0


def selftest():
    st = local_status()
    print("=" * 78)
    print(" 自检（不下载、不写游戏目录、不起服务）")
    print("=" * 78)
    for k in ("builder_current", "builder_previous", "game_version",
              "versions_path", "versions_real", "downloads_path", "game_dir"):
        print("  %-20s %s" % (k, st[k]))
    print("  %-20s %s" % ("platform", st["platform"]))
    print("  %-20s %s" % ("builder_found", st["builder_found"]))
    print("  %-20s %s" % ("builder_src", st["builder_src"]))
    print("  %-20s %s" % ("game_src", st["game_src"]))
    print("  %-20s %s" % ("busy", st["busy"]))
    rel = release_json("")
    txt = fetch_setup_text(rel, "")
    setup = parse_setup(txt)
    print("\n  官方清单 tag=%s  release=%s  published=%s  parts=%d" %
          (setup["tag"], rel.get("tag_name"), rel.get("published_at"), len(setup["parts"])))
    for cur in (st["builder_current"], "v2.0.14"):
        sim = cur == "v2.0.14"
        # 模拟升级前：hash 取自 v2.0.14\parts.txt；目录结构借用当前完整工具链
        # （真实升级时旧 tools\* 已被 MoveFileW 搬走，旧目录只剩 parts.txt）
        plan = compute_plan(setup, cur,
                            folder_tag=(st["builder_current"] if sim else cur))
        print("\n  --- %s current=%s ---" % ("模拟升级前" if sim else "真实", cur))
        print("  has_update=%s  need=%d/%s  reuse=%d  cached=%d  old_parts=%d" %
              (plan["has_update"], plan["need_count"], human(plan["need_bytes"]),
               plan["reuse_count"], plan["cached_count"], plan["old_parts_count"]))
        for p in plan["parts"]:
            print("    %-34s %9s  %-8s %s" %
                  (p["name"], human(p["size"]), p["state"], p["reason"]))
    print("\n  自检完成：未下载任何文件。")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(serve())
