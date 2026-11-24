"""离线冻结和校验独立的 FAQ 套件，不接触集合或旧数据。"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from textwrap import indent
from typing import Literal, cast

from flowpay.core.data_sources import DataSources, digest
from flowpay.core.json import JsonObject, parse_json, require_object

from scripts.faq_content import FaqRecords, validate_content, validate_snapshot
from scripts.ingestion.corpus import DatasetRelease, ReleaseArtifact, ReleaseGroup, checked_bytes

RELEASE = "data/release.json"
CANONICAL = "data/generated/canonical"
GROUPS = {
    "published_knowledge": ("agent_knowledge", "data/knowledge"),
    "canonical_agent_data": ("agent_data", CANONICAL),
    "faq_benchmark_dev": ("benchmark_only", "data/benchmark/dev"),
    "faq_benchmark_test": ("benchmark_only", "data/benchmark/test"),
}
RUNTIME_GROUPS = {"published_knowledge", "canonical_agent_data"}
CANONICAL_FILES = {"faq.sqlite", "manifest.json", "payers.jsonl", "source-policy.toml"}
# 保留初版 SQLite 的存储定义，确保同一环境重制得到相同摘要。
SNAPSHOT_SQL = indent(
    """
PRAGMA foreign_keys=ON;
CREATE TABLE payers (
    payer_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL UNIQUE
);
CREATE TABLE payments (
    payment_id TEXT PRIMARY KEY, payer_id TEXT NOT NULL REFERENCES payers(payer_id),
    order_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'pending', 'failed', 'reversed')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0), currency TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('balance', 'bank'))
);
CREATE INDEX payments_payer_order ON payments(payer_id, order_ref);
CREATE TABLE refunds (
    refund_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL REFERENCES payments(payment_id),
    status TEXT NOT NULL CHECK (status IN ('requested', 'processing', 'completed', 'failed')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0), currency TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    settled_at TEXT CHECK (settled_at IS NULL OR status = 'completed')
);
CREATE INDEX refunds_payment ON refunds(payment_id);
""",
    " " * 12,
)


def _json_file(path: Path, data: JsonObject) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as out:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), file=out)


def _create_snapshot(source: Path, target: Path, release_id: str, records: FaqRecords) -> None:
    target.mkdir(parents=True)
    _ = shutil.copyfile(source / "payers.jsonl", target / "payers.jsonl")
    with closing(sqlite3.connect(target / "faq.sqlite")) as db:
        _ = db.executescript(SNAPSHOT_SQL)
        _ = db.executemany(
            "INSERT INTO payers VALUES (:payer_id, :subject_id)", records.payers.values()
        )
        _ = db.executemany(
            "INSERT INTO payments VALUES (:payment_id, :payer_id, :order_ref, :status, "
            + ":amount_minor, :currency, :occurred_at, :channel)",
            records.payments.values(),
        )
        _ = db.executemany(
            "INSERT INTO refunds VALUES (:refund_id, :payment_id, :status, :amount_minor, "
            + ":currency, :requested_at, :settled_at)",
            records.refunds.values(),
        )
        db.commit()
    manifest = target / "manifest.json"
    _json_file(
        manifest,
        {
            "release_id": release_id,
            "files": {
                name: {"sha256": digest(target / name), "bytes": (target / name).stat().st_size}
                for name in ("faq.sqlite", "payers.jsonl")
            },
        },
    )
    with (target / "source-policy.toml").open("x", encoding="utf-8", newline="\n") as out:
        _ = out.write(f'release_id = "{release_id}"\nmanifest = "manifest.json"\n')
        _ = out.write(f'manifest_sha256 = "{digest(manifest)}"\n\n')
        _ = out.write((source / "source-policy.toml").read_text(encoding="utf-8"))


def _paths(directory: Path, root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in directory.rglob("*") if p.is_file()}


def _artifact(root: Path, relative: str) -> ReleaseArtifact:
    path = root / relative
    return ReleaseArtifact(path=relative, bytes=path.stat().st_size, sha256=digest(path))


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(source, target)


def _snapshot_rows(
    db: sqlite3.Connection, table: Literal["payers", "payments", "refunds"], key: str
) -> dict[str, JsonObject]:
    rows = cast(Iterator[sqlite3.Row], db.execute(f"SELECT * FROM {table}"))
    return {cast(str, row[key]): require_object(dict(row)) for row in rows}


def _read_snapshot(root: Path, release_id: str) -> FaqRecords:
    canonical = root / CANONICAL
    manifest = canonical / "manifest.json"
    data = require_object(parse_json(manifest.read_text(encoding="utf-8")))
    if data.get("release_id") != release_id:
        raise ValueError("Canonical release identity mismatch")
    files = require_object(data.get("files"))
    if set(files) != {"faq.sqlite", "payers.jsonl"}:
        raise ValueError("Canonical file inventory mismatch")
    for name, entry in files.items():
        info = require_object(entry)
        path = canonical / name
        if digest(path) != info.get("sha256") or path.stat().st_size != info.get("bytes"):
            raise ValueError(f"Canonical artifact mismatch: {name}")
    sources = DataSources.from_config(canonical / "source-policy.toml", canonical)
    if sources.policy["release_id"] != release_id or sources.manifest != manifest.resolve():
        raise ValueError("Canonical policy identity mismatch")
    sql = sources.policy.get("sql", {})
    if set(sql) != {"payments", "refunds"} or sources.policy.get("logs"):
        raise ValueError("Unexpected FAQ sources")
    for spec in sql.values():
        if (
            spec["path"] != "faq.sqlite"
            or spec["scope"] != {"account_id": "p.payer_id"}
            or {"payer_id", "account_id"} & spec["filters"].keys()
            or any(item.rsplit(".", 1)[-1] in {"payer_id", "account_id"} for item in spec["select"])
        ):
            raise ValueError("FAQ sources must query through the authenticated payer scope")
    with closing(
        sqlite3.connect((canonical / "faq.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Broken foreign key in canonical FAQ snapshot")
        records = FaqRecords(
            _snapshot_rows(db, "payers", "payer_id"),
            _snapshot_rows(db, "payments", "payment_id"),
            _snapshot_rows(db, "refunds", "refund_id"),
        )
    payer_rows = [
        require_object(parse_json(line))
        for line in (canonical / "payers.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(payer_rows) != len(records.payers) or any(
        records.payers.get(cast(str, row.get("payer_id"))) != row for row in payer_rows
    ):
        raise ValueError("Payer mappings do not match the snapshot")
    return records


def _check_group_path(group: str, relative: str) -> None:
    path = PurePosixPath(relative)
    prefix = PurePosixPath(GROUPS[group][1])
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in relative
        or path.as_posix() != relative
        or not path.is_relative_to(prefix)
    ):
        raise ValueError(f"Invalid FAQ artifact path: {relative}")
    subpath = path.relative_to(prefix)
    if group == "canonical_agent_data":
        valid = subpath.as_posix() in CANONICAL_FILES
    elif group == "published_knowledge":
        valid = len(subpath.parts) == 1 and subpath.suffix == ".md"
    else:
        valid = (
            len(subpath.parts) == 2
            and subpath.parts[0] in {"questions", "rubrics"}
            and subpath.suffix == ".jsonl"
        )
    if not valid:
        raise ValueError(f"Unexpected FAQ artifact in {group}: {relative}")


def _verify_view(
    root: Path, sha256: str, *, view: Literal["full", "runtime", "build"]
) -> DatasetRelease:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("An expected SHA-256 is required")
    release = DatasetRelease.model_validate_json(checked_bytes(root, RELEASE, sha256), strict=True)
    if release.status != "frozen" or not re.fullmatch(
        r"[a-z0-9]+(?:[-_][a-z0-9]+)*", release.release_id
    ):
        raise ValueError("Invalid frozen FAQ release identity")
    groups = {group.group_id: group for group in release.artifact_groups}
    if groups.keys() != GROUPS.keys() or len(groups) != len(release.artifact_groups):
        raise ValueError("Missing, duplicate, or unknown FAQ artifact groups")
    expected = {RELEASE}
    seen: set[str] = set()
    for group_id, group in groups.items():
        if (
            group.visibility != GROUPS[group_id][0]
            or not group.artifacts
            or len(group.artifacts) != group.file_count
        ):
            raise ValueError(f"FAQ artifact group metadata mismatch: {group_id}")
        for entry in group.artifacts:
            _check_group_path(group_id, entry.path)
            if entry.path in seen:
                raise ValueError(f"Duplicate FAQ artifact: {entry.path}")
            seen.add(entry.path)
            if view != "full" and group_id not in RUNTIME_GROUPS:
                continue
            if not (root / entry.path).is_file():
                raise ValueError(f"Missing FAQ artifact in {group_id}: {entry.path}")
            content = checked_bytes(root, entry.path, entry.sha256)
            if len(content) != entry.bytes:
                raise ValueError(f"FAQ artifact byte count mismatch: {entry.path}")
            expected.add(entry.path)
    canonical = {
        filename.removeprefix(CANONICAL + "/")
        for filename in expected
        if filename.startswith(CANONICAL + "/")
    }
    if canonical != CANONICAL_FILES:
        raise ValueError("Incomplete canonical FAQ group")
    if view == "build":
        actual = {RELEASE}
        for group in RUNTIME_GROUPS:
            actual.update(_paths(root / GROUPS[group][1], root))
    else:
        actual = _paths(root if view == "runtime" else root / "data", root)
    if actual != expected:
        raise ValueError(f"Unexpected FAQ artifacts: {sorted(actual - expected)}")
    return release


def verify_build_inputs(root: Path, sha256: str) -> DatasetRelease:
    """校验构建使用的知识与同版业务快照，不读取题面或金标。"""
    release = _verify_view(root, sha256, view="build")
    _ = validate_snapshot(_read_snapshot(root, release.release_id))
    return release


def verify_release(root: Path, sha256: str) -> DatasetRelease:
    """核对外部固定摘要、四组全部文件、只读快照和隔离运行视图。"""
    release = _verify_view(root, sha256, view="full")
    _ = _verify_view(root / "runtime", sha256, view="runtime")
    records = _read_snapshot(root, release.release_id)
    _ = validate_content(root / "data", records)
    return release


def _copy_sources(source: Path, root: Path, group: str, prefix: str) -> list[ReleaseArtifact]:
    # 只复制合同允许的标准路径，不从制包目录读取额外文件或符号链接目标。
    source_dir = source / prefix.removeprefix("data/")
    entries: list[ReleaseArtifact] = []
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic link in FAQ sources: {path}")
        if not path.is_file():
            continue
        relative = f"{prefix}/{path.relative_to(source_dir).as_posix()}"
        _check_group_path(group, relative)
        _copy(path, root / relative)
        entries.append(_artifact(root, relative))
    return entries


def _check_release_id_drift(output: Path, release_id: str, sha256: str) -> None:
    # 同一发布目录下，同 ID 只允许完全相同的字节；其他位置由固定摘要界定身份。
    for previous in output.parent.glob(f"*/{RELEASE}"):
        if previous.is_file():
            old = DatasetRelease.model_validate_json(previous.read_bytes())
            if old.release_id == release_id and digest(previous) != sha256:
                raise ValueError(f"Same release ID has different contents: {release_id}")


def freeze_release(source: Path, output: Path, release_id: str) -> str:
    """从已校验的合成源文件构造新目录，返回应单独保存的发布摘要。"""
    if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", release_id):
        raise ValueError("Invalid FAQ release ID")
    if output.exists() or output.is_symlink():
        raise ValueError(f"FAQ release output already exists: {output}")
    if output.resolve().is_relative_to(source.resolve()):
        raise ValueError("FAQ release output cannot be inside the source directory")
    records = validate_content(source)
    seed = source / "seed"
    policy = seed / "source-policy.toml"
    if not policy.is_file() or not policy.resolve().is_relative_to(source.resolve()):
        raise ValueError("Missing or invalid FAQ source policy")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".faq-release-", dir=output.parent) as temp:
        root = Path(temp) / "release"
        _create_snapshot(seed, root / CANONICAL, release_id, records)
        groups: list[ReleaseGroup] = []
        for group, (visibility, prefix) in GROUPS.items():
            entries = (
                [_artifact(root, path) for path in sorted(_paths(root / CANONICAL, root))]
                if group == "canonical_agent_data"
                else _copy_sources(source, root, group, prefix)
            )
            groups.append(
                ReleaseGroup(
                    group_id=group,
                    visibility=visibility,
                    file_count=len(entries),
                    artifacts=entries,
                )
            )
        release = DatasetRelease(release_id=release_id, status="frozen", artifact_groups=groups)
        _json_file(root / RELEASE, release.model_dump())
        runtime = root / "runtime"
        _copy(root / RELEASE, runtime / RELEASE)
        for group in groups:
            if group.group_id in RUNTIME_GROUPS:
                for item in group.artifacts:
                    _copy(root / item.path, runtime / item.path)
        sha256 = digest(root / RELEASE)
        _ = verify_release(root, sha256)
        _check_release_id_drift(output, release_id, sha256)
        if output.exists() or output.is_symlink():
            raise ValueError(f"FAQ release output already exists: {output}")
        _ = root.rename(output)
    return sha256
