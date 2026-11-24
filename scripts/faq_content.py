"""校验 FAQ 文档、业务记录及评测材料的内容和用途边界。"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

import yaml
from flowpay.core.chat.record_ids import RECORD_ID
from flowpay.core.json import (
    JsonObject,
    parse_json,
    require_int,
    require_list,
    require_object,
    require_text,
)
from flowpay.core.knowledge.documents import parse_document, section_ranges, split_document

DOC_ID = re.compile(r"KB-FAQ-([A-Z0-9]+(?:-[A-Z0-9]+)*)-V([1-9][0-9]*)")
RECORD_IN_TEXT = re.compile(RECORD_ID.pattern, re.ASCII)
RECORD_KEYS = {"payment": "payment_id", "refund": "refund_id"}
DIFFICULTIES = ("easy", "medium", "hard")
TYPE_COUNTS = {
    "simple_faq": (6, 9),
    "ambiguous_clarification": (6, 9),
    "personalized_cross_source": (10, 15),
    "temporal_conflict": (4, 6),
    "multi_hop": (6, 9),
    "multi_turn": (8, 12),
}

type RecordRef = tuple[str, str]
type Rows = dict[str, JsonObject]


def _text(row: JsonObject, key: str) -> str:
    value = require_text(row.get(key))
    if not (value.strip()):
        raise ValueError(str(f"Empty {key}: {row}"))
    return value


def _time(value: object) -> datetime:
    timestamp = (
        value if isinstance(value, datetime) else datetime.fromisoformat(require_text(value))
    )
    if not (timestamp.utcoffset() is not None):
        raise ValueError(str(f"Timestamp has no timezone: {value!r}"))
    return timestamp


def _files(root: Path, pattern: str) -> list[Path]:
    paths = sorted(root.glob(pattern))
    if not (paths):
        raise ValueError(str(f"Missing FAQ dataset files: {root / pattern}"))
    for path in paths:
        if not (path.is_file() and path.resolve().is_relative_to(root.resolve())):
            raise ValueError(str(f"Invalid source file: {path}"))
    return paths


def _rows(root: Path, pattern: str, key: str) -> Rows:
    rows: Rows = {}
    for path in _files(root, pattern):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = require_object(parse_json(line))
                identifier = _text(row, key)
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: invalid {key} row: {exc}") from exc
            if not (identifier not in rows):
                raise ValueError(str(f"{path}:{number}: duplicate {key}: {identifier}"))
            rows[identifier] = row
    if not (rows):
        raise ValueError(str(f"No rows in {pattern}"))
    return rows


def _items(row: JsonObject, key: str) -> list[JsonObject]:
    return [require_object(item) for item in require_list(row.get(key))]


def _same_value(actual: object, expected: object) -> bool:
    return type(actual) is type(expected) and actual == expected


@dataclass(frozen=True)
class FaqDocument:
    sections: set[str]
    start: datetime | None
    end: datetime | None
    supersedes: str | None


def read_documents(root: Path) -> dict[str, FaqDocument]:
    """从正式题库源目录读取 FAQ，额外校验首版篇幅。"""
    paths = _files(root, "knowledge/*.md")
    if len(paths) != 20:
        raise ValueError("Expected 20 official FAQ documents")
    return validate_documents(
        ((path.name, path.read_text(encoding="utf-8")) for path in paths), official=True
    )


def validate_documents(
    sources: Iterable[tuple[str, str]], *, official: bool = False
) -> dict[str, FaqDocument]:
    """校验 FAQ 原文、真实分块位置及版本关系；摄入只传已核对哈希的文档。"""
    documents: dict[str, FaqDocument] = {}
    versions: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for path, content in sources:
        front, opening, rest = content.partition("\n")
        if not (opening and front.strip() == "---"):
            raise ValueError(str(f"Missing YAML front matter: {path}"))
        closing = re.search(r"(?m)^---[ \t]*$", rest)
        if not (closing):
            raise ValueError(str(f"Unclosed YAML front matter: {path}"))
        meta = require_object(cast(object, yaml.safe_load(rest[: closing.start()])))
        doc_id = _text(meta, "doc_id")
        match = DOC_ID.fullmatch(doc_id)
        if not (match and doc_id not in documents):
            raise ValueError(str(f"Invalid or duplicate FAQ doc_id: {doc_id}"))
        if (meta.get("doc_type"), meta.get("audience"), meta.get("indexing")) != (
            "faq",
            "external",
            "include",
        ):
            raise ValueError(f"Expected indexable public FAQ (audience=external): {path}")
        body = rest[closing.end() :]
        sections: set[str] = set()
        for start, end, _ in section_ranges(body, 0):
            lines = body[start:end].splitlines()
            heading = re.fullmatch(r" {0,3}##[ \t]+(.+?)\s*", lines[0])
            if heading:
                title = heading[1]
                if title in sections or not any(line.strip() for line in lines[1:]):
                    raise ValueError(f"{doc_id}: duplicate or empty FAQ section: {title}")
                sections.add(title)
        if not sections or (official and not 4 <= len(sections) <= 6):
            raise ValueError(f"{doc_id}: invalid number of FAQ sections: {len(sections)}")
        document, body_start = parse_document(path, content)
        if not (document.doc_id == doc_id):
            raise ValueError("Invalid FAQ content in read_documents")
        if not (
            sections
            == {
                _text(chunk.source_locator, "heading")
                for chunk in split_document(document, body_start, 1000)
                if chunk.source_locator["heading"]
            }
        ):
            raise ValueError(str(f"{doc_id}: section titles must match the real chunk locators"))
        start = _time(meta["effective_from"]) if "effective_from" in meta else None
        end = _time(meta["effective_to"]) if meta.get("effective_to") is not None else None
        supersedes = _text(meta, "supersedes") if meta.get("supersedes") is not None else None
        if not (end is None or (start is not None and start < end)):
            raise ValueError(str(doc_id))
        documents[doc_id] = FaqDocument(sections, start, end, supersedes)
        versions[match[1]].append((int(match[2]), doc_id))
    for chain in versions.values():
        previous = None
        for _, doc_id in sorted(chain):
            doc = documents[doc_id]
            if not (doc.supersedes == previous):
                raise ValueError(str(f"{doc_id}: expected supersedes={previous}"))
            if previous is not None:
                prior = documents[previous]
                if not (
                    prior.end is not None and doc.start is not None and (prior.end <= doc.start)
                ):
                    raise ValueError(
                        str(f"{previous} and {doc_id}: missing or overlapping effective periods")
                    )
            previous = doc_id
    return documents


def _record_ref(row: JsonObject, key: str = "record_id") -> RecordRef:
    kind = _text(row, "record_type")
    if kind not in RECORD_KEYS:
        raise ValueError(str(f"Invalid record_type: {kind}"))
    return kind, _text(row, key)


@dataclass(frozen=True)
class FaqRecords:
    payers: Rows
    payments: Rows
    refunds: Rows


def read_seed(root: Path) -> FaqRecords:
    return FaqRecords(
        _rows(root, "seed/payers.jsonl", "payer_id"),
        _rows(root, "seed/payments/*.jsonl", "payment_id"),
        _rows(root, "seed/refunds/*.jsonl", "refund_id"),
    )


def validate_snapshot(
    snapshot: FaqRecords,
) -> tuple[dict[RecordRef, JsonObject], dict[RecordRef, str]]:
    """校验记录及退款归属，返回可供金标匹配的记录和持有人索引。"""
    payers, payments, refunds = snapshot.payers, snapshot.payments, snapshot.refunds
    if not (
        len(payers) >= 2 and len({_text(p, "subject_id") for p in payers.values()}) == len(payers)
    ):
        raise ValueError("Invalid FAQ content in validate_snapshot")
    records: dict[RecordRef, JsonObject] = {}
    owners: dict[RecordRef, str] = {}
    orders: dict[str, set[str]] = defaultdict(set)
    refunded: Counter[str] = Counter()
    for payment_id, payment in payments.items():
        if not (
            _text(payment, "payment_id") == payment_id
            and payment_id.startswith("pay_")
            and RECORD_ID.fullmatch(payment_id)
        ):
            raise ValueError(str(payment_id))
        payer_id = _text(payment, "payer_id")
        if payer_id not in payers:
            raise ValueError(str((payment_id, payer_id)))
        if _text(payment, "status") not in {"succeeded", "pending", "failed", "reversed"}:
            raise ValueError(str(payment_id))
        if not (require_int(payment.get("amount_minor")) > 0):
            raise ValueError(str(payment_id))
        if not (_text(payment, "currency") == "CNY"):
            raise ValueError(str(payment_id))
        if _text(payment, "channel") not in {"balance", "bank"}:
            raise ValueError(str(payment_id))
        _ = _time(payment.get("occurred_at"))
        orders[_text(payment, "order_ref")].add(payer_id)
        records["payment", payment_id] = payment
        owners["payment", payment_id] = payer_id
    for refund_id, refund in refunds.items():
        if not (
            _text(refund, "refund_id") == refund_id
            and refund_id.startswith("ref_")
            and RECORD_ID.fullmatch(refund_id)
        ):
            raise ValueError(str(refund_id))
        payment_id = _text(refund, "payment_id")
        if payment_id not in payments:
            raise ValueError(str((refund_id, payment_id)))
        payment = payments[payment_id]
        if not (payment["status"] == "succeeded" and "payer_id" not in refund):
            raise ValueError(str(refund_id))
        status = _text(refund, "status")
        if status not in {"requested", "processing", "completed", "failed"}:
            raise ValueError(str(refund_id))
        if not (_text(refund, "currency") == payment["currency"]):
            raise ValueError(str(refund_id))
        requested = _time(refund.get("requested_at"))
        if not (_time(payment["occurred_at"]) <= requested):
            raise ValueError(str(refund_id))
        if refund["settled_at"] is not None:
            if not (status == "completed" and requested <= _time(refund["settled_at"])):
                raise ValueError(str(refund_id))
        amount = require_int(refund.get("amount_minor"))
        if not (0 < amount <= require_int(payment["amount_minor"])):
            raise ValueError(str(refund_id))
        if status != "failed":
            refunded[payment_id] += amount
        records["refund", refund_id] = refund
        owners["refund", refund_id] = owners["payment", payment_id]
    if not (any(len(payer_ids) > 1 for payer_ids in orders.values())):
        raise ValueError("Missing cross-payer order collision")
    for payment_id, total in refunded.items():
        if not (total <= require_int(payments[payment_id]["amount_minor"])):
            raise ValueError(str((payment_id, total)))
    return records, owners


def read_benchmark(root: Path) -> tuple[Rows, Rows]:
    """读取分开的题面与金标，检查 ID 配对和分层配额。"""
    questions: Rows = {}
    rubrics: Rows = {}
    for index, split in enumerate(("dev", "test")):
        base = f"benchmark/{split}"
        split_questions = _rows(root, f"{base}/questions/*.jsonl", "question_id")
        split_rubrics = _rows(root, f"{base}/rubrics/*.jsonl", "question_id")
        if not (split_questions.keys() == split_rubrics.keys()):
            raise ValueError(str(f"{split}: question/rubric ID mismatch"))
        if not (not questions.keys() & split_questions.keys()):
            raise ValueError("Question IDs must be globally unique")
        if not all(q.get("split") == split for q in split_questions.values()):
            raise ValueError(f"{split}: question split mismatch")
        type_counts = Counter(_text(q, "question_type") for q in split_questions.values())
        if type_counts != {kind: counts[index] for kind, counts in TYPE_COUNTS.items()}:
            raise ValueError(f"{split}: unexpected question type counts {dict(type_counts)}")
        expected = ((12, 20, 8), (18, 30, 12))[index]
        difficulty_counts = Counter(_text(q, "difficulty") for q in split_questions.values())
        if difficulty_counts != dict(zip(DIFFICULTIES, expected, strict=True)):
            raise ValueError(f"{split}: unexpected difficulty counts {dict(difficulty_counts)}")
        for kind in TYPE_COUNTS:
            if not (
                {q["difficulty"] for q in split_questions.values() if q["question_type"] == kind}
                == set(DIFFICULTIES)
            ):
                raise ValueError(str((split, kind)))
        questions.update(split_questions)
        rubrics.update(split_rubrics)
    return questions, rubrics


def _labeled_items(rubric: JsonObject, key: str) -> list[JsonObject]:
    items = _items(rubric, key)
    if not (len({_text(item, "id") for item in items}) == len(items)):
        raise ValueError(str((rubric["question_id"], key)))
    return items


def _check_dialogue(
    question: JsonObject,
    rubric: JsonObject,
    behaviors: list[JsonObject],
) -> list[str]:
    question_id = question["question_id"]
    question_type = question["question_type"]
    stop = require_object(rubric.get("stop"))
    limit = require_int(stop.get("max_turns"))
    clarify_only = question_type == "ambiguous_clarification"
    if not (limit >= 1 and stop.get("kind") == ("clarify" if clarify_only else "answer")):
        raise ValueError(str(question_id))
    clarifications = sorted(
        (require_int(b.get("turn")), _text(b, "missing"))
        for b in behaviors
        if b["kind"] == "clarify"
    )
    if not (all(1 <= require_int(b["turn"]) <= limit for b in behaviors if "turn" in b)):
        raise ValueError(str(question_id))
    if clarify_only:
        if not (clarifications):
            raise ValueError(str(question_id))
    scripted = "script" in question
    if not (scripted == ("script_family" in question)):
        raise ValueError(str(question_id))
    if not (scripted or question_type != "multi_turn"):
        raise ValueError(str(question_id))
    if not scripted:
        return []
    script = _items(question, "script")
    triggers = [_text(step, "when") for step in script]
    if not (len(triggers) >= 2 and len(set(triggers)) == len(triggers)):
        raise ValueError(str(question_id))
    if question_type == "multi_turn":
        if not (clarifications == list(enumerate(triggers, 1))):
            raise ValueError(str(question_id))
        if not ({"reuse", "no_reask", "answer"} <= {b["kind"] for b in behaviors}):
            raise ValueError(str(question_id))
        if not (len(script) + 1 <= limit):
            raise ValueError(str(f"{question_id}: budget does not fit the final answer"))
        if not (
            all(
                b.get("turn", len(script) + 1) == len(script) + 1
                for b in behaviors
                if b["kind"] == "answer"
            )
        ):
            raise ValueError(str(question_id))
    return [_text(step, "user_text") for step in script]


def _check_evidence(
    rubric: JsonObject,
    payer_id: str,
    documents: dict[str, FaqDocument],
    records: dict[RecordRef, JsonObject],
    owners: dict[RecordRef, str],
) -> tuple[list[set[str]], set[str], dict[RecordRef, set[str]]]:
    kinds: list[set[str]] = []
    cited_docs: set[str] = set()
    cited_fields: dict[RecordRef, set[str]] = defaultdict(set)
    for assertion in _labeled_items(rubric, "required_assertions"):
        _ = _text(assertion, "description")
        evidence = _items(assertion, "evidence")
        context = (rubric["question_id"], assertion["id"])
        if not (evidence):
            raise ValueError(str(context))
        kinds.append({_text(item, "kind") for item in evidence})
        for item in evidence:
            if item["kind"] == "document":
                doc_id = _text(item, "doc_id")
                title = _text(item, "section_title")
                if not (doc_id in documents and title in documents[doc_id].sections):
                    raise ValueError(str((context, item)))
                cited_docs.add(doc_id)
            else:
                if not (item["kind"] == "record"):
                    raise ValueError(str((context, item)))
                ref = _record_ref(item)
                if not (owners.get(ref) == payer_id):
                    raise ValueError(str((context, "record missing or not owned", ref)))
                fields = require_object(item.get("fields"))
                if not (fields):
                    raise ValueError(str((context, ref)))
                actual = records[ref]
                for field, expected in fields.items():
                    if not (field in actual and _same_value(actual[field], expected)):
                        raise ValueError(str((context, ref, field, expected, actual.get(field))))
                cited_fields[ref].update(fields)
    return kinds, cited_docs, cited_fields


def _check_dependencies(
    rubric: JsonObject,
    initial_text: str,
    cited_fields: dict[RecordRef, set[str]],
    records: dict[RecordRef, JsonObject],
) -> None:
    dependencies = _items(rubric, "dependencies")
    if not (dependencies):
        raise ValueError(str(rubric["question_id"]))
    earlier_sources: set[RecordRef] = set()
    for dependency in dependencies:
        source = require_object(dependency.get("from"))
        target = require_object(dependency.get("to"))
        src, dst = _record_ref(source), _record_ref(target)
        field, lookup = _text(source, "field"), _text(target, "filter")
        context = (rubric["question_id"], dependency)
        if not (field in cited_fields.get(src, ()) and dst in cited_fields):
            raise ValueError(str(context))
        output = records[src][field]
        if not (lookup in records[dst] and _same_value(output, records[dst][lookup])):
            raise ValueError(str(context))
        if not (output is not None and str(output) and (str(output) not in initial_text)):
            raise ValueError(str(context))
        if not (src != dst and dst not in earlier_sources):
            raise ValueError(str((context, "invalid dependency order")))
        earlier_sources.add(src)


def _check_temporal(
    question: JsonObject,
    rubric: JsonObject,
    cited_docs: set[str],
    cited_fields: dict[RecordRef, set[str]],
    documents: dict[str, FaqDocument],
    records: dict[RecordRef, JsonObject],
) -> tuple[str, str, datetime] | None:
    when = _time(question["decision_at"]) if "decision_at" in question else None
    for doc_id in cited_docs:
        doc = documents[doc_id]
        if when is None:
            if doc.end is not None:
                raise ValueError(
                    str((question["question_id"], "historical citation needs a date", doc_id))
                )
        else:
            if not (
                (doc.start is None or doc.start <= when) and (doc.end is None or when < doc.end)
            ):
                raise ValueError(
                    str(
                        (
                            question["question_id"],
                            "citation outside its effective period",
                            doc_id,
                            when,
                        )
                    )
                )
    if question["question_type"] != "temporal_conflict":
        return None
    temporal = require_object(rubric.get("temporal"))
    if not (when is not None and _time(temporal.get("decision_at")) == when):
        raise ValueError(str(question["question_id"]))
    basis, doc_id = _text(temporal, "basis"), _text(temporal, "doc_id")
    if not (doc_id in cited_docs and documents[doc_id].start is not None):
        raise ValueError(str(temporal))
    if basis not in {"as_of", "payment.occurred_at"}:
        raise ValueError(str(temporal))
    if basis == "payment.occurred_at":
        if not (
            any(
                (
                    ref[0] == "payment"
                    and "occurred_at" in fields
                    and (_time(records[ref]["occurred_at"]) == when)
                    for ref, fields in cited_fields.items()
                )
            )
        ):
            raise ValueError(
                str((question["question_id"], "no cited payment supports the decision time"))
            )
    return basis, doc_id, when


def validate_content(root: Path, snapshot: FaqRecords | None = None) -> FaqRecords:
    """验证正式版源数据或冻结包，协调版本、证据与行为的交叉检查。"""
    documents = read_documents(root)
    snapshot = snapshot if snapshot is not None else read_seed(root)
    records, owners = validate_snapshot(snapshot)
    payers = snapshot.payers
    questions, rubrics = read_benchmark(root)
    groups: dict[tuple[str, str], tuple[str, str]] = {}
    utterances: dict[str, set[str]] = defaultdict(set)
    lookup_controls: dict[tuple[str, str], set[str]] = defaultdict(set)
    temporal_examples: set[tuple[str, str, datetime]] = set()
    completion_is_not_arrival = False
    for question_id, question in questions.items():
        rubric = rubrics[question_id]
        kind, payer_id = _text(question, "question_type"), _text(question, "payer_id")
        split, group_id = _text(question, "split"), _text(question, "group_id")
        initial = _text(question, "user_text")
        if payer_id not in payers:
            raise ValueError(str(question_id))
        behaviors = _labeled_items(rubric, "required_behaviors")
        for item in behaviors:
            _ = _text(item, "kind")
        dialogue = [initial, *_check_dialogue(question, rubric, behaviors)]
        utterances[split].update(dialogue)
        forbidden = _labeled_items(rubric, "forbidden_assertions")
        for item in forbidden:
            _ = _text(item, "description")
        kinds, docs, cited_fields = _check_evidence(rubric, payer_id, documents, records, owners)
        if kind == "ambiguous_clarification":
            if not (not kinds and forbidden):
                raise ValueError(str(question_id))
        else:
            if not (kinds):
                raise ValueError(str(question_id))
        if kind == "simple_faq":
            if not (all(evidence == {"document"} for evidence in kinds)):
                raise ValueError(str(question_id))
        if kind == "personalized_cross_source":
            if not ({"document"} in kinds and {"record"} in kinds):
                raise ValueError(
                    str(f"{question_id}: FAQ and owned record must support separate assertions")
                )
        if kind == "multi_hop":
            _check_dependencies(rubric, initial, cited_fields, records)
        temporal = _check_temporal(question, rubric, docs, cited_fields, documents, records)
        if temporal is not None:
            temporal_examples.add(temporal)
        completion_is_not_arrival |= any(
            ref[0] == "refund"
            and {"status", "settled_at"} <= fields
            and records[ref]["status"] == "completed"
            and records[ref]["settled_at"] is None
            for ref, fields in cited_fields.items()
        )

        for record_type, _ in cited_fields:
            lookup_controls[payer_id, record_type].add("owned")
        associated_ids = {ref[1] for ref in cited_fields}
        associated_ids.update(
            match[0]
            for text in dialogue
            for match in RECORD_IN_TEXT.finditer(text)
            if match[0].startswith(("pay_", "ref_"))
        )
        for item in behaviors:
            if item["kind"] == "lookup_empty":
                ref = _record_ref(item, "identifier")
                owner = owners.get(ref)
                if not (owner != payer_id):
                    raise ValueError(
                        str((question_id, "lookup_empty targets an owned record", ref))
                    )
                lookup_controls[payer_id, ref[0]].add("missing" if owner is None else "other_payer")
                associated_ids.add(ref[1])
        # 关联同一笔付款的不同退款也属于同一数据分组。
        associated_ids.update(
            _text(records["refund", identifier], "payment_id")
            for identifier in tuple(associated_ids)
            if ("refund", identifier) in records
        )
        keys = [
            (key, _text(question, key))
            for key in ("group_id", "variant_family", "script_family")
            if key in question
        ] + [("record", identifier) for identifier in associated_ids]
        for key in keys:
            if not (groups.setdefault(key, (split, group_id)) == (split, group_id)):
                raise ValueError(str((question_id, key, groups[key], (split, group_id))))

    if not (not utterances["dev"] & utterances["test"]):
        raise ValueError("A verbatim utterance crosses splits")
    if not ({basis for basis, _, _ in temporal_examples} == {"as_of", "payment.occurred_at"}):
        raise ValueError("Invalid FAQ content in validate_content")
    tested_versions = {doc_id for _, doc_id, _ in temporal_examples}
    switches = {doc_id: doc.supersedes for doc_id, doc in documents.items() if doc.supersedes}
    if not (switches and switches.keys() | switches.values() <= tested_versions):
        raise ValueError(str(switches))
    if not (
        any(
            (
                doc_id in switches
                and when == documents[doc_id].start == documents[switches[doc_id]].end
                for _, doc_id, when in temporal_examples
            )
        )
    ):
        raise ValueError("No temporal question lands exactly on a version switch")
    if not (
        any(
            {"owned", "missing", "other_payer"} <= controls for controls in lookup_controls.values()
        )
    ):
        raise ValueError(
            "Missing same-payer, same-type controls for owned, other-payer, and nonexistent records"
        )
    if not (completion_is_not_arrival):
        raise ValueError("Missing evidence: completed refund without confirmed arrival")
    return snapshot
