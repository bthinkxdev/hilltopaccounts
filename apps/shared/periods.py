from datetime import date

def period_context(request, today=None) -> dict:
    """Validated month/year (or whole-year) period from the query string, plus what the picker needs to render.

    Bad or missing input falls back to the current month, so a hand-edited URL can never break a page.
    """
    from apps.billing.selectors import period_bounds
    today = today or date.today()
    period = 'year' if request.GET.get('period') == 'year' else 'month'
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
    except ValueError:
        year, month = today.year, today.month
    if not 2000 <= year <= today.year + 5:
        year = today.year
    if not 1 <= month <= 12:
        month = today.month
    start, end = period_bounds(year, month if period == 'month' else None)
    label = str(year) if period == 'year' else f'{start:%B %Y}'
    return {'period': period, 'year': year, 'month': month, 'period_start': start, 'period_end': end, 'period_label': label, 'month_choices': [(m, date(2000, m, 1).strftime('%B')) for m in range(1, 13)], 'year_choices': list(range(today.year - 4, today.year + 2))}
