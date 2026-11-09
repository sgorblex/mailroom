"""The send log as `send/gate` will use it: claiming a send, its outcome, and retries.

The concurrent cases run on real Postgres, as the per-user lock is what they exercise.
"""

from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import event, text

from mailroom_core.store import (
    CORE_MIGRATIONS,
    SEND_CEILING,
    AccountStore,
    SendClaim,
    SendCount,
    SendLimitReached,
    SendRecord,
    SendsBusy,
    downgrade,
    forget_user,
    migrate,
    upgrade,
)
from mailroom_core.store.schema import metadata
from mailroom_core.testing import schema_differences

T0 = datetime(2026, 11, 5, 12, tzinfo=UTC)
ALICE = "alice"
BOB = "bob"
KEY = "6a0f3a3e-0c7b-4f7e-9b34-2f2f7f0f1a11"
LIMIT = 20


def claim(
    store: AccountStore,
    key: str | None = KEY,
    now: datetime = T0,
    *,
    limit: int = LIMIT,
    confirm_resend: bool = False,
) -> SendClaim:
    return store.claim_send(key, "acc", limit, confirm_resend=confirm_resend, now=now)


def finished(
    store: AccountStore, state, result=None, key: str | None = KEY, now: datetime = T0
) -> SendRecord:
    send = claim(store, key, now).send
    assert store.finish_send(send, state, result)
    return send


def rows(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("select count(*) from core.sends")).scalar_one()


def test_write_the_send_before_smtp_and_count_it(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    first = claim(store)
    assert first.fresh
    assert first.send == SendRecord(
        id=first.send.id, key=KEY, state="sending", account="acc", result=None, at=T0
    )
    assert store.send_state(first.send.id, T0) == first.send
    assert store.send_state_by_key(KEY, T0) == first.send
    assert store.sends_since(T0 - timedelta(seconds=1)) == SendCount(count=1, oldest=T0)


def test_finish_a_send_with_its_result(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = claim(store).send
    assert store.finish_send(send, "sent", {"filed": True, "rejected": ["x@example.com"]})
    stored = store.send_state(send.id, T0)
    assert (stored.state, stored.result) == ("sent", {"filed": True, "rejected": ["x@example.com"]})


def test_replay_a_sent_key_without_delivering_or_counting_again(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = finished(store, "sent", {"filed": True})
    retry = claim(store, now=T0 + timedelta(minutes=1), limit=1)
    assert not retry.fresh
    assert (retry.send.id, retry.send.state, retry.send.result) == (
        send.id,
        "sent",
        {"filed": True},
    )
    assert rows(core_engine) == 1


def test_answer_a_key_still_sending_with_the_same_id(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = claim(store).send
    retry = claim(store, now=T0 + SEND_CEILING, limit=1)
    assert not retry.fresh
    assert (retry.send.id, retry.send.state) == (send.id, "sending")
    assert rows(core_engine) == 1


def test_reclaim_a_refused_key_as_a_new_attempt(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = finished(store, "failed", {"code": "smtp"})
    later = T0 + timedelta(minutes=1)
    retry = store.claim_send(KEY, "other", LIMIT, now=later)
    assert retry.fresh
    assert retry.send == SendRecord(
        id=send.id, key=KEY, state="sending", account="other", result=None, at=later
    )
    assert store.sends_since(T0 - timedelta(seconds=1)) == SendCount(count=2, oldest=T0)


def test_refuse_an_unknown_key_unless_the_resend_is_confirmed(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = finished(store, "unknown", {"code": "lost"})
    later = T0 + timedelta(minutes=1)
    refused = claim(store, now=later)
    assert not refused.fresh
    assert (refused.send.id, refused.send.state) == (send.id, "unknown")
    assert (refused.send.at, refused.send.result) == (T0, {"code": "lost"})
    confirmed = claim(store, now=later, confirm_resend=True)
    assert confirmed.fresh
    assert (confirmed.send.id, confirmed.send.state) == (send.id, "sending")
    assert store.sends_since(T0 - timedelta(seconds=1)).count == 2


@pytest.mark.parametrize("state", ["sent", "sending"])
def test_ignore_confirm_resend_where_nothing_is_unknown(core_engine, state) -> None:
    store = AccountStore(core_engine, ALICE)
    send = claim(store).send
    if state == "sent":
        store.finish_send(send, "sent")
    retry = claim(store, now=T0 + timedelta(seconds=1), confirm_resend=True)
    assert not retry.fresh
    assert retry.send.state == state


def test_read_a_send_past_the_ceiling_as_unknown(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = claim(store).send
    stale = T0 + SEND_CEILING + timedelta(seconds=1)
    assert store.send_state(send.id, T0 + SEND_CEILING).state == "sending"
    assert store.send_state(send.id, stale).state == "unknown"
    assert store.send_state_by_key(KEY, stale).state == "unknown"
    retry = claim(store, now=stale)
    assert not retry.fresh
    assert retry.send.state == "unknown"
    assert claim(store, now=stale, confirm_resend=True).fresh


def test_take_the_real_outcome_of_a_send_past_the_ceiling(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = claim(store).send
    assert store.finish_send(send, "sent")
    assert store.send_state(send.id, T0 + timedelta(days=1)).state == "sent"


def test_ignore_the_outcome_of_an_attempt_that_was_reclaimed(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    old = claim(store).send
    new = claim(store, now=T0 + SEND_CEILING * 2, confirm_resend=True).send
    assert not store.finish_send(old, "failed")
    assert store.send_state(new.id, new.at).state == "sending"
    assert store.finish_send(new, "sent")


def test_let_no_outcome_land_between_a_reclaims_read_and_its_write(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    worker = claim(store).send
    late = T0 + SEND_CEILING + timedelta(seconds=1)
    finishing: list[Future[bool]] = []

    def right_after_the_read(_conn, _cursor, statement, *_rest) -> None:
        if "core.sends.key = " not in statement or finishing:
            return
        finishing.append(pool.submit(store.finish_send, worker, "sent", {"rejected": []}))
        with suppress(FutureTimeout):
            finishing[0].result(timeout=0.5)

    event.listen(core_engine, "after_cursor_execute", right_after_the_read)
    with ThreadPoolExecutor(1) as pool:
        reclaimed = claim(store, now=late, confirm_resend=True)
        assert reclaimed.fresh
        assert finishing[0].result(timeout=5) is False
    event.remove(core_engine, "after_cursor_execute", right_after_the_read)

    assert store.send_state(worker.id, late) == reclaimed.send


def test_replay_an_outcome_that_landed_before_a_reclaims_read(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    worker = claim(store).send
    assert store.finish_send(worker, "sent", {"rejected": []})

    late = T0 + SEND_CEILING + timedelta(seconds=1)
    replayed = claim(store, now=late, confirm_resend=True)

    assert not replayed.fresh
    assert replayed.send.state == "sent"


def test_keep_a_finished_state_and_let_its_result_be_amended(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = finished(store, "sent", {"filed": None})
    assert not store.finish_send(send, "failed")
    assert not store.finish_send(send, "unknown")
    assert store.send_state(send.id, T0).state == "sent"
    assert store.finish_send(send, "sent", {"filed": False})
    assert store.send_state(send.id, T0).result == {"filed": False}


def test_refuse_a_claim_at_the_limit(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    claim(store, None, T0, limit=2)
    claim(store, None, T0 + timedelta(minutes=30), limit=2)
    with pytest.raises(SendLimitReached) as refused:
        claim(store, None, T0 + timedelta(minutes=59), limit=2)
    assert refused.value.recent == SendCount(count=2, oldest=T0)
    assert claim(store, None, T0 + timedelta(minutes=60), limit=2).fresh


@pytest.mark.parametrize("state", ["failed", "unknown"])
def test_hold_a_retry_to_the_limit_too(core_engine, state) -> None:
    store = AccountStore(core_engine, ALICE)
    send = finished(store, state, {"code": "smtp"})
    with pytest.raises(SendLimitReached):
        claim(store, now=T0 + timedelta(minutes=1), limit=1, confirm_resend=True)
    stored = store.send_state(send.id, T0)
    assert (stored.state, stored.result) == (state, {"code": "smtp"})


def test_count_the_attempts_of_a_reclaimed_key_separately(core_engine) -> None:
    """A retried send may have been delivered each time: repeating one can't beat the limit."""
    store = AccountStore(core_engine, ALICE)
    send = claim(store, limit=3).send
    store.finish_send(send, "unknown")
    send = claim(store, now=T0 + timedelta(minutes=1), limit=3, confirm_resend=True).send
    store.finish_send(send, "unknown")
    send = claim(store, now=T0 + timedelta(minutes=2), limit=3, confirm_resend=True).send
    store.finish_send(send, "unknown")
    with pytest.raises(SendLimitReached):
        claim(store, now=T0 + timedelta(minutes=3), limit=3, confirm_resend=True)


def test_send_keyless_messages_as_separate_sends(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    first = claim(store, None)
    second = claim(store, None)
    assert first.fresh
    assert second.fresh
    assert first.send.id != second.send.id
    assert first.send.key is None


def test_let_a_key_be_used_by_each_user_alone(core_engine) -> None:
    alice = AccountStore(core_engine, ALICE)
    bob = AccountStore(core_engine, BOB)
    send = finished(alice, "sent", {"filed": True})
    assert claim(bob).fresh
    assert bob.send_state(send.id, T0) is None
    assert bob.send_state_by_key(KEY, T0).id != send.id
    assert not bob.finish_send(send, "failed")
    assert alice.send_state(send.id, T0).state == "sent"


def test_not_report_a_row_that_only_counts_as_a_send(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    store.record_send(T0)
    with core_engine.connect() as connection:
        send_id = connection.execute(text("select id from core.sends")).scalar_one()
    assert store.send_state(send_id, T0) is None
    assert claim(store, None, T0, limit=2).fresh
    with pytest.raises(SendLimitReached):
        claim(store, None, T0, limit=2)


def test_keep_keyed_rows_thirty_days_and_others_a_day(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    finished(store, "sent", key="old")
    finished(store, "sent", key=None)
    store.record_send(T0)

    store.record_send(T0 + timedelta(days=1))
    assert store.send_state_by_key("old", T0) is not None
    assert rows(core_engine) == 2

    store.record_send(T0 + timedelta(days=29, hours=23))
    assert store.send_state_by_key("old", T0) is not None
    claim(store, None, T0 + timedelta(days=30))
    assert store.send_state_by_key("old", T0) is None


def test_treat_a_key_as_new_once_it_has_expired(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    finished(store, "sent")
    assert claim(store, now=T0 + timedelta(days=30)).fresh
    assert rows(core_engine) == 1


def test_prune_on_a_claim_only_this_users_rows(core_engine) -> None:
    alice = AccountStore(core_engine, ALICE)
    bob = AccountStore(core_engine, BOB)
    claim(bob, None)
    claim(alice, None, T0 + timedelta(days=2))
    assert bob.sends_since(T0 - timedelta(seconds=1)).count == 1


def test_admit_exactly_the_limit_of_concurrent_claims(core_engine) -> None:
    claims = 12
    limit = 5
    store = AccountStore(core_engine, ALICE)
    barrier = Barrier(claims)

    def attempt(_: int) -> SendClaim | SendLimitReached:
        barrier.wait()
        try:
            return store.claim_send(None, "acc", limit)
        except SendLimitReached as refused:
            return refused

    with ThreadPoolExecutor(claims) as pool:
        outcomes = list(pool.map(attempt, range(claims)))
    assert sum(isinstance(outcome, SendClaim) for outcome in outcomes) == limit
    assert rows(core_engine) == limit


def test_let_one_of_several_concurrent_claims_of_a_key_win(core_engine) -> None:
    claims = 12
    store = AccountStore(core_engine, ALICE)
    barrier = Barrier(claims)

    def attempt(_: int) -> SendClaim:
        barrier.wait()
        return store.claim_send(KEY, "acc", LIMIT)

    with ThreadPoolExecutor(claims) as pool:
        outcomes = list(pool.map(attempt, range(claims)))
    assert sum(outcome.fresh for outcome in outcomes) == 1
    assert len({outcome.send.id for outcome in outcomes}) == 1
    assert rows(core_engine) == 1


def test_let_one_of_several_concurrent_reclaims_win(core_engine) -> None:
    claims = 12
    store = AccountStore(core_engine, ALICE)
    finished(store, "failed", now=datetime.now(UTC))
    barrier = Barrier(claims)

    def attempt(_: int) -> SendClaim:
        barrier.wait()
        return store.claim_send(KEY, "acc", LIMIT)

    with ThreadPoolExecutor(claims) as pool:
        outcomes = list(pool.map(attempt, range(claims)))
    assert sum(outcome.fresh for outcome in outcomes) == 1


def test_give_each_user_their_own_limit_under_concurrency(core_engine) -> None:
    users = [ALICE, BOB]
    barrier = Barrier(len(users) * 4)

    def attempt(user_id: str) -> int:
        barrier.wait()
        try:
            AccountStore(core_engine, user_id).claim_send(None, "acc", 2)
        except SendLimitReached:
            return 0
        return 1

    with ThreadPoolExecutor(len(users) * 4) as pool:
        admitted = list(pool.map(attempt, users * 4))
    assert sum(admitted[::2]) == 2
    assert sum(admitted[1::2]) == 2


def test_refuse_a_claim_while_the_users_log_is_held_too_long(core_engine) -> None:
    """Only the same user's claim waits, and the refusal writes nothing."""
    short = timedelta(milliseconds=100)
    store = AccountStore(core_engine, ALICE, lock_timeout=short)
    with core_engine.begin() as other:
        other.execute(
            text("select pg_advisory_xact_lock(hashtext('core.sends'), hashtext(:user))"),
            {"user": ALICE},
        )
        with pytest.raises(SendsBusy, match=r"^another send is still being claimed$"):
            claim(store)
        assert claim(AccountStore(core_engine, BOB, lock_timeout=short)).fresh
    assert store.send_state_by_key(KEY, T0) is None


def test_refuse_a_claim_while_its_row_is_held_too_long(core_engine) -> None:
    store = AccountStore(core_engine, ALICE, lock_timeout=timedelta(milliseconds=50))
    claim(store)

    with core_engine.begin() as holder:
        holder.execute(text("select 1 from core.sends for update"))
        with pytest.raises(SendsBusy, match=r"^another send is still being claimed$"):
            claim(store)


def test_forget_a_users_claimed_sends(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    send = finished(store, "sent")
    with core_engine.begin() as connection:
        forget_user(connection, ALICE)
    assert store.send_state(send.id, T0) is None
    assert not store.finish_send(send, "sent")


def test_keep_the_rows_stored_before_claims(throwaway_databases) -> None:
    with throwaway_databases.migrated(lambda _: None) as engine:
        upgrade(engine, CORE_MIGRATIONS, "core_0002")
        store = AccountStore(engine, ALICE)
        with engine.begin() as connection:
            connection.execute(
                text("insert into core.sends (user_id, at) values (:user, :at)"),
                {"user": ALICE, "at": T0},
            )
        migrate(engine)
        assert schema_differences(engine, metadata) == []
        assert store.sends_since(T0 - timedelta(seconds=1)).count == 1
        claim(store, now=T0)
        claim(store, None, now=T0)
        claim(store, None, now=T0)

        downgrade(engine, CORE_MIGRATIONS, "core_0002")
        with engine.connect() as connection:
            assert connection.execute(text("select count(*) from core.sends")).scalar_one() == 4
        migrate(engine)
        assert store.sends_since(T0 - timedelta(seconds=1)).count == 4
