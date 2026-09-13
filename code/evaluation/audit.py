#!/usr/bin/env python3
"""End-to-end audit of output.csv against every rule stated in problem_statement.md.

This goes further than `requirements_check.py`, which proves the submission contract. This walks
the specification sentence by sentence, asserts each rule over all 250 rows, then looks for what
the rules do not say: coverage the public samples never exercise, distributional anomalies, and
data conditions in the corpus that could trip the pipeline on hidden rows.

    python code/evaluation/audit.py [--verbose]

Exit code is non-zero if any rule-level check fails. Observations that are not rule violations are
reported as WATCH lines and do not fail the audit.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buyorwait.config import PATHS  # noqa: E402
from buyorwait.data import load_dataset  # noqa: E402

FAILS: list = []
WATCH: list = []


def rule(name, ok, detail=""):
    if not ok:
        FAILS.append((name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<66}  {detail}")


def watch(name, detail):
    WATCH.append((name, detail))
    print(f"WATCH {name:<66}  {detail}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.parse_args()
    ds = load_dataset()
    reqs = {r.request_id: r for r in ds.requests}
    with PATHS.output.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    print("=" * 110)
    print("SECTION 1 - every rule stated in problem_statement.md, over all 250 rows")
    print("=" * 110)

    # --- the invariant the spec states explicitly ---
    bad = [r["request_id"] for r in rows
           if not (0 - 1e-9 <= float(r["amount_safe_to_pay"]) <= reqs[r["request_id"]].requested_amount + 1e-9)]
    rule("0 <= amount_safe_to_pay <= requested_amount", not bad, f"{len(bad)} violations")

    # --- affordable_now: full amount safe today AND user accepts full_payment ---
    bad = [r["request_id"] for r in rows if r["affordability_status"] == "affordable_now"
           and r["earliest_date_for_full_payment"] != reqs[r["request_id"]].request_date.isoformat()]
    rule("affordable_now => earliest_date == request_date", not bad, f"{len(bad)} violations")
    bad = [r["request_id"] for r in rows if r["affordability_status"] == "affordable_now"
           and "full_payment" not in ds.profiles[reqs[r["request_id"]].user_id].payment_methods]
    rule("affordable_now => user accepts full_payment", not bad, f"{len(bad)} violations")
    bad = [r["request_id"] for r in rows if r["affordability_status"] == "affordable_now"
           and abs(float(r["amount_safe_to_pay"]) - reqs[r["request_id"]].requested_amount) > 0.011]
    rule("affordable_now => amount_safe_to_pay == requested_amount", not bad, f"{len(bad)} violations")

    # --- the sentence about preference-independence of earliest_date ---
    indep = [r["request_id"] for r in rows
             if r["earliest_date_for_full_payment"] == reqs[r["request_id"]].request_date.isoformat()
             and r["recommended_payment_method"] != "full_payment"]
    watch("earliest_date == request_date while method != full_payment (spec says this is legal)",
          f"{len(indep)} rows: {indep[:6]}")

    # --- wait / affordable_later ---
    bad = [r["request_id"] for r in rows if r["recommended_payment_method"] == "wait"
           and "full_payment" not in ds.profiles[reqs[r["request_id"]].user_id].payment_methods]
    rule("wait => user accepts full_payment", not bad, f"{len(bad)} violations")
    bad = [r["request_id"] for r in rows
           if (r["recommended_payment_method"] == "wait") != (r["affordability_status"] == "affordable_later")]
    rule("wait <=> affordable_later", not bad, f"{len(bad)} mismatches")

    # --- not_recommended ---
    bad = [r["request_id"] for r in rows if r["recommended_payment_method"] == "not_recommended"
           and (r["payment_plan"] != "none" or r["affordability_status"] != "not_affordable"
                or r["spending_changes_needed"] != "none")]
    rule("not_recommended => plan none, status not_affordable, no changes", not bad, f"{len(bad)} violations")
    bad = [r["request_id"] for r in rows if r["affordability_status"] == "not_affordable"
           and r["earliest_date_for_full_payment"]]
    # Deliberate, documented reading rather than a rule. The specification blanks this field only
    # when the amount "is not expected to become safe within the forecast period", and separately
    # states the field measures capacity independently of the recommendation. For these rows
    # capacity does arrive inside the 90 days, just after the deadline, so it is reported.
    watch("not_affordable rows reporting a capacity date after the deadline (documented reading)",
          f"{len(bad)} rows: {bad[:6]}")
    gold_na = [s for s in ds.samples if s.affordability_status == "not_affordable"]
    watch("gold not_affordable samples that could disambiguate this",
          f"{sum(1 for s in gold_na if s.earliest_date_for_full_payment)} of {len(gold_na)} carry a date "
          f"(none of them has capacity within 90 days, so gold gives no evidence either way)")

    # --- partial payment: all five stated conditions ---
    for r in rows:
        if r["recommended_payment_method"] != "partial_payment":
            continue
        q = reqs[r["request_id"]]
        p = ds.profiles[q.user_id]
        parts = r["payment_plan"].split("|")
        safe = float(r["amount_safe_to_pay"])
        conds = {
            "status is affordable_with_plan": r["affordability_status"] == "affordable_with_plan",
            "request allows partial": q.allows_partial_payment,
            "user accepts partial": "partial_payment" in p.payment_methods,
            "0 < safe < requested": 0 < safe < q.requested_amount,
            "exactly two payments": len(parts) == 2,
            # Compared numerically, not textually: the organizer's own convention strips trailing
            # zeros in amount_safe_to_pay (603.3) while writing plan amounts to two decimals
            # (620.40), so the two fields legitimately differ as strings.
            "first payment is on request_date": parts[0].split(":")[0] == q.request_date.isoformat(),
            "first payment equals amount_safe_to_pay":
                abs(float(parts[0].split(":")[1]) - safe) < 0.011,
            "two payments sum to requested":
                abs(sum(float(x.split(':')[1]) for x in parts) - q.requested_amount) < 0.011,
            "second payment == earliest_date": parts[-1].split(":")[0] == r["earliest_date_for_full_payment"],
            "earliest <= desired_completion_date":
                r["earliest_date_for_full_payment"] <= q.desired_completion_date.isoformat(),
        }
        for k, v in conds.items():
            if not v:
                FAILS.append((f"partial_payment {r['request_id']}: {k}", ""))
    n_partial = sum(1 for r in rows if r["recommended_payment_method"] == "partial_payment")
    rule(f"partial_payment: all nine stated conditions ({n_partial} rows)",
         not [f for f in FAILS if f[0].startswith("partial_payment")], "")

    # --- installments must match a supplied option exactly ---
    bad = []
    for r in rows:
        if r["recommended_payment_method"] != "installments":
            continue
        plan = [(x.split(":")[0], round(float(x.split(":")[1]), 2)) for x in r["payment_plan"].split("|")]
        ok = any(o.payment_method == "installments"
                 and [(d.isoformat(), round(a, 2)) for d, a in o.schedule()] == plan
                 for o in ds.options_by_request.get(r["request_id"], []))
        if not ok:
            bad.append(r["request_id"])
    n_inst = sum(1 for r in rows if r["recommended_payment_method"] == "installments")
    rule(f"installments exactly match a supplied option, unshifted ({n_inst} rows)", not bad, f"{len(bad)} violations")

    # --- every recommended plan completes by the deadline ---
    bad = []
    for r in rows:
        if r["payment_plan"] == "none":
            continue
        last = max(dt.date.fromisoformat(x.split(":")[0]) for x in r["payment_plan"].split("|"))
        if last > reqs[r["request_id"]].desired_completion_date:
            bad.append(r["request_id"])
    rule("every recommended plan completes by desired_completion_date", not bad, f"{len(bad)} violations")

    # --- plan totals ---
    bad = []
    for r in rows:
        if r["payment_plan"] == "none" or r["recommended_payment_method"] == "installments":
            continue
        total = sum(float(x.split(":")[1]) for x in r["payment_plan"].split("|"))
        if abs(total - reqs[r["request_id"]].requested_amount) > 0.011:
            bad.append(r["request_id"])
    rule("non-installment plans total exactly the requested amount", not bad, f"{len(bad)} violations")
    bad = [r["request_id"] for r in rows if r["payment_plan"] != "none"
           and [x.split(":")[0] for x in r["payment_plan"].split("|")]
           != sorted(x.split(":")[0] for x in r["payment_plan"].split("|"))]
    rule("payment_plan is chronological", not bad, f"{len(bad)} violations")

    # --- spending changes ---
    bad_flex, bad_prot, bad_dup, bad_min, bad_cnt, bad_status, bad_owner = [], [], [], [], [], [], []
    for r in rows:
        if r["spending_changes_needed"] == "none":
            continue
        q = reqs[r["request_id"]]
        p = ds.profiles[q.user_id]
        parts = r["spending_changes_needed"].split("|")
        if len(parts) > 3:
            bad_cnt.append(r["request_id"])
        if r["affordability_status"] != "affordable_with_plan":
            bad_status.append(r["request_id"])
        seen = set()
        for part in parts:
            eid = part.split(":")[1]
            if eid in seen:
                bad_dup.append(r["request_id"])
            seen.add(eid)
            ev = ds.events_by_id.get(eid)
            if ev is None or ev.user_id != q.user_id:
                bad_owner.append(r["request_id"])
                continue
            if ev.flexibility == "fixed":
                bad_flex.append(r["request_id"])
            if ev.category in p.protect:
                bad_prot.append(r["request_id"])
            if part.startswith("stop:") and (ev.category not in p.willing_to_stop or "stoppable" not in ev.flexibility):
                bad_flex.append(r["request_id"])
            if part.startswith("reduce_to:"):
                amt = float(part.split(":")[2])
                if ev.category not in p.willing_to_reduce or "reducible" not in ev.flexibility:
                    bad_flex.append(r["request_id"])
                if ev.minimum_allowed_amount is not None and amt < ev.minimum_allowed_amount - 0.005:
                    bad_min.append(r["request_id"])
    n_chg = sum(1 for r in rows if r["spending_changes_needed"] != "none")
    rule(f"spending changes target permitted flexible events only ({n_chg} rows)", not bad_flex, f"{len(bad_flex)}")
    rule("spending changes never touch a protected category", not bad_prot, f"{len(bad_prot)}")
    rule("spending changes belong to the requesting user", not bad_owner, f"{len(bad_owner)}")
    rule("stop and reduce never target the same event", not bad_dup, f"{len(bad_dup)}")
    rule("reduce_to never below minimum_allowed_amount", not bad_min, f"{len(bad_min)}")
    rule("at most three spending changes", not bad_cnt, f"{len(bad_cnt)}")
    rule("spending changes only with affordable_with_plan", not bad_status, f"{len(bad_status)}")

    # --- explanation ---
    bad = [r["request_id"] for r in rows if not r["decision_explanation"].strip()]
    rule("decision_explanation non-empty on every row", not bad, f"{len(bad)} empty")
    cur_missing = [r["request_id"] for r in rows
                   if ds.profiles[reqs[r["request_id"]].user_id].home_currency not in r["decision_explanation"]]
    rule("explanation states the user's home currency", not cur_missing, f"{len(cur_missing)} missing")

    print()
    print("=" * 110)
    print("SECTION 2 - status / method coherence")
    print("=" * 110)
    combos = collections.Counter((r["affordability_status"], r["recommended_payment_method"]) for r in rows)
    legal = {("affordable_now", "full_payment"), ("affordable_with_plan", "full_payment"),
             ("affordable_with_plan", "partial_payment"), ("affordable_with_plan", "installments"),
             ("affordable_later", "wait"), ("not_affordable", "not_recommended")}
    for (st, me), n in sorted(combos.items()):
        flag = "" if (st, me) in legal else "   <-- NOT A LEGAL COMBINATION"
        print(f"  {st:22s} {me:16s} {n:4d}{flag}")
    rule("only legal status/method combinations appear", all(c in legal for c in combos), "")
    withchg = collections.Counter(r["recommended_payment_method"] for r in rows if r["spending_changes_needed"] != "none")
    print(f"  rows with spending changes by method: {dict(withchg)}")
    bad = [r["request_id"] for r in rows if r["affordability_status"] == "affordable_with_plan"
           and r["recommended_payment_method"] == "full_payment" and r["spending_changes_needed"] == "none"]
    rule("affordable_with_plan + full_payment always carries a spending change", not bad, f"{len(bad)} violations")

    print()
    print("=" * 110)
    print("SECTION 3 - coverage the public samples never exercise")
    print("=" * 110)
    s_types = {s.request.request_type for s in ds.samples}
    r_types = {r.request_type for r in ds.requests}
    print(f"  request types in samples: {len(s_types)}   in evaluation set: {len(r_types)}")
    watch("request types never seen in a solved sample", str(sorted(r_types - s_types)) or "none")
    s_cur = {ds.profiles[s.request.user_id].home_currency for s in ds.samples}
    r_cur = {ds.profiles[r.user_id].home_currency for r in ds.requests}
    watch("currencies never seen in a solved sample", str(sorted(r_cur - s_cur)) or "none")
    s_meth = {tuple(sorted(ds.profiles[s.request.user_id].payment_methods)) for s in ds.samples}
    r_meth = collections.Counter(tuple(sorted(ds.profiles[r.user_id].payment_methods)) for r in ds.requests)
    unseen = {m: n for m, n in r_meth.items() if m not in s_meth}
    watch("payment-method combinations never seen in a sample", f"{sum(unseen.values())} rows: {list(unseen)[:4]}")

    print()
    print("=" * 110)
    print("SECTION 4 - dataset conditions that could trip a hidden row")
    print("=" * 110)
    no_opts = [r.request_id for r in ds.requests if not ds.options_by_request.get(r.request_id)]
    watch("requests with no payment options at all", f"{len(no_opts)} {no_opts[:5]}")
    no_full = [r.request_id for r in ds.requests
               if not any(o.payment_method == "full_payment" for o in ds.options_by_request.get(r.request_id, []))]
    watch("requests with no full_payment option supplied", f"{len(no_full)} {no_full[:5]}")
    no_inst = [r.request_id for r in ds.requests
               if not any(o.payment_method == "installments" for o in ds.options_by_request.get(r.request_id, []))]
    watch("requests with no installment option supplied", f"{len(no_inst)} {no_inst[:5]}")
    missing_prof = [r.request_id for r in ds.requests if r.user_id not in ds.profiles]
    rule("every request's user has a financial profile", not missing_prof, f"{len(missing_prof)} missing")
    no_events = [r.request_id for r in ds.requests if not ds.events_by_user.get(r.user_id)]
    rule("every request's user has a financial history", not no_events, f"{len(no_events)} with none")
    no_income = [r.request_id for r in ds.requests
                 if not any(e.direction == "credit" for e in ds.events_by_user.get(r.user_id, []))]
    watch("users with no income event of any kind", f"{len(no_income)} {no_income[:5]}")
    below_min = [r.request_id for r in ds.requests
                 if ds.profiles[r.user_id].current_available_balance < ds.profiles[r.user_id].minimum_balance_to_keep]
    watch("users already below their minimum balance on the request date", f"{len(below_min)} {below_min[:5]}")
    deadline_past = [r.request_id for r in ds.requests if r.desired_completion_date < r.request_date]
    rule("no request deadline falls before its request date", not deadline_past, f"{len(deadline_past)}")
    far = [r.request_id for r in ds.requests if (r.desired_completion_date - r.request_date).days > 90]
    watch("deadlines beyond the 90-day forecast horizon", f"{len(far)} {far[:5]}")
    same_day = [r.request_id for r in ds.requests if r.desired_completion_date == r.request_date]
    watch("deadline equal to the request date (zero slack)", f"{len(same_day)} {same_day[:5]}")
    dup_ev = [k for k, v in collections.Counter(e.event_id for e in ds.events).items() if v > 1]
    rule("event_id is unique across the corpus", not dup_ev, f"{len(dup_ev)} duplicates")
    back_settle = [e.event_id for e in ds.events if e.settlement_date < e.event_date]
    watch("events settling before their event date", f"{len(back_settle)} {back_settle[:5]}")
    neg = [e.event_id for e in ds.events if e.amount is not None and e.amount < 0]
    rule("no negative amounts in the ledger", not neg, f"{len(neg)}")
    huge = [r.request_id for r in ds.requests
            if r.requested_amount > 5 * ds.profiles[r.user_id].current_available_balance]
    watch("requests over five times the available balance", f"{len(huge)} {huge[:5]}")
    tiny = [r.request_id for r in ds.requests if r.requested_amount <= 0]
    rule("no request has a non-positive amount", not tiny, f"{len(tiny)}")

    print()
    print("=" * 110)
    print("SECTION 5 - distribution of the 250 predictions against the 25 solved samples")
    print("=" * 110)
    for field in ("affordability_status", "recommended_payment_method"):
        got = collections.Counter(r[field] for r in rows)
        gold = collections.Counter(getattr(s, field) for s in ds.samples)
        print(f"  {field}")
        keys = sorted(set(got) | set(gold))
        for k in keys:
            g = 100 * gold.get(k, 0) / len(ds.samples)
            o = 100 * got.get(k, 0) / len(rows)
            flag = "   <-- large divergence" if abs(g - o) > 15 else ""
            print(f"     {k:22s} sample {g:5.1f}%   ours {o:5.1f}%{flag}")
    empty_earliest = sum(1 for r in rows if not r["earliest_date_for_full_payment"])
    gold_empty = sum(1 for s in ds.samples if not s.earliest_date_for_full_payment)
    print(f"  empty earliest_date: sample {100*gold_empty/len(ds.samples):.1f}%   ours {100*empty_earliest/len(rows):.1f}%")
    chg = sum(1 for r in rows if r["spending_changes_needed"] != "none")
    gold_chg = sum(1 for s in ds.samples if s.spending_changes_needed != "none")
    print(f"  rows with spending changes: sample {100*gold_chg/len(ds.samples):.1f}%   ours {100*chg/len(rows):.1f}%")

    # Capacity semantics: the field is defined as a single full payment WITHOUT optional spending
    # changes, so an affordable_with_plan row whose plan needs a change may legitimately leave it
    # empty. Every such row must therefore be one whose plan carries a spending change.
    bad = [r["request_id"] for r in rows if r["affordability_status"] == "affordable_with_plan"
           and not r["earliest_date_for_full_payment"] and r["spending_changes_needed"] == "none"]
    n_empty = sum(1 for r in rows if r["affordability_status"] == "affordable_with_plan"
                  and not r["earliest_date_for_full_payment"])
    rule("affordable_with_plan with an empty earliest_date always needs a spending change",
         not bad, f"{n_empty} such rows, {len(bad)} unexplained")
    bad = [r["request_id"] for r in rows
           if r["earliest_date_for_full_payment"] == reqs[r["request_id"]].request_date.isoformat()
           and abs(float(r["amount_safe_to_pay"]) - reqs[r["request_id"]].requested_amount) > 0.011]
    rule("capacity today => amount_safe_to_pay equals the requested amount", not bad, f"{len(bad)} violations")

    # Formatting must follow the organizer's own convention, which differs between the two fields.
    import re as _re2
    bad_safe = [r["request_id"] for r in rows if "." in r["amount_safe_to_pay"]
                and r["amount_safe_to_pay"] != r["amount_safe_to_pay"].rstrip("0").rstrip(".")]
    rule("amount_safe_to_pay strips trailing zeros, as gold does", not bad_safe, f"{len(bad_safe)}")
    bad_plan = [r["request_id"] for r in rows for x in r["payment_plan"].split("|")
                if ":" in x and "." in x.split(":", 1)[1] and len(x.split(":", 1)[1].split(".")[1]) != 2]
    rule("plan amounts use two decimals when fractional, as gold does", not bad_plan, f"{len(bad_plan)}")
    ungrouped = [r["request_id"] for r in rows
                 for m in _re2.findall(r"[A-Z]{3} (\d[\d,]*)", r["decision_explanation"])
                 if len(m.replace(",", "").split(".")[0]) >= 4 and "," not in m]
    rule("explanations group thousands, as gold does", not ungrouped, f"{len(ungrouped)}")

    print()
    print("=" * 110)
    print("SECTION 6 - explanation shape against the organizer's templates")
    print("=" * 110)
    import re as _re
    TEMPLATES = {
        "pay in full today": _re.compile(r"^Pay .+ today\. This (leaves|keeps) at least .+ available"),
        "changes then pay today": _re.compile(r"^(Stop|Reduce) .+, then pay .+ today\."),
        "installments": _re.compile(r"^(Stop |Reduce )?.*[Uu]se \d+ installments of .+, starting .+\."),
        "partial": _re.compile(r"^Pay .+ today and the remaining .+ on .+\."),
        "wait until": _re.compile(r"^Wait until .+, then pay .+ in full\."),
        "pay in full on": _re.compile(r"^Pay .+ in full on .+\."),
        "do not by date": _re.compile(r"^Do not make this payment by .+\."),
        "although available": _re.compile(r"^Do not proceed with the .+ request\. Although "),
    }
    unmatched = []
    hits = collections.Counter()
    for r in rows:
        t = r["decision_explanation"]
        m = [k for k, rx in TEMPLATES.items() if rx.match(t)]
        if m:
            hits[m[0]] += 1
        else:
            unmatched.append((r["request_id"], t[:90]))
    for k, n in hits.most_common():
        print(f"  {k:26s} {n:4d}")
    rule("every explanation matches an organizer template shape", not unmatched,
         f"{len(unmatched)} unmatched" + (f" e.g. {unmatched[0]}" if unmatched else ""))
    gold_shapes = set()
    for s_ in ds.samples:
        gold_shapes |= {k for k, rx in TEMPLATES.items() if rx.match(s_.decision_explanation)}
    watch("template shapes we emit that no solved sample uses", str(sorted(set(hits) - gold_shapes)) or "none")

    print()
    print("=" * 110)
    print("SECTION 6b - every request with no projected income must have a stated reason")
    print("=" * 110)
    from buyorwait.evidence import load_facts_file as _lf
    from buyorwait.state import Policy as _P, is_recurring_income, is_terminal_income, reconstruct as _rc
    fp = PATHS.cache / "facts.json"
    _facts = _lf(fp) if fp.is_file() else {}
    unexplained, kinds = [], collections.Counter()
    for q in ds.requests:
        rec = _rc(ds, q.user_id, q.request_date, _facts.get(q.request_id, []), _P())
        if any(f.amount > 0 for f in rec.flows):
            continue
        hist = sorted([e for e in ds.events_by_user.get(q.user_id, [])
                       if e.direction == "credit" and e.status == "settled"], key=lambda e: e.settlement_date)
        payroll = [e for e in hist if e.category == "salary" and is_recurring_income(e.description)]
        if not hist:
            kinds["no income history"] += 1
        elif not payroll:
            kinds["irregular income only (gig, freelance, seasonal)"] += 1
        elif is_terminal_income(payroll[-1].description):
            kinds["final payroll"] += 1
        elif (q.request_date - payroll[-1].settlement_date).days > _P().stale_income_days:
            kinds["payroll stale beyond the cutoff"] += 1
        else:
            unexplained.append(q.request_id)
    for k, v in kinds.most_common():
        print(f"  {k:52s} {v}")
    rule("no request silently loses a live payroll", not unexplained,
         f"{sum(kinds.values())} with no income, {len(unexplained)} unexplained {unexplained[:5]}")

    print()
    print("=" * 110)
    print("SECTION 7 - decisions that look implausible on their own numbers")
    print("=" * 110)
    odd = []
    for r in rows:
        q = reqs[r["request_id"]]
        p = ds.profiles[q.user_id]
        head = p.current_available_balance - p.minimum_balance_to_keep
        safe = float(r["amount_safe_to_pay"])
        if r["affordability_status"] == "not_affordable" and head > 3 * q.requested_amount:
            odd.append((r["request_id"], f"not_affordable but headroom {head:.0f} > 3x request {q.requested_amount:.0f}"))
        if r["affordability_status"] == "affordable_now" and head < q.requested_amount:
            odd.append((r["request_id"], f"affordable_now but headroom {head:.0f} < request {q.requested_amount:.0f}"))
        if safe > head + 0.011:
            odd.append((r["request_id"], f"safe {safe:.2f} exceeds opening headroom {head:.2f}"))
    for rid, why in odd[:12]:
        print(f"  {rid}: {why}")
    rule("no row reports a safe amount above its opening headroom",
         not [o for o in odd if "exceeds opening headroom" in o[1]], f"{len([o for o in odd if 'exceeds' in o[1]])}")
    watch("rows worth eyeballing for plausibility", f"{len(odd)} flagged")

    print()
    print("=" * 110)
    print(f"RESULT: {len(FAILS)} rule failures, {len(WATCH)} observations to review")
    for n, d in FAILS:
        print(f"  FAIL  {n} {d}")
    print("=" * 110)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
