#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出卷 —— 生成「练习笔记」。纯标准库，不调模型。

  python practice.py new  --account jason [--n 10] [--new-n 10] [--n-max 15] [--date YYYY-MM-DD] [--seed N]
  python practice.py list --account jason
  python practice.py void --account jason          # 作废无作答的旧笔记（解出门控）

题面**不含词本身**（否则就白考了）；题号→词的映射写在项目根的隐藏密钥文件
`.keys/<账户>/<日期>.txt` —— 交付成果之外，不上云，Obsidian 也看不见。

门控（2026-09-21 改，见 ADR-0007）：若存在**完全没作答**的旧笔记，不出新卷。
旧机制是"作废无作答笔记 → 词回池 → 次日重出同一批"，实际效果是用户面对的
卷子题目不变却永远判不了（2026-09-12~21 踩过）。用户明确不想反复填同一张卷，
改为：不答就不再出，笔记留在 vault 里等你补答；决定跳过就 `void` 作废它。

练习笔记形态与交卷规则见 CONTEXT.md。
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review as R  # noqa: E402

PRACTICE_SUBDIR = "复习/练习"
# 密钥刻意放在**项目根**、交付成果之外 —— 交付物目录要上云（坚果云），
# 密钥若留在里面会被一起同步到云和手机，等于把答案送出去。
KEY_BASE = Path(__file__).resolve().parent.parent / ".keys"
# 作废笔记也放在交付成果之外：它不是交付物，不该跟着上云。
VOID_BASE = Path(__file__).resolve().parent.parent / ".voided"
# 注意用 [ \t]* 而非 \s* —— \s 会吃掉换行，空白题后面的 `## 2.` 会被误判成"有作答"
ANSWERED_RE = re.compile(r"^答[ \t]*[:：][ \t]*\S", re.M)


def practice_dir(account: str, create: bool = False) -> Path:
    return R.account_dir(account, create=create) / PRACTICE_SUBDIR


def key_path(day: dt.date, account: str) -> Path:
    return KEY_BASE / account / f"{day.isoformat()}.txt"


def has_answers(p: Path) -> bool:
    """笔记里有任何一行 `答：<非空>` 就算作答过（哪怕只答了一题）。"""
    return bool(ANSWERED_RE.search(p.read_text(encoding="utf-8")))


def void_empty_notes(account: str, day: dt.date) -> list[str]:
    """把 ≤ 今天、且**一题都没答**的练习笔记移出池子（附密钥一起）。

    只由 `practice.py void` 显式调用 —— 出卷不再自动作废（见 ADR-0007）。
    作废后这些词回到池子，下次出卷可能被重新抽到。
    移到 `.voided/`（交付成果之外）而不是直接删，留个可回退的尾巴。
    """
    d = practice_dir(account)
    if not d.exists():
        return []
    gone = []
    for p in sorted(d.glob("*.md")):
        if p.stem > day.isoformat() or has_answers(p):
            continue
        dst = VOID_BASE / account
        dst.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dst / p.name))
        kp = key_path(dt.date.fromisoformat(p.stem), account)
        if kp.exists():
            shutil.move(str(kp), str(dst / kp.name))
        gone.append(p.stem)
    return gone


def build_note(account: str, words: list[dict], day: dt.date) -> str:
    lines = [
        "---",
        "type: 练习",
        f"account: {account}",
        f"date: {day.isoformat()}",
        "status: 待作答",
        f"questions: {len(words)}",
        "---",
        "",
        f"# 练习 {day.isoformat()}",
        "",
        "> 每题在 `答：` 后面**同一行**写下那个英文词。",
        "> 想不起来就写「不会」—— **别留空**，留空我会以为你还没写完。",
        "> 答一题算一题：只答了一部分也会判分，剩下的明天补也行。",
        "> 写完同步回电脑，结算器会自己判定并在这份笔记末尾追加成绩单。",
        "",
    ]
    for i, w in enumerate(words, 1):
        clue, _ = R.clue_for(w)
        # 题面只放线索（clue_for 已按阶段算好，可含多行）。
        # 不放词本身、也不放音标 —— 短词的 IPA 基本等于拼写（/nest/、/peɪs/）。
        lines += [
            f"## {i}.",
            "",
            clue,
            "",
            "答：",
            "",
        ]
    return "\n".join(lines)


def find_unanswered(account: str, day: dt.date) -> list[str]:
    """≤ day 且一题都没答的笔记（不含今天）。用于出卷门控。"""
    d = practice_dir(account)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md")
                  if p.stem < day.isoformat() and not has_answers(p))


def pick_words(words: list[dict], n: int, new_cap: int, n_max: int,
               seed: int | None = None) -> tuple[list[dict], list[dict]]:
    """从到期词中选词：**新词填空 + 卷长自适应，新词无保底**（ADR-0010）。纯函数，可确定性测试。

    背景（ADR-0010）：ADR-0009 的「新词保底」方向本身是错的 —— 新词不是免费名额，
    它按间隔阶梯占用固定槽位：每引进 1 个 stage0 新词（间隔 1 天），此后每天固定吃掉
    1 个槽位，走完 8 级阶梯（1/2/4/7/15/30/60/120）一生共吃 8 个槽位。每天硬塞 3 个
    新词 = 永久 +3 槽/天负荷。蒙特卡洛（无限新词供给，400 天，85% 准确率）：
      固定保底 3 → 复习延迟均值 59.2 天、积压峰 1189；填空规则 → 延迟 0.32 天、积压峰 11。
    压力测试（准确率 45%，复习池长期饱和）：加「保底 1 个新词」→ 积压峰 4→497、
    延迟 0.05→16.2 天。故新词**绝不能有任何保底下限**。

    规则（精确算术，参数 base_n=n / new_cap / n_max）：
      nd       = 到期复习词数（非 new）              = len(rev_pool)
      nn       = min(new_cap, max(0, n - nd))        # 新词名额 = 复习占不满基准卷长时的空位，无下限
      plen     = min(n_max,   max(n, nd + 2))        # 卷长：复习多则加长（+2 缓冲，保证复习不被新词挤掉）
      take_rev = min(nd, plen - nn)
      take_new = min(nn, plen - take_rev, len(new_pool))

    **关键：`nn` 里用的是基准卷长 `n`，不是 `plen`。** 若改用 `(plen - nd)`，饱和期会
    复利式积累积压（模拟：积压峰 16 vs 4）。不要「优化」这一点。

    同池内顺序：due 升序，同一 due 内部随机（先 shuffle 再稳定排序）—— seed 可复现。
    返回 (picked, rest)：picked 是本次出卷的词，rest 是没被抽中的剩余到期词。
    """
    new_pool = [w for w in words if str(w.get("state") or "new") == "new"]
    rev_pool = [w for w in words if str(w.get("state") or "new") != "new"]

    rng = random.Random(seed)

    def ordered(pool: list[dict]) -> list[dict]:
        pool = pool[:]
        rng.shuffle(pool)                                  # 先随机
        pool.sort(key=lambda w: R.as_date(w.get("due")) or dt.date.min)  # 再稳定排序
        return pool

    new_pool, rev_pool = ordered(new_pool), ordered(rev_pool)

    nd = len(rev_pool)
    nn = min(new_cap, max(0, n - nd))                      # 用 n 而非 plen（ADR-0010，勿改）
    plen = min(n_max, max(n, nd + 2))
    take_rev = min(nd, plen - nn)
    take_new = min(nn, plen - take_rev, len(new_pool))     # 上限再受新词池量约束（池不足时少出，不报错）

    picked = rev_pool[:take_rev] + new_pool[:take_new]
    rest = rev_pool[take_rev:] + new_pool[take_new:]
    return picked, rest


def cmd_new(a) -> None:
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()

    # 门控（ADR-0007）：有旧卷子一题没答 → 不出新卷。
    # 不答的卷子留在原地，要么补答后结算，要么 `practice.py void` 明确作废。
    if not a.force:
        stuck = find_unanswered(a.account, day)
        if stuck:
            print(f"不出卷：有 {len(stuck)} 份旧卷子完全没作答（{', '.join(stuck)}）。")
            print("补答后等同步回来即可结算；确定不答了就运行：")
            print(f"  python src/practice.py void --account {a.account}")
            return

    path = practice_dir(a.account, create=True) / f"{day.isoformat()}.md"
    if path.exists():
        print(f"已存在，未覆盖：{path}")
        return

    words = [w for w in R.load_words(a.account) if R.is_due(w, day)]
    if not words:
        print(f"账户 {a.account} 在 {day.isoformat()} 没有到期词条，不出卷。")
        return
    # 选词（ADR-0010）：新词填空 + 卷长自适应，新词无保底 —— 见 pick_words()。
    # nd 只统计到期复习词（非 new）；nn 用基准卷长 a.n 算空位，不是用 plen（用了会复利式积累积压）。
    nd = sum(1 for w in words if str(w.get("state") or "new") != "new")
    plen = min(a.n_max, max(a.n, nd + 2))
    words, rest = pick_words(words, a.n, a.new_n, a.n_max, seed=a.seed)
    n_new = sum(1 for w in words if str(w.get("state") or "new") == "new")
    n_rev = len(words) - n_new

    path.write_text(build_note(a.account, words, day), encoding="utf-8")

    kp = key_path(day, a.account)
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_text("".join(f"{i}={w['word']}\n" for i, w in enumerate(words, 1)), encoding="utf-8")

    tags: dict[str, int] = {}
    for w in words:
        _, tag = R.clue_for(w)
        tags[tag] = tags.get(tag, 0) + 1
    print(f"练习笔记已生成：{path}")
    print(f"  题数 {len(words)}　来源分布 新词 {n_new} / 复习 {n_rev}"
          f"（上限 {a.new_n}，卷长 {plen}/{a.n_max}）")
    print(f"  题型分布 " + " / ".join(f"{k} {v}" for k, v in sorted(tags.items())))
    print(f"  密钥文件（别打开，Obsidian 也看不见）：{kp}")
    print(f"  剩余到期未出卷 {len(rest)} 词")


def cmd_list(a) -> None:
    d = practice_dir(a.account)
    if not d.exists():
        print("还没有任何练习笔记。")
        return
    rows = []
    for p in sorted(d.glob("*.md")):
        fm = R.parse_front(p.read_text(encoding="utf-8"))
        t = p.read_text(encoding="utf-8")
        rows.append((p.stem, str(fm.get("status", "?")), fm.get("questions", "?"),
                     len(re.findall(r"^答[ \t]*[:：][ \t]*\S", t, re.M))))
    if not rows:
        print("还没有任何练习笔记。")
        return
    print(f"账户 {a.account} 练习笔记 {len(rows)} 份：")
    for day, status, n, ans in rows:
        print(f"  {day}  {status}   {n} 题　已答 {ans}")


def cmd_void(a) -> None:
    """显式作废无作答的旧笔记（词回池），解出门控。"""
    day = dt.date.today()
    gone = void_empty_notes(a.account, day)
    if gone:
        print(f"作废 {len(gone)} 份无作答的笔记（词已回池）：{', '.join(gone)}")
    else:
        print("没有 0 作答的笔记，无需作废。")


def main() -> None:
    p = argparse.ArgumentParser(description="vocab-app 出卷")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("new")
    sp.add_argument("--account", required=True)
    sp.add_argument("--n", type=int, default=10,
                    help="基准卷长（默认 10，见 ADR-0010）")
    sp.add_argument("--new-n", type=int, default=10,
                    help="新词名额**上限，非保底**（默认 10；复习占满基准卷长时可为 0，见 ADR-0010）")
    sp.add_argument("--n-max", type=int, default=15,
                    help="卷长上限（默认 15，见 ADR-0010）")
    sp.add_argument("--date", default=None)
    sp.add_argument("--seed", type=int, default=None)
    sp.add_argument("--force", action="store_true",
                    help="跳过门控（有未作答旧卷也照出）")
    sp.set_defaults(func=cmd_new)

    sp2 = sub.add_parser("list")
    sp2.add_argument("--account", required=True)
    sp2.set_defaults(func=cmd_list)

    sp3 = sub.add_parser("void")
    sp3.add_argument("--account", required=True)
    sp3.set_defaults(func=cmd_void)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
