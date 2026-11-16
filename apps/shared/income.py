"""Which month a collection counts as income of.

Money counts in the month it was actually collected. The one exception is rent (or an advance) paid ahead for a coming
month: that counts in the month it is for. So the income month is the later of the two."""
from datetime import date


def income_month_for(rent_month: date, collected_at: date) -> date:
    """First day of the later of: the month the rent is for, and the month it was collected."""
    return max(rent_month.replace(day=1), collected_at.replace(day=1))
