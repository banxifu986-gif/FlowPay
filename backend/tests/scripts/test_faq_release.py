"""FAQ 正式发布命令的离线契约。"""

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from hashlib import sha256
from pathlib import Path

import pytest
from flowpay.core.json import parse_json, require_list, require_object

from scripts.ingestion.corpus import DatasetRelease

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "flowpay-faq-dataset"
RELEASE_ID = "faq-011-contract"
MANIFEST = "data/release.json"
KNOWLEDGE = "data/knowledge/pay-pending-v1.md"
CANONICAL = "data/generated/canonical/faq.sqlite"
QUESTION = "data/benchmark/dev/questions/simple_faq.jsonl"
RUBRIC = "data/benchmark/dev/rubrics/simple_faq.jsonl"
GROUPS = {
    "published_knowledge": ("agent_knowledge", "data/knowledge/"),
    "canonical_agent_data": ("agent_data", "data/generated/canonical/"),
    "faq_benchmark_dev": ("benchmark_only", "data/benchmark/dev/"),
    "faq_benchmark_test": ("benchmark_only", "data/benchmark/test/"),
}


type CliResult = subprocess.CompletedProcess[str]
type Frozen = tuple[Path, CliResult]


def cli(*args: str | Path) -> CliResult:
    return subprocess.run(
        [sys.executable, "-m", "scripts.faq", *map(str, args)],
        cwd=REPO,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )


def freeze(source: Path, output: Path, release_id: str = RELEASE_ID) -> CliResult:
    return cli("freeze", "--source", source, "--output", output, "--release-id", release_id)


def verify(root: Path, pin: str) -> CliResult:
    return cli("verify", "--faq-root", root, "--release-sha256", pin)


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def accepted(result: CliResult) -> None:
    assert result.returncode == 0, result.stderr or result.stdout


def rejected(result: CliResult, hint: str = "") -> None:
    output = result.stderr + result.stdout
    assert result.returncode != 0, output
    if hint:
        assert re.search(hint, output, re.IGNORECASE), output


@pytest.fixture(scope="module")
def frozen(tmp_path_factory: pytest.TempPathFactory) -> Frozen:
    root = tmp_path_factory.mktemp("faq-release") / "release"
    source_before = files(SOURCE)
    result = freeze(SOURCE, root)
    assert files(SOURCE) == source_before
    return root, result


def test_freeze_and_verify(frozen: Frozen) -> None:
    root, result = frozen
    accepted(result)
    published = files(root)
    manifest = published[MANIFEST]
    pin = sha256(manifest).hexdigest()
    assert pin in result.stdout.lower(), result.stdout
    release = DatasetRelease.model_validate_json(manifest)
    assert (release.release_id, release.status) == (RELEASE_ID, "frozen")
    assert sorted(group.group_id for group in release.artifact_groups) == sorted(GROUPS)

    authored = files(SOURCE)
    runtime = {MANIFEST: manifest}
    seen = {MANIFEST}
    for group in release.artifact_groups:
        visibility, prefix = GROUPS[group.group_id]
        assert group.visibility == visibility
        assert group.file_count == len(group.artifacts) > 0
        if group.group_id != "canonical_agent_data":
            assert {item.path for item in group.artifacts} == {
                f"data/{path}" for path in authored if f"data/{path}".startswith(prefix)
            }
        for item in group.artifacts:
            assert item.path.startswith(prefix) and item.path not in seen
            assert "\\" not in item.path and not {"", ".", ".."}.intersection(item.path.split("/"))
            seen.add(item.path)
            content = published[item.path]
            assert (item.bytes, item.sha256) == (len(content), sha256(content).hexdigest())
            if group.group_id != "canonical_agent_data":
                assert content == authored[item.path.removeprefix("data/")]
            if not group.group_id.startswith("faq_benchmark_"):
                runtime[item.path] = content
    assert seen == {path for path in published if path.startswith("data/")}
    assert files(root / "runtime") == runtime

    accepted(verify(root, pin))
    assert files(root) == published


@pytest.fixture
def release_copy(frozen: Frozen, tmp_path: Path) -> tuple[Path, str]:
    root, result = frozen
    accepted(result)
    copy = shutil.copytree(root, tmp_path / "release", copy_function=shutil.copyfile)
    pin = sha256((copy / MANIFEST).read_bytes()).hexdigest()
    accepted(verify(copy, pin))
    return copy, pin


def repin(root: Path, relative: str, *, new_path: str | None = None) -> str:
    """重签外层清单，不改其他声明或业务清单。"""
    manifest = require_object(parse_json((root / MANIFEST).read_bytes()))
    entries = (
        require_object(entry)
        for group in require_list(manifest["artifact_groups"])
        for entry in require_list(require_object(group)["artifacts"])
    )
    artifact = next(entry for entry in entries if entry["path"] == relative)
    path = new_path or relative
    data = (root / path).read_bytes()
    artifact.update(path=path, bytes=len(data), sha256=sha256(data).hexdigest())
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    for view in (root, root / "runtime"):
        _ = (view / MANIFEST).write_bytes(payload)
    return sha256(payload).hexdigest()


@pytest.mark.parametrize("fault", "wrong-pin missing tamper mixed-canonical escape gold".split())
def test_verify_rejects_invalid_release(release_copy: tuple[Path, str], fault: str) -> None:
    root, pin = release_copy
    hint = ""
    if fault == "wrong-pin":
        pin = ("1" if pin[0] == "0" else "0") + pin[1:]
        hint = r"sha256|digest|hash|pin|摘要"
    elif fault == "missing":
        (root / QUESTION).unlink()
    elif fault == "tamper":
        content = (root / KNOWLEDGE).read_bytes()
        assert b"FlowPay" in content
        for view in (root, root / "runtime"):
            _ = (view / KNOWLEDGE).write_bytes(content.replace(b"FlowPay", b"Flowpay", 1))
    elif fault == "mixed-canonical":
        snapshot = root / CANONICAL
        before = snapshot.read_bytes()
        with closing(sqlite3.connect(snapshot)) as db:
            _ = db.execute("PRAGMA user_version = 2011")
        assert snapshot.read_bytes() != before
        _ = shutil.copyfile(snapshot, root / "runtime" / CANONICAL)
        pin = repin(root, CANONICAL)
        hint = r"canonical|business|snapshot|sqlite|快照|业务清单"
    elif fault == "escape":
        _ = (root / QUESTION).rename(root.parent / "outside.jsonl")
        pin = repin(root, QUESTION, new_path="data/benchmark/dev/../../../../outside.jsonl")
        hint = r"path|escape|traversal|outside|路径|越界"
    else:
        gold = root / "runtime" / RUBRIC
        gold.parent.mkdir(parents=True)
        _ = shutil.copyfile(root / RUBRIC, gold)
        hint = r"runtime|benchmark|rubric|gold|运行视图|评测"
    rejected(verify(root, pin), hint)


@pytest.mark.parametrize("fault", ["quota", "reference"])
def test_verify_rejects_bad_content_after_repinning(
    release_copy: tuple[Path, str], fault: str
) -> None:
    root, _ = release_copy
    if fault == "quota":
        relative, old, new = QUESTION, b'"easy"', b'"hard"'
        hint = r"quota|difficulty|count|配额|难度|计数"
    else:
        relative, old, new = RUBRIC, b"KB-FAQ-PAY-PENDING-V1", b"KB-FAQ-PAY-ABSENTX-V1"
        hint = r"document|doc_id|evidence|reference|section|引用|证据|文档"
    path = root / relative
    content = path.read_bytes()
    assert old in content
    _ = path.write_bytes(content.replace(old, new, 1))
    rejected(verify(root, repin(root, relative)), hint)


@pytest.mark.parametrize("fault", ["existing", "inside-source", "same-id-drift"])
def test_freeze_rejects_invalid_destination(tmp_path: Path, fault: str) -> None:
    source = shutil.copytree(SOURCE, tmp_path / "authoring")
    output = tmp_path / "release"
    if fault == "same-id-drift":
        sibling = tmp_path / "first-release"
        accepted(freeze(source, sibling))
        first = files(sibling)
        article = source / KNOWLEDGE.removeprefix("data/")
        _ = article.write_bytes(article.read_bytes() + "\n请以支付记录为准。\n".encode())
        rejected(freeze(source, output))
        assert files(sibling) == first
        accepted(freeze(source, tmp_path / "new-id", f"{RELEASE_ID}-next"))
    elif fault == "existing":
        output.mkdir()
        _ = (output / "sentinel").write_bytes(b"keep")
        rejected(freeze(source, output), r"exists|already|overwrite|已存在|覆盖")
        assert files(output) == {"sentinel": b"keep"}
    else:
        output = source / "release"
        before = files(source)
        rejected(freeze(source, output), r"inside|within|nested|overlap|源目录")
        assert files(source) == before
        assert not output.exists()
