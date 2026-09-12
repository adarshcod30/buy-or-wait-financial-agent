"""Typed loading of the participant-facing dataset.

Every table is loaded once into dataclasses and indexed by the identifiers the problem
statement says to join on: user_id, request_id, related_event_id, (rate_date, from, to).
Blank amounts are kept as None (never zero) so the evidence layer must resolve them.
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import PATHS


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s.strip())


def _f(s: str) -> Optional[float]:
    s = (s or "").strip()
    return float(s) if s else None


def _list(s: str) -> List[str]:
    return [x for x in (s or "").split("|") if x]


@dataclass(frozen=True)
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: List[str]
    protect: List[str]
    willing_to_reduce: List[str]
    willing_to_stop: List[str]
    payment_methods: List[str]
    max_installment_months: Optional[int]


@dataclass(frozen=True)
class Event:
    event_id: str
    user_id: str
    event_type: str          # expense | subscription | income | debt_payment | refund | investment_*
    description: str
    category: str
    direction: str           # debit | credit | non_cash
    amount: Optional[float]  # None when blank in the file (resolved from an image later)
    currency: str
    event_date: dt.date
    settlement_date: dt.date
    status: str              # settled | pending | scheduled | cancelled | failed | unrealized
    linked_event_id: str
    flexibility: str         # fixed | reducible | stoppable | reducible_or_stoppable
    minimum_allowed_amount: Optional[float]


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: dt.date
    request_type: str
    requested_amount: float
    desired_completion_date: dt.date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str      # full_payment | installments
    payment_amount: float
    number_of_payments: int
    first_payment_date: dt.date
    payment_frequency_days: Optional[int]
    financing_fee: float
    total_payable_amount: float

    @property
    def option_number(self) -> int:
        return int(self.payment_option_id.rsplit("_", 1)[1])

    def schedule(self) -> List[tuple]:
        """(date, amount) pairs exactly as the seller specifies them."""
        out = []
        for i in range(self.number_of_payments):
            step = (self.payment_frequency_days or 0) * i
            out.append((self.first_payment_date + dt.timedelta(days=step), self.payment_amount))
        return out


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: str
    related_event_id: str
    sent_at: str
    source_type: str
    message_text: str


@dataclass(frozen=True)
class ImageRef:
    image_id: str
    user_id: str
    request_id: str
    related_event_id: str

    @property
    def path(self) -> Path:
        return PATHS.images / f"{self.image_id}.png"


@dataclass(frozen=True)
class SampleRow:
    request: Request
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str


@dataclass
class Dataset:
    profiles: Dict[str, Profile]
    events: List[Event]
    events_by_user: Dict[str, List[Event]]
    events_by_id: Dict[str, Event]
    requests: List[Request]
    samples: List[SampleRow]
    options_by_request: Dict[str, List[PaymentOption]]
    messages_by_user: Dict[str, List[Message]]
    images_by_event: Dict[str, ImageRef]
    images: List[ImageRef]
    rates: Dict[tuple, float] = field(default_factory=dict)  # (date, from, to) -> rate

    @property
    def stats(self) -> dict:
        return {
            "profiles": len(self.profiles),
            "events": len(self.events),
            "requests": len(self.requests),
            "samples": len(self.samples),
            "payment_options": sum(len(v) for v in self.options_by_request.values()),
            "messages": sum(len(v) for v in self.messages_by_user.values()),
            "images": len(self.images),
            "rates": len(self.rates),
        }


def _read(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _request_from_row(r: dict) -> Request:
    return Request(
        request_id=r["request_id"],
        user_id=r["user_id"],
        request_date=_d(r["request_date"]),
        request_type=r["request_type"],
        requested_amount=float(r["requested_amount"]),
        desired_completion_date=_d(r["desired_completion_date"]),
        allows_partial_payment=r["allows_partial_payment"].strip().lower() == "true",
        request_text=r["request_text"],
    )


def load_dataset(dataset_dir: Optional[Path] = None) -> Dataset:
    d = dataset_dir or PATHS.dataset

    profiles: Dict[str, Profile] = {}
    for r in _read(d / "financial_profiles.csv"):
        mim = r.get("max_installment_months", "").strip()
        profiles[r["user_id"]] = Profile(
            user_id=r["user_id"],
            home_currency=r["home_currency"],
            current_available_balance=float(r["current_available_balance"]),
            minimum_balance_to_keep=float(r["minimum_balance_to_keep"]),
            financial_priorities=_list(r["financial_priorities"]),
            protect=_list(r["expense_categories_to_protect"]),
            willing_to_reduce=_list(r["expense_categories_user_is_willing_to_reduce"]),
            willing_to_stop=_list(r["expense_categories_user_is_willing_to_stop"]),
            payment_methods=_list(r["payment_methods_user_will_consider"]),
            max_installment_months=int(mim) if mim else None,
        )

    events: List[Event] = []
    for r in _read(d / "financial_events.csv"):
        events.append(Event(
            event_id=r["event_id"], user_id=r["user_id"], event_type=r["event_type"],
            description=r["description"], category=r["category"], direction=r["direction"],
            amount=_f(r["amount"]), currency=r["currency"],
            event_date=_d(r["event_date"]), settlement_date=_d(r["settlement_date"] or r["event_date"]),
            status=r["status"], linked_event_id=r["linked_event_id"].strip(),
            flexibility=r["flexibility"], minimum_allowed_amount=_f(r["minimum_allowed_amount"]),
        ))
    events_by_user: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        events_by_user[e.user_id].append(e)
    for v in events_by_user.values():
        v.sort(key=lambda e: (e.event_date, e.event_id))
    events_by_id = {e.event_id: e for e in events}

    requests = [_request_from_row(r) for r in _read(d / "requests.csv")]

    samples: List[SampleRow] = []
    for r in _read(d / "sample_requests.csv"):
        samples.append(SampleRow(
            request=_request_from_row(r),
            amount_safe_to_pay=float(r["amount_safe_to_pay"]),
            affordability_status=r["affordability_status"],
            recommended_payment_method=r["recommended_payment_method"],
            payment_plan=r["payment_plan"],
            earliest_date_for_full_payment=r["earliest_date_for_full_payment"],
            spending_changes_needed=r["spending_changes_needed"],
            decision_explanation=r["decision_explanation"],
        ))

    options_by_request: Dict[str, List[PaymentOption]] = defaultdict(list)
    for r in _read(d / "request_payment_options.csv"):
        freq = r["payment_frequency_days"].strip()
        options_by_request[r["request_id"]].append(PaymentOption(
            payment_option_id=r["payment_option_id"], request_id=r["request_id"],
            payment_method=r["payment_method"], payment_amount=float(r["payment_amount"]),
            number_of_payments=int(r["number_of_payments"]), first_payment_date=_d(r["first_payment_date"]),
            payment_frequency_days=int(freq) if freq else None,
            financing_fee=float(r["financing_fee"] or 0), total_payable_amount=float(r["total_payable_amount"]),
        ))

    messages_by_user: Dict[str, List[Message]] = defaultdict(list)
    for r in _read(d / "messages.csv"):
        messages_by_user[r["user_id"]].append(Message(
            message_id=r["message_id"], user_id=r["user_id"], request_id=r["request_id"].strip(),
            related_event_id=r["related_event_id"].strip(), sent_at=r["sent_at"],
            source_type=r["source_type"], message_text=r["message_text"],
        ))

    images = [ImageRef(r["image_id"], r["user_id"], r["request_id"].strip(), r["related_event_id"].strip())
              for r in _read(d / "images.csv")]
    images_by_event = {i.related_event_id: i for i in images if i.related_event_id}

    rates: Dict[tuple, float] = {}
    for r in _read(d / "exchange_rates.csv"):
        rates[(_d(r["rate_date"]), r["from_currency"], r["to_currency"])] = float(r["rate"])

    return Dataset(
        profiles=profiles, events=events, events_by_user=dict(events_by_user), events_by_id=events_by_id,
        requests=requests, samples=samples, options_by_request=dict(options_by_request),
        messages_by_user=dict(messages_by_user), images_by_event=images_by_event, images=images, rates=rates,
    )
