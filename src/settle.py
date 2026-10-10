#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""结算器 —— 扫描练习笔记，判定并回写。纯标准库，不调模型。

  python settle.py                    # 扫所有账户
  python settle.py --account jason    # 扫一个账户
  python settle.py --account jason --file <note.md>   # 只结算指定笔记
  python settle.py --dry-run          # 只看会做什么，不改任何文件

题号→词的映射来自隐藏密钥文件 `.keys/<date>.txt`（题面里不含词）。
密钥缺失时**拒绝结算并报错**，绝不猜测。

交卷规则（**2026-09-21 改**，见 ADR-0006）：
  答了就判，未答的留待下次。全部判完才把 status 翻成「已批改」，否则「部分批改」。
  旧规则是「任一空白即整份跳过」—— 那会让"答了 7 题、留 3 题空白"的 7 题永远拿不到分，
  状态冻结、天天出同一批词（2026-09-12~21 实际踩过这个坑）。
  已判过的题以成绩单的题号为账，不重复判 → 可反复扫描，幂等。
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review as R  # noqa: E402

PRACTICE_SUBDIR = "复习/练习"
JUDGED_PREFIX = "#judged="          # 已判账本（写在密钥文件里，见 load_judged）
# 统一用 [ \t]* 而不是 \s*：\s 会跨行，冒号后面的 \s* 会把下一行（成绩单的 `---`）
# 当成作答内容。2026-09-21 实测踩过 —— 部分结算时第 10 题会被凭空判错。
BLOCK_RE = re.compile(r"^##[ \t]*(\d+)\.[ \t]*(.*?)[ \t]*$", re.M)
ANS_RE = re.compile(r"^答[ \t]*[:：][ \t]*(.*)$", re.M)
REPORT_HEAD = "## 成绩单"
ROW_RE = re.compile(r"^\|[ \t]*(\d+)[ \t]*\|")
ROW_WORD_RE = re.compile(r"^\|[ \t]*\d+[ \t]*\|[ \t]*([^|]+?)[ \t]*\|")
LINK_RE = re.compile(r"^\[\[(.+)\]\]$")


def link(word: str) -> str:
    """词 → Obsidian 双链 [[Word]]（ADR-0008）。词条与练习笔记在同一 vault，
    [[Word]] 按文件名直接解析，无需路径。
    已是 [[X]] 形态的原样返回：render_report 重建错词行时会从旧成绩单行里取词，
    旧行的词列已是双链 → 再包一次就成了 [[[X]]]，重扫就不幂等了。"""
    w = word.strip()
    return w if LINK_RE.fullmatch(w) else f"[[{w}]]"


def parse_note(path: Path) -> dict:
    """解析练习笔记 → {front, questions:[{no, answer}], text, path}。题面不含词。"""
    text = path.read_text(encoding="utf-8")
    fm = R.parse_front(text)
    m = R.FM_RE.match(text)
    body = text[m.end():] if m else text

    marks = list(BLOCK_RE.finditer(body))
    qs = []
    for i, mk in enumerate(marks):
        start = mk.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        am = ANS_RE.search(body[start:end])
        qs.append({"no": int(mk.group(1)), "answer": (am.group(1).strip() if am else "")})
    return {"front": fm, "questions": qs, "text": text, "path": path}


def key_path(note: Path) -> Path:
    """密钥在**项目根**、交付成果之外 —— 交付物要上云，密钥不能跟着走。"""
    return Path(__file__).resolve().parent.parent / ".keys" / note.parent.parent.parent.name \
        / f"{note.stem}.txt"


def load_key(note: Path) -> dict:
    p = key_path(note)
    if not p.exists():
        return {}
    out: dict[int, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().isdigit() and v.strip():
            out[int(k.strip())] = v.strip()
    return out


def load_judged(note: Path) -> set[int]:
    """已判题号账本。

    记在**密钥文件**里，不是笔记里 —— 密钥在 vault 之外，用户改不到、也不会被
    坚果云同步冲突覆盖。笔记里的成绩单只作备份；两者取并集。
    为什么需要：手机打开笔记 → 电脑追加成绩单 → 手机保存旧版本，成绩单就没了；
    只认成绩单的话会把已判过的题再判一遍，stage 被重复推进。
    """
    p = key_path(note)
    if not p.exists():
        return set()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith(JUDGED_PREFIX):
            return {int(x) for x in line[len(JUDGED_PREFIX):].split(",") if x.strip().isdigit()}
    return set()


def save_judged(note: Path, nos: set[int]) -> None:
    p = key_path(note)
    lines = [l for l in p.read_text(encoding="utf-8").splitlines()
             if not l.startswith(JUDGED_PREFIX)]
    lines.append(JUDGED_PREFIX + ",".join(str(n) for n in sorted(nos)))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_report(text: str) -> tuple[list[str], set[int]]:
    """从笔记里读回已有成绩单 → (表格行原文, 已判题号集合)。"""
    i = text.find(REPORT_HEAD)
    if i < 0:
        return [], set()
    rows: list[str] = []
    nos: set[int] = set()
    for line in text[i:].splitlines():
        m = ROW_RE.match(line)
        if m:
            rows.append(line)
            nos.add(int(m.group(1)))
    return rows, nos


def render_report(old_rows: list[str], new_rows: list[str], when: dt.datetime,
                  total_q: int) -> str:
    """重建整份成绩单：老行原样保留 + 新行追加，汇总行重算。"""
    rows = old_rows + new_rows
    ok = sum(1 for r in rows if "✅" in r)
    bad = len(rows) - ok
    rate = (ok / len(rows) * 100) if rows else 0
    out = ["", "---", "", REPORT_HEAD, "",
           f"判定时间：{when:%Y-%m-%d %H:%M}　｜　对 {ok} / 错 {bad}　｜　正确率 {rate:.0f}%"
           f"　｜　已判 {len(rows)}/{total_q}", "",
           "| # | 词 | 你的作答 | 判定 | 阶段 | 下次复习 |",
           "|:-:|----|---------|:---:|:---:|---------|"]
    out += rows
    wrong = [link(ROW_WORD_RE.match(r).group(1))
             for r in rows if "❌" in r and ROW_WORD_RE.match(r)]
    out += ["", f"错词：{', '.join(wrong) if wrong else '无'}"
                + ("　（已降 2 阶，明天再到期）" if wrong else ""), ""]
    return "\n".join(out)


def grade(rows_in: list[dict], vdir: Path, dry_run: bool) -> list[dict]:
    """判定一批题 → 结果行。stage 回写只走 R.apply_result()。"""
    rows = []
    for q in rows_in:
        word = q["word"]
        row = {"no": q["no"], "word": word, "answer": q["answer"],
               "correct": R.judge(q["answer"], word) == "correct",
               "old_stage": 0, "stage": 0, "due": ""}
        cand = vdir / f"{word[:1].upper()}{word[1:]}.md"
        if not cand.exists():
            hit = [x for x in vdir.glob("*.md") if x.stem.lower() == word.lower()]
            cand = hit[0] if hit else None
        if cand is None:
            row["missing"] = True
            rows.append(row)
            continue
        if dry_run:
            fm = R.parse_front(cand.read_text(encoding="utf-8"))
            old = int(fm.get("stage") or 0)
            stage, _state, gap = R.next_schedule(old, row["correct"])
            row["old_stage"] = old
            row["stage"] = stage
            row["due"] = (R.today() + dt.timedelta(days=gap)).isoformat()
        else:
            res = R.apply_result(cand, row["correct"])
            row.update(old_stage=res["old_stage"], stage=res["stage"], due=res["due"])
        rows.append(row)
    return rows


def fmt_rows(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        mark = "✅" if r["correct"] else "❌"
        ans = (r["answer"] or "").replace("|", "｜").replace("\n", " ")
        if R.is_explicit_giveup(r["answer"]):
            ans = f"{ans}（显式放弃）"
        out.append(f"| {r['no']} | {link(r['word'])} | {ans} | {mark} | "
                   f"{r['old_stage']}→{r['stage']} | {r['due']} |")
    return out


def settle_one(path: Path, dry_run: bool = False) -> dict:
    note = parse_note(path)
    key = load_key(path)
    qs = note["questions"]

    if not qs or not key or any(q["no"] not in key for q in qs):
        return {"path": path, "state": "broken", "rows": []}

    old_rows, reported = parse_report(note["text"])
    judged = reported | load_judged(path)
    todo = [q for q in qs if q["no"] not in judged and not R.is_blank_answer(q["answer"])]
    if not todo:
        return {"path": path, "state": "done" if len(judged) >= len(qs) else "pending",
                "rows": [], "judged": len(judged), "total": len(qs)}

    account = str(note["front"].get("account") or "")
    vdir = R.account_dir(account) / "词库"
    rows = grade([{**q, "word": key[q["no"]]} for q in todo], vdir, dry_run)

    if not dry_run:
        base = note["text"]
        i = base.find(REPORT_HEAD)
        if i >= 0:
            cut = base.rfind("---", 0, i)
            base = base[:cut if cut >= 0 else i].rstrip() + "\n"
        path.write_text(base + render_report(old_rows, fmt_rows(rows), dt.datetime.now(), len(qs)),
                        encoding="utf-8")
        R.write_front(path, {"status": "已批改" if len(judged) + len(rows) >= len(qs)
                                       else "部分批改"})
        save_judged(path, judged | {r["no"] for r in rows})

    return {"path": path, "state": "settled", "rows": rows,
            "judged_before": len(judged), "total": len(qs)}


def scan(account: str, only: Path | None, dry_run: bool) -> tuple[int, int, int]:
    d = R.account_dir(account) / PRACTICE_SUBDIR
    if not d.exists():
        return (0, 0, 0)
    files = [only] if only else sorted(d.glob("*.md"))
    settled = pending = done = 0
    for p in files:
        r = settle_one(p, dry_run=dry_run)
        if r["state"] == "settled":
            settled += 1
            ok = sum(1 for x in r["rows"] if x["correct"])
            tag = "[预演] " if dry_run else ""
            allr = r["judged_before"] + len(r["rows"]) >= r["total"]
            print(f"{tag}结算 {r['path'].stem}：本轮判 {len(r['rows'])} 题，"
                  f"对 {ok} / 错 {len(r['rows']) - ok}"
                  f"　累计已判 {r['judged_before'] + len(r['rows'])}/{r['total']}"
                  f"　→ status={'已批改' if allr else '部分批改'}")
            for x in r["rows"]:
                if not x["correct"]:
                    print(f"    ✗ {x['word']}　你写的是「{x['answer']}」"
                          f"　阶段 {x['old_stage']}→{x['stage']}　下次 {x['due']}")
        elif r["state"] == "pending":
            pending += 1
            print(f"暂缓 {p.stem}：已判 {r['judged']}/{r['total']}，"
                  f"剩 {r['total'] - r['judged']} 题等你作答")
        elif r["state"] == "broken":
            print(f"⚠️  跳过 {p.stem}：缺少密钥文件或题号不全 → {key_path(p)}"
                  "（拒绝结算，不猜测）")
        else:
            done += 1
    return settled, pending, done


def main() -> None:
    p = argparse.ArgumentParser(description="vocab-app 结算器")
    p.add_argument("--account", default=None)
    p.add_argument("--file", default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    accounts = [a.account] if a.account else R.all_accounts()
    if a.file and not a.account:
        raise SystemExit("--file 必须与 --account 一起用")
    if not accounts:
        print("没有找到任何账户。")
        return

    total = [0, 0, 0]
    for acc in accounts:
        only = Path(a.file) if a.file else None
        s, pend, dn = scan(acc, only, a.dry_run)
        total = [total[0] + s, total[1] + pend, total[2] + dn]
    if total[0] == 0 and total[1] == 0:
        print(f"无事可做（已批改 {total[2]} 份）。")


if __name__ == "__main__":
    main()
