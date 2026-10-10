#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""外部 AI 会话自动导入词条 —— 纯标准库，零第三方依赖。

扫描源目录下的 `deepseek-word*.md`（SyncNos 插件同步的 DeepSeek **对话全文**），
把其中的新词条按现有规范结构写入 `交付成果/accounts/<account>/词库/<Word>.md`。

核心设计（见 docs/adr.md ADR-0011）：
- **只解析** `^## <n> assistant$` 块。You 块一律不解析 —— 文件里 `## 0 You`
  内嵌了解词规范与 Serendipity 示例，绝不能被当成词条导入。
- 块内第一条「以 `## ` 开头且含至少一个 /…/ 段」的行 = 词条头。
- 只新建文件，**绝不修改任何已存在文件**；已存在（按文件名 stem 或 word 字段）
  即跳过，一个字节都不动。
- 出生时按既有惯例写满 19 个调度键（stage/state/due/reviews/correct/wrong），
  此后这些字段只允许 review.py 的 apply_result() 写。

用法：
  python src/import_words.py --account jason [--dir <路径>] [--glob "deepseek-word*.md"]
                             [--dry-run] [--verbose]
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

# 默认源目录：SyncNos 同步的 DeepSeek 对话全文
DEFAULT_DIR = Path(r"D:\Jason's stuff\Obsidian\Jason\01_Inspire Inbox\97_SyncNos-AIChats")
DEFAULT_GLOB = "deepseek-word*.md"
ACCOUNTS = Path(__file__).resolve().parent.parent / "交付成果" / "accounts"

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
# 块头：行首 `## <数字> You|assistant`（独占一行）
BLOCK_RE = re.compile(r"(?m)^## (\d+) (You|assistant)[ \t]*$")
# 词条头里任一个 /…/ 段（IPA 不含斜杠，故 [^/]+ 安全）
SLASH_RE = re.compile(r"/([^/]+)/")
# 汉字（CJK 统一表意文字，含扩展 A）
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

FIELD_IMAGE = "**原始画面**："
FIELD_FORMULA = "**核心意象**："
FIELD_DEEP = "**深层解释**："


def norm(s: str) -> str:
    """去空白 + 小写。仅用于判断 queried_as 是否与词条名不同。"""
    return re.sub(r"\s+", "", (s or "").strip().lower())


def dedup_key(s: str) -> str:
    """去首尾空白 + 小写。用于「是否已在库」的比对（AGENTS.md §二.4）。"""
    return (s or "").strip().lower()


def cap(word: str) -> str:
    """首字母大写 + 其余原样（AGENTS.md §二.4）。不 lower 其余字母。"""
    return word[:1].upper() + word[1:]


def first_line(text: str) -> str:
    for line in text.split("\n"):
        if line.strip():
            return line.strip()
    return ""


# ── 源文件 frontmatter ──────────────────────────────────────────────────────
def source_url(text: str) -> str | None:
    """取源文件 frontmatter 的 `url:`（对话 URL）；取不到返回 None。"""
    m = FM_RE.match(text)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.startswith("url:"):
            v = line.split(":", 1)[1].strip()
            return v.strip("\"'") or None
    return None


# ── 块解析 ──────────────────────────────────────────────────────────────────
def split_blocks(text: str) -> list[tuple[int, str, str]]:
    """切成 [(编号, 'You'|'assistant', 块正文)]，按文件出现顺序。"""
    parts = BLOCK_RE.split(text)  # [pre, num, kind, body, num, kind, body, ...]
    out = []
    i = 1
    while i + 2 < len(parts):
        out.append((int(parts[i]), parts[i + 1], parts[i + 2]))
        i += 3
    return out


def parse_header(line: str) -> tuple[str, str, str] | None:
    """解析词条头行 → (word, ipa, meaning)；非词条头返回 None。

    容错（必须吃下 #57 的双音标行）：
      word    = `## ` 到第一个 `/` 之间的文本（strip）
      ipa     = 第一个 /…/ 段的内容
      meaning = 去掉所有 /…/ 段后、再去掉开头 word 的剩余文本（strip）
    """
    if not line.startswith("## "):
        return None
    rest = line[3:].strip()
    m = SLASH_RE.search(rest)
    if not m:
        return None
    word = rest[: rest.index("/")].strip()
    ipa = m.group(1).strip()
    if not word:
        return None
    no_ipa = SLASH_RE.sub("", rest)
    if no_ipa.startswith(word):
        meaning = no_ipa[len(word):].strip()
    else:
        meaning = no_ipa.strip()
    return word, ipa, meaning


def _field(lines: list[str], marker: str) -> str | None:
    for line in lines:
        if marker in line:
            v = line.split(marker, 1)[1].strip()
            return v or None
    return None


def _deep(lines: list[str]) -> str | None:
    """**深层解释**：到下一个 `>` 行（金句）或块尾，保留内部换行与分段。"""
    start = None
    for i, line in enumerate(lines):
        if FIELD_DEEP in line:
            start = i
            break
    if start is None:
        return None
    buf = [lines[start].split(FIELD_DEEP, 1)[1]]
    for line in lines[start + 1:]:
        if line.startswith(">"):
            break
        buf.append(line)
    return "\n".join(buf).strip() or None


def _quote(lines: list[str]) -> str | None:
    """第一行 `> "…"`，去掉 `> "` 与结尾 `"`。"""
    for line in lines:
        s = line.lstrip()
        if s.startswith('> "'):
            q = s[3:].rstrip()
            if q.endswith('"'):
                q = q[:-1]
            return q or None
    return None


def parse_assistant(content: str) -> dict | None:
    """解析一个 assistant 块。无词条头（噪声块）返回 None。

    返回 dict 字段：word/ipa/meaning/image/formula/deep/quote（缺字段为 None）。
    """
    lines = content.split("\n")
    header = None
    for line in lines:
        if line.startswith("## ") and SLASH_RE.search(line):
            header = parse_header(line)
            if header is not None:
                break
    if header is None:
        return None
    word, ipa, meaning = header
    return {
        "word": word,
        "ipa": ipa,
        "meaning": meaning,
        "image": _field(lines, FIELD_IMAGE),
        "formula": _field(lines, FIELD_FORMULA),
        "deep": _deep(lines),
        "quote": _quote(lines),
    }


def split_quote(quote: str) -> tuple[str, str]:
    """在第一个汉字处切分金句 → (quote_en, quote_zh)。

    必须按汉字切、不能按句号切：Spike 的英文句尾是 `.` 而非 `。`，
    按句号切会把整句双语塞进 quote_en。无汉字 → quote_en = 整句、quote_zh = ''。
    """
    m = CJK_RE.search(quote)
    if not m:
        return quote.strip(), ""
    en = quote[: m.start()].rstrip()
    zh = quote[m.start():].strip().strip("\"'「」“”").strip()
    return en, zh


# ── 词条文件构造 ────────────────────────────────────────────────────────────
def build_entry(e: dict, first_seen: str, source: str) -> str:
    """按现有 49 词的规范结构（逐行、含空行位置）拼出词条正文。

    用 `\\n` 拼装；落盘走 Path.write_text(..., encoding="utf-8")，与
    review.py 的 write_front 一致 —— Windows 下默认把 `\\n` 转成 CRLF。
    文件结尾不留换行（与既有词条逐字节一致）。
    """
    word, ipa, meaning = e["word"], e["ipa"], e["meaning"]
    image, formula, deep = e["image"], e["formula"], e["deep"]
    qe, qz = e["quote_en"], e["quote_zh"]
    qe_yaml = qe.replace("\\", "\\\\").replace('"', '\\"')
    quote_line = f'> "{qe} {qz}"' if qz else f'> "{qe}"'

    L = [
        "---",
        f"word: {word}",
        f"ipa: /{ipa}/",
        f"meaning: {meaning}",
    ]
    if e.get("queried_as"):
        L.append(f"queried_as: {e['queried_as']}")
    L += [
        f"image: {image}",
        f"formula: {formula}",
        f'quote_en: "{qe_yaml}"',
        f"quote_zh: {qz}",
        "roots: []",
        "themes: []",
        f"first_seen: {first_seen}",
        f"source: {source}",
        "lookups: 1",
        "stage: 0",
        "state: new",
        f"due: {first_seen}",
        "reviews: 0",
        "correct: 0",
        "wrong: 0",
        "---",
        "",
        f"## {word}  /{ipa}/  {meaning}",
        "",
        "### 核心语义",
        "",
        f"- **原始画面**：{image}",
        f"- **核心意象**：{formula}",
        "",
        "### 深层解释",
        "",
        deep,
        "",
        "### 一语道破",
        "",
        quote_line,
        "",
        "### 复习锚点",
        "",
        f"- 画面猜词题：**{image}** → {word}",
        f"- 金句填空题：「{qz}」",
        "",
        "<!-- 查询历史",
        f"{first_seen}  来源 DeepSeek 会话 {source}",
        "-->",
    ]
    return "\n".join(L)


def load_existing(lib: Path) -> set[str]:
    """已存在集合 = 全部文件名 stem（小写）∪ 全部 `word:` 字段（小写）。"""
    keys: set[str] = set()
    if not lib.is_dir():
        return keys
    for p in lib.glob("*.md"):
        if p.is_file():
            keys.add(dedup_key(p.stem))
            m = re.search(r"(?m)^word:[ \t]*(.+?)[ \t]*$", p.read_text(encoding="utf-8"))
            if m:
                keys.add(dedup_key(m.group(1).strip().strip("\"'")))
    return keys


# ── 主流程 ──────────────────────────────────────────────────────────────────
def run(account: str, src_dir: Path, pattern: str, dry_run: bool, verbose: bool) -> int:
    acc_dir = ACCOUNTS / account
    if not acc_dir.is_dir():
        print(f"警告：账户目录不存在，拒绝导入（不创建账户）：{acc_dir}")
        return 1
    lib = acc_dir / "词库"

    if not src_dir.is_dir():
        print(f"警告：源目录不存在：{src_dir}")
        return 0

    files = sorted(p for p in src_dir.glob(pattern) if p.is_file())
    if not files:
        return 0  # 目录存在但无匹配文件 → 静默

    existing = load_existing(lib)
    first_seen = dt.date.today().isoformat()
    seen: set[str] = set()
    new: list[tuple[int, dict, str]] = []

    for p in files:
        text = p.read_text(encoding="utf-8")
        src = source_url(text) or str(p)
        blocks = split_blocks(text)
        yous = {num: body.strip() for num, kind, body in blocks if kind == "You"}
        for num, kind, body in blocks:
            if kind != "assistant":
                continue
            e = parse_assistant(body)
            if e is None:
                if verbose:
                    print(f"无词条头（噪声）：#{num} {first_line(body)[:60]}")
                continue
            missing = [k for k in ("image", "formula", "deep", "quote") if not e.get(k)]
            if missing:
                print(f"跳过（字段不全）：#{num} {first_line(body)[:60]}")
                continue
            word = cap(e["word"])
            key = dedup_key(word)
            if key in existing or key in seen:
                continue
            en, zh = split_quote(e["quote"])
            if not zh:
                print(f"提示：#{num} {word} 金句无汉字，quote_zh 为空")
            you_raw = yous.get(num - 1, "")
            queried_as = you_raw if (you_raw and norm(you_raw) != norm(word)) else ""
            e.update(word=word, ipa=e["ipa"].strip("/"), quote_en=en, quote_zh=zh,
                     queried_as=queried_as)
            seen.add(key)
            new.append((num, e, src))

    if not new:
        return 0  # 无新词 → 无任何输出

    if dry_run:
        names = ", ".join(e["word"] for _, e, _ in new)
        print(f"[dry-run] 将导入 {len(new)} 个新词：{names}")
        return 0

    written: list[str] = []
    for num, e, src in new:
        path = lib / f"{e['word']}.md"
        if path.exists():  # 双保险：绝不覆盖已存在文件
            continue
        path.write_text(build_entry(e, first_seen, src), encoding="utf-8")
        written.append(e["word"])
        print(f"+ {e['word']}  ← #{num}  /{e['ipa']}/")
    if written:
        print(f"导入 {len(written)} 个新词：" + ", ".join(written))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="外部 AI 会话自动导入词条（纯标准库）")
    p.add_argument("--account", default="jason", help="账户名（默认 jason）")
    p.add_argument("--dir", default=str(DEFAULT_DIR), help="源目录（默认 SyncNos DeepSeek 对话目录）")
    p.add_argument("--glob", default=DEFAULT_GLOB, help='源文件名通配（默认 "deepseek-word*.md"）')
    p.add_argument("--dry-run", action="store_true", help="只报将导入的词，不写盘")
    p.add_argument("--verbose", action="store_true", help="列出无词条头的噪声块")
    a = p.parse_args()
    sys.exit(run(a.account, Path(a.dir), a.glob, a.dry_run, a.verbose))


if __name__ == "__main__":
    main()
