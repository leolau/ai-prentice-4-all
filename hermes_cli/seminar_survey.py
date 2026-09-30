"""Seminar follow-up: WhatsApp request -> personal survey link -> slides.

A seminar attendee scans a QR code that opens WhatsApp with a pre-filled
request to the seminar number. The WhatsApp triage agent recognises that text
(:func:`is_survey_request`) before any LLM call and records a sign-up for the
sender's chat. The row written here is both the durable record *and* the queue
item, so nothing is lost if the outreach worker is down when somebody asks:

``queued`` -> ``inviting`` -> ``invited`` -> ``submitted`` -> ``completing`` ->
``completed`` (``failed`` once retries are exhausted).

* The worker claims a ``queued`` row, mints the survey token, and sends the
  personal link ``/survey/<token>`` into the chat the request came from. Only
  the token's SHA-256 is stored; the raw value exists in that one message, so
  the link is what ties an answer to the chat it was sent to.
* Submitting the survey stores the answers against that row and moves it to
  ``submitted``; the worker then sends the slides and the thank-you into the
  same chat, once.

Claims are ``FOR UPDATE SKIP LOCKED`` with a lease, so two workers never send
the same message and a worker that dies mid-send is retried after the lease.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Mapping

from hermes_cli.config import load_config
from hermes_cli.datastore import get_store

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SupabaseAppStore

TABLE = "seminar_survey_signups"

#: Entropy of a survey token; ``secrets.token_urlsafe(24)`` is 32 characters.
TOKEN_BYTES = 24

SignupStatus = Literal[
    "queued", "inviting", "invited", "submitted", "completing", "completed", "failed"
]
SIGNUP_STATUSES: tuple[SignupStatus, ...] = (
    "queued",
    "inviting",
    "invited",
    "submitted",
    "completing",
    "completed",
    "failed",
)
_STATUSES_SQL = ", ".join(f"'{status}'" for status in SIGNUP_STATUSES)

JobKind = Literal["invite", "complete"]

SignupOutcome = Literal["queued", "requeued", "unchanged"]
SubmitOutcome = Literal["submitted", "already_submitted", "not_found"]

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    sender_name TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ({_STATUSES_SQL})),
    token_hash BYTEA,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_until TIMESTAMPTZ,
    last_error TEXT,
    invite_message_id TEXT,
    invited_at TIMESTAMPTZ,
    answers JSONB,
    submitted_at TIMESTAMPTZ,
    completion_message_id TEXT,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (campaign, chat_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS {TABLE}_token_idx
    ON {TABLE} (token_hash) WHERE token_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS {TABLE}_due_idx
    ON {TABLE} (status, next_attempt_at);
ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;
"""

# --- survey content ---------------------------------------------------------

#: Mirrors the seminar's Google Form. The agent-home page renders the labels;
#: these value sets are the server-side authority on what may be stored.
GRADES: tuple[str, ...] = ("P1", "P2", "P3", "P4", "P5", "P6")
SHARE_CHOICES: tuple[str, ...] = ("named", "anonymous", "no")
NEXT_STEPS: tuple[str, ...] = (
    "enrol",
    "workshop",
    "consultation",
    "updates",
    "not_now",
)
MAX_NAME_LENGTH = 100
MAX_COMMENT_LENGTH = 2000


class SurveyError(ValueError):
    """A sign-up or survey submission was rejected; the message is user-safe."""


@dataclass(frozen=True)
class SurveySettings:
    """``seminar_survey`` in ``config.yaml``."""

    campaign: str = "ai-kids-flourish"
    whatsapp_source: str = "phone2"
    trigger_phrases: tuple[str, ...] = (
        "I want to do the survey and get the presentation link",
        "我想填寫問卷並索取講座簡報",
    )
    public_base_url: str = "https://home.leolau.ai-and-i.io"
    bridge_url: str = "http://127.0.0.1:3001"
    presentation_url: str = "https://canva.link/puysezdoxbb5sk6"
    send_interval_seconds: float = 3.0
    poll_seconds: float = 5.0
    lease_seconds: float = 300.0
    max_attempts: int = 5
    retry_backoff_seconds: float = 60.0
    resend_cooldown_seconds: float = 600.0

    @classmethod
    def from_config(cls, config: Mapping[str, object] | None) -> "SurveySettings":
        section: object = (config or {}).get("seminar_survey")
        if not isinstance(section, Mapping):
            return cls()
        values: dict[str, object] = {str(k): v for k, v in section.items()}
        defaults = cls()
        return cls(
            campaign=_str(values, "campaign", defaults.campaign),
            whatsapp_source=_str(
                values, "whatsapp_source", defaults.whatsapp_source
            ),
            trigger_phrases=_phrases(values, defaults.trigger_phrases),
            public_base_url=_str(
                values, "public_base_url", defaults.public_base_url
            ).rstrip("/"),
            bridge_url=_str(values, "bridge_url", defaults.bridge_url).rstrip("/"),
            presentation_url=_str(
                values, "presentation_url", defaults.presentation_url
            ),
            send_interval_seconds=_num(
                values, "send_interval_seconds", defaults.send_interval_seconds
            ),
            poll_seconds=_num(values, "poll_seconds", defaults.poll_seconds),
            lease_seconds=_num(values, "lease_seconds", defaults.lease_seconds),
            max_attempts=int(
                _num(values, "max_attempts", float(defaults.max_attempts))
            ),
            retry_backoff_seconds=_num(
                values, "retry_backoff_seconds", defaults.retry_backoff_seconds
            ),
            resend_cooldown_seconds=_num(
                values, "resend_cooldown_seconds", defaults.resend_cooldown_seconds
            ),
        )


def _str(values: Mapping[str, object], key: str, default: str) -> str:
    raw = values.get(key)
    if raw is None:
        return default
    text = str(raw).strip()
    return text or default


def _phrases(values: Mapping[str, object], default: tuple[str, ...]) -> tuple[str, ...]:
    raw = values.get("trigger_phrases")
    if not isinstance(raw, list):
        return default
    phrases = tuple(str(p) for p in raw if _normalize_text(str(p)))
    return phrases or default


def _num(values: Mapping[str, object], key: str, default: float) -> float:
    raw = values.get(key)
    if raw is None:
        return default
    try:
        value = float(str(raw))
    except ValueError:
        return default
    return value if value > 0 else default


def mint_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def survey_link(settings: SurveySettings, token: str) -> str:
    return f"{settings.public_base_url}/survey/{token}"


def _normalize_text(text: str) -> str:
    """Case-fold and reduce punctuation/emoji/whitespace runs to one space."""
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def is_survey_request(text: str, phrases: tuple[str, ...]) -> bool:
    """Whether ``text`` contains one of the QR code's pre-filled requests.

    Matching ignores case, punctuation and spacing (people add a "hi" or drop
    the full stop) but needs the whole phrase, word for word.
    """
    body = f" {_normalize_text(text)} "
    return any(
        f" {phrase} " in body
        for phrase in (_normalize_text(p) for p in phrases)
        if phrase
    )


def survey_request_messages(
    batch: Mapping[str, object], settings: SurveySettings
) -> list[Mapping[str, object]]:
    """The messages in a WhatsApp triage batch that ask for the survey.

    Only one-to-one chats on the seminar number count: a group mention of the
    phrase must not make the number post a personal link into the group.
    """
    if batch.get("source_phone") != settings.whatsapp_source:
        return []
    messages = batch.get("messages")
    if not isinstance(messages, list):
        return []
    found: list[Mapping[str, object]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        fields: dict[str, object] = {str(k): v for k, v in message.items()}
        if fields.get("is_group") or not fields.get("chat_id"):
            continue
        if is_survey_request(str(fields.get("text") or ""), settings.trigger_phrases):
            found.append(fields)
    return found


def validate_answers(raw: Mapping[str, object]) -> dict[str, object]:
    """Return the answers to store, or raise :class:`SurveyError`."""
    rating = raw.get("rating")
    if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
        raise SurveyError("Please rate the seminar from 1 to 5. 請為講座評分（1 至 5）。")
    grades_raw = raw.get("grades")
    grades = (
        [g for g in GRADES if g in grades_raw] if isinstance(grades_raw, list) else []
    )
    if not grades:
        raise SurveyError("Please choose your child's grade. 請選擇孩子就讀的年級。")
    comment = str(raw.get("comment") or "").strip()[:MAX_COMMENT_LENGTH]
    share = raw.get("share")
    if share not in SHARE_CHOICES:
        raise SurveyError("Please tell us whether we may share your comment. 請選擇可否分享您的意見。")
    next_step = raw.get("next_step")
    if next_step not in NEXT_STEPS:
        raise SurveyError("Please choose what you'd like to do next. 請選擇您希望進一步做甚麼。")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise SurveyError("Please enter your name. 請輸入您的姓名。")
    return {
        "rating": rating,
        "grades": grades,
        "comment": comment,
        "share": share,
        "next_step": next_step,
        "name": name[:MAX_NAME_LENGTH],
        "stay_in_touch": raw.get("stay_in_touch") is True,
    }


# --- WhatsApp copy (Traditional Chinese + English) ------------------------------


def invite_message(link: str) -> str:
    return (
        "您好！多謝您出席 AI&I 講座「如何幫助孩子在AI時代茁壯成長？」。\n"
        "請按以下連結填寫約 1 分鐘的問卷，完成後我們會在這裡傳送講座簡報給您：\n"
        f"{link}\n\n"
        "Hi! Thank you for attending the AI&I seminar "
        "\u201cHow to help our children to flourish with AI?\u201d.\n"
        "Please take this 1-minute survey \u2014 once you finish, we'll send you "
        "the presentation slides right here:\n"
        f"{link}"
    )


def completion_message(presentation_url: str) -> str:
    return (
        "多謝您出席講座並完成問卷！這是講座簡報：\n"
        f"{presentation_url}\n"
        "再次感謝您的參與！\n\n"
        "Thank you for attending the seminar and completing the survey! "
        "Here are the presentation slides:\n"
        f"{presentation_url}\n"
        "Thank you once again!"
    )


# --- store --------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    """One claimed unit of outreach work. ``token`` is set for invites only."""

    kind: JobKind
    signup_id: str
    chat_id: str
    token: str | None = None


@dataclass(frozen=True)
class SurveyState:
    status: SignupStatus
    submitted: bool


class SurveyStore:
    """Sign-ups, the outreach queue, and survey answers in one table."""

    def __init__(self, store: "SupabaseAppStore", settings: SurveySettings) -> None:
        self._store = store
        self._settings = settings
        self._initialized = False

    @property
    def settings(self) -> SurveySettings:
        return self._settings

    async def initialize(self, *, connection: "asyncpg.Connection | None" = None) -> None:
        own = connection is None
        conn = connection or await self._store.connect()
        try:
            await conn.execute(SCHEMA_SQL)
            self._initialized = True
        finally:
            if own:
                await conn.close()

    async def _connect(self) -> "asyncpg.Connection":
        """A connection with the table created (once per process)."""
        conn = await self._store.connect()
        if not self._initialized:
            try:
                await self.initialize(connection=conn)
            except BaseException:
                await conn.close()
                raise
        return conn

    async def signup(self, chat_id: str, sender_name: str | None = None) -> SignupOutcome:
        """Persist a survey request from ``chat_id``; that row *is* the queue item.

        Asking again re-sends a (fresh) survey link only when the earlier invite
        failed or is older than ``resend_cooldown_seconds``; after the survey is
        done, asking again re-sends the slides the same way.
        """
        campaign = self._settings.campaign
        conn = await self._connect()
        try:
            async with conn.transaction():
                inserted = await conn.fetchrow(
                    f"""INSERT INTO {TABLE} (campaign, chat_id, sender_name)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (campaign, chat_id) DO NOTHING
                        RETURNING id""",
                    campaign,
                    chat_id,
                    sender_name,
                )
                if inserted is not None:
                    return "queued"
                row = await conn.fetchrow(
                    f"""SELECT id, status, submitted_at,
                               COALESCE(completed_at, invited_at)
                                   < NOW() - make_interval(secs => $3)
                                   AS cooled_down
                          FROM {TABLE}
                         WHERE campaign = $1 AND chat_id = $2
                         FOR UPDATE""",
                    campaign,
                    chat_id,
                    self._settings.resend_cooldown_seconds,
                )
                if row is None:
                    return "unchanged"
                status = str(row["status"])
                if status == "failed":
                    target = "queued" if row["submitted_at"] is None else "submitted"
                elif status == "invited" and row["cooled_down"]:
                    target = "queued"
                elif status == "completed" and row["cooled_down"]:
                    target = "submitted"
                else:
                    return "unchanged"
                await conn.execute(
                    f"""UPDATE {TABLE}
                           SET status = $2, attempts = 0, last_error = NULL,
                               next_attempt_at = NOW(), requested_at = NOW(),
                               updated_at = NOW()
                         WHERE id = $1""",
                    row["id"],
                    target,
                )
                return "requeued"
        finally:
            await conn.close()

    async def claim(self) -> Job | None:
        """Claim the next due job, completions first. ``None`` when idle.

        An invite claim mints the survey token in the same statement, so the
        stored hash always belongs to the link about to be sent.
        """
        token = mint_token()
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"""UPDATE {TABLE} AS s
                       SET status = CASE WHEN s.status IN ('submitted', 'completing')
                                         THEN 'completing' ELSE 'inviting' END,
                           token_hash = CASE WHEN s.status IN ('queued', 'inviting')
                                             THEN $2 ELSE s.token_hash END,
                           lease_until = NOW() + make_interval(secs => $1),
                           attempts = s.attempts + 1,
                           updated_at = NOW()
                     WHERE s.id = (
                           SELECT id FROM {TABLE}
                            WHERE (status IN ('queued', 'submitted')
                                   AND next_attempt_at <= NOW())
                               OR (status IN ('inviting', 'completing')
                                   AND lease_until < NOW())
                            ORDER BY (status IN ('submitted', 'completing')) DESC,
                                     next_attempt_at, created_at
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED)
                 RETURNING s.id, s.status, s.chat_id""",
                self._settings.lease_seconds,
                hash_token(token),
            )
        finally:
            await conn.close()
        if row is None:
            return None
        if row["status"] == "completing":
            return Job("complete", str(row["id"]), str(row["chat_id"]))
        return Job("invite", str(row["id"]), str(row["chat_id"]), token)

    async def mark_sent(self, job: Job, message_id: str) -> None:
        conn = await self._connect()
        try:
            if job.kind == "invite":
                await conn.execute(
                    f"""UPDATE {TABLE}
                           SET status = 'invited', invited_at = NOW(),
                               invite_message_id = $2, attempts = 0,
                               lease_until = NULL, last_error = NULL,
                               updated_at = NOW()
                         WHERE id = $1 AND status = 'inviting'""",
                    job.signup_id,
                    message_id,
                )
            else:
                await conn.execute(
                    f"""UPDATE {TABLE}
                           SET status = 'completed', completed_at = NOW(),
                               completion_message_id = $2, lease_until = NULL,
                               last_error = NULL, updated_at = NOW()
                         WHERE id = $1 AND status = 'completing'""",
                    job.signup_id,
                    message_id,
                )
        finally:
            await conn.close()

    async def mark_failed(self, job: Job, error: str) -> None:
        """Release a claim for a later retry, or give up after ``max_attempts``."""
        claimed = "inviting" if job.kind == "invite" else "completing"
        retry = "queued" if job.kind == "invite" else "submitted"
        conn = await self._connect()
        try:
            await conn.execute(
                f"""UPDATE {TABLE}
                       SET status = CASE WHEN attempts >= $3 THEN 'failed'
                                         ELSE $4 END,
                           next_attempt_at = NOW() + make_interval(
                               secs => $5 * attempts),
                           lease_until = NULL, last_error = $2,
                           updated_at = NOW()
                     WHERE id = $1 AND status = $6""",
                job.signup_id,
                error[:500],
                self._settings.max_attempts,
                retry,
                self._settings.retry_backoff_seconds,
                claimed,
            )
        finally:
            await conn.close()

    async def survey_state(self, token: str) -> SurveyState | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"SELECT status, submitted_at FROM {TABLE} WHERE token_hash = $1",
                hash_token(token),
            )
        finally:
            await conn.close()
        if row is None:
            return None
        return SurveyState(
            status=row["status"], submitted=row["submitted_at"] is not None
        )

    async def submit(self, token: str, answers: Mapping[str, object]) -> SubmitOutcome:
        """Store the answers and queue the slides; a second submit changes nothing."""
        conn = await self._connect()
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""SELECT id, submitted_at FROM {TABLE}
                         WHERE token_hash = $1 FOR UPDATE""",
                    hash_token(token),
                )
                if row is None:
                    return "not_found"
                if row["submitted_at"] is not None:
                    return "already_submitted"
                await conn.execute(
                    f"""UPDATE {TABLE}
                           SET answers = $2::jsonb, submitted_at = NOW(),
                               invited_at = COALESCE(invited_at, NOW()),
                               status = 'submitted', attempts = 0,
                               next_attempt_at = NOW(), lease_until = NULL,
                               last_error = NULL, updated_at = NOW()
                         WHERE id = $1""",
                    row["id"],
                    json.dumps(dict(answers), ensure_ascii=False),
                )
                return "submitted"
        finally:
            await conn.close()


def default_store(config: Mapping[str, object] | None = None) -> SurveyStore:
    loaded = config if config is not None else (load_config() or {})
    return SurveyStore(
        get_store("supabase-app", "prod", config=loaded),
        SurveySettings.from_config(loaded),
    )
