"""冻结、校验及构建独立 FAQ 数据集。"""

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from flowpay.core.config.loader import apply_environment_overrides, build_settings
from flowpay.core.data_sources import digest
from yaml import YAMLError

from scripts.faq_release import CANONICAL, freeze_release, verify_release


class Arguments(argparse.Namespace):
    command: str = ""
    source: Path = Path()
    output: Path = Path()
    release_id: str = ""
    faq_root: Path = Path()
    release_sha256: str = ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="从合成源数据生成新的冻结发布物")
    _ = freeze.add_argument("--source", type=Path, required=True)
    _ = freeze.add_argument("--output", type=Path, required=True)
    _ = freeze.add_argument("--release-id", required=True)
    verify = commands.add_parser("verify", help="核对固定发布摘要、包内容及运行视图")
    build = commands.add_parser("build", help="只向独立 FAQ 集合摄入公开知识")
    for command in (verify, build):
        _ = command.add_argument("--faq-root", type=Path, required=True)
        _ = command.add_argument("--release-sha256", required=True)
    arguments = parser.parse_args(argv, namespace=Arguments())
    try:
        if arguments.command == "freeze":
            sha256 = freeze_release(arguments.source, arguments.output, arguments.release_id)
            print(f"release_sha256: {sha256}")
            print(f"canonical_sha256: {digest(arguments.output / CANONICAL / 'manifest.json')}")
        elif arguments.command == "verify":
            release = verify_release(arguments.faq_root, arguments.release_sha256)
            print(f"{release.release_id}: release and isolated runtime verified")
        else:
            from scripts.ingestion.runner import run_faq_build

            output = Path("var/faq/build")
            settings = build_settings(
                apply_environment_overrides(
                    {"data_dir": str(output), "dataset_path": str(arguments.faq_root)}
                )
            )
            result = asyncio.run(
                run_faq_build(arguments.faq_root, arguments.release_sha256, settings, output)
            )
            print(json.dumps(result, ensure_ascii=False))
    except (KeyError, OSError, ValueError, YAMLError, sqlite3.Error) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
