import types
import unittest
from typing import (
    Literal,
    NotRequired,
    TypedDict,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

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
}
ALL_JSON_TYPES = {
    "array",
    "boolean",
    "integer",
    "null",
    "number",
    "object",
    "string",
}
UNSUPPORTED_STRUCTURAL_KEYWORDS = ("anyOf", "oneOf", "prefixItems")


class _ObsoleteCritique(TypedDict, total=False):
    target_source_finding_id: str
    reviewer: str
    verdict: Literal["agree", "dispute", "noise", "duplicate"]
    rationale: str
    duplicate_of: str | None
    severity_adjustment: domain_types.Severity | None


class _WrongScalarPostResult(TypedDict):
    schema_version: Literal["post_result.v1"]
    run_id: int
    status: domain_types.PostStatus
    head_sha: str
    current_head_sha: str
    created_discussions: str
    updated_discussions: int
    resolved_discussions: int
    skipped_unchanged: int
    stale_unverified: int
    posted_discussions: list[domain_types.PostedDiscussion]
    warnings: list[str]
    summary_comment: NotRequired[domain_types.SummaryComment]


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


def _json_type_for_value(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if isinstance(value, str):
        return "string"
    raise AssertionError(f"unsupported JSON literal value: {value!r}")


def _annotation_json_types(annotation: object) -> set[str] | None:
    annotation = _unwrap_alias(annotation)
    origin = get_origin(annotation)
    if origin in (types.UnionType, Union):
        member_types: set[str] = set()
        for member in get_args(annotation):
            resolved = _annotation_json_types(member)
            if resolved is None:
                return None
            member_types.update(resolved)
        return member_types
    if origin is Literal:
        return {_json_type_for_value(value) for value in get_args(annotation)}
    if is_typeddict(annotation) or origin is dict:
        return {"object"}
    if origin is list:
        return {"array"}
    return {
        str: {"string"},
        int: {"integer"},
        float: {"number"},
        bool: {"boolean"},
        type(None): {"null"},
    }.get(annotation)


def _schema_json_types(node: dict[str, object]) -> set[str] | None:
    schema_type = node.get("type")
    if isinstance(schema_type, str):
        return {schema_type}
    if isinstance(schema_type, list):
        assert all(isinstance(item, str) for item in schema_type)
        return set(schema_type)
    if "const" in node:
        return {_json_type_for_value(node["const"])}
    enum = node.get("enum")
    if isinstance(enum, list):
        return {_json_type_for_value(value) for value in enum}
    return None


def _assert_annotation_matches_schema(
    annotation: object,
    schema_node: dict[str, object],
    schema_root: dict[str, object],
    label: str,
) -> None:
    schema_node = _resolve_ref(schema_node, schema_root)
    unsupported = [
        keyword for keyword in UNSUPPORTED_STRUCTURAL_KEYWORDS if keyword in schema_node
    ]
    assert not unsupported, (
        f"{label}: unsupported schema composition: {', '.join(unsupported)}"
    )
    assert _is_nullable(annotation) == _schema_is_nullable(schema_node), label
    expected_json_types = _schema_json_types(schema_node)
    if expected_json_types is not None:
        assert _annotation_json_types(annotation) == expected_json_types, label

    expected_values: set[object] | None = None
    if "enum" in schema_node:
        enum = schema_node["enum"]
        assert isinstance(enum, list)
        expected_values = set(enum)
    elif "const" in schema_node:
        expected_values = {schema_node["const"]}
    if expected_values is not None:
        assert _literal_values(annotation) == expected_values, label

    unwrapped = _unwrap_alias(annotation)
    members = (
        get_args(unwrapped)
        if get_origin(unwrapped) in (types.UnionType, Union)
        else (unwrapped,)
    )
    for member in members:
        member = _unwrap_alias(member)
        if member is type(None):
            continue
        if is_typeddict(member):
            _assert_typed_dict_matches_schema(member, schema_node, schema_root)
            continue
        origin = get_origin(member)
        if origin is list:
            items = schema_node.get("items")
            assert isinstance(items, dict), (
                f"{label}: array schema must declare object-valued items"
            )
            _assert_annotation_matches_schema(
                get_args(member)[0], items, schema_root, f"{label}[]"
            )
            continue
        if origin is dict:
            key_type, value_type = get_args(member)
            assert _unwrap_alias(key_type) is str, f"{label} key"
            additional_properties = schema_node.get("additionalProperties")
            assert additional_properties is not False, (
                f"{label}: dict annotation cannot match a closed object schema"
            )
            if isinstance(additional_properties, dict):
                _assert_annotation_matches_schema(
                    value_type,
                    additional_properties,
                    schema_root,
                    f"{label} value",
                )
            else:
                assert additional_properties in (None, True), (
                    f"{label}: additionalProperties must be a schema or boolean"
                )
                assert _annotation_json_types(value_type) == ALL_JSON_TYPES, (
                    f"{label}: open object values must cover the full JsonValue domain"
                )


def _assert_typed_dict_matches_schema(
    artifact_type: type[object],
    schema_node: dict[str, object],
    schema_root: dict[str, object],
) -> None:
    # TypedDicts describe structural JSON types. Patterns, numeric bounds, and
    # other value constraints remain the responsibility of schema validation.
    schema_node = _resolve_ref(schema_node, schema_root)
    assert is_typeddict(artifact_type)
    assert schema_node.get("additionalProperties") is False
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
        _assert_annotation_matches_schema(annotation, raw_field_schema, schema_root, field)


class ArtifactTypeSchemaAlignmentTests(unittest.TestCase):
    def test_artifact_types_recursively_match_schemas(self) -> None:
        for schema_name, artifact_type in ARTIFACT_TYPES.items():
            with self.subTest(schema_name=schema_name, artifact_type=artifact_type.__name__):
                schema = load_schema(schema_name)
                _assert_typed_dict_matches_schema(artifact_type, schema, schema)

    def test_conditional_branches_reference_only_live_group_fields(self) -> None:
        """`allOf` is invisible to the alignment walk above.

        `_assert_typed_dict_matches_schema` compares `properties` against the
        TypedDict and never descends into `allOf`, so a stale reference to a
        removed field left inside the `ambiguous_unassigned` conditional would
        pass every other check in this module while constraining nothing.
        """
        group = load_schema("consensus.schema.json")["$defs"]["group"]
        live_fields = set(group["properties"])

        for index, clause in enumerate(group["allOf"]):
            for branch in ("if", "then", "else"):
                named = set(clause.get(branch, {}).get("properties", {}))
                named |= set(clause.get(branch, {}).get("required", []))
                with self.subTest(clause=index, branch=branch):
                    self.assertEqual(named - live_fields, set())

    def test_schema_alignment_comparator_branches(self) -> None:
        critique_root = load_schema("critique_batch.schema.json")
        critique_schema = critique_root["properties"]["critiques"]["items"]
        post_schema = load_schema("post_result.schema.json")
        string_array = {"type": "array", "items": {"type": "string"}}
        string_dictionary = {
            "type": "object",
            "additionalProperties": {"type": "string"},
        }
        nested_collection = {"type": "array", "items": string_dictionary}
        itemless_array = {"type": "array"}
        open_object = {"type": "object"}
        explicit_open_object = {"type": "object", "additionalProperties": True}
        composition = {"anyOf": [{"type": "string"}, {"type": "integer"}]}

        cases: tuple[
            tuple[str, object, dict[str, object], dict[str, object], str | None], ...
        ] = (
            ("obsolete critique keys", _ObsoleteCritique, critique_schema, critique_root, ""),
            (
                "wrong scalar types",
                _WrongScalarPostResult,
                post_schema,
                post_schema,
                r"^run_id\b",
            ),
            ("wrong array item scalar", list[int], string_array, string_array, ""),
            (
                "wrong dictionary value scalar",
                dict[str, int],
                string_dictionary,
                string_dictionary,
                "",
            ),
            (
                "nested array and dictionary scalars",
                list[dict[str, str]],
                nested_collection,
                nested_collection,
                None,
            ),
            (
                "array without items",
                list[str],
                itemless_array,
                itemless_array,
                "values: array schema must declare object-valued items",
            ),
            (
                "open object rejects narrow dictionary",
                dict[str, str],
                open_object,
                open_object,
                "open object values must cover the full JsonValue domain",
            ),
            (
                "open object accepts JsonValue dictionary",
                domain_types.JsonObject,
                explicit_open_object,
                explicit_open_object,
                None,
            ),
            (
                "unsupported schema composition", str | int, composition, composition,
                "values: unsupported schema composition: anyOf",
            ),
        )

        for name, annotation, schema, root, expected_error in cases:
            with self.subTest(name=name):
                if expected_error is None:
                    _assert_annotation_matches_schema(annotation, schema, root, "values")
                elif expected_error:
                    with self.assertRaisesRegex(AssertionError, expected_error):
                        _assert_annotation_matches_schema(
                            annotation, schema, root, "values"
                        )
                else:
                    with self.assertRaises(AssertionError):
                        _assert_annotation_matches_schema(
                            annotation, schema, root, "values"
                        )


if __name__ == "__main__":
    unittest.main()
