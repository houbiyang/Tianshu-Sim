"""⚖️ 裁决参数库（Adjudication Library）· 每场战役一套几何参数。

## 为什么单独成库

几何内核（`adjudicator.py`）算的是**物理**：走得动多远、粮道通不通、退路断没断。
这些算法跨战役通用。但内核里的六个**尺度**不通用 ——

    弓弩射程 300 步（约 0.9 km）、线膛枪 300 m、现代火炮 20 km 以上，
    兵力密度与战场纵深差一个数量级，用同一套「3 km 算接触」去裁决，
    古代战报会合理，机械化战役会把大量实际交火判成「未接触」——
    而且不报错，只是安静地输出错误结论。

所以把尺度拆出来，一场战役一个文件，改参数不必改代码。

## 三种来源的优先级（高 → 低）

1. 裁决库 `adjudication/<战役名>.md`
2. 史料语料 `corpus/<战役名>.md` 的 frontmatter（旧位置，仍兼容）
3. `PARAM_SCHEMA` 里的默认值

⚠️ 关键约束：**一旦选中某个文件，就只用它那一份的值**，不再把目录里
所有文件「合并 + 后者覆盖前者」。旧实现遍历所有命中文件逐个 update，
加第二场战役时新战役会悄悄覆盖老战役的参数 —— 战役之间必须互不串味。

## 加一场新战役

    python -c "from gentactix import adjudication_lib as L; L.save_battle('诺曼底', {'contact_km': 8, 'control_radius_km': 6})"

或者直接复制 `adjudication/_template.md` 改名、改值，无需注册、无需重启。

## 写错会怎样

拼错的键名、非数字、超范围值都会进 `spec.errors` 并在界面上显示，
**不再静默忽略**。合法的键照常生效 —— 一个笔误不该废掉整场战役。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 默认目录（相对项目根），可用参数覆盖，便于测试时指向临时目录
LIBRARY_DIRNAME = "adjudication"
CORPUS_DIRNAME = "corpus"

# 允许出现在 frontmatter 里、但不参与几何计算的元信息键
META_KEYS = {
    "battle_name",
    "display_name",
    "era",
    "period",
    "note",
    "source_ref",
    "updated",
}

# ==========================================
# 📐 参数规格：键名 / 默认值 / 允许范围 / 含义
# ==========================================
# 默认值刻意选在「冷兵器会战」量级（接触线 3 km ≈ 弓弩有效射程的 3 倍余量），
# 因为它对应长平这类已标定战役；其它时代请在各自的战役文件里覆盖。
PARAM_SCHEMA = {
    "contact_km": {
        "default": 3.0,
        "bounds": (0.1, 500.0),
        "doc": "双方最近单位 ≤ 此距离视为接触",
    },
    "control_radius_km": {
        "default": 3.0,
        "bounds": (0.1, 500.0),
        "doc": "敌方区域控制半径的下限（射程更长时按射程算）",
    },
    "encircle_radius_km": {
        "default": 12.0,
        "bounds": (0.5, 500.0),
        "doc": "合围判定向外搜索的半径",
    },
    "retreat_check_km": {
        "default": 12.0,
        "bounds": (0.5, 500.0),
        "doc": "退路判定向外搜索的半径",
    },
    "sealed_ratio_encircled": {
        "default": 0.85,
        "bounds": (0.05, 1.0),
        "doc": "被封锁方向占比 ≥ 此值判「被围」",
    },
    "sealed_ratio_partial": {
        "default": 0.50,
        "bounds": (0.05, 1.0),
        "doc": "被封锁方向占比 ≥ 此值判「半包围」",
    },
}

# 文件名里不允许出现的字符（Windows 与一切平台的安全交集）
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def default_params():
    """返回一份全新的默认参数字典（调用方可随意修改）。"""
    return {key: float(spec["default"]) for key, spec in PARAM_SCHEMA.items()}


# ==========================================
# 📄 结果对象
# ==========================================
@dataclass
class BattleSpec:
    """一场战役的最终生效参数，附带「它从哪来、改了什么、哪里写错了」。"""

    name: str
    params: dict                       # 默认值 + 本战役显式值，键保证齐全
    explicit: dict                     # 本战役文件里真正写下的值（传给 adjudicator）
    source: Path | None = None
    source_kind: str = "default"       # library / corpus / default
    matched_by: str = "fallback"       # explicit / exact / contains / fallback / miss
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def calibrated(self) -> bool:
        """True 表示这场战役有专门的标定数据，False 表示在用默认几何。"""
        return bool(self.explicit)

    def overridden(self):
        """被本战役显式覆盖的键（按 schema 顺序，便于稳定显示）。"""
        return [k for k in PARAM_SCHEMA if k in self.explicit]

    def summary_line(self) -> str:
        """一行给界面用：谁在生效、改了什么。"""
        if not self.calibrated:
            base = "未标定" if self.source_kind == "default" else "文件里没有有效参数"
            return (
                f"{self.name or '未指定战役'} · {base} · "
                f"使用默认几何（接触 {self.params['contact_km']:g} km）"
            )
        items = [f"{k}={self.explicit[k]:g}" for k in self.overridden()]
        if len(items) > 3:
            changed = "、".join(items[:3]) + f" 等 {len(items)} 项"
        else:
            changed = "、".join(items)
        kind = "裁决库" if self.source_kind == "library" else "语料 frontmatter"
        return f"{self.name} · {kind} · 已标定 {len(self.explicit)} 项（{changed}）"


# ==========================================
# 🔍 发现战役文件
# ==========================================
def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _library_dir(library_dir=None) -> Path:
    return Path(library_dir) if library_dir else _project_root() / LIBRARY_DIRNAME


def _corpus_dir(corpus_dir=None) -> Path:
    return Path(corpus_dir) if corpus_dir else _project_root() / CORPUS_DIRNAME


def _is_battle_file(path: Path) -> bool:
    stem = path.stem
    if not stem or stem.startswith("_") or stem.startswith("."):
        return False
    return stem.upper() not in {"README", "INDEX"}


def _collect(library_dir=None, corpus_dir=None):
    """收集两处的战役文件，返回 [(name, path, kind)]，library 排在前面。"""
    items = []
    for directory, kind in ((_library_dir(library_dir), "library"),
                            (_corpus_dir(corpus_dir), "corpus")):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if _is_battle_file(path):
                items.append((path.stem, path, kind))
    return items


def list_battles(library_dir=None, corpus_dir=None):
    """库中所有可用的战役名（去重、排序）。"""
    names = {name for name, _, _ in _collect(library_dir, corpus_dir)}
    return sorted(names)


# ==========================================
# 🧾 解析与校验
# ==========================================
def parse_frontmatter(path: Path):
    """读取 `--- ... ---` 里的 key: value，返回 (字典, 错误列表)。

    与 corpus 里那套解析保持同一写法（`#` 之后是注释），但**不再吞错**：
    缺结束标记、写了无法识别的行都会报出来。
    """
    errors = []
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [f"无法读取 `{Path(path).name}`：{exc}"]

    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, [f"`{Path(path).name}` 缺少开头的 `---` 标记"]

    data = {}
    closed = False
    for lineno, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            closed = True
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            errors.append(f"`{Path(path).name}` 第 {lineno} 行不是 key: value：{stripped[:40]}")
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.split("#", 1)[0].strip()
        if not key or not value:
            continue
        try:
            data[key] = float(value)
        except ValueError:
            data[key] = value
    if not closed:
        errors.append(f"`{Path(path).name}` 缺少结尾的 `---` 标记")
    return data, errors


def validate_params(raw, base=None):
    """校验一份参数，返回 (合法值, 错误列表)。

    只有 PARAM_SCHEMA 里的键会被接受；元信息键直接跳过。
    `base` 是交叉校验的基准（通常是默认值）。
    """
    base = default_params() if base is None else dict(base)
    ok, errors = {}, []

    for key, value in dict(raw or {}).items():
        if key in META_KEYS:
            continue
        if key not in PARAM_SCHEMA:
            errors.append(
                f"未知参数 `{key}` —— 会被忽略。可用键：{', '.join(PARAM_SCHEMA)}")
            continue
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            errors.append(f"参数 `{key}` 不是数字：{value!r}")
            continue
        low, high = PARAM_SCHEMA[key]["bounds"]
        if not (low <= number <= high):
            errors.append(
                f"参数 `{key}` = {number:g} 超出允许范围 [{low:g}, {high:g}]")
            continue
        ok[key] = number

    # 交叉校验：半包围阈值必须低于被围阈值，否则「半包围」永远不会先于「被围」出现
    merged = dict(base)
    merged.update(ok)
    if merged["sealed_ratio_partial"] >= merged["sealed_ratio_encircled"]:
        errors.append(
            "sealed_ratio_partial（{:.2f}）必须小于 sealed_ratio_encircled（{:.2f}）"
            .format(merged["sealed_ratio_partial"], merged["sealed_ratio_encircled"]))
        ok.pop("sealed_ratio_partial", None)
        ok.pop("sealed_ratio_encircled", None)

    return ok, errors


def _prefer_library(hits, warnings):
    """同名战役在库里和语料里都有时，取库里那份（单一真相源）。"""
    library_hits = [h for h in hits if h[2] == "library"]
    chosen = (library_hits or hits)[0]
    if library_hits and len(hits) > len(library_hits):
        warnings.append(
            f"「{chosen[0]}」在裁决库与 corpus 中同时存在，已采用裁决库那份，"
            "corpus 里的同名字段被忽略。")
    return chosen


def _pick(items, scenario_text, battle, warnings):
    """决定用哪一个文件，返回 ((name, path, kind) | None, matched_by)。"""
    if battle:
        wanted = str(battle).strip()
        hits = [it for it in items if it[0] == wanted]
        if not hits:
            hits = [it for it in items if it[0].casefold() == wanted.casefold()]
        if not hits:
            warnings.append(
                f"裁决库中没有战役「{wanted}」，已回落到默认几何参数。")
            return None, "miss"
        return _prefer_library(hits, warnings), "explicit"

    text = str(scenario_text or "").strip()
    if not text:
        return None, "fallback"

    exact = [it for it in items if it[0] == text]
    if exact:
        return _prefer_library(exact, warnings), "exact"

    contains = [it for it in items if it[0] in text]
    if contains:
        # 名字越长越具体：「长平之战」优于「长平」
        longest = max(len(it[0]) for it in contains)
        top = [it for it in contains if len(it[0]) == longest]
        names = sorted({it[0] for it in top})
        if len(names) > 1:
            warnings.append(
                "多个战役名同时命中（" + "、".join(names) + "），取排序第一份，请改用显式指定。")
        return _prefer_library(top, warnings), "contains"

    return None, "fallback"


# ==========================================
# 🎯 对外主入口
# ==========================================
def resolve(scenario_text="", battle=None, corpus_dir=None, library_dir=None):
    """算出这场战役最终该用哪套参数。

    优先级：显式 battle > 场景名精确匹配 > 场景名包含（取最长）> 默认。
    返回值里 params 是完整参数，explicit 只含本战役写下的键。
    """
    warnings = []
    items = _collect(library_dir=library_dir, corpus_dir=corpus_dir)
    picked, matched_by = _pick(items, scenario_text, battle, warnings)

    if picked is None:
        return BattleSpec(
            name=str(battle) if battle else "",
            params=default_params(),
            explicit={},
            source=None,
            source_kind="default",
            matched_by=matched_by,
            warnings=warnings,
        )

    name, path, kind = picked
    raw, parse_errors = parse_frontmatter(path)
    valid, validate_errors = validate_params(raw)
    errors = parse_errors + validate_errors

    params = default_params()
    params.update(valid)

    return BattleSpec(
        name=name,
        params=params,
        explicit=valid,
        source=path,
        source_kind=kind,
        matched_by=matched_by,
        warnings=warnings,
        errors=errors,
    )


def load_battle(name, corpus_dir=None, library_dir=None):
    """按战役名精确加载（找不到时返回默认参数 + warning，不抛异常）。"""
    return resolve(battle=name, corpus_dir=corpus_dir, library_dir=library_dir)


# ==========================================
# 💾 写回（新增 / 更新一场战役）
# ==========================================
def _check_name(name):
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("战役名不能为空")
    if _BAD_NAME_CHARS.search(cleaned):
        raise ValueError("战役名不能包含  \\  /  :  *  ?  \"  <  >  |  等字符")
    if cleaned.startswith(("_", ".")):
        raise ValueError("战役名不能以 _ 或 . 开头（这类文件不会被索引）")
    if cleaned.upper() in {"README", "INDEX"}:
        raise ValueError("README / INDEX 是保留名")
    return cleaned


def render_battle_md(name, params, note="", era=""):
    """把参数渲染成可人工编辑的 Markdown（frontmatter + 说明正文）。

    ⚠️ frontmatter 里**只写传进来的键**，不把默认值逐条抄一遍。
    抄一遍的后果是：以后调整 PARAM_SCHEMA 的默认值，所有已存在的战役文件
    都停在旧值上，而且看不出来 —— 改默认值等于没改。
    没传的键以注释形式列出，教师一眼能看到可选项，取消注释即可启用。
    """
    given = {key: float(value) for key, value in dict(params or {}).items()
             if key in PARAM_SCHEMA}

    head = ["---", f"battle_name: {name}"]
    if era:
        head.append(f"era: {era}")
    if note:
        head.append(f"note: {note}")
    head.append("")
    head.append("# ── 几何尺度：只写要区别于默认值的键，其余走默认 ──")
    for key, spec in PARAM_SCHEMA.items():
        if key in given:
            head.append(f"{key}: {given[key]:g}    # {spec['doc']}")
        else:
            head.append(f"# {key}: {spec['default']:g}  ← 未标定，走默认值。{spec['doc']}")
    head.append("---")

    body = [
        "",
        f"# {name} · 裁决参数",
        "",
        "本文件只影响 `adjudicator` 的几何尺度，**不参与史料检索**（史料在 `corpus/`）。",
        "改完保存即生效（下一次推演就读到），无需重启、无需改代码。",
        "",
        "| 参数 | 生效值 | 来源 | 含义 |",
        "|---|---|---|---|",
    ]
    for key, spec in PARAM_SCHEMA.items():
        value = given.get(key, spec["default"])
        origin = "本战役标定" if key in given else "默认"
        body.append(f"| `{key}` | {value:g} | {origin} | {spec['doc']} |")
    body.append("")
    return "\n".join(head + body)


def save_battle(name, params, note="", era="", library_dir=None):
    """写入或更新一场战役的参数，返回文件路径。参数非法时抛 ValueError。"""
    cleaned = _check_name(name)
    valid, errors = validate_params(params)
    if errors:
        raise ValueError("；".join(errors))

    directory = _library_dir(library_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{cleaned}.md"
    text = render_battle_md(cleaned, valid, note=note, era=era)

    # 先写临时文件再替换：中途失败不会留下半截文件
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return target


def param_table(spec):
    """给界面用的参数表：值 / 是否本战役标定 / 含义。"""
    rows = []
    for key, meta in PARAM_SCHEMA.items():
        rows.append({
            "参数": key,
            "值": spec.params[key],
            "来源": "本战役标定" if key in spec.explicit else "默认",
            "含义": meta["doc"],
        })
    return rows
