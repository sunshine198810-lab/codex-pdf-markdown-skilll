"""validation：自检与 JSON Schema 校验（对应设计方案 §13/§17）。

接线后（jsonschema 已装）实现：
  - 本地 registry：跨文件 `$ref`（如 manifest → identity）离线解析，不依赖网络。
  - validate_json：单个数据 vs 单个 schema 的严格校验。
  - validate_schema_files：每个 schema 本身符合 draft-07（自我合规）。
  - validate_package：研究包关键产物（run/manifest/quality/document_map/review_queue）逐个过 schema。
  - selfcheck：纳入 schema 文件合规与可解析。

P0 未实现：五层质量检查、财务校验、覆盖/结构/语义验证（需引擎产物）。
"""

from __future__ import annotations

import importlib
import json

from . import schemas_io
from .version import MODULES, SCHEMA_VERSION


# ---- jsonschema 可用性 -------------------------------------------------------

def _jsonschema_available() -> bool:
    try:
        import jsonschema  # noqa: F401

        return True
    except Exception:
        return False


def _build_registry():
    """构建本地 referencing registry：按每个 schema 的 $id 注册，使 $ref 离线可解。"""
    from referencing import Registry, Resource  # type: ignore
    from referencing.jsonschema import DRAFT7  # type: ignore

    resources = {}
    for fname in schemas_io.list_schemas():
        schema = schemas_io.load_schema(fname)
        rid = schema.get("$id")
        if rid:
            resources[rid] = Resource.from_contents(schema, default_specification=DRAFT7)
    return Registry().with_resources(resources.items())


# ---- 校验 ---------------------------------------------------------------

def validate_json(data: dict, schema_name: str) -> dict:
    """单个数据 vs 单个 schema 的严格校验（jsonschema 可用时）。

    未安装时返回 checked=False（不伪装通过）；$ref 通过本地 registry 解析，
    不发网络请求（远程默认关闭，见 SKILL.md 运行纪律）。
    """
    if not _jsonschema_available():
        return {
            "schema": schema_name,
            "checked": False,
            "reason": "jsonschema 未安装，跳过严格校验",
        }
    schema = schemas_io.load_schema(schema_name)
    import jsonschema  # type: ignore

    try:
        validator = jsonschema.Draft7Validator(schema, registry=_build_registry())
    except TypeError:  # 旧版 jsonschema 不支持 registry kwarg
        validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if not errors:
        return {"schema": schema_name, "checked": True, "valid": True}
    first = errors[0]
    return {
        "schema": schema_name,
        "checked": True,
        "valid": False,
        "error": first.message,
        "path": "/".join(str(p) for p in first.absolute_path),
    }


def validate_schema_files() -> list:
    """校验每个 schema 文件本身符合 draft-07。返回问题列表；空表示全部合规。"""
    if not _jsonschema_available():
        return ["jsonschema 未安装，跳过 schema 自我合规检查"]
    import jsonschema  # type: ignore

    problems = []
    for fname in schemas_io.list_schemas():
        schema = schemas_io.load_schema(fname)
        try:
            jsonschema.Draft7Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:  # type: ignore
            problems.append(f"schema 不合 draft-07 {fname}: {exc.message}")
    return problems


_PACKAGE_SCHEMA_MAP = [
    ("_internal/run.json", "run"),
    ("索引/manifest.json", "manifest"),
    ("索引/quality.json", "quality"),
    ("索引/document_map.json", "document-map"),
]


def validate_package(package_dir) -> list:
    """研究包关键产物逐个过 schema；返回逐文件结果列表。

    覆盖 run / manifest / quality / document_map / review_queue（逐行）。
    缺失文件、未装 jsonschema 均如实记录，不伪装通过。
    """
    import pathlib

    root = pathlib.Path(package_dir)
    out = []
    for rel, schema in _PACKAGE_SCHEMA_MAP:
        p = root / rel
        if not p.is_file():
            out.append({"file": rel, "schema": schema, "checked": False, "reason": "文件缺失"})
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        res = validate_json(data, schema)
        out.append({"file": rel, **res})

    rq = root / "索引/review_queue.jsonl"
    if rq.is_file():
        lines = [ln for ln in rq.read_text(encoding="utf-8").splitlines() if ln.strip()]
        for i, line in enumerate(lines, 1):
            try:
                data = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                out.append(
                    {"file": f"索引/review_queue.jsonl#{i}", "schema": "review-queue", "checked": False, "reason": f"JSON 解析失败: {exc}"}
                )
                continue
            res = validate_json(data, "review-queue")
            out.append({"file": f"索引/review_queue.jsonl#{i}", **res})

    # 人工签核记录（复核/signoffs.jsonl）逐行过 signoff schema；缺失文件不报错。
    so = root / "复核/signoffs.jsonl"
    if so.is_file():
        for i, line in enumerate(so.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                out.append(
                    {"file": f"复核/signoffs.jsonl#{i}", "schema": "signoff", "checked": False, "reason": f"JSON 解析失败: {exc}"}
                )
                continue
            res = validate_json(data, "signoff")
            out.append({"file": f"复核/signoffs.jsonl#{i}", **res})

    # M1 标准解析产物：逐页、逐表严格校验；旧切片组装包保持其中间形态边界。
    run_mode = None
    try:
        run_mode = json.loads((root / "_internal/run.json").read_text(encoding="utf-8")).get("mode")
    except Exception:
        pass
    if run_mode != "standard_parse":
        return out

    # 大 JSONL 汇总校验，避免 quality 膨胀为数千行。
    for p in sorted((root / "_internal/pages").glob("page-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            res = validate_json(data, "page")
        except Exception as exc:  # noqa: BLE001
            res = {"schema": "page", "checked": False, "valid": False, "reason": str(exc)}
        out.append({"file": str(p.relative_to(root)), **res})

    for p in sorted((root / "表格").glob("table-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            res = validate_json(data, "table")
        except Exception as exc:  # noqa: BLE001
            res = {"schema": "table", "checked": False, "valid": False, "reason": str(exc)}
        out.append({"file": str(p.relative_to(root)), **res})

    for p in sorted((root / "表格").glob("logical-table-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            res = validate_json(data, "logical-table")
        except Exception as exc:  # noqa: BLE001
            res = {"schema": "logical-table", "checked": False, "valid": False, "reason": str(exc)}
        out.append({"file": str(p.relative_to(root)), **res})

    for rel, schema in (("_internal/objects.jsonl", "object"), ("索引/evidence.jsonl", "evidence")):
        p = root / rel
        if not p.is_file():
            continue
        checked = 0
        first_failure = None
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            checked += 1
            try:
                result = validate_json(json.loads(line), schema)
            except Exception as exc:  # noqa: BLE001
                result = {"valid": False, "reason": str(exc)}
            if result.get("valid") is False and first_failure is None:
                first_failure = {"line": i, **result}
        out.append(
            {
                "file": rel,
                "schema": schema,
                "checked": True,
                "valid": first_failure is None,
                "records_checked": checked,
                "first_failure": first_failure,
            }
        )
    for rel in ("数据/facts.jsonl", "数据/candidates.jsonl"):
        p = root / rel
        if not p.is_file():
            continue
        checked = 0
        first_failure = None
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            checked += 1
            try:
                result = validate_json(json.loads(line), "observation")
            except Exception as exc:  # noqa: BLE001
                result = {"valid": False, "reason": str(exc)}
            if result.get("valid") is False and first_failure is None:
                first_failure = {"line": i, **result}
        out.append({"file": rel, "schema": "observation", "checked": True,
                    "valid": first_failure is None, "records_checked": checked,
                    "first_failure": first_failure})
    return out


def _module_importable(name: str):
    try:
        importlib.import_module(f"pipeline.{name}")
        return None
    except Exception as exc:  # noqa: BLE001
        return f"pipeline.{name}: {exc}"


def selfcheck() -> list:
    """运行自检，返回问题列表；空列表表示通过。"""
    problems = []

    # 1) 模块可导入
    for name in MODULES:
        err = _module_importable(name)
        if err:
            problems.append(err)

    # 2) pipeline 包内每个 .py 都应是登记模块或私有/工具模块
    import pathlib

    pkg_dir = pathlib.Path(__file__).resolve().parent
    allow = set(MODULES) | {
        "__init__",
        "version",
        "schemas_io",
    }
    for py in pkg_dir.glob("*.py"):
        stem = py.name[: -len(".py")]
        if stem not in allow:
            problems.append(f"pipeline 中存在未登记模块: {stem}")

    # 3) Schema 文件可加载、可解析且本身合规（draft-07）
    for fname in schemas_io.list_schemas():
        try:
            schemas_io.load_schema(fname)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"schema 解析失败 {fname}: {exc}")
    problems.extend(validate_schema_files())

    # 4) 能力声明必须包含 implemented/pending，避免只有布尔值却无边界说明。
    for name in ("layout", "sections", "tables", "figures", "semantics", "standard", "financials"):
        mod = importlib.import_module(f"pipeline.{name}")
        cap = getattr(mod, "capabilities", {})
        if not isinstance(cap.get("implemented"), list) or not isinstance(cap.get("pending"), list):
            problems.append(f"pipeline.{name}.capabilities 缺 implemented/pending 清单")
    return problems
