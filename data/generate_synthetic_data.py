"""Generate a synthetic, intentionally messy CRM gift export for Carmari Academy.

This script simulates what a raw export out of a Salesforce-Ascend-style
advancement CRM typically looks like before any cleanup: inconsistent date
formats, stray whitespace, a handful of malformed emails, a few negative or
absurdly large gift amounts, some duplicate donor IDs, and missing optional
fields. All data is fabricated with Faker -- no real people, institutions,
or gifts are represented.

Usage:
    python data/generate_synthetic_data.py --records 5000 --output data/sample_donors.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

FUND_DESIGNATIONS = [
    "Annual Fund",
    "Scholarship Fund",
    "Athletics",
    "Capital Campaign",
    "Unrestricted",
    "Endowment",
]

CONSTITUENT_TYPES_WEIGHTED = [
    ("Alumni", 55),
    ("Parent", 15),
    ("Friend", 15),
    ("Faculty/Staff", 10),
    ("Organization", 5),
]

# Giving capacity tier -> (lognormal mean, lognormal sigma) used to draw gift amounts.
CAPACITY_TIERS = {
    "Low": (4.5, 0.5),       # typically tens to low hundreds of dollars
    "Medium": (6.0, 0.6),    # typically hundreds to low thousands
    "High": (8.0, 0.7),      # typically thousands to tens of thousands
    "Principal": (10.5, 0.8),  # major/principal gift territory
}
CAPACITY_WEIGHTED = [("Low", 50), ("Medium", 30), ("High", 15), ("Principal", 5)]

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%d-%b-%Y"]


def _weighted_choice(options: list[tuple[str, int]]) -> str:
    """Pick a value from a list of (value, weight) tuples."""
    labels = [o[0] for o in options]
    weights = [o[1] for o in options]
    return random.choices(labels, weights=weights, k=1)[0]


def _messy_date_string(d: date, messy: bool) -> str:
    """Render a date as a string, optionally using a non-ISO format to mimic raw exports."""
    fmt = random.choice(DATE_FORMATS) if messy else "%Y-%m-%d"
    return d.strftime(fmt)


def _maybe_mangle_email(email: str, rng: random.Random) -> str:
    """Occasionally corrupt an email address to simulate dirty CRM data."""
    roll = rng.random()
    if roll < 0.04:
        return ""  # missing
    if roll < 0.07:
        return email.replace("@", "_at_")  # malformed, no @
    if roll < 0.09:
        return email.replace(".", "")  # malformed, no TLD separator
    return email


def _fiscal_year_window(anchor: date, years_back: int) -> tuple[date, date]:
    """Return the (start, end) date range covering `years_back` fiscal years before `anchor`."""
    fy_end_year = anchor.year + 1 if anchor.month >= 7 else anchor.year
    earliest_fy_start = date(fy_end_year - years_back, 7, 1)
    return earliest_fy_start, anchor


def generate_donors(fake: Faker, rng: random.Random, donor_count: int, today: date) -> list[dict]:
    """Build the donor dimension with correlated demographic and capacity attributes."""
    donors = []
    for i in range(donor_count):
        donor_id = f"CA{100000 + i}"
        constituent_type = _weighted_choice(CONSTITUENT_TYPES_WEIGHTED)
        capacity_tier = _weighted_choice(CAPACITY_WEIGHTED)

        class_year = ""
        if constituent_type == "Alumni":
            # Alumni class years skew toward more recent decades, with a long tail.
            years_since_grad = int(rng.triangular(1, 60, 12))
            class_year = today.year - years_since_grad
            # A small slice of alumni records are missing class_year (real-world messiness).
            if rng.random() < 0.03:
                class_year = ""

        first_name = fake.first_name()
        last_name = fake.last_name()
        raw_email = f"{first_name}.{last_name}{rng.randint(1,999)}@{fake.free_email_domain()}".lower()
        email = _maybe_mangle_email(raw_email, rng)

        donors.append(
            {
                "donor_id": donor_id,
                "first_name": first_name if rng.random() > 0.02 else f"  {first_name} ",
                "last_name": last_name if rng.random() > 0.02 else last_name.upper(),
                "email": email,
                "phone": fake.phone_number() if rng.random() > 0.1 else "",
                "constituent_type": constituent_type,
                "class_year": class_year,
                "capacity_tier": capacity_tier,
                "city": fake.city(),
                "state": fake.state_abbr(),
            }
        )
    return donors


def generate_gifts(
    donors: list[dict], rng: random.Random, target_records: int, today: date
) -> list[dict]:
    """Build gift transactions against the donor dimension, with realistic messiness."""
    window_start, window_end = _fiscal_year_window(today, years_back=5)
    total_days = (window_end - window_start).days

    gifts = []
    gift_seq = 1

    # Introduce a handful of duplicate donor_id records (same id, slightly different name
    # spelling/casing) to exercise the duplicate-detection validation rule.
    duplicate_candidates = rng.sample(donors, k=max(1, len(donors) // 150))
    duplicate_lookup = {d["donor_id"] for d in duplicate_candidates}

    while len(gifts) < target_records:
        donor = rng.choice(donors)
        mean, sigma = CAPACITY_TIERS[donor["capacity_tier"]]
        amount = round(rng.lognormvariate(mean, sigma), 2)

        # Rare data-entry errors: a negative amount, or an implausibly large one.
        roll = rng.random()
        if roll < 0.003:
            amount = -abs(amount)
        elif roll < 0.006:
            amount = round(amount * 1000, 2)  # fat-fingered extra zeros

        gift_date = window_start + timedelta(days=rng.randint(0, max(total_days, 1)))

        # Rare future-dated gift (data entry error) to exercise that validation rule.
        if rng.random() < 0.002:
            gift_date = today + timedelta(days=rng.randint(1, 30))

        fund = rng.choice(FUND_DESIGNATIONS)
        if rng.random() < 0.02:
            fund = ""  # missing fund designation

        gifts.append(
            {
                "gift_id": f"G{200000 + gift_seq}",
                "donor_id": donor["donor_id"],
                "first_name": donor["first_name"],
                "last_name": donor["last_name"],
                "email": donor["email"],
                "phone": donor["phone"],
                "constituent_type": donor["constituent_type"],
                "class_year": donor["class_year"],
                "capacity_tier": donor["capacity_tier"],
                "city": donor["city"],
                "state": donor["state"],
                "gift_date": _messy_date_string(gift_date, messy=rng.random() < 0.3),
                "gift_amount": amount,
                "fund_designation": fund,
            }
        )
        gift_seq += 1

        # Occasionally emit a second row for a "duplicate" donor_id with mangled name casing,
        # simulating a records-merge problem in the source CRM.
        if donor["donor_id"] in duplicate_lookup and rng.random() < 0.15:
            dup = dict(donor)
            gifts.append(
                {
                    "gift_id": f"G{200000 + gift_seq}",
                    "donor_id": dup["donor_id"],
                    "first_name": dup["first_name"].strip().upper(),
                    "last_name": dup["last_name"].strip().lower(),
                    "email": dup["email"],
                    "phone": dup["phone"],
                    "constituent_type": dup["constituent_type"],
                    "class_year": dup["class_year"],
                    "capacity_tier": dup["capacity_tier"],
                    "city": dup["city"],
                    "state": dup["state"],
                    "gift_date": _messy_date_string(gift_date, messy=True),
                    "gift_amount": amount,
                    "fund_designation": fund,
                }
            )
            gift_seq += 1

    return gifts[:target_records]


def main() -> None:
    """Parse CLI arguments and write the synthetic gift export to disk."""
    parser = argparse.ArgumentParser(description="Generate a synthetic Carmari Academy gift export.")
    parser.add_argument("--records", type=int, default=5000, help="Number of gift records to generate.")
    parser.add_argument("--donors", type=int, default=1800, help="Number of unique donors to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "sample_donors.csv",
        help="Output CSV path.",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    Faker.seed(args.seed)
    fake = Faker()
    today = date.today()

    donors = generate_donors(fake, rng, args.donors, today)
    gifts = generate_gifts(donors, rng, args.records, today)

    fieldnames = [
        "gift_id",
        "donor_id",
        "first_name",
        "last_name",
        "email",
        "phone",
        "constituent_type",
        "class_year",
        "capacity_tier",
        "city",
        "state",
        "gift_date",
        "gift_amount",
        "fund_designation",
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(gifts)

    print(f"Wrote {len(gifts)} gift records for {len(donors)} donors to {args.output}")


if __name__ == "__main__":
    main()
