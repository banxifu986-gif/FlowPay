"""验收 011 正式数据；制包及验包复用这些内容规则。"""

from pathlib import Path

from scripts.faq_content import (
    read_benchmark,
    read_documents,
    read_seed,
    validate_content,
    validate_snapshot,
)

DATASET = Path(__file__).resolve().parents[3] / "flowpay-faq-dataset"


def test_official_faq_dataset_exists() -> None:
    assert DATASET.is_dir(), f"Missing official FAQ dataset root: {DATASET}"


def test_faq_document_sections_and_history() -> None:
    _ = read_documents(DATASET)


def test_faq_snapshot_integrity_and_payer_isolation() -> None:
    _ = validate_snapshot(read_seed(DATASET))


def test_faq_benchmark_distribution_and_rubric_pairing() -> None:
    _ = read_benchmark(DATASET)


def test_faq_benchmark_evidence_behaviors_and_isolation() -> None:
    _ = validate_content(DATASET)
