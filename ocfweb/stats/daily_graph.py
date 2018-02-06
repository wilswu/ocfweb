import urllib.parse
from datetime import date
from datetime import datetime
from datetime import timedelta

from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from matplotlib.dates import DateFormatter
from matplotlib.figure import Figure
from ocflib.lab.hours import Day
from ocflib.lab.stats import list_desktops
from ocflib.lab.stats import UtilizationProfile

from ocfweb.api.hours import display_hours
from ocfweb.caching import periodic
from ocfweb.component.graph import plot_to_image_bytes


# Binomial-shaped weights for moving average
#AVERAGE_WEIGHTS = tuple(zip(range(-2, 3), (n / 16 for n in (1, 4, 6, 4, 1))))
AVERAGE_WEIGHTS = tuple(zip(range(-2, 3), (n / 16 for n in (0, 0, 16, 0, 0))))


@periodic(60)
def _daily_graph_image(day=None):
    if not day:
        day = date.today()

    return HttpResponse(
        plot_to_image_bytes(get_daily_plot(day), format='svg'),
        content_type='image/svg+xml',
    )


def daily_graph_image(request):
    try:
        day = datetime.strptime(request.GET.get('date', ''), '%Y-%m-%d').date()
    except ValueError:
        day = date.today()

    # redirect to canonical url
    if request.GET.get('date') != day.isoformat():
        return redirect('{}?{}'.format(
            reverse('daily_graph_image'),
            urllib.parse.urlencode({'date': day.isoformat()}),
        ))

    if day == date.today():
        return _daily_graph_image()
    else:
        return _daily_graph_image(day=day)


def get_open_close(day):
    """Return datetime objects representing open and close for a day rounded
    down to the hour.

    If the lab is closed all day (e.g. holiday), just return our weekday hours.
    """
    d = Day.from_date(day)
    regular_hours = display_hours()

    if not d.closed_all_day:
        start = datetime(day.year, day.month, day.day, min(h.open.hour for h in d.hours))
        end = datetime(day.year, day.month, day.day, max(h.close.hour for h in d.hours))
    else:
        start = datetime(
            day.year,
            day.month,
            day.day,
            min(h.open.hour for hour_list in regular_hours.values() for h in hour_list),
        )
        end = datetime(
            day.year,
            day.month,
            day.day,
            max(h.close.hour for hour_list in regular_hours.values() for h in hour_list),
        )

    return start, end


# TODO: caching; we can cache for a long time if it's a day that's already happened
def get_daily_plot(day):
    """Return matplotlib plot representing a day's plot."""
    start, end = get_open_close(day)
    desktops = list_desktops(public_only=True)
    profiles = UtilizationProfile.from_hostnames(desktops, start, end).values()
    desks_count = len(desktops)

    now = datetime.now()
    latest = min(end, now)
    minute = timedelta(minutes=1)
    times = [start + i * minute for i in range((latest - start) // minute + 1)]
    if now >= end or now <= start:
        now = None
    sums = []
    week = timedelta(days=7)
    lw_sums = []

    for t in times:
        instant15 = t + timedelta(seconds=15)
        instant45 = t + timedelta(seconds=45)
        in_use = sum(1 if profile.in_use(instant15) or profile.in_use(instant45) else 0 for profile in profiles)
        lw_in_use = sum(1 if profile.in_use(instant15 - week) or profile.in_use(instant45 - week) else 0 for profile in profiles)
        sums.append(in_use)
        lw_sums.append(lw_in_use)

    # Do a weighted moving average to smooth out the data
    processed = [0] * len(sums)
    for i in range(len(sums)):
        for delta_i, weight in AVERAGE_WEIGHTS:
            m = i if (i + delta_i < 0 or i + delta_i >= len(sums)) else i + delta_i
            # Don't use data that hasn't occurred yet
            if now and times[i] <= now and times[m] >= now:
                processed[i] += weight * sums[i]
            elif now and times[i] > now:
                processed[i] = 0
            else:
                processed[i] += weight * sums[m]

    lw_processed = [0] * len(lw_sums)
    for i in range(len(lw_sums)):
        for delta_i, weight in AVERAGE_WEIGHTS:
            m = i if (i + delta_i < 0 or i + delta_i >= len(lw_sums)) else i + delta_i
            lw_processed[i] += weight * lw_sums[m]

    fig = Figure(figsize=(10, 4))
    ax = fig.add_subplot(1, 1, 1)

    ax.grid(True)
    ax.plot_date(times, processed, fmt='b-', color='k', linewidth=1.5)
    ax.plot_date(times, lw_processed, fmt='r-', color='r', linewidth=1.5)

    # Draw a vertical line, if applicable, showing current time
    if now:
        ax.axvline(now, linewidth=1.5)

    # Draw a horizontal line for total desktops
    ax.axhline(desks_count, ls='dashed')
    ax.annotate('   Total desktops', xy=(start, desks_count + 1))

    ax.xaxis.set_major_formatter(DateFormatter('%-I%P'))
    ax.set_xlim(start, end)

    ax.set_ylim(0, desks_count + 5)
    ax.set_ylabel('Computers in Use')

    ax.set_title('Lab Utilization {:%a %b %d}'.format(day))
    return fig
