"""📚 本地史料检索层（BM25 · 纯标准库，零第三方依赖）。

为什么不上向量检索
------------------
本项目刻意保持「stdlib 优先、离线可测、不额外烧 Key」。
语料量级是「每场战役一份几百到几千字的卡片」，在这个规模上 BM25 的召回
已经够用；向量检索要引入 embedding 服务（又一个 Key、又一次网络往返、
又一次费用），收益与成本不成比例。

中文切词
--------
没有分词词典，用「字符二元组 + 单字」：
- 二元组负责准确率（"长平"、"粮道"、"白起"），不会因为新出现的人名地名而失配；
- 单字负责召回率（查询里的"围"能命中"被围"），权重由 IDF 自然压低。
英文与数字按词切，保证 "1944"、"z13" 这类查询可用。

降级原则
--------
本模块任何一环失败（目录不存在、文件读不了、语料为空、零命中）都返回空结果，
由调用方在界面上**明确告知**，绝不静默退化成「模型自由发挥」而让用户以为有史料支撑。
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path

# 语料目录：默认在仓库根目录的 corpus/，可用环境变量覆盖（便于容器挂载或指向共享盘）
_DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
CORPUS_DIR = Path(os.environ.get("GENTACTIX_CORPUS_DIR") or _DEFAULT_CORPUS_DIR)

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-z0-9]+")

# 索引缓存：键 = (目录, 文件指纹)，语料一改指纹就变，自动重建
_CACHE: dict = {}


# ==========================================
# ✂️ 切词
# ==========================================
def tokenize(text: str) -> list:
    """中文二元组 + 单字，英文/数字按词切。纯函数，便于单测。"""
    if not text:
        return []
    lowered = str(text).lower()
    tokens = []
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
        # 单字额外入列：提升"围""粮"这类单字查询的召回，权重由 IDF 自动压低
        tokens.extend(run)
    tokens.extend(_LATIN_WORD.findall(lowered))
    return tokens


# ==========================================
# 📄 语料解析
# ==========================================
def _should_index(path: Path) -> bool:
    """README 是格式说明，不是史料；下划线/点开头视为草稿。"""
    name = path.name
    if name.lower().startswith("readme"):
        return False
    return not (name.startswith("_") or name.startswith("."))


def _parse_markdown(path: Path):
    """按 `##` 切块。返回 (文档标题, [(小节标题, 正文), ...])。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, []

    lines = raw.splitlines()

    # 剥掉可选的 frontmatter（裁决器参数表）：`---` 包起来的是键值对，不是史料正文。
    # 不剥的话它会变成「概述」chunk 的开头一段 `contact_km: 3.0` —— 既是噪声，
    # 又会让「概述」这个块在检索时凭一串无意义 token 拿到不该有的分数。
    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                lines = lines[idx + 1:]
                break

    doc_title = path.stem
    body = []
    for line in lines:
        # 只认第一个一级标题作为来源名；`## ` 不会被 `# ` 误匹配（第二个字符是 '#'）
        if line.startswith("# ") and doc_title == path.stem:
            doc_title = line[2:].strip() or doc_title
            continue
        body.append(line)

    chunks = []
    cur_title, buf = "概述", []
    for line in body:
        if line.startswith("## "):
            text = "\n".join(buf).strip()
            if text:
                chunks.append((cur_title, text))
            cur_title, buf = line[3:].strip() or "概述", []
        else:
            buf.append(line)
    text = "\n".join(buf).strip()
    if text:
        chunks.append((cur_title, text))
    return doc_title, chunks


def _fingerprint(root: Path):
    """文件清单 + mtime + size。目录不存在返回 None。"""
    if not root.exists() or not root.is_dir():
        return None
    sig = []
    for path in sorted(root.glob("**/*.md")):
        if not _should_index(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        sig.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(sig)


def load_corpus(corpus_dir=None):
    """载入语料并返回 chunk 列表（带 mtime 缓存）。"""
    root = Path(corpus_dir) if corpus_dir else CORPUS_DIR
    fingerprint = _fingerprint(root)
    if fingerprint is None:
        return []

    key = (str(root.resolve()), fingerprint)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    chunks = []
    for path in sorted(root.glob("**/*.md")):
        if not _should_index(path):
            continue
        doc_title, sections = _parse_markdown(path)
        if doc_title is None:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        for section, text in sections:
            chunks.append({
                "source": rel,           # 相对路径，界面上直接显示给用户核对
                "doc_title": doc_title,
                "section": section,
                "text": text,
            })

    _CACHE.clear()                  # 语料一变就丢弃旧指纹，防止缓存无界增长
    _CACHE[key] = chunks
    return chunks


def corpus_stats(corpus_dir=None) -> dict:
    """界面上用来回答「到底有没有史料」。"""
    root = Path(corpus_dir) if corpus_dir else CORPUS_DIR
    chunks = load_corpus(root)
    files = {c["source"] for c in chunks}
    return {
        "dir": str(root),
        "exists": root.exists(),
        "files": len(files),
        "chunks": len(chunks),
        "chars": sum(len(c["text"]) for c in chunks),
    }


# ==========================================
# 🔍 BM25 检索
# ==========================================
class BM25Index:
    """标准 BM25（k1=1.5, b=0.75），实现不过二十行，够用。"""

    def __init__(self, chunks, k1: float = 1.5, b: float = 0.75,
                 title_bonus: float = 5.0):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self.title_bonus = title_bonus
        self.docs = []
        self.title_tokens = []
        doc_freq = Counter()
        for chunk in chunks:
            # 标题词重复一次 = 轻微加权，避免正文里顺带提到的词压过主题词
            tokens = tokenize(chunk["text"]) + tokenize(chunk["section"])
            tf = Counter(tokens)
            self.docs.append((tf, len(tokens)))
            self.title_tokens.append(set(tokenize(chunk["doc_title"])))
            for token in tf:
                doc_freq[token] += 1

        total = len(chunks) or 1
        self.avgdl = sum(dl for _, dl in self.docs) / total if self.docs else 0.0
        self.idf = {
            token: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for token, freq in doc_freq.items()
        }

    def search(self, query: str, top_k: int = 4, min_score: float = 1.0,
               relative_floor: float = 0.35, allowed_sources=None):
        """返回 [{score, source, section, doc_title, text}]，按分数降序。

        「强 token 门控」是这里最关键的一条，不是调参能替代的：
        中文单字（战 / 军 / 年）几乎在任何史料里都高频出现，只靠 IDF 压不住，
        实测「中途岛海战」会以 2.3 分误命中长平之战的段落 —— 把长平的史料
        注入到中途岛推演里，比没有史料更有害。
        因此要求每条结果**至少命中一个多字 token**（bigram 或英文词）；
        单字只作为排序微调（权重 0.35），不能单独构成命中。
        """
        query_tokens = tokenize(query)
        if not query_tokens or not self.docs:
            return []
        unique = set(query_tokens)
        strong = {token for token in unique if len(token) >= 2}
        if not strong:          # 纯单字查询（如「围」）不具备检索意义
            return []

        scored = []
        for idx, (tf, dl) in enumerate(self.docs):
            if allowed_sources is not None and self.chunks[idx]["source"] not in allowed_sources:
                continue
            score = 0.0
            strong_hit = False
            for token in unique:
                freq = tf.get(token)
                if not freq:
                    continue
                weight = 1.0 if token in strong else 0.35
                if weight == 1.0:
                    strong_hit = True
                idf = self.idf.get(token, 0.0)
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
                score += weight * idf * freq * (self.k1 + 1) / denom

            # 文档级标题加成：查询命中战役名时，整篇所有片段都获得同等加分。
            # 这一层不能用「把标题塞进每个片段」替代 —— 那样战役名会在全部片段里
            # 高频出现，IDF 被稀释到接近 0，反而丢掉召回（实测过，分数从 4.9 掉到 2.4）。
            # 加分只影响「是否被召回」，排序仍由正文 BM25 决定。
            title_hits = strong & self.title_tokens[idx]
            if title_hits:
                score += self.title_bonus * len(title_hits)
                strong_hit = True

            if strong_hit and score > 0:
                scored.append((score, idx))

        if not scored:
            return []
        scored.sort(key=lambda item: (-item[0], item[1]))

        # 双重门槛：绝对下限过滤弱命中，相对下限砍掉长尾（避免把顺带提一句的段落当史料注入）
        top = scored[0][0]
        floor = max(min_score, top * relative_floor)
        results = []
        for score, idx in scored[:max(top_k, 1)]:
            if score < floor:
                continue
            chunk = self.chunks[idx]
            results.append({
                "score": round(score, 3),
                "source": chunk["source"],
                "doc_title": chunk["doc_title"],
                "section": chunk["section"],
                "text": chunk["text"],
            })
        return results


def search(query: str, corpus_dir=None, top_k: int = 4, min_score: float = 1.0,
           battle=None, phase=None, simulation_mode=None):
    """一步到位：载入语料 → 建索引 → 检索。语料为空则返回 []。"""
    chunks = load_corpus(corpus_dir)
    if not chunks:
        return []
    # 先锁定系列和场景，再排名；跨场战果和不同年份的同名渡江不可混用。
    from . import adjudication_lib
    root = Path(corpus_dir) if corpus_dir else CORPUS_DIR
    spec = adjudication_lib.resolve(query, battle=battle, corpus_dir=root)
    selected = f"{spec.name}.md"
    allowed = None
    series = {"抗美援朝-": "抗美援朝/", "红军长征-": "红军长征/", "抗日战争-": "抗日战争/"}
    prefix = next((p for p in series if spec.name.startswith(p)), None)
    if prefix and any(c["source"] == selected for c in chunks):
        allowed = {c["source"] for c in chunks
                   if c["source"] == selected or c["source"].startswith(series[prefix])}
        query = f"{spec.name} {query}"
    if simulation_mode == "open":
        # Do not inject future timelines and aggregate outcomes into an open decision.
        chunks = [c for c in chunks if c["section"] not in
                  {"作战过程与指挥时间线", "作战过程要点", "伤亡、兵力快照与结果", "伤亡与结果", "资料与出处"}]
    index = BM25Index(chunks)
    if phase not in ("init", "step") or allowed is None:
        return index.search(query, top_k=top_k, min_score=min_score,
                            allowed_sources=allowed)
    # 初始化必须看见战役范围、指挥关系和证据限制；不能让链接目录挤掉它们。
    ranked = index.search(query, top_k=len(chunks), min_score=min_score,
                          allowed_sources=allowed)
    selected_chunks = {c["section"]: c for c in chunks if c["source"] == selected}
    if phase == 'init':
        required = ['时间与地点', '双方兵力与指挥', '争议与待考']
    else:
        # Historical families use different headings for the same essential constraints.
        required = [next((name for name in alternatives if name in selected_chunks), alternatives[0])
                    for alternatives in [('推演目标、关键决策与反事实', '对推演建模的关键约束'),
                                         ('争议与待考', '双方兵力与指挥'), ('兵种、装备与补给', '兵种与装备')]]
    if simulation_mode == 'replay' and phase == 'step':
        required.append('作战过程与指挥时间线' if '作战过程与指挥时间线' in selected_chunks else '作战过程要点')
    result = []
    for section in required:
        if section in selected_chunks:
            hit = next((h for h in ranked if h["source"] == selected and
                        h["section"] == section), None)
            result.append(dict(hit or dict(selected_chunks[section], score=0.0), required=True))
    seen = {(h["source"], h["section"]) for h in result}
    for hit in sorted(ranked, key=lambda h: h["section"] == "资料与出处"):
        key = (hit["source"], hit["section"])
        if key not in seen:
            result.append(hit)
            seen.add(key)
    return result[:max(top_k, 1)]


# ==========================================
# 🧾 注入 prompt 的史料摘要
# ==========================================
BRIEFING_HEADER = """【史料摘录 · 证据与适用范围】
以下内容来自本地登记来源的资料库。公开报道、回忆转述、日期异文和教学假设的证据等级不同，不等于均已核验原始档案。

硬性规则：
1. 保留条目来源、统计范围与证据限定；不同来源冲突时并列说明，不擅自裁定。数字、日期、人名、地名不得改写、四舍五入或补全；教学假设不得称为史实。
2. 史料明确标注「未载」「有争议」的内容，你必须在文字中保留这一限定，禁止编造成确数。
3. 史料未覆盖的细节可以合理推演，但不得伪造出处、不得虚构书名或章节。
4. 这些内容只能写进文字字段（situation_report / report / description / source_note），
   不得新增其他 JSON 字段。其中**出处、争议、未载的说明写进 source_note**，
   不要塞进正文——正文是战场讲解，读者在那里不想读到文献综述。"""


def _evidence_blocks(text):
    """完整段落、列表项或带表头的整行；绝不切开数值、范围或出处链接。"""
    blocks, paragraph, table = [], [], []

    def flush():
        if paragraph:
            blocks.append("\n".join(paragraph))
            paragraph.clear()

    for line in text.splitlines():
        if line.startswith("|"):
            flush()
            if len(table) < 2:
                table.append(line)
            else:
                blocks.append("\n".join(table + [line]))
        else:
            table.clear()
            if not line.strip():
                flush()
            elif re.match(r"(?:[-*] |\d+[.)] )", line):
                flush()
                paragraph.append(line)
            else:
                paragraph.append(line)
    flush()
    return blocks


def select_passages(results, max_chars=2400):
    """按轮分配预算，保证短的必要章节有机会入选。预算不含固定规则头。

    返回真正入选的内容供提示词和应用层工具共同使用；过长条目整体省略。
    """
    marker = "（受预算限制，仅列完整条目；省略内容不得自行补全。）"
    groups = [_evidence_blocks(h["text"]) for h in results]
    selected = [[] for _ in results]
    selected_indexes = [set() for _ in results]
    budget = max(0, max_chars)

    def take(i, j):
        nonlocal budget
        if j in selected_indexes[i]:
            return
        block = groups[i][j]
        overhead = 2
        if not selected[i]:
            head = f"\n[史料 {i + 1}] 来源：{results[i]['doc_title']} · {results[i]['section']}"
            overhead += len(head) + len(marker) + 2
        if len(block) + overhead <= budget:
            selected[i].append(block)
            selected_indexes[i].add(j)
            budget -= len(block) + overhead

    # 完整优先保留初始化所需的范围、编组和争议，再给长时间线分配剩余预算。
    for i, item in enumerate(results):
        if item.get("required"):
            for j in range(len(groups[i])):
                take(i, j)
                if selected[i]:
                    break
    for i, item in enumerate(results):
        if item.get("required"):
            for j in range(len(groups[i])):
                take(i, j)
    for turn in range(max((len(g) for g in groups), default=0)):
        for i, group in enumerate(groups):
            if turn >= len(group):
                continue
            take(i, turn)
    passages = []
    for i, blocks in enumerate(selected):
        if not blocks:
            continue
        body = "\n\n".join(blocks)
        if len(blocks) < len(groups[i]):
            body += "\n" + marker
        passages.append(dict(results[i], text=body, excerpted=len(blocks) < len(groups[i])))
    return passages


def build_briefing(results, max_chars: int = 2400):
    """把检索结果拼成一段可注入的史料摘要。

    返回 (text, used_count)。used_count 是**实际写进 prompt 的段数**——
    它可能小于 len(results)（超长完整条目会被省略），界面必须显示真实数字，
    否则用户会以为命中的段落全都进去了。
    """
    if not results:
        return "", 0

    passages = select_passages(results, max_chars)
    if not passages:
        return "", 0
    blocks = [BRIEFING_HEADER]
    for i, item in enumerate(passages, 1):
        head = f"\n[史料 {i}] 来源：{item['doc_title']} · {item['section']}"
        blocks.append(f"{head}\n{item['text']}")
    return "\n".join(blocks), len(passages)


def format_sources(results) -> str:
    """给界面用的一行来源摘要。"""
    if not results:
        return ""
    parts = [f"{item['doc_title']}·{item['section']}" for item in results]
    return "、".join(parts)


def clear_cache():
    """测试用：丢弃索引缓存。"""
    _CACHE.clear()
