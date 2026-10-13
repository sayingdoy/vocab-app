#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""vocab-app 内核 —— 词条读写、间隔阶梯、判定、统计。纯标准库。

被 `practice.py`（出卷）与 `settle.py`（结算）import，也可独立使用：
  python review.py stat --account jason
  python review.py due  --account jason [--n 20]

数据契约见 AGENTS.md §三；记忆曲线规则见 §四；术语见 CONTEXT.md。
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

try:  # 中文 Windows 控制台默认 cp936，强制 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── 共享内核（LESSON-OS · learn_engine）——P2 接入 ─────────────────────────
# 消费方式：`vendor/LESSON-OS` 为 **git submodule**，按 semver 钉 commit（ADR-0003）。
# 内核**零外部依赖、只读**；本模块只**委托**判分/排期逻辑，绝不改动内核源码。
#   · 判分（`judge`）                → `learn_engine.grader.ExactMatchGrader`
#   · 排期（`next_schedule`）         → `learn_engine.scheduler.next_schedule`
#   · 结算计算（`apply_result`）       → `learn_engine.scheduler.apply_result`
#   · 日期解析（`as_date`）            → `learn_engine.vault.as_date`
#   · `is_due` / `parse_front` / `write_front` / `pick_words` **保留本地**：
#     is_due 有边界差异（内核对 int/bool due 直接比较会 TypeError，见其 docstring）；
#     parse_front 有类型强转差异（bool/int/引号剥离，内核版没有）；
#     write_front 保持 `SystemExit` 语义与 CRLF 行尾稳定（交付成果不在版本库，
#     行尾无 git 噪声，但不改变既有字节行为）；
#     pick_words 是 ADR-0010 蒙特卡洛定制的选词算法，与内核 WeightedPicker
#     语义不同（同 bb-prep 补位分支裁决②：内核保留自身语义，消费方不调用）。
#
# 内核路径按 `__file__` 解析（**不用 CWD**）：cron / 异目录调用也能找到。
_KERNEL_DIR = Path(__file__).resolve().parents[1] / "vendor" / "LESSON-OS"
if not _KERNEL_DIR.is_dir():
    raise ImportError(
        f"共享内核未就位：{_KERNEL_DIR}\n"
        "在 vocab-app 根目录执行：git submodule update --init --recursive"
    )
if str(_KERNEL_DIR) not in sys.path:
    sys.path.insert(0, str(_KERNEL_DIR))
import learn_engine as LE  # noqa: E402

# 判分原语实例化（ExactMatchGrader 无需注入策略 —— 归一化语义即 vocab-app 形态）
_GRADER_EXACT = LE.ExactMatchGrader()

LADDER = [1, 2, 4, 7, 15, 30, 60, 120]
MASTER_STAGE = len(LADDER) - 1
BASE = Path(__file__).resolve().parent.parent / "交付成果" / "accounts"
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

# 显式放弃的作答记号（归一化后比对）
WRONG_TOKENS = {"不会", "x", "-", "－", "×", "no"}


def today() -> dt.date:
    return dt.date.today()


# ── frontmatter 读写 ────────────────────────────────────────────────────────
def parse_front(text: str) -> dict:
    m = FM_RE.match(text)
    if not m:
        return {}
    data: dict = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            data[k] = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
        elif v.lower() in ("true", "false"):
            data[k] = v.lower() == "true"
        elif re.fullmatch(r"-?\d+", v):
            data[k] = int(v)
        else:
            data[k] = v.strip("\"'")
    return data


def write_front(path: Path, updates: dict) -> None:
    """只改 frontmatter 中已存在的键；不存在的键补到末尾。正文一字不动。"""
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        raise SystemExit(f"文件缺少 frontmatter: {path}")
    lines = m.group(1).splitlines()
    seen = set()
    for i, line in enumerate(lines):
        if ":" not in line or line.startswith(" "):
            continue
        key = line.split(":", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}: {updates[key]}"
            seen.add(key)
    for key, val in updates.items():
        if key not in seen:
            lines.append(f"{key}: {val}")
    path.write_text("---\n" + "\n".join(lines) + "\n---\n" + text[m.end():], encoding="utf-8")


def as_date(v) -> dt.date | None:
    """宽松解析日期 —— **委托内核** `learn_engine.vault.as_date`（P2，2026-10-13）。

    两侧全边界等价（None / "" / int / bool / 坏字符串 / ISO 日期 / 带时间串，
    29 例比对 0 差异）。
    """
    return LE.as_date(v)


# ── 词库 ────────────────────────────────────────────────────────────────────
def account_dir(account: str, create: bool = False) -> Path:
    d = BASE / account
    if create:
        (d / "词库").mkdir(parents=True, exist_ok=True)
        (d / "复习" / "练习").mkdir(parents=True, exist_ok=True)
    if not d.exists():
        raise SystemExit(f"账户不存在: {d}")
    return d


def all_accounts() -> list[str]:
    if not BASE.exists():
        return []
    return sorted(p.name for p in BASE.iterdir() if p.is_dir() and not p.name.startswith("_"))


def load_words(account: str) -> list[dict]:
    d = account_dir(account) / "词库"
    out = []
    for p in sorted(d.glob("*.md")):
        fm = parse_front(p.read_text(encoding="utf-8"))
        if not fm:
            continue
        fm["_path"] = p
        fm.setdefault("stage", 0)
        fm.setdefault("state", "new")
        fm.setdefault("correct", 0)
        fm.setdefault("wrong", 0)
        fm.setdefault("reviews", 0)
        fm.setdefault("lookups", 0)
        out.append(fm)
    return out


def is_due(w: dict, ref: dt.date) -> bool:
    """到期判定。**刻意不委托内核** `scheduler.is_due`（P2 裁决）：

    内核对非 str 非 None 的 due（int / bool）直接 `d <= ref` → **TypeError**；
    本实现经 `as_date` 归一（坏值 → None → 到期），旧行为不崩。
    该边界差异在 bb-prep 侧同样存在（其 `is_due` 亦保留本地）——
    属内核已知边界，不擅自改内核（改内核须走 audit-os + bump 流程）。
    """
    d = as_date(w.get("due"))
    return d is None or d <= ref


# ── 判定（确定性，模型不得参与）─────────────────────────────────────────────
def normalize(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def judge(raw: str, word: str) -> str:
    """返回 'correct' | 'wrong'。空作答由调用方在交卷判定阶段挡掉。

    P2（2026-10-13）：**委托内核** `learn_engine.grader.ExactMatchGrader`
    （内核 docstring 明列「vocab-app 风格」原语）。归一化语义逐字相同
    （57 词 × 17 种作答电池比对，0 差异）。
    """
    return _GRADER_EXACT.judge(raw, word)


def is_blank_answer(raw: str) -> bool:
    return normalize(raw) == ""


def is_explicit_giveup(raw: str) -> bool:
    return normalize(raw) in {normalize(t) for t in WRONG_TOKENS}


# ── 题型（答案形态统一为「英文词」，区别只在线索）───────────────────────────
def question_type(stage: int) -> tuple[str, str]:
    if stage <= 1:
        return "A", "义→词"
    if stage <= 4:
        return "B", "句→词"
    return "C", "画→词"


def clue_for(w: dict) -> tuple[str, str]:
    """返回 (线索文字, 题型标签)。

    线索随阶段「渐隐」（fading cues）—— 首次编码给足，之后逐步撤掉：
      A  stage 0–1   中文义 + 意象   （双线索，帮第一次记住）
      B  stage 2–4   中文金句        （换上句子线索）
      C  stage 5–7   原始画面        （只给画面，逼主动提取）
    """
    stage = int(w.get("stage") or 0)
    code, name = question_type(stage)
    meaning = str(w.get("meaning") or "").strip()
    image = str(w.get("image") or "").strip()
    quote = str(w.get("quote_zh") or "").strip()

    if code == "A":
        parts = []
        if meaning:
            parts.append(f"线索：{meaning}")
        if image:
            parts.append(f"意象：{image}")
        text = "\n".join(parts)
    elif code == "B":
        text = f"线索：{quote}" if quote else (f"线索：{meaning}" if meaning else "")
    else:
        text = f"线索：{image}" if image else (f"线索：{meaning}" if meaning else "")
    return (text or "（缺线索）"), f"{code} {name}"


# ── 间隔阶梯 ────────────────────────────────────────────────────────────────
def next_schedule(old_stage: int, correct: bool) -> tuple[int, str, int]:
    """纯函数：给定当前 stage 与判定结果 → (新 stage, 新 state, 间隔天数)。

    阶梯规则的**唯一事实来源** —— apply_result() 与结算器的预演都调它，
    规则只写一遍，预演才敢说"不写盘也能算准"。

    P2（2026-10-13）：**委托内核** `learn_engine.scheduler.next_schedule`，
    `LADDER` 由本模块**显式注入**（ADR-0001：bb-prep 与 vocab-app 阶梯不同）。
    逻辑与旧实现逐行等价（18 组 stage×结果全组合比对，0 差异）。
    """
    return LE.next_schedule(old_stage, correct, ladder=LADDER)


def apply_result(path: Path, correct: bool) -> dict:
    """按阶梯规则更新一个词的调度字段。这是唯一允许写这些字段的地方。

    P2（2026-10-13）：状态机计算**委托内核** `learn_engine.scheduler.apply_result`
    （与真源同源；`LADDER` 与 `today` 由本模块**显式注入** —— 阶梯两项目不同，
    ADR-0001；today 注入便于测试）。写盘仍走本模块 `write_front`
    （保持 `SystemExit` 语义与既有字节行为）；返回结构保持原样
    （含 `word` / `old_stage`，`settle.py` 依赖）。
    改前/改后经 114 组排期计算 + 3 个真实词条写盘**字节级**比对，0 差异。
    """
    w = parse_front(path.read_text(encoding="utf-8"))
    old = int(w.get("stage") or 0)
    upd = LE.apply_result(w, correct, ladder=LADDER, today=today())
    write_front(path, {
        "stage": upd["stage"], "state": upd["state"], "due": upd["due"],
        "reviews": upd["reviews"], "correct": upd["correct"], "wrong": upd["wrong"],
    })
    return {"word": w.get("word") or path.stem, "old_stage": old,
            "stage": upd["stage"], "state": upd["state"], "due": upd["due"]}


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_stat(a) -> None:
    words = load_words(a.account)
    if not words:
        print(f"账户 {a.account} 词库为空。")
        return
    t = today()
    dist: dict[str, int] = {}
    for w in words:
        dist[str(w.get("state"))] = dist.get(str(w.get("state")), 0) + 1
    tc = sum(int(w.get("correct") or 0) for w in words)
    tw = sum(int(w.get("wrong") or 0) for w in words)
    week_ago = t - dt.timedelta(days=7)
    new_week = [w for w in words if (as_date(w.get("first_seen")) or t) > week_ago]
    stuck = sorted(words, key=lambda w: (int(w.get("stage") or 0), -int(w.get("wrong") or 0)))[:5]

    print(f"账户 {a.account}  词库 {len(words)} 词")
    print(f"  今日到期      : {sum(1 for w in words if is_due(w, t))}")
    print("  掌握度分布    : " + " / ".join(f"{k} {v}" for k, v in sorted(dist.items())))
    rate = (tc / (tc + tw) * 100) if (tc + tw) else 0
    print(f"  累计正确率    : {rate:.0f}%  ({tc}/{tc + tw})")
    print(f"  本周新增      : {len(new_week)}")
    print("  卡最久（前5） :")
    for w in stuck:
        print(f"    - {w.get('word')}  stage={w.get('stage')} 错{int(w.get('wrong') or 0)}次 到期={w.get('due')}")


def cmd_due(a) -> None:
    words = [w for w in load_words(a.account) if is_due(w, today())]
    words.sort(key=lambda w: (as_date(w.get("due")) or dt.date.min, int(w.get("stage") or 0)))
    words = words[: a.n]
    if not words:
        print("今天没有到期词条。")
        return
    print(f"今日到期 {len(words)} 词：")
    for w in words:
        clue, tag = clue_for(w)
        print(f"  - {w.get('word')}  [{tag}]  (stage {w.get('stage')}, 到期 {w.get('due')})")


def main() -> None:
    p = argparse.ArgumentParser(description="vocab-app 内核")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("stat", "due"):
        sp = sub.add_parser(name)
        sp.add_argument("--account", required=True)
        sp.add_argument("--n", type=int, default=20)
        sp.set_defaults(func=globals()[f"cmd_{name}"])
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
