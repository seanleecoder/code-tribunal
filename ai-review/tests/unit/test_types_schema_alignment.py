from __future__ import annotations

import types
import unittest
from typing import Literal, TypedDict, Union, get_args, get_origin, get_type_hints, is_typeddict

from ai_review import types as domain_types
from ai_review.schema import load_schema

ARTIFACT_TYPES = {
    "raw_finding_batch.schema.json": domain_types.RawFindingBatch,
    "finding_batch.schema.json": domain_types.FindingBatch,
    "critique_batch.schema.json": domain_types.CritiqueBatch,
    "adapter_status.schema.json": domain_types.AdapterStatusArtifact,
    "consensus.schema.json": domain_types.Consensus,
    "state.schema.json": domain_types.State,
    "state_aliases.schema.json": domain_types.StateAliasesArtifact,
    "post_result.schema.json": domain_types.PostResult,
    "gate_result.schema.json": domain_types.GateResult,
}


class _ObsoleteCritique(TypedDict, total=False):
    target_source_finding_id: str
    reviewer: str
    verdict: Literal["agree", "dispute", "noise", "duplicate"]
    rationale: str
    duplicate_of: str | None
    severity_adjustment: domain_types.Severity | None


def _unwrap_alias(annotation: object) -> object:
    while hasattr(annotation, "__value__"):
        annotation = annotation.__value__
    return annotation


def _resolve_ref(node: dict[str, object], root: dict[str, object]) -> dict[str, object]:
    while "$ref" in node:
        ref = node["$ref"]
        assert isinstance(ref, str) and ref.startswith("#/")
        target: object = root
        for part in ref[2:].split("/"):
            assert isinstance(target, dict)
            target = target[part]
        assert isinstance(target, dict)
        node = target
    return node


def _literal_values(annotation: object) -> set[object] | None:
    annotation = _unwrap_alias(annotation)
    origin = get_origin(annotation)
    if origin is Literal:
        return set(get_args(annotation))
    if origin in (types.UnionType, Union):
        values: set[object] = set()
        for member in get_args(annotation):
            if member is type(None):
                values.add(None)
                continue
            member_values = _literal_values(member)
            if member_values is None:
                return None
            values.update(member_values)
        return values
    return None


def _is_nullable(annotation: object) -> bool:
    annotation = _unwrap_alias(annotation)
    if annotation is type(None):
        return True
    if get_origin(annotation) is Literal:
        return None in get_args(annotation)
    if get_origin(annotation) in (types.UnionType, Union):
        return any(_is_nullable(member) for member in get_args(annotation))
    return False


def _schema_is_nullable(node: dict[str, object]) -> bool:
    schema_type = node.get("type")
    return (
        schema_type == "null"
        or isinstance(schema_type, list)
        and "null" in schema_type
        or isinstance(node.get("enum"), list)
        and None in node["enum"]
        or node.get("const", object()) is None
    )


def _assert_typed_dict_matches_schema(
    artifact_type: type[object],
    schema_node: dict[str, object],
    schema_root: dict[str, object],
) -> None:
    schema_node = _resolve_ref(schema_node, schema_root)
    assert is_typeddict(artifact_type)
    properties = schema_node.get("properties")
    assert isinstance(properties, dict)
    required = schema_node.get("required", [])
    assert isinstance(required, list)

    hints = get_type_hints(artifact_type)
    assert set(hints) == set(properties)
    assert set(artifact_type.__required_keys__) == set(required)
    assert set(artifact_type.__optional_keys__) == set(properties) - set(required)

    for field, annotation in hints.items():
        raw_field_schema = properties[field]
        assert isinstance(raw_field_schema, dict)
        field_schema = _resolve_ref(raw_field_schema, schema_root)
        assert _is_nullable(annotation) == _schema_is_nullable(field_schema), field

        expected_values: set[object] | None = None
        if "enum" in field_schema:
            enum = field_schema["enum"]
            assert isinstance(enum, list)
            expected_values = set(enum)
        elif "const" in field_schema:
            expected_values = {field_schema["const"]}
        if expected_values is not None:
            assert _literal_values(annotation) == expected_values, field

        unwrapped = _unwrap_alias(annotation)
        if is_typeddict(unwrapped):
            _assert_typed_dict_matches_schema(unwrapped, field_schema, schema_root)
            continue
        if get_origin(unwrapped) is list:
            item_type = _unwrap_alias(get_args(unwrapped)[0])
            items = field_schema.get("items")
            if is_typeddict(item_type):
                assert isinstance(items, dict)
                _assert_typed_dict_matches_schema(item_type, items, schema_root)


class ArtifactTypeSchemaAlignmentTests(unittest.TestCase):
    def test_artifact_types_recursively_match_schemas(self) -> None:
        for schema_name, artifact_type in ARTIFACT_TYPES.items():
            with self.subTest(schema_name=schema_name, artifact_type=artifact_type.__name__):
                schema = load_schema(schema_name)
                _assert_typed_dict_matches_schema(artifact_type, schema, schema)

    def test_obsolete_critique_keys_fail_alignment(self) -> None:
        schema = load_schema("critique_batch.schema.json")
        critique_schema = schema["properties"]["critiques"]["items"]
        with self.assertRaises(AssertionError):
            _assert_typed_dict_matches_schema(_ObsoleteCritique, critique_schema, schema)


if __name__ == "__main__":
    unittest.main()
