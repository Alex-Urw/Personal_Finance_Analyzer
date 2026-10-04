"""Core logic for the Bank Statement Analyzer.

This file reads statements, remembers merchants, and calculates the report.
The graphical interface is kept in app.py so the calculation code is easier
to learn and test by itself.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Optional


# These are the choices shown in the app.
EXPENSE_CATEGORIES = (
    "Rent",
    "Bills",
    "Emergency Spending",
    "Dining Out",
    "Gas",
    "Entertainment",
    "Shopping",
    "Groceries",
    "Miscellaneous",
)

CATEGORIES = EXPENSE_CATEGORIES + ("Income", "Transfer / Ignore")

# The project currently only has these three categories are essential.
ESSENTIAL_CATEGORIES = {"Rent", "Bills", "Emergency Spending", "Groceries"}
NONESSENTIAL_CATEGORIES = set(EXPENSE_CATEGORIES) - ESSENTIAL_CATEGORIES
EXCLUDED_CATEGORIES = {"Income", "Transfer / Ignore"}


# Built-in rules handle common merchants before asking the user.
# A word or phrase can be added here later if you want more automatic matches.
DEFAULT_MERCHANT_RULES = (
    ("Rent", ("monthly rent", "rent payment", "landlord", "property management")),
    (
        "Bills",
        (
            "con edison",
            "national grid",
            "spectrum",
            "verizon",
            "t-mobile",
            "tmobile",
            "at&t",
            "geico",
            "insurance",
            "internet",
            "utility",
            "electric bill",
            "phone bill",
        ),
    ),
    (
        "Emergency Spending",
        ("emergency room", "urgent care", "ambulance", "tow truck", "locksmith"),
    ),
    (
        "Dining Out",
        (
            "mcdonald",
            "burger king",
            "wendy",
            "chipotle",
            "starbucks",
            "dunkin",
            "restaurant",
            "doordash",
            "uber eats",
            "grubhub",
            "pizza",
            "cafe",
        ),
    ),
    ("Gas", ("exxon", "mobil", "shell", "sunoco", "bp gas", "chevron", "gas station")),
    (
        "Entertainment",
        (
            "netflix",
            "hulu",
            "spotify",
            "disney plus",
            "disney+",
            "max.com",
            "movie theater",
            "cinema",
            "concert",
            "playstation",
            "xbox",
        ),
    ),
    (
        "Groceries",
        (
            "whole foods",
            "trader joe",
            "stop & shop",
            "stop and shop",
            "shoprite",
            "supermarket",
            "grocery",
            "aldi",
            "wegmans",
        ),
    ),
    (
        "Shopping",
        ("amazon", "walmart", "target", "best buy", "ebay", "etsy", "shopify"),
    ),
)

INCOME_WORDS = (
    "payroll",
    "direct deposit",
    "salary",
    "paycheck",
    "wages",
    "employer deposit",
    "interest paid",
)


# Common column names from different banks.
DATE_HEADERS = ("date", "transaction date", "posted date", "posting date")
MERCHANT_HEADERS = (
    "merchant",
    "description",
    "transaction",
    "payee",
    "details",
    "memo",
    "name",
)
AMOUNT_HEADERS = ("amount", "transaction amount", "value")
DEBIT_HEADERS = ("debit", "withdrawal", "withdrawals", "money out", "charge")
CREDIT_HEADERS = ("credit", "deposit", "deposits", "money in")


TWOPLACES = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Round money to two decimal places."""

    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    """Turn a Decimal into text such as $1,234.56."""

    return f"${money(value):,.2f}"


def normalize_merchant(name: str) -> str:
    """Create a consistent merchant key for matching and memory."""

    cleaned = name.casefold().replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return " ".join(cleaned.split())


def normalize_header(name: str) -> str:
    """Create a consistent CSV column name."""

    return normalize_merchant(name.lstrip("\ufeff"))


def parse_amount(value: object) -> Optional[Decimal]:
    """Convert common bank amount formats into a Decimal."""

    if value is None:
        return None

    text = str(value).strip()
    if not text or text.casefold() in {"n/a", "na", "none", "--", "-"}:
        return None

    negative = False
    upper_text = text.upper()

    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    if upper_text.endswith(" DR") or upper_text.endswith("DR"):
        negative = True
        text = re.sub(r"\s*DR$", "", text, flags=re.IGNORECASE)
    elif upper_text.endswith(" CR") or upper_text.endswith("CR"):
        text = re.sub(r"\s*CR$", "", text, flags=re.IGNORECASE)

    text = text.replace("$", "").replace(",", "").replace(" ", "")

    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Could not read amount: {value!r}") from exc

    if negative and number > 0:
        number = -number

    return money(number)


@dataclass
class Transaction:
    """One normalized transaction from the statement."""

    date: str
    merchant: str
    amount: Decimal
    category: Optional[str]
    original_amount: Decimal

    @property
    def needs_review(self) -> bool:
        return self.category is None

    @property
    def is_expense(self) -> bool:
        return self.category in EXPENSE_CATEGORIES


@dataclass
class RawTransaction:
    """A transaction before its amount direction is interpreted."""

    date: str
    merchant: str
    amount: Optional[Decimal] = None
    debit: Optional[Decimal] = None
    credit: Optional[Decimal] = None


@dataclass
class CategoryResult:
    """Totals and percentages for one spending category."""

    category: str
    total: Decimal
    essential: bool
    percent_of_income: Decimal
    percent_of_spending: Decimal


@dataclass
class SavingsPrediction:
    """Possible savings from reducing one category."""

    category: str
    current_monthly: Decimal
    save_25_monthly: Decimal
    save_25_yearly: Decimal
    save_50_monthly: Decimal
    save_50_yearly: Decimal


@dataclass
class AnalysisResult:
    """The complete result displayed by the app."""

    income_total: Decimal
    spending_total: Decimal
    essential_total: Decimal
    nonessential_total: Decimal
    spending_to_income_ratio: Decimal
    categories: list[CategoryResult]
    predictions: list[SavingsPrediction]


class MerchantMemory:
    """Save user-selected merchant categories between app sessions."""

    def __init__(self, path: Optional[Path] = None) -> None:
        default_path = Path.home() / ".bank_statement_analyzer" / "merchant_categories.json"
        self.path = path or default_path
        self.categories: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        """Load saved merchant choices if the file exists."""

        if not self.path.exists():
            return

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.categories = {
                    str(key): str(value)
                    for key, value in data.items()
                    if value in CATEGORIES
                }
        except (OSError, json.JSONDecodeError):
            # A damaged memory file should not stop the app from opening.
            self.categories = {}

    def save(self) -> None:
        """Write merchant choices to the user's computer."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.categories, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def get(self, merchant: str) -> Optional[str]:
        """Find a remembered category for a merchant."""

        return self.categories.get(normalize_merchant(merchant))

    def remember(self, merchant: str, category: str) -> None:
        """Remember one merchant and save the memory file."""

        if category not in CATEGORIES:
            raise ValueError(f"Unknown category: {category}")
        self.categories[normalize_merchant(merchant)] = category
        self.save()

    def clear(self) -> None:
        """Remove all user-saved merchant choices."""

        self.categories = {}
        self.save()


def classify_merchant(merchant: str, memory: MerchantMemory) -> Optional[str]:
    """Use saved choices and built-in words to categorize a merchant."""

    remembered = memory.get(merchant)
    if remembered:
        return remembered

    normalized = normalize_merchant(merchant)
    for category, keywords in DEFAULT_MERCHANT_RULES:
        if any(normalize_merchant(keyword) in normalized for keyword in keywords):
            return category

    return None


def _find_header(headers: Iterable[str], choices: Iterable[str]) -> Optional[str]:
    """Find an original header that matches one of the common names."""

    normalized_choices = {normalize_header(choice) for choice in choices}
    for header in headers:
        if normalize_header(header) in normalized_choices:
            return header
    return None


def _detect_delimiter(path: Path) -> str:
    """Detect comma, semicolon, or tab-separated statement files."""

    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _positive_amounts_are_income(raw_rows: list[RawTransaction]) -> bool:
    """Guess the sign format when the user selects Auto-detect."""

    income_hints = []
    for row in raw_rows:
        description = normalize_merchant(row.merchant)
        if row.amount is not None and any(word in description for word in INCOME_WORDS):
            income_hints.append(row.amount)

    if income_hints:
        positive_hints = sum(amount > 0 for amount in income_hints)
        return positive_hints >= (len(income_hints) / 2)

    amounts = [row.amount for row in raw_rows if row.amount is not None and row.amount != 0]
    has_positive = any(amount > 0 for amount in amounts)
    has_negative = any(amount < 0 for amount in amounts)

    # A normal bank export often has negative spending and positive income.
    if has_positive and has_negative:
        return True

    # All-negative files usually contain bank spending with no income rows.
    # All-positive files are commonly credit-card spending exports.
    return has_negative


def _build_transactions(
    raw_rows: list[RawTransaction],
    memory: MerchantMemory,
    sign_mode: str,
) -> list[Transaction]:
    """Turn raw rows into consistent income, expense, or ignored transactions."""

    valid_modes = {"auto", "bank", "credit_card"}
    if sign_mode not in valid_modes:
        raise ValueError(f"sign_mode must be one of {sorted(valid_modes)}")

    positive_is_income = sign_mode == "bank"
    if sign_mode == "auto":
        positive_is_income = _positive_amounts_are_income(raw_rows)

    transactions: list[Transaction] = []

    for row in raw_rows:
        if row.debit is not None or row.credit is not None:
            debit = abs(row.debit or Decimal("0"))
            credit = abs(row.credit or Decimal("0"))

            if debit > 0:
                category = classify_merchant(row.merchant, memory)
                transactions.append(
                    Transaction(row.date, row.merchant, money(debit), category, money(debit))
                )

            if credit > 0:
                transactions.append(
                    Transaction(row.date, row.merchant, money(credit), "Income", money(credit))
                )
            continue

        if row.amount is None or row.amount == 0:
            continue

        original = money(row.amount)

        if positive_is_income:
            is_income = original > 0
        else:
            is_income = False

        if is_income:
            category = "Income"
        elif original < 0 and not positive_is_income:
            # On credit-card exports, negative values are usually payments or refunds.
            category = "Transfer / Ignore"
        else:
            category = classify_merchant(row.merchant, memory)

        transactions.append(
            Transaction(
                date=row.date,
                merchant=row.merchant,
                amount=money(abs(original)),
                category=category,
                original_amount=original,
            )
        )

    return transactions


def read_csv_transactions(
    path: str | Path,
    memory: MerchantMemory,
    sign_mode: str = "auto",
) -> list[Transaction]:
    """Read transactions from a CSV file with flexible column names."""

    statement_path = Path(path)
    delimiter = _detect_delimiter(statement_path)

    with statement_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as file:
        reader = csv.DictReader(file, delimiter=delimiter)
        headers = reader.fieldnames or []

        merchant_column = _find_header(headers, MERCHANT_HEADERS)
        date_column = _find_header(headers, DATE_HEADERS)
        amount_column = _find_header(headers, AMOUNT_HEADERS)
        debit_column = _find_header(headers, DEBIT_HEADERS)
        credit_column = _find_header(headers, CREDIT_HEADERS)

        if not merchant_column:
            raise ValueError(
                "No merchant/description column was found. Expected a heading such as "
                "Merchant, Description, Transaction, or Payee."
            )

        if not amount_column and not debit_column and not credit_column:
            raise ValueError(
                "No amount column was found. Expected Amount, Transaction Amount, "
                "Debit/Withdrawal, or Credit/Deposit."
            )

        raw_rows: list[RawTransaction] = []
        for line_number, row in enumerate(reader, start=2):
            merchant = (row.get(merchant_column) or "").strip()
            if not merchant:
                continue

            try:
                raw_rows.append(
                    RawTransaction(
                        date=(row.get(date_column) or "").strip() if date_column else "",
                        merchant=merchant,
                        amount=parse_amount(row.get(amount_column)) if amount_column else None,
                        debit=parse_amount(row.get(debit_column)) if debit_column else None,
                        credit=parse_amount(row.get(credit_column)) if credit_column else None,
                    )
                )
            except ValueError as exc:
                raise ValueError(f"CSV line {line_number}: {exc}") from exc

    if not raw_rows:
        raise ValueError("The CSV was read, but it did not contain any transaction rows.")

    transactions = _build_transactions(raw_rows, memory, sign_mode)
    if not transactions:
        raise ValueError("No non-zero transactions could be read from the CSV.")
    return transactions


def _pdf_table_to_raw_rows(table: list[list[object]]) -> list[RawTransaction]:
    """Convert one extracted PDF table into raw rows."""

    header_index: Optional[int] = None
    headers: list[str] = []

    for index, row in enumerate(table[:8]):
        possible_headers = [str(cell or "").replace("\n", " ").strip() for cell in row]
        merchant = _find_header(possible_headers, MERCHANT_HEADERS)
        amount = _find_header(possible_headers, AMOUNT_HEADERS)
        debit = _find_header(possible_headers, DEBIT_HEADERS)
        credit = _find_header(possible_headers, CREDIT_HEADERS)
        if merchant and (amount or debit or credit):
            header_index = index
            headers = possible_headers
            break

    if header_index is None:
        return []

    merchant_column = _find_header(headers, MERCHANT_HEADERS)
    date_column = _find_header(headers, DATE_HEADERS)
    amount_column = _find_header(headers, AMOUNT_HEADERS)
    debit_column = _find_header(headers, DEBIT_HEADERS)
    credit_column = _find_header(headers, CREDIT_HEADERS)

    raw_rows: list[RawTransaction] = []
    for values in table[header_index + 1 :]:
        padded = list(values) + [""] * max(0, len(headers) - len(values))
        row = {
            headers[index]: str(padded[index] or "").replace("\n", " ").strip()
            for index in range(len(headers))
        }
        merchant = row.get(merchant_column or "", "").strip()
        if not merchant:
            continue

        try:
            raw_rows.append(
                RawTransaction(
                    date=row.get(date_column or "", "").strip(),
                    merchant=merchant,
                    amount=parse_amount(row.get(amount_column)) if amount_column else None,
                    debit=parse_amount(row.get(debit_column)) if debit_column else None,
                    credit=parse_amount(row.get(credit_column)) if credit_column else None,
                )
            )
        except ValueError:
            # PDF pages often contain totals or footers that are not transactions.
            continue

    return raw_rows


def _pdf_text_to_raw_rows(text: str) -> list[RawTransaction]:
    """Try to read simple transaction lines when a PDF has no table grid."""

    date_pattern = r"(?P<date>(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?)|(?:\d{4}-\d{2}-\d{2}))"
    amount_pattern = r"(?P<amount>\(?-?\$?\d[\d,]*\.\d{2}\)?(?:\s*(?:CR|DR))?)"
    line_pattern = re.compile(
        rf"^\s*{date_pattern}\s+(?P<merchant>.+?)\s+{amount_pattern}\s*$",
        flags=re.IGNORECASE,
    )

    raw_rows: list[RawTransaction] = []
    for line in text.splitlines():
        match = line_pattern.match(line)
        if not match:
            continue
        try:
            amount = parse_amount(match.group("amount"))
        except ValueError:
            continue
        raw_rows.append(
            RawTransaction(
                date=match.group("date"),
                merchant=match.group("merchant").strip(),
                amount=amount,
            )
        )
    return raw_rows


def read_pdf_transactions(
    path: str | Path,
    memory: MerchantMemory,
    sign_mode: str = "auto",
) -> list[Transaction]:
    """Read a text-based PDF statement using tables first, then line matching."""

    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "PDF support needs pdfplumber. Run: python -m pip install -r requirements.txt"
        ) from exc

    raw_rows: list[RawTransaction] = []
    all_text: list[str] = []

    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                raw_rows.extend(_pdf_table_to_raw_rows(table))
            all_text.append(page.extract_text() or "")

    if not raw_rows:
        raw_rows = _pdf_text_to_raw_rows("\n".join(all_text))

    if not raw_rows:
        raise ValueError(
            "No transactions could be read from this PDF. It may be scanned, password-"
            "protected, or use a layout this importer does not recognize. Downloading a CSV "
            "from the bank is the most reliable option."
        )

    transactions = _build_transactions(raw_rows, memory, sign_mode)
    if not transactions:
        raise ValueError("No non-zero transactions could be read from the PDF.")
    return transactions


def read_statement(
    path: str | Path,
    memory: MerchantMemory,
    sign_mode: str = "auto",
) -> list[Transaction]:
    """Choose the correct importer from the file extension."""

    suffix = Path(path).suffix.casefold()
    if suffix == ".csv":
        return read_csv_transactions(path, memory, sign_mode)
    if suffix == ".pdf":
        return read_pdf_transactions(path, memory, sign_mode)
    raise ValueError("Please choose a .csv or .pdf statement file.")


def analyze_transactions(transactions: Iterable[Transaction]) -> AnalysisResult:
    """Calculate totals, percentages, and savings predictions."""

    transaction_list = list(transactions)
    unresolved = [transaction for transaction in transaction_list if transaction.needs_review]
    if unresolved:
        raise ValueError(f"{len(unresolved)} transaction(s) still need a category.")

    income_total = money(
        sum(
            (transaction.amount for transaction in transaction_list if transaction.category == "Income"),
            Decimal("0"),
        )
    )

    category_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for transaction in transaction_list:
        if transaction.category in EXPENSE_CATEGORIES:
            category_totals[transaction.category] += transaction.amount

    spending_total = money(sum(category_totals.values(), Decimal("0")))
    essential_total = money(
        sum(
            (total for category, total in category_totals.items() if category in ESSENTIAL_CATEGORIES),
            Decimal("0"),
        )
    )
    nonessential_total = money(spending_total - essential_total)

    if income_total > 0:
        spending_to_income_ratio = money((spending_total / income_total) * Decimal("100"))
    else:
        spending_to_income_ratio = Decimal("0.00")

    category_results: list[CategoryResult] = []
    for category in EXPENSE_CATEGORIES:
        total = money(category_totals.get(category, Decimal("0")))
        percent_of_income = (
            money((total / income_total) * Decimal("100"))
            if income_total > 0
            else Decimal("0.00")
        )
        percent_of_spending = (
            money((total / spending_total) * Decimal("100"))
            if spending_total > 0
            else Decimal("0.00")
        )
        category_results.append(
            CategoryResult(
                category=category,
                total=total,
                essential=category in ESSENTIAL_CATEGORIES,
                percent_of_income=percent_of_income,
                percent_of_spending=percent_of_spending,
            )
        )

    # Only nonessential categories are used for savings suggestions.
    ranked_nonessential = sorted(
        (
            (category, money(total))
            for category, total in category_totals.items()
            if category in NONESSENTIAL_CATEGORIES and total > 0
        ),
        key=lambda item: item[1],
        reverse=True,
    )[:3]

    predictions: list[SavingsPrediction] = []
    for category, total in ranked_nonessential:
        save_25_monthly = money(total * Decimal("0.25"))
        save_50_monthly = money(total * Decimal("0.50"))
        predictions.append(
            SavingsPrediction(
                category=category,
                current_monthly=total,
                save_25_monthly=save_25_monthly,
                save_25_yearly=money(save_25_monthly * Decimal("12")),
                save_50_monthly=save_50_monthly,
                save_50_yearly=money(save_50_monthly * Decimal("12")),
            )
        )

    return AnalysisResult(
        income_total=income_total,
        spending_total=spending_total,
        essential_total=essential_total,
        nonessential_total=nonessential_total,
        spending_to_income_ratio=spending_to_income_ratio,
        categories=category_results,
        predictions=predictions,
    )


def build_text_report(result: AnalysisResult) -> str:
    """Create a plain-text version that can be saved or printed."""

    ratio_text = (
        f"{result.spending_to_income_ratio:.2f}%" if result.income_total > 0 else "N/A"
    )
    lines = [
        "BANK STATEMENT ANALYSIS",
        "=" * 50,
        f"Total income:              {format_money(result.income_total)}",
        f"Total spending:            {format_money(result.spending_total)}",
        f"Essential spending:        {format_money(result.essential_total)}",
        f"Nonessential spending:     {format_money(result.nonessential_total)}",
        f"Spending-to-income ratio:  {ratio_text}",
        "",
        "CATEGORY BREAKDOWN",
        "-" * 50,
    ]

    for item in result.categories:
        kind = "Essential" if item.essential else "Nonessential"
        income_percent = (
            f"{item.percent_of_income:.2f}%" if result.income_total > 0 else "N/A"
        )
        lines.append(
            f"{item.category}: {format_money(item.total)} | {kind} | "
            f"{income_percent} of income | "
            f"{item.percent_of_spending:.2f}% of spending"
        )

    lines.extend(["", "POSSIBLE SAVINGS", "-" * 50])
    if result.predictions:
        for prediction in result.predictions:
            lines.extend(
                [
                    f"{prediction.category} (currently {format_money(prediction.current_monthly)}):",
                    f"  Reduce by 25%: save {format_money(prediction.save_25_monthly)} "
                    f"per month / {format_money(prediction.save_25_yearly)} per year",
                    f"  Reduce by 50%: save {format_money(prediction.save_50_monthly)} "
                    f"per month / {format_money(prediction.save_50_yearly)} per year",
                ]
            )
    else:
        lines.append("No nonessential spending was available for a savings estimate.")

    return "\n".join(lines) + "\n"
