"""Deterministic synthetic data + golden set generator (fixed seed).

Writes:
  data/structured/{sales_orders,support_tickets,headcount}.csv
  data/docs/data_retention_policy.pdf          (fpdf2, fixed creation date → byte-stable)
  eval/golden_set.jsonl                        (SQL ground truth computed here from the generated rows)

Run:  uv run python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRUCT = ROOT / "data" / "structured"
DOCS = ROOT / "data" / "docs"
EVAL = ROOT / "eval"
SEED = 20260917

REGIONS = ["NA", "EMEA", "APAC", "LATAM"]
REGION_W = [0.38, 0.30, 0.22, 0.10]
PRODUCTS = {"Atlas AMR": 42000.0, "Orion Arm": 28500.0, "Vega Vision Kit": 6200.0, "Nova Controller": 3400.0}
PRODUCT_W = [0.30, 0.25, 0.27, 0.18]
SEGMENTS = {"Enterprise": 0.12, "Mid-Market": 0.06, "SMB": 0.0}
SEGMENT_W = [0.35, 0.40, 0.25]
UNITS = {"Enterprise": (5, 40), "Mid-Market": (2, 15), "SMB": (1, 5)}
START, END = date(2024, 1, 1), date(2026, 9, 30)

DEPARTMENTS = {"Engineering": 120, "Sales": 45, "Support": 38, "Operations": 52, "Finance": 14, "People": 11}
LOCATIONS = {"Austin": 0.45, "Berlin": 0.25, "Singapore": 0.18, "Sao Paulo": 0.12}
SEVERITIES = ["SEV1", "SEV2", "SEV3", "SEV4"]
SEV_W = [0.05, 0.17, 0.48, 0.30]
SEV_HOURS = {"SEV1": (2, 10), "SEV2": (6, 36), "SEV3": (18, 120), "SEV4": (24, 240)}


def quarters() -> list[str]:
    out = []
    for y in (2024, 2025, 2026):
        for q in (1, 2, 3, 4):
            if (y, q) <= (2026, 3):
                out.append(f"{y}-Q{q}")
    return out


def gen_sales(rng: random.Random) -> list[dict]:
    rows = []
    days = (END - START).days
    for i in range(1, 5001):
        # mild growth: later dates more likely
        d = START + timedelta(days=int(days * (rng.random() ** 0.85)))
        product = rng.choices(list(PRODUCTS), PRODUCT_W)[0]
        segment = rng.choices(list(SEGMENTS), SEGMENT_W)[0]
        units = rng.randint(*UNITS[segment])
        price = round(PRODUCTS[product] * (1 - SEGMENTS[segment]) * rng.uniform(0.97, 1.03), 2)
        rows.append(
            {
                "order_id": f"SO-{i:06d}",
                "order_date": d.isoformat(),
                "region": rng.choices(REGIONS, REGION_W)[0],
                "product": product,
                "customer_segment": segment,
                "units": units,
                "unit_price_usd": f"{price:.2f}",
                "revenue_usd": f"{round(units * price, 2):.2f}",
            }
        )
    rows.sort(key=lambda r: (r["order_date"], r["order_id"]))
    return rows


def gen_tickets(rng: random.Random) -> list[dict]:
    rows = []
    span = int((datetime(2026, 9, 30, 23, 59, tzinfo=UTC) - datetime(2024, 1, 1, tzinfo=UTC)).total_seconds())
    for i in range(1, 3001):
        created = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=int(span * (rng.random() ** 0.9)))
        sev = rng.choices(SEVERITIES, SEV_W)[0]
        age_days = (datetime(2026, 9, 30, tzinfo=UTC) - created).days
        if age_days < 10:
            status = rng.choices(["Open", "In Progress", "Resolved"], [0.45, 0.35, 0.20])[0]
        else:
            status = rng.choices(["Resolved", "Closed", "Open", "In Progress"], [0.30, 0.66, 0.02, 0.02])[0]
        hours = round(rng.uniform(*SEV_HOURS[sev]), 1) if status in ("Resolved", "Closed") else None
        rows.append(
            {
                "ticket_id": f"TK-{i:06d}",
                "created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
                "product": rng.choices(list(PRODUCTS), [0.40, 0.30, 0.20, 0.10])[0],
                "severity": sev,
                "status": status,
                "resolution_hours": "" if hours is None else f"{hours:.1f}",
                "region": rng.choices(REGIONS, REGION_W)[0],
            }
        )
    rows.sort(key=lambda r: (r["created_at"], r["ticket_id"]))
    return rows


def gen_headcount(rng: random.Random) -> list[dict]:
    rows = []
    for qi, q in enumerate(quarters()):
        growth = 1 + 0.035 * qi
        for dept, base in DEPARTMENTS.items():
            for loc, share in LOCATIONS.items():
                n = max(1, round(base * share * growth * rng.uniform(0.95, 1.05)))
                rows.append({"department": dept, "location": loc, "quarter": q, "headcount": n})
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------------------------------------
RETENTION_PDF = [
    ("title", "Helios Robotics Data Retention Policy"),
    (
        "p",
        "Policy ID: LEG-007. Version 2.3. Effective May 1, 2026. Owner: Legal and Privacy. This policy "
        "defines how long Helios Robotics keeps business records and data, and how data is deleted.",
    ),
    ("h", "1 Purpose"),
    (
        "p",
        "Helios keeps data only as long as needed for business, legal and safety purposes. Retaining data "
        "longer than necessary increases cost and privacy risk; deleting it too early can breach legal "
        "obligations.",
    ),
    ("h", "2 Scope"),
    (
        "p",
        "This policy applies to all data created or processed by Helios Robotics, in any system, including "
        "data held by processors on our behalf. Customer-specific contractual terms take precedence where "
        "they are stricter.",
    ),
    ("h", "3 Retention Schedule"),
    ("h", "3.1 Customer Contracts"),
    (
        "p",
        "Signed customer contracts, orders and amendments are retained for 7 years after the contract "
        "terminates or expires.",
    ),
    ("h", "3.2 Robot Telemetry"),
    (
        "p",
        "Raw robot telemetry (sensor logs, trajectories and camera snapshots) is retained for 18 months "
        "from collection. After 18 months telemetry is aggregated and anonymised; anonymised aggregates "
        "may be kept indefinitely for product improvement.",
    ),
    ("h", "3.3 Support Tickets"),
    (
        "p",
        "Customer support tickets and their attachments are retained for 5 years after the ticket is closed.",
    ),
    ("h", "3.4 Employee Records"),
    (
        "p",
        "Personnel files are retained for 7 years after an employee leaves Helios. Recruiting records for "
        "candidates who were not hired are deleted after 12 months unless the candidate consents to a "
        "longer period.",
    ),
    ("h", "3.5 Application Logs"),
    (
        "p",
        "Application and infrastructure logs are retained for 13 months. Logs must not contain secrets or "
        "unmasked personal data.",
    ),
    ("h", "3.6 Security Audit Logs"),
    (
        "p",
        "Security audit logs, including authentication events and privileged-access sessions, are retained "
        "for 3 years in write-once storage.",
    ),
    ("h", "3.7 Backups"),
    (
        "p",
        "Production database backups are retained for 35 days. Monthly snapshots used for disaster recovery "
        "testing are retained for 12 months.",
    ),
    ("h", "4 Deletion"),
    (
        "p",
        "Data must be deleted or anonymised within 30 days after its retention period ends. Deletion must "
        "cover replicas and derived datasets; backups expire on their own schedule. A legal hold issued by "
        "Legal suspends deletion of the affected data until the hold is released.",
    ),
    ("h", "5 Customer Deletion Requests"),
    (
        "p",
        "Requests from customers or individuals to delete personal data are acknowledged within 5 business "
        "days and fulfilled within 30 days, in line with GDPR and similar laws. The Privacy team tracks "
        "every request to completion.",
    ),
    ("h", "6 Responsibilities"),
    (
        "p",
        "Data owners apply this schedule in their systems and confirm compliance in the annual data "
        "inventory review. Questions go to privacy@helios-robotics.example.",
    ),
]


def write_pdf(path: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_creation_date(datetime(2026, 5, 1, tzinfo=UTC))
    pdf.set_title("Helios Robotics Data Retention Policy")
    pdf.set_author("Helios Robotics Legal")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    for kind, text in RETENTION_PDF:
        if kind == "title":
            pdf.set_font("Helvetica", "B", 18)
            pdf.multi_cell(0, 10, text, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif kind == "h":
            if text.startswith("3.5"):
                pdf.add_page()  # make the PDF multi-page so page numbers are exercised
            pdf.set_font("Helvetica", "B", 13 if "." not in text.split()[0] else 11)
            pdf.ln(3)
            pdf.multi_cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
    pdf.output(str(path))


# ------------------------------------------------------------------------------------------------
def doc_items() -> list[dict]:
    H, S, IR, P, T, R, RN, IT = (
        "employee_handbook.md",
        "information_security_policy.md",
        "incident_response_runbook.md",
        "product_faq_and_warranty.md",
        "travel_policy.html",
        "data_retention_policy.pdf",
        "release_notes.md",
        "it_service_desk_faq.txt",
    )
    q = [
        (
            "How many PTO days do full-time employees accrue per month?",
            [H],
            ["1.67"],
            "Full-time employees accrue 1.67 days of PTO per month (20 days per year).",
        ),
        (
            "How many unused PTO days can be carried over to the next year, and by when must they be used?",
            [H],
            ["5", "March 31"],
            "Up to 5 unused PTO days can be carried over and must be used by March 31.",
        ),
        (
            "How many days per week can employees work remotely under the hybrid policy?",
            [H],
            ["3"],
            "Employees may work remotely up to 3 days per week.",
        ),
        (
            "What is the maximum reimbursable amount per person for a client meal?",
            [H],
            ["85"],
            "Client meals are reimbursed up to $85 per person including tax and tip.",
        ),
        (
            "What is the deadline for submitting expense reports?",
            [H],
            ["30 days"],
            "Expenses must be submitted within 30 days of being incurred.",
        ),
        (
            "What is the minimum password length required by the security policy?",
            [S],
            ["14"],
            "Passwords must be at least 14 characters long.",
        ),
        (
            "What type of MFA is required for administrator accounts?",
            [S],
            ["FIDO2"],
            "Administrator and privileged accounts must use phishing-resistant FIDO2 hardware security keys.",
        ),
        (
            "How often must API keys and service-account credentials be rotated?",
            [S],
            ["90 days"],
            "API keys and service-account credentials must be rotated at least every 90 days.",
        ),
        (
            "What are the four data classification levels and which one covers customer PII?",
            [S],
            ["Restricted", "Confidential"],
            "The levels are Public, Internal, Confidential and Restricted; customer PII is Restricted.",
        ),
        (
            "What is the acknowledgement SLA for a SEV2 incident?",
            [IR],
            ["30 minutes"],
            "SEV2 incidents must be acknowledged within 30 minutes, 24/7.",
        ),
        (
            "Within how many days must a postmortem be published after a SEV1 incident?",
            [IR],
            ["5 business days"],
            "A blameless postmortem draft must be published within 5 business days.",
        ),
        (
            "What is the maximum payload and battery runtime of the Atlas AMR?",
            [P],
            ["150", "10 hours"],
            "Atlas AMR carries up to 150 kg and runs about 10 hours per charge.",
        ),
        (
            "How long is the standard warranty on the Orion Arm?",
            [P],
            ["18 months"],
            "The Orion Arm has an 18-month standard warranty.",
        ),
        (
            "When is business class allowed for flights?",
            [T],
            ["10 hours"],
            "Business class is allowed for flights over 10 hours of scheduled flight time, or with VP approval.",
        ),
        (
            "What is the daily meals per diem in Tier 1 cities?",
            [T],
            ["95"],
            "The Tier 1 meals per diem is $95 per day (hotel cap $350/night).",
        ),
        (
            "How long is raw robot telemetry retained?",
            [R],
            ["18 months"],
            "Raw robot telemetry is retained for 18 months, then aggregated and anonymised.",
        ),
        (
            "How long are production database backups retained?",
            [R],
            ["35 days"],
            "Production database backups are retained for 35 days.",
        ),
        (
            "Which Helios OS release added single sign-on support?",
            [RN],
            ["4.4"],
            "Helios OS 4.4 (September 15, 2026) added SSO via SAML 2.0 and OIDC.",
        ),
        (
            "How often are company laptops replaced?",
            [IT],
            ["3 years"],
            "Company laptops are refreshed every 3 years.",
        ),
    ]
    return [
        {
            "id": f"doc-{i:02d}",
            "question": qq,
            "expected_route": "vector",
            "relevant_sources": src,
            "must_contain": mc,
            "reference_answer": ref,
            "category": "document",
        }
        for i, (qq, src, mc, ref) in enumerate(q, start=1)
    ]


def _f(x: float) -> str:
    return f"{x:.2f}"


def sql_items(sales: list[dict], tickets: list[dict], hc: list[dict]) -> list[dict]:
    def rev(pred) -> float:  # type: ignore[no-untyped-def]
        return round(sum(float(r["revenue_usd"]) for r in sales if pred(r)), 2)

    items = []

    def add(question: str, must: list[str], ref: str, sql_hint: str) -> None:
        items.append(
            {
                "id": f"sql-{len(items) + 1:02d}",
                "question": question,
                "expected_route": "sql",
                "relevant_sources": [],
                "must_contain": must,
                "reference_answer": ref,
                "category": "sql",
                "reference_sql": sql_hint,
            }
        )

    v = rev(lambda r: r["region"] == "EMEA" and r["order_date"].startswith("2025"))
    add(
        "What was the total revenue in EMEA in 2025?",
        [_f(v)],
        f"EMEA revenue in 2025 was ${v:,.2f}.",
        "SELECT SUM(revenue_usd) FROM sales_orders WHERE region='EMEA' AND EXTRACT(YEAR FROM order_date)=2025",
    )

    n = sum(1 for t in tickets if t["severity"] == "SEV1" and t["created_at"].startswith("2025"))
    add(
        "How many SEV1 support tickets were opened in 2025?",
        [str(n)],
        f"{n} SEV1 tickets were opened in 2025.",
        "SELECT COUNT(*) FROM support_tickets WHERE severity='SEV1' AND EXTRACT(YEAR FROM created_at)=2025",
    )

    hrs = [
        float(t["resolution_hours"])
        for t in tickets
        if t["severity"] == "SEV2" and t["product"] == "Orion Arm" and t["resolution_hours"]
    ]
    avg = round(sum(hrs) / len(hrs), 1)
    add(
        "What is the average resolution time in hours for SEV2 tickets about the Orion Arm?",
        [f"{avg:.1f}"],
        f"The average resolution time is {avg:.1f} hours.",
        "SELECT AVG(resolution_hours) FROM support_tickets WHERE severity='SEV2' AND product='Orion Arm'",
    )

    by_region: dict[str, float] = defaultdict(float)
    for r in sales:
        if r["order_date"].startswith("2024"):
            by_region[r["region"]] += float(r["revenue_usd"])
    top = max(by_region, key=lambda k: by_region[k])
    add(
        "Which region had the highest revenue in 2024?",
        [top],
        f"{top} had the highest 2024 revenue (${by_region[top]:,.2f}).",
        "SELECT region, SUM(revenue_usd) FROM sales_orders WHERE EXTRACT(YEAR FROM order_date)=2024 "
        "GROUP BY region ORDER BY 2 DESC LIMIT 1",
    )

    u = sum(
        int(r["units"])
        for r in sales
        if r["product"] == "Atlas AMR" and "2026-01-01" <= r["order_date"] <= "2026-03-31"
    )
    add(
        "How many Atlas AMR units were sold in Q1 2026?",
        [str(u)],
        f"{u} Atlas AMR units were sold in Q1 2026.",
        "SELECT SUM(units) FROM sales_orders WHERE product='Atlas AMR' AND order_date BETWEEN '2026-01-01' AND '2026-03-31'",
    )

    e = sum(
        int(h["headcount"])
        for h in hc
        if h["department"] == "Engineering" and h["location"] == "Berlin" and h["quarter"] == "2026-Q3"
    )
    add(
        "What was the Engineering headcount in Berlin in 2026-Q3?",
        [str(e)],
        f"Engineering in Berlin had {e} employees in 2026-Q3.",
        "SELECT SUM(headcount) FROM headcount WHERE department='Engineering' AND location='Berlin' AND quarter='2026-Q3'",
    )

    tot = sum(int(h["headcount"]) for h in hc if h["quarter"] == "2026-Q3")
    add(
        "What was the total company headcount in 2026-Q3?",
        [str(tot)],
        f"Total headcount in 2026-Q3 was {tot}.",
        "SELECT SUM(headcount) FROM headcount WHERE quarter='2026-Q3'",
    )

    o = sum(1 for t in tickets if t["status"] == "Open")
    add(
        "How many support tickets currently have status Open?",
        [str(o)],
        f"{o} tickets are Open.",
        "SELECT COUNT(*) FROM support_tickets WHERE status='Open'",
    )

    s = rev(
        lambda r: (
            r["customer_segment"] == "SMB" and r["region"] == "APAC" and r["order_date"].startswith("2026")
        )
    )
    add(
        "What was the revenue from SMB customers in APAC in 2026?",
        [_f(s)],
        f"SMB revenue in APAC in 2026 was ${s:,.2f}.",
        "SELECT SUM(revenue_usd) FROM sales_orders WHERE customer_segment='SMB' AND region='APAC' "
        "AND EXTRACT(YEAR FROM order_date)=2026",
    )

    c = Counter(t["product"] for t in tickets if t["created_at"].startswith("2025"))
    p, pn = c.most_common(1)[0]
    add(
        "Which product had the most support tickets in 2025?",
        [p],
        f"{p} had the most tickets in 2025 ({pn}).",
        "SELECT product, COUNT(*) FROM support_tickets WHERE EXTRACT(YEAR FROM created_at)=2025 "
        "GROUP BY product ORDER BY 2 DESC LIMIT 1",
    )
    return items


def hybrid_items(tickets: list[dict], hc: list[dict]) -> list[dict]:
    n_atlas = sum(
        1
        for t in tickets
        if t["severity"] == "SEV1" and t["product"] == "Atlas AMR" and t["created_at"].startswith("2025")
    )
    hrs = [
        float(t["resolution_hours"])
        for t in tickets
        if t["severity"] == "SEV1" and t["created_at"].startswith("2025") and t["resolution_hours"]
    ]
    avg = round(sum(hrs) / len(hrs), 1)
    sup = sum(int(h["headcount"]) for h in hc if h["department"] == "Support" and h["quarter"] == "2026-Q3")
    rows = [
        (
            "What is the standard warranty period for the Atlas AMR, and how many SEV1 support tickets were "
            "opened for the Atlas AMR in 2025?",
            ["product_faq_and_warranty.md"],
            ["24 months", str(n_atlas)],
            f"Atlas AMR has a 24-month warranty; {n_atlas} SEV1 Atlas tickets were opened in 2025.",
        ),
        (
            "What acknowledgement SLA does the incident runbook set for SEV1 incidents, and what was the average "
            "resolution time in hours for SEV1 support tickets in 2025?",
            ["incident_response_runbook.md"],
            ["15 minutes", f"{avg:.1f}"],
            f"SEV1 must be acknowledged within 15 minutes; 2025 SEV1 tickets averaged {avg:.1f} hours to resolve.",
        ),
        (
            "Which support tier offers 24/7 phone coverage, and what was the total Support department headcount "
            "in 2026-Q3?",
            ["product_faq_and_warranty.md"],
            ["Premier", str(sup)],
            f"Premier offers 24/7 phone support; the Support department had {sup} employees in 2026-Q3.",
        ),
    ]
    return [
        {
            "id": f"hyb-{i:02d}",
            "question": q,
            "expected_route": "both",
            "relevant_sources": src,
            "must_contain": mc,
            "reference_answer": ref,
            "category": "hybrid",
        }
        for i, (q, src, mc, ref) in enumerate(rows, start=1)
    ]


def oos_items() -> list[dict]:
    qs = [
        "What is the capital of Australia?",
        "What is the current Helios Robotics stock price?",
        "Who won the 2018 FIFA World Cup?",
    ]
    return [
        {
            "id": f"oos-{i:02d}",
            "question": q,
            "expected_route": "vector",
            "relevant_sources": [],
            "must_contain": [],
            "reference_answer": "The assistant should say it does not have enough information.",
            "category": "out_of_scope",
        }
        for i, q in enumerate(qs, start=1)
    ]


def main() -> None:
    rng = random.Random(SEED)
    STRUCT.mkdir(parents=True, exist_ok=True)
    EVAL.mkdir(parents=True, exist_ok=True)
    sales, tickets, hc = gen_sales(rng), gen_tickets(rng), gen_headcount(rng)
    write_csv(STRUCT / "sales_orders.csv", sales)
    write_csv(STRUCT / "support_tickets.csv", tickets)
    write_csv(STRUCT / "headcount.csv", hc)
    write_pdf(DOCS / "data_retention_policy.pdf")
    items = doc_items() + sql_items(sales, tickets, hc) + hybrid_items(tickets, hc) + oos_items()
    with (EVAL / "golden_set.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"sales={len(sales)} tickets={len(tickets)} headcount={len(hc)} golden={len(items)}")


if __name__ == "__main__":
    main()
