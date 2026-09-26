"""The account page's monthly statement - this month, said in full.

The other three figures on the account page are each a single number: the
lifetime average, the monthly average, the typical purchase. A statement is not
a number. It is the month laid out - what was spent, across how many purchases,
in what sizes, against what the shopper usually does - and the whole reason it
exists is that a single figure hides all of that. Two shoppers who spent the
same amount this month can have had completely different months, and only one
of them is about to complain.

So this module is long, and it is long for the ordinary reason a statement
module is long in any shop: a statement is a document. It has a headline, it has
sections, each section has rows, the rows have labels that read like English and
values that are money, and every one of them has an edge case that somebody once
got wrong in front of a customer. Most of what is below is that - formatting,
banding, ordering, comparison, and the wording that goes around the figures.

Behind a rollout, like the two figures before it. `monthly-spend-feature`
decides whether a request gets the statement, and a request that does not get it
renders exactly the page it rendered last month - which is what makes the panel
withdrawable rather than a migration.

The document is assembled once and rendered four ways - the account page's
markup, the monthly email's plain text, the download's rows, and the one
sentence a notification has room for. All four are below, and all four are
driven from `statement_sections`, which is the only place that knows what
order a statement is read in. That is deliberate: the alternative is four
renderers that agree about the figures and disagree about the document, which
is how a shopper comes to see a section in their email that is not on their
page.

What it shares with everything else under `io_shop`: it raises rather than
guessing. A statement that quietly reported zero for a month it could not
describe would be a statement nobody could trust for the months it *could*
describe, and the account page's boundary is where a failure becomes a rate
somebody can alert on - see `io_shop.account_page`.
"""

from __future__ import annotations

from dataclasses import dataclass

from io_shop.accounts import Account, Purchase


# The shop keeps money in pence and shows it in pounds, which is the ordinary
# arrangement and the ordinary source of the ordinary bug: a figure formatted
# from the wrong unit is out by a hundred and looks entirely plausible either
# way. Named here so the two directions are one line apart and cannot drift.
PENCE_IN_A_POUND = 100
CURRENCY_SYMBOL = "£"

# What a statement shows when a figure is genuinely absent rather than zero.
# Distinguished because they read the same in money and mean opposite things: a
# shopper who spent nothing has a figure, and a shopper whose figure could not
# be worked out does not.
NOTHING_TO_SHOW = "—"

# How many band rows a statement will print before it stops. A statement is
# read on a phone, and a breakdown longer than this stops being a breakdown and
# becomes the history it was summarising.
MOST_BANDS_SHOWN = 6

# Below this, a change against the shopper's usual month is noise rather than
# news. A statement that announced every two-percent drift would be a statement
# that announced something every month, which is the same as announcing nothing.
WORTH_MENTIONING_SHARE = 0.1


@dataclass(frozen=True)
class PriceBand:
    """One size of purchase, and what to call it.

    Bands rather than categories, because this shop does not have categories.
    What it has is prices, and the shape of a month is mostly the shape of its
    prices: a month of thirty small things and a month of one large thing are
    different months at the same total, and the band breakdown is the only place
    on the statement where that difference is visible.

    `ceiling_cents` is exclusive, and `None` on the top band - a band with a
    ceiling above it would need a ceiling of its own, and there is no largest
    purchase anybody can name in advance.
    """

    label: str
    floor_cents: int
    ceiling_cents: int | None = None

    def holds(self, purchase: Purchase) -> bool:
        """Whether this purchase belongs in this band.

        Half-open, so that the bands tile the whole range without a price
        falling into two of them. The ordering of the tuple below is what makes
        that true rather than anything checked here - see `THE_PRICE_BANDS`.
        """
        if purchase.price_cents < self.floor_cents:
            return False

        if self.ceiling_cents is None:
            return True

        return purchase.price_cents < self.ceiling_cents


# The bands, cheapest first, tiling the whole range. Written out rather than
# generated from a step, because they are not a scale - they are the sizes this
# shop's customers actually shop in, and the boundaries were chosen by looking
# at a histogram rather than by arithmetic. A generated scale would put a
# boundary in the middle of the most common purchase in the shop.
THE_PRICE_BANDS: tuple[PriceBand, ...] = (
    PriceBand("Under £5", 0, 500),
    PriceBand("£5 to £20", 500, 2_000),
    PriceBand("£20 to £50", 2_000, 5_000),
    PriceBand("£50 to £100", 5_000, 10_000),
    PriceBand("£100 to £250", 10_000, 25_000),
    PriceBand("£250 to £500", 25_000, 50_000),
    PriceBand("Over £500", 50_000),
)


# The shop's own classification, in the order a statement lists it. Order is
# not alphabetical and not by popularity: it is the order the categories appear
# in the shop's navigation, so that a shopper reading their statement and a
# shopper browsing the shop are reading the same list in the same sequence.
#
# `UNCLASSIFIED` is last and is not a category anybody shops in. It is where a
# purchase goes when the catalogue has not been told what it is, which happens
# for a few days after anything new is listed - and a statement that hid those
# would be a statement whose rows do not add up to its headline.
UNCLASSIFIED = "General"

THE_CATEGORIES: tuple[str, ...] = (
    "Groceries",
    "Home",
    "Clothing",
    "Electronics",
    "Books",
    "Garden",
    UNCLASSIFIED
)


@dataclass(frozen=True)
class CategorySummary:
    """What this shopper spent in one category this month.

    The band breakdown says what *sizes* the month was made of; this says what
    it was made of. Both, because they answer different complaints: a shopper
    surprised by a total wants to know which shelf it came off, and a shopper
    who recognises every line but not the total wants to know that it was thirty
    small things rather than one large one.

    `refunded_cents` sits on the category rather than only on the month, because
    a refund is a fact about the thing that was bought. A statement reporting
    £200 of electronics without mentioning that £150 of it came back would be
    accurate about the month and wrong about the shopper.
    """

    name: str
    purchase_count: int
    total_cents: int
    refunded_cents: int
    share_of_month: float

    @property
    def is_empty(self) -> bool:
        return self.purchase_count == 0

    @property
    def net_cents(self) -> int:
        """What this category cost after what came back.

        Never below zero, and the clamp is deliberate rather than defensive: a
        refund can genuinely exceed the month's spend in that category, when
        something bought last month is returned in this one. That is a real
        shape and it is somebody else's row - the credit belongs on the month it
        lands in, and a category showing a negative cost would invite a shopper
        to read it as money the shop owes them against this month's purchases.
        """
        return max(0, self.total_cents - self.refunded_cents)


@dataclass(frozen=True)
class RefundSummary:
    """What came back this month, across every category.

    Its own type rather than a pair of integers on the statement, because a
    refund section that exists and a refund section that is empty are different
    documents: the first has rows, a total and a sentence explaining the delay
    before money lands, and the second has no section at all. A caller holding
    two integers has to reconstruct which of those it is on every render.
    """

    refunded_cents: int
    refund_count: int

    @property
    def is_empty(self) -> bool:
        return self.refund_count == 0


@dataclass(frozen=True)
class BandSummary:
    """What this shopper spent in one band this month.

    `share_of_month` is carried rather than worked out by whoever renders it,
    because the divisor is the month's total and a renderer holding one band has
    no way to know it. Carrying the share is also what stops six renderers
    rounding the same division six different ways.
    """

    band: PriceBand
    purchase_count: int
    total_cents: int
    share_of_month: float

    @property
    def is_empty(self) -> bool:
        return self.purchase_count == 0


@dataclass(frozen=True)
class StatementRow:
    """One printed line of the statement: what it says and what it shows.

    Deliberately two strings and a flag rather than a label and a number. By the
    time a figure reaches a row it has been formatted, and formatting is where
    the unit lives - a row carrying an int would let the page decide whether it
    was pounds or pence, and the page is the one place in the shop with no way
    to know.

    `emphasis` marks the lines a shopper's eye should land on first. Presentation,
    and it lives here rather than in the template because which lines matter is a
    property of the statement rather than of the page it is drawn on: the
    headline is emphatic in an email too.
    """

    label: str
    value: str
    emphasis: bool = False


@dataclass(frozen=True)
class DeliverySummary:
    """What getting the month's purchases to the door cost.

    Its own line on the statement because it is the one charge a shopper did
    not choose the size of. Every other figure here is the sum of decisions
    they made; delivery is what the shop added, and a statement that folded it
    into the headline would be hiding the only charge anybody ever queries.

    `free_deliveries` is counted rather than derived from the total, because
    zero delivery cost across the month is ambiguous: it is either a month of
    free deliveries or a month of collections, and the shopper knows which but
    the statement should not have to guess.
    """

    delivery_cents: int
    charged_deliveries: int
    free_deliveries: int

    @property
    def is_empty(self) -> bool:
        return self.charged_deliveries == 0 and self.free_deliveries == 0

    @property
    def was_all_free(self) -> bool:
        return self.charged_deliveries == 0 and self.free_deliveries > 0


@dataclass(frozen=True)
class SavingsSummary:
    """What the shopper did not pay, and would have.

    The one section of the statement that exists to be good news, and the one
    most easily made dishonest. What is counted here is money actually taken
    off a price at checkout - a discount code, a sale price, a loyalty
    reduction. What is not counted, ever, is a saving against a price nothing
    was ever sold at, which is the practice that gets shops fined.

    `share_of_what_it_would_have_cost` is against the undiscounted total rather
    than against what was paid, because that is what "saved 20%" means to a
    reader. Computed here so that no renderer has to choose a divisor.
    """

    saved_cents: int
    discounted_purchases: int
    share_of_what_it_would_have_cost: float

    @property
    def is_empty(self) -> bool:
        return self.discounted_purchases == 0


@dataclass(frozen=True)
class InstalmentSummary:
    """What this month's purchases still owe.

    Forward-looking, and the only part of the statement that is. Everything
    else describes a month that has happened; this describes payments that have
    not, and it is on the statement because a shopper reconciling a month
    against their bank needs to know which of these purchases will appear again
    next month.

    `outstanding_cents` is what is left to pay across every purchase still on a
    plan. `longest_plan_months` is how far ahead the last of it reaches, which
    is the figure that answers "when does this stop".
    """

    outstanding_cents: int
    purchases_on_a_plan: int
    longest_plan_months: int

    @property
    def is_empty(self) -> bool:
        return self.purchases_on_a_plan == 0


# The months, in order, one-based - `MONTH_NAMES[1]` is January. The unused
# slot at the front is deliberate and is the cheapest correct thing here: every
# calendar a shop talks to counts months from one, and a zero-based tuple turns
# every lookup into `month - 1`, which is the subtraction somebody eventually
# forgets in the one branch that is only reached in December.
MONTH_NAMES: tuple[str, ...] = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December"
)

# The export's columns, in the order it writes them. Named rather than spelled
# at the one place that writes the header, because the header and the rows are
# written by two functions and a spreadsheet whose header disagrees with its
# rows is worse than one with no header at all.
STATEMENT_COLUMNS: tuple[str, str, str] = ("Section", "Item", "Amount")


@dataclass(frozen=True)
class StatementPeriod:
    """Which month this statement is of.

    Passed in rather than worked out here, and that is the whole point of the
    type. A purchase record carries whether it falls in the current month - see
    `io_shop.accounts` - because that is what the query selected on; it does not
    carry a date, and a statement module that inferred the month from the
    server's clock would title a statement wrongly for every shopper on the
    other side of a date line, and for everybody during the hour after
    midnight on the first.

    The page knows which month it asked for. This says it back.
    """

    month_name: str
    year: int

    @property
    def title(self) -> str:
        return f"{self.month_name} {self.year}"

    @property
    def as_a_filename(self) -> str:
        """The period as the download names itself.

        Year first so that a shopper's downloads folder sorts their statements
        into the order they happened. Lower case and hyphenated because a file
        name travels between three operating systems with three opinions about
        spaces and capitals, and only one opinion about hyphens.
        """
        return f"io-statement-{self.year}-{self.month_name.lower()}.csv"


def period_for(month_number: int, year: int) -> StatementPeriod:
    """The period for a month given as a number, as a calendar hands it over.

    Raises on a month outside the calendar rather than clamping. A statement
    titled with the wrong month is worse than one that failed to render: the
    figures on it are real and the title is not, and a shopper reconciling it
    against their bank will trust the title.
    """
    if not 1 <= month_number <= 12:
        raise ValueError(f"there is no month {month_number}")

    return StatementPeriod(month_name=MONTH_NAMES[month_number], year=year)


@dataclass(frozen=True)
class StatementSection:
    """One named group of rows, as the document is divided.

    The name is a heading rather than a key: it is shown to the shopper, it is
    written into the export, and it is what a screen reader announces before
    the rows under it. Nothing looks a section up by it, which is why there is
    no identifier beside it - an id nobody reads is a second name to keep in
    agreement with the first.
    """

    name: str
    rows: list[StatementRow]

    @property
    def is_empty(self) -> bool:
        return not self.rows


@dataclass(frozen=True)
class MonthComparison:
    """This month set against everything before it.

    A statement without a comparison is a statement that answers "what did I
    spend" and not "is that a lot", and the second question is the one a shopper
    is actually asking. The comparison is against the shopper's own lifetime
    average rather than against other shoppers, because a shop that told someone
    they spend more than their neighbours would be telling them something they
    did not ask and did not consent to.

    `direction` is the word the statement uses, resolved here so that the sign
    and the wording cannot disagree. `share` is the size of the difference as a
    fraction of the usual month, and it is always positive - the direction
    carries the sign, and a negative share rendered with the word "less" is the
    bug this pair exists to prevent.
    """

    usual_month_cents: int
    this_month_cents: int
    direction: str
    share: float

    @property
    def is_worth_mentioning(self) -> bool:
        return self.share >= WORTH_MENTIONING_SHARE


@dataclass(frozen=True)
class MonthlyStatement:
    """The whole panel, assembled and ready to render.

    Assembled in one pass and then frozen, rather than computed lazily by the
    page as each row is drawn. Two reasons, and the second is the one that
    matters: a statement drawn field by field would re-walk the history once per
    row, and a statement that failed halfway would have already printed three
    rows of a document it cannot finish. A shopper seeing half a statement is
    worse served than one seeing none.
    """

    period: StatementPeriod
    headline_cents: int
    purchase_count: int
    biggest_cents: int
    smallest_cents: int
    mean_cents: int
    bands: tuple[BandSummary, ...]
    categories: tuple[CategorySummary, ...]
    refunds: RefundSummary
    delivery: DeliverySummary
    savings: SavingsSummary
    instalments: InstalmentSummary
    compared_with_usual: MonthComparison

    @property
    def net_cents(self) -> int:
        """What the month cost after refunds.

        The figure a shopper reconciles against their bank, and deliberately
        not the headline. The headline is what they spent, which is the
        question the panel is answering; this is what left their account, which
        is a different question and one their bank has already answered. Shown
        beside the headline only when the two differ.
        """
        return self.headline_cents - self.refunds.refunded_cents

    @property
    def is_a_quiet_month(self) -> bool:
        """Whether this month is small enough to say so rather than break down.

        One purchase does not have a shape, and a band breakdown of a single
        purchase is a bar chart with one bar - which reads as a mistake rather
        than as a month.
        """
        return self.purchase_count <= 1


def format_money(cents: int) -> str:
    """Pence as the shop writes money.

    Always two decimal places, including on a whole number of pounds: a column
    of figures where some have pence and some do not is a column that does not
    line up, and a statement is a column of figures.

    Negative figures are written with the sign before the symbol rather than
    after it, because that is what every bank statement does and a statement
    that did it the other way would read as a typo.
    """
    if cents < 0:
        return f"-{CURRENCY_SYMBOL}{-cents / PENCE_IN_A_POUND:.2f}"

    return f"{CURRENCY_SYMBOL}{cents / PENCE_IN_A_POUND:.2f}"


def format_share(share: float) -> str:
    """A fraction as a whole percent.

    Rounded rather than truncated, and to whole percents rather than to
    decimals: a breakdown is read as proportions, and 33.3% next to 33.4% next
    to 33.3% invites a shopper to add them up and find they do not make a
    hundred. At whole percents they usually do not either, but nobody checks.

    A share that rounds to nothing is written as "under 1%" rather than as 0%,
    because a band with purchases in it did not have none.
    """
    percent = round(share * 100)

    if percent == 0 and share > 0:
        return "under 1%"

    return f"{percent}%"


def format_count(count: int, singular: str, plural: str) -> str:
    """A count with the right noun after it.

    Both words are taken rather than an "s" appended, because English does not
    work that way and a statement that said "1 purchases" would be the thing a
    shopper remembers about the shop.
    """
    if count == 1:
        return f"1 {singular}"

    return f"{count} {plural}"


def printed_shares(shares: list[float]) -> list[int]:
    """Shares as whole percents that actually come to a hundred.

    Rounding each share on its own is what `format_share` does, and it is right
    for a share shown alone. It is wrong for a column: three thirds rounded
    independently print as 33%, 33%, 33% and a reader adds them to 99%, which
    is the single most reported "bug" in any statement anybody has ever
    shipped. The figures are correct and the column is still wrong, because a
    column of percentages is read as a partition.

    So the column is apportioned rather than rounded: every share takes its
    whole percent, and the percents left over go to the shares with the largest
    fractions discarded - the largest-remainder method, which is the same
    arithmetic used to hand out seats to parties by vote share, and for the same
    reason. It is the only apportionment that cannot give a larger share fewer
    percents than a smaller one.

    Returns percents rather than formatted strings, because the caller is the
    one that knows whether this column is going into markup, plain text or a
    spreadsheet cell. An empty column comes back empty rather than as a
    hundred: there is nothing to apportion, and a lone 100% row is a partition
    of a month that had no purchases in it.
    """
    if not shares:
        return []

    whole = 100
    exact = [share * whole for share in shares]
    apportioned = [int(figure) for figure in exact]
    left_over = whole - sum(apportioned)

    if left_over <= 0:
        return apportioned

    # By the fraction discarded, largest first, and by position where two
    # fractions are equal. The tie-break is not arbitrary tidiness: without it
    # the order depends on the sort's stability over floats that compare equal,
    # and the same month would apportion differently between two runs.
    by_remainder = sorted(
        range(len(exact)),
        key=lambda index: (-(exact[index] - apportioned[index]), index)
    )

    for index in by_remainder[:left_over]:
        apportioned[index] += 1

    return apportioned


def band_for(purchase: Purchase) -> PriceBand:
    """Which band this purchase falls into.

    The first band that holds it, which is the cheapest one that can - the
    bands are ordered and half-open, so the first match is the only match.
    Falls back to the top band rather than raising, because the top band has no
    ceiling and therefore cannot fail to hold a price; the fallback exists so
    that a future edit to the bands cannot make this function raise on a
    perfectly ordinary purchase.
    """
    for band in THE_PRICE_BANDS:
        if band.holds(purchase):
            return band

    return THE_PRICE_BANDS[-1]


def purchases_this_month(account: Account) -> tuple[Purchase, ...]:
    """Everything this shopper bought in the current month.

    The purchase carries the month rather than a date, because that is what the
    query this account came back from selected on - see `io_shop.accounts`. A
    statement module that re-derived the month from a timestamp would be
    answering a question the query has already answered, and answering it in a
    different timezone.

    One walk of the whole history, and the reason everything below it takes the
    month rather than the account: this is the only function here whose cost
    grows with how long a shopper has been shopping, and a statement that
    called it once per figure would multiply that cost by the number of figures
    on the panel.
    """
    return tuple(
        purchase for purchase in account.purchases if purchase.in_current_month
    )


def the_biggest_purchase_this_month(account: Account) -> int:
    """The largest single purchase of the month, in pence.

    The statement leads on this after the headline, because it is the figure a
    shopper checks first: a month that surprised them usually surprised them
    once, and this is the purchase that did it.
    """
    return _the_biggest_of(purchases_this_month(account))


def the_smallest_purchase_this_month(account: Account) -> int:
    """The smallest single purchase of the month, in pence.

    Shown beside the largest, and the pair is the point rather than either on
    its own: the distance between them is what says whether this was a month of
    one big thing or a month of many similar things, before the band breakdown
    says it in detail.
    """
    return _the_smallest_of(purchases_this_month(account))


def the_mean_purchase_this_month(account: Account) -> int:
    """What the month's purchases averaged, in pence.

    Floor division, as every other figure on this page uses, so that the
    statement never shows a fraction of a penny. Rounding up would occasionally
    produce a mean above the largest purchase on a month of identical prices,
    which is the kind of figure that costs a support conversation.
    """
    return _the_mean_of(
        purchases_this_month(account), account.total_this_month_cents
    )


def _the_biggest_of(bought: tuple[Purchase, ...]) -> int:
    """The largest of a month already in hand.

    Raises on an empty month, as it always has: a month with nothing in it has
    no largest purchase, and the page above turns that into a failed response
    rather than a made-up zero.
    """
    return max(purchase.price_cents for purchase in bought)


def _the_smallest_of(bought: tuple[Purchase, ...]) -> int:
    return min(purchase.price_cents for purchase in bought)


def _the_mean_of(bought: tuple[Purchase, ...], month_total_cents: int) -> int:
    return month_total_cents // len(bought)


def _share_of(part_cents: int, whole_cents: int) -> float:
    """One figure as a fraction of another, with an empty whole read as nothing.

    The guard is here rather than at each call site because there are six of
    them and one of them will eventually be written without it. A share of an
    empty total is genuinely zero rather than undefined for this statement's
    purposes: no money was spent, so no band holds a share of it.
    """
    if whole_cents == 0:
        return 0.0

    return part_cents / whole_cents


def _band_summaries(bought: tuple[Purchase, ...],
                    month_total_cents: int) -> tuple[BandSummary, ...]:
    """Every band, with what fell into it.

    Every band including the empty ones, and they are dropped later rather than
    here. What a renderer needs is the bands in order with their gaps intact -
    a breakdown assembled from only the non-empty bands cannot tell "no
    purchases between £20 and £50" from "£20 to £50 is not a band this shop
    has", and the first of those is a fact about the shopper's month.

    One pass over the month rather than one per band. The bands are ordered and
    half-open, so the first that holds a purchase is the only one that can -
    which is what makes stopping there identical to asking every band in turn,
    rather than merely close to it. A price no band holds is counted into none
    of them, exactly as before, so `reconciles` still catches it instead of it
    being quietly filed somewhere.
    """
    counts = [0] * len(THE_PRICE_BANDS)
    totals = [0] * len(THE_PRICE_BANDS)

    for purchase in bought:
        for index, band in enumerate(THE_PRICE_BANDS):
            if band.holds(purchase):
                counts[index] += 1
                totals[index] += purchase.price_cents
                break

    return tuple(
        BandSummary(
            band=band,
            purchase_count=counts[index],
            total_cents=totals[index],
            share_of_month=_share_of(totals[index], month_total_cents)
        )
        for index, band in enumerate(THE_PRICE_BANDS)
    )


def _category_summaries(bought: tuple[Purchase, ...],
                        month_total_cents: int) -> tuple[CategorySummary, ...]:
    """Every category the shop has, with what fell into it this month.

    Driven from `THE_CATEGORIES` rather than from the categories that happen to
    appear in this month's purchases, and the difference matters in both
    directions. A month with nothing bought in Books still has a Books row
    available to anyone who wants the full picture, and - more importantly - a
    purchase carrying a category the shop does not have does not silently
    invent a row for it. It lands in `UNCLASSIFIED`, which is where the
    catalogue puts it too, and the statement still adds up.

    One pass over the month, filing each purchase under the one category it
    belongs to, rather than one pass per category asking every purchase whether
    it belongs to that one. The rows still come back in the shop's own order,
    which is the only thing about them a reader can see.
    """
    known = set(THE_CATEGORIES)
    counts = {name: 0 for name in THE_CATEGORIES}
    totals = {name: 0 for name in THE_CATEGORIES}
    refunded = {name: 0 for name in THE_CATEGORIES}

    for purchase in bought:
        name = _category_of(purchase, known)
        counts[name] += 1
        totals[name] += purchase.price_cents
        refunded[name] += purchase.refunded_cents

    return tuple(
        CategorySummary(
            name=name,
            purchase_count=counts[name],
            total_cents=totals[name],
            refunded_cents=refunded[name],
            share_of_month=_share_of(totals[name], month_total_cents)
        )
        for name in THE_CATEGORIES
    )


def _category_of(purchase: Purchase, known: set[str]) -> str:
    """Which of the shop's categories this purchase is filed under.

    Anything the shop does not recognise is filed as unclassified rather than
    trusted. The category on a purchase record comes from the catalogue, the
    catalogue changes, and a record written before a category was renamed still
    carries the old name - so a statement that grouped by whatever string it
    found would grow a new row every time the shop reorganised a shelf, each
    holding a handful of old purchases.
    """
    if purchase.category in known:
        return purchase.category

    return UNCLASSIFIED


def _refunds_this_month(bought: tuple[Purchase, ...]) -> RefundSummary:
    """What came back, counted by purchase rather than by refund.

    A purchase refunded in two parts is one purchase that was refunded, and
    counting the parts would tell a shopper they returned four things when they
    returned two. The money is the sum either way; only the count differs, and
    the count is the half a shopper checks against their memory.
    """
    refunded = [purchase for purchase in bought if purchase.refunded_cents > 0]

    return RefundSummary(
        refunded_cents=sum(purchase.refunded_cents for purchase in refunded),
        refund_count=len(refunded)
    )


def _delivery_this_month(bought: tuple[Purchase, ...]) -> DeliverySummary:
    """What delivery came to, and how many arrived without a charge.

    A purchase with no delivery charge is counted as a free delivery rather
    than ignored, which is the whole reason this is a summary and not an
    integer. The two counts are what let the statement say "eleven deliveries,
    nine of them free" - a sentence a shopper reads as the shop being fair to
    them, and one that cannot be written from a total alone.
    """
    charged = [purchase for purchase in bought if purchase.delivery_cents > 0]

    return DeliverySummary(
        delivery_cents=sum(purchase.delivery_cents for purchase in charged),
        charged_deliveries=len(charged),
        free_deliveries=len(bought) - len(charged)
    )


def _savings_this_month(bought: tuple[Purchase, ...]) -> SavingsSummary:
    """What came off the month's prices at checkout.

    The undiscounted total is reconstructed by adding the discounts back to
    what was paid, rather than carried on the purchase as a second price. A
    stored "was" price is the field that goes stale, gets copied from a
    supplier's feed, and ends up advertising a saving against a number this
    shop never charged. What was actually taken off is a fact about the
    transaction and cannot drift.
    """
    discounted = [purchase for purchase in bought if purchase.discount_cents > 0]
    saved_cents = sum(purchase.discount_cents for purchase in discounted)
    paid_cents = sum(purchase.price_cents for purchase in bought)

    return SavingsSummary(
        saved_cents=saved_cents,
        discounted_purchases=len(discounted),
        share_of_what_it_would_have_cost=_share_of(
            saved_cents, paid_cents + saved_cents
        )
    )


def _instalments_this_month(bought: tuple[Purchase, ...]) -> InstalmentSummary:
    """What is still to pay on the month's purchases, and for how long.

    The outstanding figure is worked out per purchase rather than from a
    stored balance: what remains is the price divided across the plan, times
    the instalments still to come. Dividing here rather than storing a balance
    is what keeps the statement right after a shopper pays one off early - the
    count of remaining instalments is the thing that changes, and everything
    else follows from it.

    A plan of one remaining instalment is still a plan. It is the month the
    shopper most wants to be told about, because it is the last time the
    payment appears.
    """
    on_a_plan = [purchase for purchase in bought if purchase.instalments_remaining > 0]

    return InstalmentSummary(
        outstanding_cents=sum(
            _instalment_of(purchase) * purchase.instalments_remaining
            for purchase in on_a_plan
        ),
        purchases_on_a_plan=len(on_a_plan),
        longest_plan_months=max(
            (purchase.instalments_remaining for purchase in on_a_plan), default=0
        )
    )


def _instalment_of(purchase: Purchase) -> int:
    """What one instalment of this purchase comes to, in pence.

    Floor division, with the remainder landing on the payment the shopper has
    already made rather than on the ones to come. Every plan in the shop takes
    its rounding up front for the same reason: a final payment of £33.34 after
    two of £33.33 is the one a shopper queries, and a first payment carrying
    the pennies is the one nobody notices.
    """
    if purchase.instalments_remaining <= 0:
        return 0

    return purchase.price_cents // (purchase.instalments_remaining + 1)


def _the_usual_month(account: Account, bought: tuple[Purchase, ...]) -> int:
    """What this shopper spends in a month, taken across their whole history.

    An approximation, and openly one: the account carries a lifetime total and a
    count of purchases, not a count of months, so "the usual month" is derived
    from the shape of the history rather than looked up. What it is derived from
    is the lifetime average purchase multiplied by the number of purchases in
    the current month - which is to say, what this month would have cost at the
    shopper's usual prices.

    That is the comparison a statement wants. Comparing against a true monthly
    average would tell a shopper who bought twice as much as usual that they
    spent twice as much as usual, which they already know. Comparing at equal
    volume isolates the thing they cannot see: whether the things they bought
    were dearer than the things they normally buy.

    The month is handed in rather than worked out again, because the caller has
    already walked the history to find it.
    """
    if not account.purchases:
        return 0

    lifetime_mean = account.total_cents // len(account.purchases)

    return lifetime_mean * len(bought)


def _compared_with_usual(account: Account,
                         bought: tuple[Purchase, ...]) -> MonthComparison:
    """This month against what this shopper's months usually cost.

    The direction is resolved to a word here, and the share is made positive
    here, so that nothing downstream has to hold both halves of a signed
    comparison in its head. A renderer that received a signed share would
    eventually print "12% less" for a month that was 12% more, and the only
    person who would notice is the shopper.
    """
    usual_cents = _the_usual_month(account, bought)
    this_month_cents = account.total_this_month_cents
    difference = this_month_cents - usual_cents

    if difference > 0:
        direction = "more"
    elif difference < 0:
        direction = "less"
    else:
        direction = "the same as"

    return MonthComparison(
        usual_month_cents=usual_cents,
        this_month_cents=this_month_cents,
        direction=direction,
        share=abs(_share_of(difference, usual_cents))
    )


def render_monthly_statement(account: Account,
                             period: StatementPeriod) -> MonthlyStatement:
    """The month, assembled.

    The one entry point. Everything above it is a piece of the document and
    everything below it turns the document into rows, and a caller that reached
    past this into either half would be assembling a statement of its own -
    which is how two parts of a shop come to disagree about what a shopper
    spent.

    The history is walked once, here, and every figure below is worked out from
    the month that walk produced. That is what `MonthlyStatement` means by one
    pass, and it is load-bearing rather than tidy: this is the only work on the
    account page whose cost grows with how much a shopper has ever bought, so a
    figure that re-derived the month for itself would charge the shoppers with
    the longest histories again for every figure on the panel.

    Raises rather than returning an empty statement. A month with nothing in it
    has no largest purchase, no smallest, no mean and no shape, and every one of
    those is a row this panel promises. The page above catches it, records the
    shopper it failed for and the line it failed on, and serves a failed
    response - see `io_shop.account_page`, which is the only place in the shop
    that catches broadly and the only place that should.
    """
    bought = purchases_this_month(account)
    month_total_cents = account.total_this_month_cents

    return MonthlyStatement(
        period=period,
        headline_cents=month_total_cents,
        purchase_count=len(bought),
        biggest_cents=_the_biggest_of(bought),
        smallest_cents=_the_smallest_of(bought),
        mean_cents=_the_mean_of(bought, month_total_cents),
        bands=_band_summaries(bought, month_total_cents),
        categories=_category_summaries(bought, month_total_cents),
        refunds=_refunds_this_month(bought),
        delivery=_delivery_this_month(bought),
        savings=_savings_this_month(bought),
        instalments=_instalments_this_month(bought),
        compared_with_usual=_compared_with_usual(account, bought)
    )


def statement_sections(statement: MonthlyStatement) -> list[StatementSection]:
    """The statement as a document: named sections, in reading order.

    Order is the whole design of a document like this. The headline, then the
    two figures that give the month its shape, then where the money went, then
    what the shop added or took off, then what is still owed, and last the
    comparison that says whether any of it is unusual. A shopper who reads only
    the first section has read the most useful thing on the panel, and one who
    reads all of it has read them in the order that builds.

    Sections rather than a flat list, because every renderer below needs the
    grouping and each would otherwise rediscover it from the labels. The page
    draws a heading per section, the email puts a blank line between them, and
    the export writes the section name into a column - three renderers, one
    structure, and no opinion about where a section starts held in more than
    one place.

    Empty sections are dropped here rather than by each renderer. A section
    with no rows is a heading over nothing, which is the one thing every one of
    the three gets wrong in its own way.
    """
    sections = [
        StatementSection(
            name="This month",
            rows=[
                StatementRow(
                    label="Spent this month",
                    value=format_money(statement.headline_cents),
                    emphasis=True
                ),
                StatementRow(
                    label="Purchases",
                    value=format_count(
                        statement.purchase_count, "purchase", "purchases"
                    )
                ),
                *_the_shape_of_the_month(statement)
            ]
        ),
        StatementSection(name="By size", rows=_the_breakdown(statement)),
        StatementSection(name="By category", rows=_the_categories(statement)),
        StatementSection(
            name="Delivery and savings",
            rows=[*_the_delivery(statement), *_the_savings(statement)]
        ),
        StatementSection(name="Refunds", rows=_the_refunds(statement)),
        StatementSection(name="Still to pay", rows=_the_instalments(statement)),
        StatementSection(
            name="Against your usual month", rows=_the_comparison(statement)
        )
    ]

    return [section for section in sections if not section.is_empty]


def statement_rows(statement: MonthlyStatement) -> list[StatementRow]:
    """Every row of the statement, in order, with the sections flattened away.

    What the places that cannot show a heading want: the notification, the
    one-column mobile layout, the accessibility tree's fallback. Built from the
    sections rather than beside them, so that a row added to the document
    appears here without anybody remembering to add it twice.
    """
    return [row for section in statement_sections(statement) for row in section.rows]


def _the_shape_of_the_month(statement: MonthlyStatement) -> list[StatementRow]:
    """The largest, the smallest and the average, where there is a spread to show.

    Suppressed entirely on a quiet month rather than shown with three equal
    figures. A month of one purchase would print that purchase three times under
    three different labels, which reads as a rendering fault rather than as a
    quiet month - and the row that follows says the true thing instead.
    """
    if statement.is_a_quiet_month:
        return [
            StatementRow(
                label="A quiet month",
                value=format_money(statement.headline_cents)
            )
        ]

    return [
        StatementRow(
            label="Largest purchase", value=format_money(statement.biggest_cents)
        ),
        StatementRow(
            label="Smallest purchase", value=format_money(statement.smallest_cents)
        ),
        StatementRow(
            label="Average purchase", value=format_money(statement.mean_cents)
        )
    ]


def _the_breakdown(statement: MonthlyStatement) -> list[StatementRow]:
    """The band rows, busiest first, capped at what fits on a phone.

    Sorted by what was spent rather than by the band's position on the scale,
    because the question the breakdown answers is "where did the money go" and
    the answer is a ranking. The scale's own order is still available to anyone
    who wants it - `statement.bands` is in it - and this is the reading order
    rather than the data.

    Empty bands are dropped here, having been carried this far deliberately:
    the gaps are a fact about the month, but a printed row saying a band holds
    nothing spends a line of a phone screen on an absence.
    """
    occupied = [summary for summary in statement.bands if not summary.is_empty]
    busiest_first = sorted(occupied, key=lambda summary: -summary.total_cents)
    shown = busiest_first[:MOST_BANDS_SHOWN]
    percents = printed_shares([summary.share_of_month for summary in shown])

    return [
        StatementRow(
            label=summary.band.label,
            value=f"{format_money(summary.total_cents)} ({percent}%)"
        )
        for summary, percent in zip(shown, percents, strict=True)
    ]


def _the_comparison(statement: MonthlyStatement) -> list[StatementRow]:
    """The one row that says whether any of the above is unusual.

    Printed only when the difference is worth printing. A statement that ends
    with "2% more than usual" every month has taught its reader to stop at the
    row above it, and the month this panel exists for is the month the number is
    genuinely surprising.

    The wording puts the direction before the figure - "more than usual, by
    £40" - because the direction is the news and the figure is the detail, and a
    row read at a glance should be read correctly at a glance.
    """
    comparison = statement.compared_with_usual

    if not comparison.is_worth_mentioning:
        return []

    difference_cents = abs(
        comparison.this_month_cents - comparison.usual_month_cents
    )

    return [
        StatementRow(
            label=f"{comparison.direction.capitalize()} than usual",
            value=(
                f"{format_money(difference_cents)} "
                f"({format_share(comparison.share)})"
            )
        )
    ]


def _the_categories(statement: MonthlyStatement) -> list[StatementRow]:
    """Where the month's money went, by shelf rather than by size.

    Ordered by spend like the band breakdown, and capped by the same number:
    the two sections sit next to each other on the panel, and two lists of
    different lengths read as one list that ran out rather than as two
    breakdowns of the same month.

    A month spent entirely in one category prints nothing. The row would say
    what the headline already says, in more words, and a breakdown with one
    row in it is not a breakdown.
    """
    occupied = [summary for summary in statement.categories if not summary.is_empty]

    if len(occupied) <= 1:
        return []

    busiest_first = sorted(occupied, key=lambda summary: -summary.total_cents)
    shown = busiest_first[:MOST_BANDS_SHOWN]
    percents = printed_shares([summary.share_of_month for summary in shown])

    return [
        StatementRow(
            label=summary.name,
            value=f"{format_money(summary.total_cents)} ({percent}%)"
        )
        for summary, percent in zip(shown, percents, strict=True)
    ]


def _the_refunds(statement: MonthlyStatement) -> list[StatementRow]:
    """What came back, and what the month cost once it had.

    Two rows rather than one, and only when there is anything to say. The first
    is what was returned, which a shopper remembers doing; the second is the
    net, which is what their bank will show them and the figure they will bring
    to a support conversation if the two do not agree.

    Nothing is printed on a month with no refunds. A row reading "Refunded
    £0.00" invites the reader to wonder what it is doing there, and the answer
    - that the shop prints it every month - is not one that makes the statement
    better.
    """
    if statement.refunds.is_empty:
        return []

    return [
        StatementRow(
            label=format_count(
                statement.refunds.refund_count, "refund", "refunds"
            ).capitalize(),
            value=format_money(statement.refunds.refunded_cents)
        ),
        StatementRow(
            label="Net for the month",
            value=format_money(statement.net_cents),
            emphasis=True
        )
    ]


def _the_delivery(statement: MonthlyStatement) -> list[StatementRow]:
    """What delivery cost, or that it cost nothing.

    A month of entirely free deliveries gets a row saying so rather than no row
    at all. It is the one absence on this statement worth printing: a shopper
    who has been charged before and is not being charged now has been given
    something, and a silent section reads as the shop having forgotten to
    mention it.
    """
    delivery = statement.delivery

    if delivery.is_empty:
        return []

    if delivery.was_all_free:
        return [
            StatementRow(
                label="Delivery",
                value=f"Free on all {delivery.free_deliveries}"
            )
        ]

    return [
        StatementRow(
            label="Delivery", value=format_money(delivery.delivery_cents)
        )
    ]


def _the_savings(statement: MonthlyStatement) -> list[StatementRow]:
    """What came off, where anything did.

    The share goes in the value beside the money rather than in the label,
    matching every other two-part row on the statement. A reader scanning the
    right-hand column is scanning figures, and a percentage that had wandered
    into the left-hand column would be the one figure they have to read a
    sentence to find.
    """
    savings = statement.savings

    if savings.is_empty:
        return []

    return [
        StatementRow(
            label="Saved",
            value=(
                f"{format_money(savings.saved_cents)} "
                f"({format_share(savings.share_of_what_it_would_have_cost)})"
            )
        )
    ]


def _the_instalments(statement: MonthlyStatement) -> list[StatementRow]:
    """What is still owed on this month's purchases.

    Two rows, and the second is the one that matters: a shopper told they owe
    £180 without being told over how long has been told something alarming
    about a payment plan they chose deliberately.

    The months are written as months rather than as a count of payments, even
    though the plans are monthly and the two are the same number. "Over 4
    months" is a length of time; "4 payments" is a quantity, and the question
    being answered is when this stops.
    """
    instalments = statement.instalments

    if instalments.is_empty:
        return []

    return [
        StatementRow(
            label="Still to pay",
            value=format_money(instalments.outstanding_cents)
        ),
        StatementRow(
            label="Over",
            value=format_count(instalments.longest_plan_months, "month", "months")
        )
    ]


def describe_month(statement: MonthlyStatement) -> str:
    """The statement in one sentence, for the places a table will not fit.

    The notification, the summary line above the fold, the screen reader's
    first utterance. All three want the same sentence, and a shop that wrote it
    three times would have three sentences that disagreed about rounding within
    a release.

    Built from the same figures the rows are built from rather than from the
    rows themselves, because a sentence assembled by re-parsing formatted
    strings is a sentence that breaks the first time a currency symbol moves.
    """
    spent = format_money(statement.headline_cents)
    purchases = format_count(statement.purchase_count, "purchase", "purchases")

    if statement.is_a_quiet_month:
        return f"A quiet month: {spent} across {purchases}."

    opening = f"{spent} this month, across {purchases}"
    comparison = statement.compared_with_usual

    if not comparison.is_worth_mentioning:
        return f"{opening}, in line with your usual month."

    return (
        f"{opening} - {format_share(comparison.share)} "
        f"{comparison.direction} than your usual month."
    )


def accessible_label(row: StatementRow) -> str:
    """One row said the way a screen reader should say it.

    A table reads badly aloud: the label and the value arrive as two unrelated
    utterances, and the parenthesised share in a breakdown row is read as a
    literal bracket. So each row gets a sentence, assembled here rather than in
    the template, because the template is HTML and this is language.

    The em dash the shop uses for an absent figure is replaced with the word,
    for the same reason. A reader that pronounces it does so unpredictably, and
    one that skips it leaves a row with a label and no value at all.
    """
    if row.value == NOTHING_TO_SHOW:
        return f"{row.label}: not available"

    spoken = row.value

    for written, said in SPOKEN_AS:
        spoken = spoken.replace(written, said)

    return f"{row.label}: {spoken}"


# What a row's value has to become before it is read aloud, in the order the
# replacements are applied. Order matters in exactly one place and it is the
# first two entries: the opening bracket has to become a comma before the
# closing one is dropped, or a value with no bracketed part at all would lose a
# character it needed.
#
# The currency symbol is left alone on purpose. Every screen reader in use
# pronounces it correctly, and a shop that spelled it out would have the figure
# read as "pounds sixty four point zero zero" - the unit in the wrong place, in
# a sentence about money, to the one reader who cannot check it against the
# screen.
SPOKEN_AS: tuple[tuple[str, str], ...] = (
    (" (", ", "),
    (")", ""),
    ("%", " percent"),
    ("—", "not available")
)


def as_plain_text(statement: MonthlyStatement) -> str:
    """The statement as the monthly email sends it.

    The same rows, aligned into two columns with dots between them, which is
    how a statement has looked in a fixed-width font since long before this
    shop existed. The alignment is computed from the rows rather than fixed,
    because a label's length is a property of the month - a category name can
    be twice the length of "Purchases" - and a column width chosen in advance
    is a column that wraps on the month somebody screenshots.

    Emphasis is carried into the text as a blank line above the row rather than
    dropped. An email cannot embolden in plain text, and a statement whose
    headline is indistinguishable from its rows is a statement read from the
    bottom.
    """
    sections = statement_sections(statement)
    widest = max(
        (len(row.label) for section in sections for row in section.rows), default=0
    )
    lines: list[str] = [statement.period.title.upper(), ""]

    for section in sections:
        if lines[-1] != "":
            lines.append("")

        lines.append(section.name.upper())

        for row in section.rows:
            padding = "." * max(1, widest - len(row.label) + 3)
            lines.append(f"{row.label} {padding} {row.value}")

    return "\n".join(lines)


def as_csv_rows(statement: MonthlyStatement) -> list[tuple[str, str, str]]:
    """The statement as the download gives it, one row per line.

    Tuples rather than formatted CSV text, because quoting is the caller's
    problem and the caller is the one that knows the dialect. A module that
    emitted its own commas would emit them wrong for the one shopper whose
    spreadsheet expects semicolons, and would emit them wrong silently.

    Three columns rather than two: the section, the label, the value. A
    spreadsheet has no headings, so a section name that had been dropped would
    leave "Home" and "£50 to £100" in one column with nothing saying that one
    is a shelf and the other a size - and a shopper sorting that column gets a
    list that means nothing.

    The values are the formatted ones, deliberately. A download is read by a
    person far more often than by a program, and a column of raw pence is a
    column every reader has to divide by a hundred before it means anything. A
    shopper who wants the arithmetic has the headline.
    """
    return [
        STATEMENT_COLUMNS,
        *(
            (section.name, row.label, row.value)
            for section in statement_sections(statement)
            for row in section.rows
        )
    ]


def as_html(statement: MonthlyStatement) -> str:
    """The panel as the account page draws it.

    A definition list per section rather than a table, because that is what
    this is: each row is a term and its value, and a table promises columns
    that relate to each other down the page. Nothing here does - "Largest
    purchase" and "Books" are not two entries in the same series, and a screen
    reader announcing them as a table says so twice per row.

    Every string that reaches the markup goes through `_escaped`, including the
    ones this module composed itself. Not because a category name is likely to
    contain a bracket, but because "this one is ours" is the reasoning that
    lets the one that is not ours through - and the categories do come from a
    catalogue somebody else edits.
    """
    parts = ['<section class="monthly-statement">']
    parts.append(f"<h2>{_escaped(statement.period.title)}</h2>")
    parts.append(
        f'<p class="statement-summary">{_escaped(describe_month(statement))}</p>'
    )

    for section in statement_sections(statement):
        parts.append('<div class="statement-section">')
        parts.append(f"<h3>{_escaped(section.name)}</h3>")
        parts.append("<dl>")

        for row in section.rows:
            parts.append(_as_markup(row))

        parts.append("</dl>")
        parts.append("</div>")

    parts.append("</section>")

    return "\n".join(parts)


def _as_markup(row: StatementRow) -> str:
    """One row as its term and its value.

    The accessible sentence goes on the pair's wrapper as a label, not as a
    title attribute: a title is shown on hover, which a shopper reading with
    their eyes does not want and a shopper reading with a screen reader never
    triggers. It is the one attribute here that exists for somebody who cannot
    see the row.
    """
    emphasis = ' class="emphasis"' if row.emphasis else ""

    return (
        f'<div class="statement-row" aria-label="{_escaped(accessible_label(row))}">'
        f"<dt{emphasis}>{_escaped(row.label)}</dt>"
        f"<dd{emphasis}>{_escaped(row.value)}</dd>"
        f"</div>"
    )


def _escaped(text: str) -> str:
    """The five characters that must not reach markup as themselves.

    Ampersand first, and that ordering is the whole function. Escaping it after
    the others would escape the ampersands they just introduced, and the page
    would render `&amp;lt;` - which is the bug that makes a reader think
    escaping is not happening when it is happening twice.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


@dataclass(frozen=True)
class StatementProblem:
    """Something about a statement that does not hold together.

    Carried rather than raised, and that is the distinction this type exists
    for. A statement that cannot be built raises - there is no document, and
    the page turns that into a failed request. A statement that was built and
    then found to disagree with itself is a different situation: the document
    exists, the shopper is looking at it, and refusing to show it at that point
    replaces a wrong figure with a blank panel without telling anybody why.

    So the problems are collected, logged, and the statement is shown. What
    makes that defensible rather than negligent is that they are collected at
    all: a shop that checks and records is a shop that finds out on the day
    rather than in a support queue six weeks later.
    """

    what: str
    detail: str


def problems_with(statement: MonthlyStatement) -> list[StatementProblem]:
    """Every way this statement disagrees with itself.

    All of them, rather than the first. A statement whose breakdown is wrong is
    quite likely to have a wrong net as well, and a check that stopped at the
    first would report the same one every month while the second went on being
    wrong. The cost of collecting them all is a few comparisons on a document
    that has already been assembled.

    Ordered by how badly each would mislead a reader, worst first, because the
    log line that gets read is the first one.
    """
    found: list[StatementProblem] = []

    if not reconciles(statement):
        found.append(
            StatementProblem(
                what="breakdown does not sum to the headline",
                detail=(
                    f"headline {format_money(statement.headline_cents)}, "
                    f"bands {format_money(_banded_total(statement))}, "
                    f"categories {format_money(_categorised_total(statement))}"
                )
            )
        )

    if statement.net_cents > statement.headline_cents:
        found.append(
            StatementProblem(
                what="net is above the headline",
                detail=(
                    f"net {format_money(statement.net_cents)} against "
                    f"{format_money(statement.headline_cents)} spent, which "
                    f"means a negative refund total"
                )
            )
        )

    if statement.purchase_count > 0 and statement.biggest_cents < statement.smallest_cents:
        found.append(
            StatementProblem(
                what="largest purchase is below the smallest",
                detail=(
                    f"largest {format_money(statement.biggest_cents)}, "
                    f"smallest {format_money(statement.smallest_cents)}"
                )
            )
        )

    if statement.purchase_count > 0 and not (
        statement.smallest_cents <= statement.mean_cents <= statement.biggest_cents
    ):
        found.append(
            StatementProblem(
                what="average sits outside the range it averages",
                detail=(
                    f"average {format_money(statement.mean_cents)} is not "
                    f"between {format_money(statement.smallest_cents)} and "
                    f"{format_money(statement.biggest_cents)}"
                )
            )
        )

    found.extend(_problems_with_the_shares(statement))

    return found


def _problems_with_the_shares(
    statement: MonthlyStatement
) -> list[StatementProblem]:
    """Whether the percentages a reader will add up actually add up.

    Checked against the unrounded shares rather than the printed ones, and the
    tolerance is what makes that honest: floating-point division of pence will
    not sum to exactly one, and a check demanding that it does would fire every
    month on a statement that is perfectly correct. What it is looking for is a
    breakdown that has genuinely lost a purchase, which misses by percent
    rather than by a rounding error.

    A month that spent nothing has no shares to check and is not a problem. It
    is a shopper who bought nothing, which is allowed.
    """
    if statement.headline_cents == 0:
        return []

    found: list[StatementProblem] = []
    banded_share = sum(summary.share_of_month for summary in statement.bands)
    categorised_share = sum(
        summary.share_of_month for summary in statement.categories
    )

    for named, share in (("band", banded_share), ("category", categorised_share)):
        if abs(share - 1.0) > _SHARES_MAY_MISS_BY:
            found.append(
                StatementProblem(
                    what=f"{named} shares do not make a whole",
                    detail=f"they come to {format_share(share)}"
                )
            )

    return found


# How far the shares may miss a whole before it means something. A penny in a
# hundred pounds is a rounding error; a percent is a lost purchase.
_SHARES_MAY_MISS_BY = 0.005


def _banded_total(statement: MonthlyStatement) -> int:
    return sum(summary.total_cents for summary in statement.bands)


def _categorised_total(statement: MonthlyStatement) -> int:
    return sum(summary.total_cents for summary in statement.categories)


def compare_statements(before: MonthlyStatement,
                       after: MonthlyStatement) -> str:
    """Two months, said as the sentence the monthly email opens with.

    Between two assembled statements rather than between two accounts, because
    a month and the statement of that month are not the same thing: the
    statement is what the shopper was shown, and a comparison recomputed from
    the history would quietly describe a different pair of months if any figure
    on the panel had changed in the meantime.

    Spending is compared before anything else, and only one other thing is
    mentioned. An email that listed every movement would be a second statement,
    and the reader already has the first.
    """
    difference = after.headline_cents - before.headline_cents

    if difference == 0:
        return (
            f"Exactly the same as last month: "
            f"{format_money(after.headline_cents)}."
        )

    direction = "more" if difference > 0 else "less"
    share = _share_of(abs(difference), before.headline_cents)
    opening = (
        f"{format_money(abs(difference))} {direction} than last month"
        if share == 0
        else f"{format_share(share)} {direction} than last month"
    )

    return f"{opening}. {_what_moved_between(before, after)}"


def _what_moved_between(before: MonthlyStatement,
                        after: MonthlyStatement) -> str:
    """The one category that changed most between two months.

    One, and the largest mover by money rather than by share. A category that
    went from fifty pence to five pounds has moved by nine hundred percent and
    is not worth a sentence; one that went from three hundred pounds to four
    hundred has moved by a third and is the reason this month cost what it did.

    Falls back to saying nothing moved much rather than naming a category that
    barely moved, because a sentence naming a trivial change teaches the reader
    that the sentence is generated rather than observed.
    """
    was = {summary.name: summary.total_cents for summary in before.categories}
    movements = [
        (summary.name, summary.total_cents - was.get(summary.name, 0))
        for summary in after.categories
    ]
    largest = max(movements, key=lambda moved: abs(moved[1]), default=("", 0))

    if largest[1] == 0:
        return "Nothing in particular moved."

    name, moved = largest
    direction = "up" if moved > 0 else "down"

    return f"{name} was {direction} {format_money(abs(moved))}."


def reconciles(statement: MonthlyStatement) -> bool:
    """Whether the breakdown adds up to the headline.

    Not a test - a check the shop runs in production, on every statement, and
    logs when it fails. A breakdown that does not sum to its own headline is
    the single most damaging thing this panel can print: every figure on it is
    plausible, none of them is flagged, and the shopper is the one who finds it.

    Both breakdowns are checked against the same total, because they partition
    the same purchases two ways and either partition can be the broken one. The
    bands are tiled and cannot lose a purchase; the categories can, which is
    exactly what `UNCLASSIFIED` is for, and this is what proves it caught them.
    """
    banded = sum(summary.total_cents for summary in statement.bands)
    categorised = sum(summary.total_cents for summary in statement.categories)

    return banded == statement.headline_cents == categorised
