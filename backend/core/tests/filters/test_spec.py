"""Tests for rule specs: to_spec(), imap_thingy.spec loaders, schema, derived eq/repr."""

import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, cast

import jsonschema
import pytest

import imap_thingy.filters as filters_module
from imap_thingy.core import Flag, Message, Path
from imap_thingy.filters import (
    Action,
    Anything,
    BccContains,
    BccIs,
    BccMatches,
    BodyContains,
    BodyMatches,
    CcContains,
    CcIs,
    CcMatches,
    Criterion,
    Delete,
    Filter,
    FromContains,
    FromIs,
    FromMatches,
    FromMatchesName,
    HasFlag,
    If,
    IsAnswered,
    IsRead,
    IsStarred,
    IsUnanswered,
    IsUnread,
    IsUnstarred,
    LacksFlag,
    MarkAsAnswered,
    MarkAsRead,
    MarkAsUnanswered,
    MarkAsUnread,
    MoveTo,
    NoOp,
    OlderThan,
    OlderThanDays,
    Star,
    SubjectContains,
    SubjectIs,
    SubjectMatches,
    ToContains,
    ToIs,
    ToMatches,
    Trash,
    Unstar,
)
from imap_thingy.spec import (
    NotSerializableError,
    SpecError,
    json_schema,
    load_action,
    load_criterion,
    load_filter,
)

CRITERIA: list[Criterion] = [
    Anything(),
    BccContains("list@"),
    BccIs("me@example.com"),
    BccMatches(r".*@example\.com"),
    BodyContains("unsubscribe"),
    BodyMatches(r"order #\d+"),
    CcContains("team"),
    CcIs("boss@example.com"),
    CcMatches(r"boss@.*"),
    FromContains("noreply"),
    FromIs("news@example.com"),
    FromMatches(r".*@example\.(com|org)"),
    FromMatchesName(r"Example .*"),
    HasFlag(Flag.FLAGGED),
    IsAnswered(),
    IsRead(),
    IsStarred(),
    IsUnanswered(),
    IsUnread(),
    IsUnstarred(),
    LacksFlag(Flag.SEEN),
    OlderThan(date(2025, 1, 31)),
    OlderThanDays(30),
    SubjectContains("Invoice"),
    SubjectIs("Weekly digest"),
    SubjectMatches(r"\[Preview\] .*"),
    ToContains("members@"),
    ToContains("members@", incl_cc=True, incl_bcc=True),
    ToIs("me@example.com"),
    ToIs("me@example.com", incl_cc=True),
    ToMatches(r"me\+.*@example\.com"),
    ToMatches(r"me\+.*@example\.com", incl_bcc=True),
]

ACTIONS: list[Action] = [
    Delete(),
    MarkAsAnswered(),
    MarkAsRead(),
    MarkAsUnanswered(),
    MarkAsUnread(),
    MoveTo(Path(["Archive", "2025"])),
    MoveTo(Path("INBOX")),
    NoOp(),
    Star(),
    Trash(),
    Trash(Path(["Deleted Items"])),
    Unstar(),
]

COMBINED_CRITERIA: list[Criterion] = [
    FromIs("a@example.com") & SubjectContains("x"),
    FromIs("a@example.com") | ToIs("b@example.com") | CcIs("c@example.com"),
    ~IsRead(),
    ~(FromIs("a@example.com") & IsUnread()),
    (FromIs("a@example.com") | FromIs("b@example.com")) & ~SubjectMatches(r"\[Preview\] .*"),
    BodyMatches("x") & OlderThan("01-Jan-2025") & HasFlag(Flag.ANSWERED),
    OlderThanDays(7) & ~IsStarred(),
]

COMBINED_ACTIONS: list[Action] = [
    MarkAsRead() + MoveTo(Path("Archive")),
    MarkAsRead().then(Star()).then(Trash()),
]


def _msg(**fields: Any) -> Message:  # noqa: ANN401
    flags = fields.pop("flags", [])
    parsed = {
        "from_": [],
        "to": [],
        "cc": [],
        "bcc": [],
        "subject": "",
        "text_plain": [],
        "text_html": [],
        "date": None,
        **fields,
    }
    return Message(0, cast("Any", SimpleNamespace(**parsed)), flags)


MESSAGES = [
    _msg(),
    _msg(
        from_=[("Example News", "news@example.com")],
        to=[("", "me@example.com")],
        subject="Weekly digest",
        text_plain=["order #12 unsubscribe"],
        date=datetime(2024, 5, 1),  # noqa: DTZ001
        flags=[b"\\Seen", b"\\Answered"],
    ),
    _msg(
        from_=[("", "a@example.com")],
        cc=[("", "boss@example.com")],
        bcc=[("", "me+x@example.com")],
        subject="[Preview] Invoice",
        date=datetime(2026, 1, 1),  # noqa: DTZ001
        flags=[b"\\Flagged"],
    ),
]


def _same_criterion(a: Criterion, b: Criterion) -> None:
    assert type(a) is type(b)
    assert a == b
    assert a.to_spec() == b.to_spec()
    assert a.imap_query.build() == b.imap_query.build()
    assert a.is_efficient == b.is_efficient
    assert [a.func(m) for m in MESSAGES] == [b.func(m) for m in MESSAGES]


def _validator(definition: str) -> jsonschema.protocols.Validator:
    schema = json_schema()
    sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(sub, format_checker=cls.FORMAT_CHECKER)


def _json_round_trip(spec: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(spec))
    return result


def _exported(base: type) -> set[type]:
    return {
        obj
        for name in filters_module.__all__
        if isinstance(obj := getattr(filters_module, name), type)
        and issubclass(obj, base)
        and obj is not base
    }


class MagicConn:
    """Records the IMAP calls an action makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return lambda *args: self.calls.append((name, args))


class TestCoverage:
    def test_every_exported_criterion_is_covered(self) -> None:
        assert {type(c) for c in CRITERIA} == _exported(Criterion)

    def test_every_exported_action_is_covered(self) -> None:
        assert {type(a) for a in ACTIONS} == _exported(Action)

    def test_schema_lists_exactly_the_registered_types(self) -> None:
        defs = json_schema()["$defs"]
        for definition, base in (("criterion_leaf", Criterion), ("action_leaf", Action)):
            in_schema = {
                name
                for variant in defs[definition]["oneOf"]
                for name in variant["properties"]["type"]["enum"]
            }
            exported = {cast("Any", cls).spec_type for cls in _exported(base)}
            assert in_schema == exported


class TestCriterionRoundTrip:
    @pytest.mark.parametrize("criterion", CRITERIA + COMBINED_CRITERIA, ids=repr)
    def test_round_trip(self, criterion: Criterion) -> None:
        loaded = load_criterion(_json_round_trip(criterion.to_spec()))
        _same_criterion(criterion, loaded)

    @pytest.mark.parametrize("criterion", CRITERIA + COMBINED_CRITERIA, ids=repr)
    def test_spec_validates_against_schema(self, criterion: Criterion) -> None:
        _validator("criterion").validate(criterion.to_spec())


class TestActionRoundTrip:
    @pytest.mark.parametrize("action", ACTIONS + COMBINED_ACTIONS, ids=repr)
    def test_round_trip(self, action: Action) -> None:
        loaded = load_action(_json_round_trip(action.to_spec()))
        assert type(loaded) is type(action)
        assert loaded == action
        assert loaded.name == action.name

    @pytest.mark.parametrize("action", ACTIONS + COMBINED_ACTIONS, ids=repr)
    def test_spec_validates_against_schema(self, action: Action) -> None:
        _validator("action").validate(action.to_spec())

    def test_move_to_executes_same_move(self) -> None:
        conn_a, conn_b = MagicConn(), MagicConn()
        action = MarkAsRead() + MoveTo(Path(["Archive", "2025"]))
        action.execute(cast("Any", conn_a), [1], delimiter=".")
        load_action(action.to_spec()).execute(cast("Any", conn_b), [1], delimiter=".")
        assert conn_a.calls == conn_b.calls
        assert conn_a.calls == [("add_flags", ([1], [b"\\Seen"])), ("move", ([1], "Archive.2025"))]


class TestFilterRoundTrip:
    def test_round_trip_and_schema(self) -> None:
        f = If(FromIs("list@example.edu") & SubjectMatches(r"Digest, Vol \d+")).then(
            MarkAsRead() + MoveTo(Path(["Lists", "Nerds"]))
        )
        spec = f.to_spec()
        assert spec == {
            "criterion": {
                "and": [
                    {"type": "from_is", "args": {"addr": "list@example.edu"}},
                    {"type": "subject_matches", "args": {"pattern": r"Digest, Vol \d+"}},
                ]
            },
            "action": {
                "seq": [
                    {"type": "mark_as_read"},
                    {"type": "move_to", "args": {"folder": ["Lists", "Nerds"]}},
                ]
            },
        }
        jsonschema.validate(spec, json_schema())
        loaded = load_filter(_json_round_trip(spec))
        assert loaded == f
        _same_criterion(loaded.criterion, f.criterion)
        assert loaded.action.name == f.action.name


class TestSpecFormat:
    def test_leaf_without_args_omits_args(self) -> None:
        assert IsRead().to_spec() == {"type": "is_read"}

    def test_defaults_are_recorded(self) -> None:
        assert ToContains("a").to_spec() == {
            "type": "to_contains",
            "args": {"addr": "a", "incl_cc": False, "incl_bcc": False},
        }
        assert Trash().to_spec() == {"type": "trash", "args": {"folder": None}}

    def test_subclass_records_its_own_type(self) -> None:
        assert IsRead().to_spec() != HasFlag(Flag.SEEN).to_spec()
        assert HasFlag(Flag.SEEN).to_spec() == {"type": "has_flag", "args": {"flag": "SEEN"}}

    @pytest.mark.parametrize("cutoff", [date(2025, 1, 1), datetime(2025, 1, 1, 12), "01-Jan-2025"])  # noqa: DTZ001
    def test_dates_normalise_to_iso(self, cutoff: date | datetime | str) -> None:
        assert OlderThan(cutoff).to_spec() == {
            "type": "older_than",
            "args": {"cutoff_date": "2025-01-01"},
        }

    def test_chains_flatten(self) -> None:
        a, b, c = FromIs("a"), FromIs("b"), FromIs("c")
        assert (a & b & c).to_spec() == {"and": [a.to_spec(), b.to_spec(), c.to_spec()]}
        assert (a & (b & c)) == (a & b & c)
        assert ((a | b) & c).to_spec() == {"and": [{"or": [a.to_spec(), b.to_spec()]}, c.to_spec()]}
        assert (NoOp() + (Star() + Unstar())).to_spec() == {
            "seq": [{"type": "no_op"}, {"type": "star"}, {"type": "unstar"}]
        }

    def test_to_spec_returns_a_copy(self) -> None:
        c = MoveTo(Path(["A"]))
        c.to_spec()["args"]["folder"].append("B")
        assert c.to_spec() == {"type": "move_to", "args": {"folder": ["A"]}}

    def test_schema_is_shipped(self) -> None:
        assert json_schema()["$defs"]["filter"]["required"] == ["criterion", "action"]


class TestEqualityAndRepr:
    def test_equal_specs_are_equal_and_hash_equal(self) -> None:
        assert FromIs("a") == FromIs("a")
        assert hash(FromIs("a")) == hash(FromIs("a"))
        assert FromIs("a") != FromIs("b")
        assert FromIs("a") != FromContains("a")
        assert ToIs("a") == ToIs("a", incl_cc=False)
        assert MoveTo(Path("X")) == MoveTo(Path(["X"]))
        assert len({IsRead(), IsRead(), IsUnread()}) == 2
        loose = ToIs("a", incl_cc=cast("Any", 1))
        assert (loose == ToIs("a", incl_cc=True)) == (hash(loose) == hash(ToIs("a", incl_cc=True)))

    def test_criterion_never_equals_action(self) -> None:
        assert Anything() != NoOp()

    def test_custom_objects_compare_by_identity(self) -> None:
        custom = Criterion(lambda _: True)
        assert custom == custom
        assert custom != Criterion(lambda _: True)
        assert (custom & IsRead()) != (custom & IsRead())

    def test_filter_equality(self) -> None:
        assert Filter(IsRead(), Star()) == If(IsRead()).then(Star())
        assert Filter(IsRead(), Star()) != Filter(IsRead(), Unstar())
        assert hash(Filter(IsRead(), Star())) == hash(Filter(IsRead(), Star()))

    def test_repr(self) -> None:
        crit = (FromIs("a@b") | ToContains("x", incl_cc=True)) & ~IsRead()
        assert repr(crit) == (
            "((FromIs(addr='a@b') | ToContains(addr='x', incl_cc=True, incl_bcc=False))"
            " & ~IsRead())"
        )
        action = MarkAsRead() + MoveTo(Path(["A", "B"]))
        assert repr(action) == "(MarkAsRead() + MoveTo(folder=['A', 'B']))"
        assert str(action) == "MarkAsRead; move to A/B"
        assert repr(Filter(IsRead(), Star())) == "Filter(IsRead(), Star())"

    def test_custom_repr_falls_back(self) -> None:
        assert repr(Criterion(lambda _: True)).startswith("<imap_thingy.")


class TestNotSerializable:
    def test_custom_criterion(self) -> None:
        with pytest.raises(NotSerializableError):
            Criterion(lambda _: True).to_spec()

    def test_custom_action(self) -> None:
        with pytest.raises(NotSerializableError):
            Action(lambda conn, msgids, *, delimiter: None, name="custom").to_spec()

    def test_combinations_with_custom_parts(self) -> None:
        custom = Criterion(lambda _: True)
        for crit in (custom & IsRead(), IsRead() | custom, ~custom):
            with pytest.raises(NotSerializableError):
                crit.to_spec()
        with pytest.raises(NotSerializableError):
            (Star() + Action(lambda conn, msgids, *, delimiter: None)).to_spec()
        with pytest.raises(NotSerializableError):
            Filter(custom, Star()).to_spec()

    def test_subclass_without_own_spec_type(self) -> None:
        class MyFrom(FromIs):
            def __init__(self) -> None:
                super().__init__("me@example.com")

        with pytest.raises(NotSerializableError):
            MyFrom().to_spec()

    def test_argument_without_json_form(self) -> None:
        with pytest.raises(NotSerializableError):
            SubjectIs(cast("Any", b"bytes")).to_spec()

    def test_is_a_type_error(self) -> None:
        assert issubclass(NotSerializableError, TypeError)


class TestLoadErrors:
    @pytest.mark.parametrize(
        "spec",
        [
            {"type": "nope"},
            {"type": "move_to", "args": {"folder": ["A"]}},
            {"type": "from_is"},
            {"type": "from_is", "args": {"address": "a"}},
            {"type": "from_is", "args": {"addr": ["a"]}},
            {"type": "from_is", "args": {"addr": "a"}, "extra": 1},
            {"type": "has_flag", "args": {"flag": "DELETED"}},
            {"type": "older_than", "args": {"cutoff_date": "31/01/2025"}},
            {"type": "older_than_days", "args": {"days": -1}},
            {"type": "older_than_days", "args": {"days": 36501}},
            {"type": "older_than_days", "args": {"days": "7"}},
            {"and": []},
            {"and": [{"type": "is_read"}], "or": [{"type": "is_read"}]},
            {"seq": [{"type": "star"}]},
            {},
        ],
        ids=json.dumps,
    )
    def test_bad_criterion_spec(self, spec: dict[str, Any]) -> None:
        with pytest.raises(SpecError):
            load_criterion(spec)
        with pytest.raises(jsonschema.ValidationError):
            _validator("criterion").validate(spec)

    def test_bad_action_spec(self) -> None:
        with pytest.raises(SpecError):
            load_action({"type": "is_read"})
        with pytest.raises(SpecError):
            load_action({"type": "move_to", "args": {"folder": "A/B"}})

    def test_bad_filter_spec(self) -> None:
        with pytest.raises(SpecError):
            load_filter({"criterion": {"type": "is_read"}})
        with pytest.raises(SpecError):
            load_filter(cast("Any", []))

    def test_spec_error_is_a_value_error(self) -> None:
        assert issubclass(SpecError, ValueError)
