"""Hand-calculated D01-D20 scenarios and independent configurable faults."""

from datetime import timedelta

from .history import utc

SCENARIOS = tuple(f"D{number:02d}" for number in range(1, 21))


def _paid_invoice(h, amount, *, pieces=None):
    invoice, group = h.invoice(amount, h.moment(6), pieces=pieces)
    h.payment(amount, h.moment(8), [invoice])
    return invoice, group


def _split(h):
    invoice, _ = h.invoice(12_000_000, h.moment(1))
    h.payment(5_000_000, h.moment(3), [invoice])
    payment, group = h.payment(7_000_000, h.moment(5), [invoice])
    return invoice, payment, group


def _foreign(h, currency, amount, rate, wrong=None, moment=None):
    moment = moment or h.moment(14)
    h.rate(moment, currency, rate)
    if wrong is not None:
        h.rate(moment - timedelta(days=1), currency, wrong)
    invoice, _ = h.invoice(h.convert(amount, currency, moment), moment)
    payment, group = h.payment(amount, moment, [invoice], currency=currency)
    return invoice, payment, group


def _cutoff(h, mutations, amount, *, latest=False, duplicate=False, utc_event=False):
    boundary = h.end if latest else h.start
    event = boundary + timedelta(minutes=5)
    invoice, group = h.invoice(amount, utc(event) if utc_event else event)
    if duplicate:
        mutations.schedule(mutations.duplicate, invoice, group)
    mutations.schedule(mutations.cutoff, invoice, group, boundary - timedelta(minutes=5))


def scenario(history, mutations, name):
    h = history
    if name == "D01":
        _paid_invoice(h, 4_230_000)
    elif name == "D02":
        _split(h)
    elif name == "D03":
        invoice, _ = h.invoice(1_200_000, h.moment(2))
        h.payment(1_600_000, h.moment(4), [invoice], allocations=[1_200_000])
        h.payment(400_000, h.moment(7), [invoice], refund=True)
    elif name == "D04":
        for _ in range(2):
            _paid_invoice(h, 8_850_000)
    elif name == "D05":
        _foreign(h, "USD", 20_000, "500.125", moment=utc(h.start))
    elif name in {"D06", "D07", "D08"}:
        params = {"D06": (1_000_000, None), "D07": (7_345_000, (3_000_000, 4_345_000)),
                  "D08": (1_825_000, None)}
        amount, pieces = params[name]
        invoice, group = _paid_invoice(h, amount, pieces=pieces)
        if name == "D08":
            _paid_invoice(h, amount)
        mutations.schedule(mutations.duplicate, invoice, group)
    elif name == "D09":
        invoice, _ = h.invoice(3_100_000, h.moment(3))
        payment, group = h.payment(3_100_000, h.moment(8), [invoice])
        mutations.schedule(mutations.allocation, payment, group, invoice, 0)
    elif name == "D10":
        invoice, payment, group = _split(h)
        mutations.schedule(mutations.allocation, payment, group, invoice, 2_000_000)
    elif name == "D11":
        first, _ = h.invoice(3_500_000, h.moment(6))
        second, _ = h.invoice(2_000_000, h.moment(6), entity=first["entity_id"])
        payment, group = h.payment(5_500_000, h.moment(8), [first, second],
                                   allocations=[3_500_000, 2_000_000])
        mutations.schedule(mutations.allocation, payment, group, first, 1_140_000)
    elif name in {"D12", "D13", "D16", "D17", "D18"}:
        params = {
            "D12": ("USD", 10_000, "500", "490", None),
            "D13": ("USD", 4_000, "501.25", "498", None),
            "D16": ("USD", 10_000, "500", "490", 8_000),
            "D17": ("EUR", 25_000, "560", "555", 20_000),
            "D18": ("USD", 6_000, "502", "497.50", 4_500),
        }
        currency, amount, correct, wrong, applied = params[name]
        invoice, payment, group = _foreign(h, currency, amount, correct, wrong)
        if applied is not None:
            mutations.schedule(mutations.allocation, payment, group, invoice, applied)
        mutations.schedule(mutations.fx, payment, group, wrong)
    elif name in {"D14", "D15", "D19", "D20"}:
        params = {"D14": 6_700_000, "D15": 2_230_000, "D19": 12_000_000, "D20": 9_265_000}
        _cutoff(h, mutations, params[name], latest=name == "D15",
                duplicate=name in {"D19", "D20"}, utc_event=name in {"D15", "D20"})


def faults(history, mutations, counts):
    h = history
    for kind, count in counts.items():
        for index in range(count):
            if kind == "duplicate_invoice":
                amount = h.rng.randrange(50_000, 2_000_000)
                mutations.schedule(mutations.duplicate, *_paid_invoice(h, amount))
            elif kind == "payment_allocation":
                amount = h.rng.randrange(50_000, 2_000_000)
                invoice, _ = h.invoice(amount, h.moment(3))
                payment, group = h.payment(amount, h.moment(8), [invoice])
                mutations.schedule(mutations.allocation, payment, group, invoice, amount // 2)
            elif kind == "fx_conversion":
                currency = ("USD", "EUR", "JPY")[index % 3]
                amount = h.rng.randrange(1_000, 50_000)
                _, payment, group = _foreign(h, currency, amount, "500.125", "499.25")
                mutations.schedule(mutations.fx, payment, group, "499.25")
            elif kind == "period_cutoff":
                _cutoff(h, mutations, h.rng.randrange(50_000, 2_000_000), latest=bool(index % 2))


def stress_scenario(h, mutations, name):
    # Two independent obligations can legitimately have the same total.
    for _ in range(2):
        _paid_invoice(h, 8_850_000)
    invoice, _ = h.invoice(1_200_000, h.moment(1))
    for day, installment in enumerate((100_000, 200_000, 400_000, 500_000), start=3):
        h.payment(installment, h.moment(day), [invoice])

    invoice, _ = h.invoice(1_200_000, h.moment(2))
    h.payment(1_800_000, h.moment(4), [invoice], allocations=[1_200_000])
    h.payment(200_000, h.moment(7), [invoice], refund=True)
    h.payment(150_000, h.moment(11), [invoice], refund=True)

    midnight = utc(h.moment(25)).replace(hour=0) + timedelta(days=1)
    before = midnight - timedelta(minutes=2)
    after = midnight + timedelta(minutes=2)
    h.rate(before - timedelta(days=1), "JPY", "3.02")
    _foreign(h, "JPY", 20_000, "3.1234", moment=before)
    _foreign(h, "JPY", 10_000, "3.1234", moment=after)

    scenario(h, mutations, name)
    number = int(name[1:])
    if number <= 5:
        return
    # The same invoice is posted again. Its revenue is split into service lines
    # on the second posting, but the net amount and original invoice match.
    amount = h.rng.randrange(60_000, 1_800_000)
    first = amount // 3
    invoice, group = _paid_invoice(h, amount, pieces=(first, amount - first))
    mutations.schedule(mutations.duplicate, invoice, group, True)
    original_count = 2 if number >= 16 else 1
    extra = 3 + (number - 6) % 3 - original_count - 1
    for kind in ("payment_allocation", "period_cutoff", "duplicate_invoice")[:extra]:
        faults(h, mutations, {kind: 1})
