from datetime import date

SESSION_KEY = 'selected_period'


def _remembered(request) -> dict:
    try:
        stored = request.session.get(SESSION_KEY)
    except Exception:
        return {}
    return stored if isinstance(stored, dict) else {}


def _remember(request, period: str, year: int, month: int) -> None:
    try:
        request.session[SESSION_KEY] = {'period': period, 'year': year, 'month': month}
    except Exception:
        pass


def period_context(request, today=None, allow_future=False) -> dict:
    """Validated month/year (or whole-year) period, plus what the navbar picker needs to render.

    The period is one global choice: an explicit ?period/year/month in the URL wins and is remembered for the session,
    so every month-based page opens on the month picked in the navbar. Bad or missing input falls back to the current
    month, so a hand-edited URL can never break a page.
    """
    from apps.billing.selectors import period_bounds
    today = today or date.today()
    explicit = any(k in request.GET for k in ('period', 'year', 'month'))
    source = request.GET if explicit else _remembered(request)
    period = 'year' if source.get('period') == 'year' else 'month'
    try:
        year = int(source.get('year', today.year))
        month = int(source.get('month', today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    if not 2000 <= year <= today.year + 5:
        year = today.year
    if not 1 <= month <= 12:
        month = today.month
    if not allow_future and (year, month if period == 'month' else 1) > (today.year, today.month if period == 'month' else 1):
        from django.contrib import messages
        messages.warning(request, 'Future periods are not available — showing the current period instead.')
        year, month = today.year, today.month
    if explicit and request.GET.get('period') != 'all':
        _remember(request, period, year, month)
    start, end = period_bounds(year, month if period == 'month' else None)
    label = str(year) if period == 'year' else f'{start:%B %Y}'
    ctx = {'period': period, 'year': year, 'month': month, 'period_start': start, 'period_end': end, 'period_label': label, 'month_choices': [(m, date(2000, m, 1).strftime('%B')) for m in range(1, 13)], 'year_choices': list(range(today.year - 4, today.year + (2 if allow_future else 1)))}
    request.period_ctx = ctx  # the navbar reads it (views may refine it, e.g. the expenses "All time" view)
    return ctx


def selected_year_month(request, today=None) -> tuple[int, int]:
    """The (year, month) the user picked in the navbar, for places that need a month but have no period in the URL
    (a new expense's date, a payout's month, an export's default range). Falls back to the current month."""
    today = today or date.today()
    stored = _remembered(request)
    year, month = stored.get('year'), stored.get('month')
    if not isinstance(year, int) or not 2000 <= year <= today.year + 5:
        return today.year, today.month
    if stored.get('period') == 'year' or not isinstance(month, int) or not 1 <= month <= 12:
        return year, (today.month if year == today.year else 1)
    return year, month


def navbar_period(request) -> dict:
    """The global period shown in the navbar. `aware` is False on pages that are deliberately not month-based."""
    today = date.today()
    ctx = getattr(request, 'period_ctx', None)
    aware = ctx is not None
    if aware:
        period, year, month = ctx['period'], ctx['year'], ctx['month']
    else:
        stored = _remembered(request)
        period = 'year' if stored.get('period') == 'year' else 'month'
        year, month = stored.get('year', today.year), stored.get('month', today.month)
        if not isinstance(year, int) or not 2000 <= year <= today.year + 5:
            year = today.year
        if not isinstance(month, int) or not 1 <= month <= 12:
            month = today.month
    is_all = aware and period == 'all'
    is_year = period == 'year'
    label = 'All time' if is_all else (str(year) if is_year else f'{date(year, month, 1):%B %Y}')
    years = list(range(today.year - 4, today.year + 2))
    if year not in years:
        years = sorted(set(years + [year]))
    return {
        'aware': aware, 'is_all': is_all, 'is_year': is_year, 'year': year, 'month': 0 if is_year else month, 'label': label,
        'is_current': not is_all and not is_year and (year, month) == (today.year, today.month),
        'month_choices': [(m, date(2000, m, 1).strftime('%B')) for m in range(1, 13)], 'year_choices': years,
        'current_year': today.year, 'current_month': today.month,
    }
