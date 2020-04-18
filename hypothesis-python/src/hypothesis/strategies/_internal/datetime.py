# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Most of this work is copyright (C) 2013-2020 David R. MacIver
# (david@drmaciver.com), but it contains contributions by others. See
# CONTRIBUTING.rst for a full list of people who may hold copyright, and
# consult the git log if you need to determine who owns an individual
# contribution.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.
#
# END HEADER

import datetime as dt
import functools
from calendar import monthrange
from typing import Optional

from hypothesis.errors import InvalidArgument
from hypothesis.internal.conjecture import utils
from hypothesis.internal.validation import check_type, check_valid_interval
from hypothesis.strategies._internal.core import (
    defines_strategy_with_reusable_values,
    deprecated_posargs,
    just,
    none,
)
from hypothesis.strategies._internal.strategies import SearchStrategy

DATENAMES = ("year", "month", "day")
TIMENAMES = ("hour", "minute", "second", "microsecond")
DRAW_NAIVE_DATETIME_PART = utils.calc_label_from_name("draw naive part of a datetime")


def is_pytz_timezone(tz):
    if not isinstance(tz, dt.tzinfo):
        return False
    module = type(tz).__module__
    return module == "pytz" or module.startswith("pytz.")


def replace_tzinfo(value, timezone):
    if is_pytz_timezone(timezone):
        # Pytz timezones are a little complicated, and using the .replace method
        # can cause some wierd issues, so we use their special "localise" instead.
        #
        # We use the fold attribute as a convenient boolean for is_dst, even though
        # they're semantically distinct.  For ambiguous or imaginary hours, fold says
        # whether you should use the offset that applies before the gap (fold=0) or
        # the offset that applies after the gap (fold=1). is_dst says whether you
        # should choose the side that is "DST" or "STD" (STD->STD or DST->DST
        # transitions are unclear as you might expect).
        #
        # WARNING: this is INCORRECT for timezones with negative DST offsets such as
        #       "Europe/Dublin", but it's unclear what we could do instead beyond
        #       documenting the problem and recommending use of `dateutil` instead.
        #
        # TODO: after dropping Python 3.5 support we won't need the getattr
        return timezone.localize(value, is_dst=not getattr(value, "fold", 0))
    return value.replace(tzinfo=timezone)


def datetime_does_not_exist(value):
    # This function tests whether the given datetime can be round-tripped to and
    # from UTC.  It is an exact inverse of (and very similar to) the dateutil method
    # https://dateutil.readthedocs.io/en/stable/tz.html#dateutil.tz.datetime_exists
    try:
        # Does the naive portion of the datetime change when round-tripped to
        # UTC?  If so, or if this overflows, we say that it does not exist.
        roundtrip = value.astimezone(dt.timezone.utc).astimezone(value.tzinfo)
    except OverflowError:
        # Overflows at datetime.min or datetime.max boundary condition.
        # Rejecting these is acceptable, because timezones are close to
        # meaningless before ~1900 and subject to a lot of change by
        # 9999, so it should be a very small fraction of possible values.
        return True
    assert value.tzinfo is roundtrip.tzinfo, "so only the naive portions are compared"
    return value != roundtrip


def datetime_is_ambiguous(value):
    # If the same value with fold=0 and fold=1 map to differnt UTC times...
    if not hasattr(value, "fold"):
        return False  # remove at Python 3.5 EOL
    fold_0 = value.replace(fold=0).astimezone(dt.timezone.utc)
    fold_1 = value.replace(fold=1).astimezone(dt.timezone.utc)
    return fold_0 != fold_1


def _leapsec(year, month):
    day = {6: 30, 12: 31}[month]
    return dt.datetime(year, month, day, 23, 59, 59, tzinfo=dt.timezone.utc)


@functools.lru_cache()
def get_leap_seconds():
    # We should really get this from the OS or a package like pytz or dateutil,
    # but since we'll want a hardcoded fallback regardless here goes.
    # Data from https://en.wikipedia.org/wiki/Leap_second
    leapsecs = (
        _leapsec(1972, 6),
        _leapsec(1972, 12),
        _leapsec(1973, 12),
        _leapsec(1974, 12),
        _leapsec(1975, 12),
        _leapsec(1976, 12),
        _leapsec(1977, 12),
        _leapsec(1978, 12),
        _leapsec(1979, 12),
        _leapsec(1981, 6),
        _leapsec(1982, 6),
        _leapsec(1983, 6),
        _leapsec(1985, 6),
        _leapsec(1987, 12),
        _leapsec(1989, 12),
        _leapsec(1990, 12),
        _leapsec(1992, 6),
        _leapsec(1993, 6),
        _leapsec(1994, 6),
        _leapsec(1995, 12),
        _leapsec(1997, 6),
        _leapsec(1998, 12),
        _leapsec(2005, 12),
        _leapsec(2008, 12),
        _leapsec(2012, 6),
        _leapsec(2015, 6),
        _leapsec(2016, 12),
    )
    # TODO: get the data at runtime, compare to hardcoded list in tests.
    assert leapsecs == tuple(sorted(leapsecs))
    assert len(leapsecs) == 27
    return leapsecs


def datetime_in_leap_smear(value):
    # We assume the mostly-consensus smear period of 24 hours centered on the leap
    # second, for simplicity.  Because we anticipate that most bugs derive from
    # comparison of durations spanning a leap second, this shouldn't make much
    # difference (empirical studies to improve on this intuition are welcome).
    #
    # TODO: get leap second list from somewhere.
    return any(
        abs(value - leap) < dt.timedelta(hours=12) for leap in get_leap_seconds()
    )


def datetime_is_nasty(value):
    return (
        datetime_does_not_exist(value)
        or datetime_is_ambiguous(value)
        or datetime_in_leap_smear(value)
    )


def get_nasty_bounds(lo, mid, hi):
    # If (lo, mid) or (mid, hi) certainly contain a nasty datetime, return the
    # interval which spans 2000-01-01; else return (None, None)
    ordered = [(lo, mid), (mid, hi)]
    if mid.replace(tzinfo=None) <= dt.datetime(2000, 1, 1):
        ordered = ordered[::-1]
    # You might think that, because the leap smears are 24hrs and imaginary or
    # ambiguous times are typically only one hour the leap smears would be much
    # more likely - no so!  By the last few steps of our binary search we usually
    # have only only option, and shorter intervals just take a few more steps.
    for min_, max_ in ordered:
        if mid.tzinfo.utcoffset(min_) != mid.tzinfo.utcoffset(max_) or any(
            min_.astimezone(dt.timezone.utc) < leap - dt.timedelta(hours=12)
            and leap + dt.timedelta(hours=12) < max_.astimezone(dt.timezone.utc)
            for leap in get_leap_seconds()
        ):
            return (min_, max_)
    return (None, None)


def draw_capped_multipart(data, min_value, max_value, forced=None):
    assert isinstance(min_value, (dt.date, dt.time, dt.datetime))
    assert type(min_value) == type(max_value)
    assert min_value <= max_value
    forced = forced or {}
    result = {}
    cap_low, cap_high = True, True
    duration_names_by_type = {
        dt.date: DATENAMES,
        dt.time: TIMENAMES,
        dt.datetime: DATENAMES + TIMENAMES,
    }
    for name in duration_names_by_type[type(min_value)]:
        low = getattr(min_value if cap_low else dt.datetime.min, name)
        high = getattr(max_value if cap_high else dt.datetime.max, name)
        if name == "day" and not cap_high:
            _, high = monthrange(**result)
        if name == "year":
            val = utils.integer_range(data, low, high, 2000, forced=forced.get("year"))
        else:
            val = utils.integer_range(data, low, high, forced=forced.get(name))
        result[name] = val
        cap_low = cap_low and val == low
        cap_high = cap_high and val == high
    if hasattr(min_value, "fold"):
        # The `fold` attribute is ignored in comparison of naive datetimes.
        # In tz-aware datetimes it would require *very* invasive changes to
        # the logic above, and be very sensitive to the specific timezone
        # (at the cost of efficient shrinking and mutation), so at least for
        # now we stick with the status quo and generate it independently.
        result["fold"] = utils.integer_range(data, 0, 1)
    return result


class DatetimeStrategy(SearchStrategy):
    def __init__(self, min_value, max_value, timezones_strat, allow_imaginary):
        assert isinstance(min_value, dt.datetime)
        assert isinstance(max_value, dt.datetime)
        assert min_value.tzinfo is None
        assert max_value.tzinfo is None
        assert min_value <= max_value
        assert isinstance(timezones_strat, SearchStrategy)
        assert isinstance(allow_imaginary, bool)
        self.min_value = min_value
        self.max_value = max_value
        self.tz_strat = timezones_strat
        self.allow_imaginary = allow_imaginary

    def do_draw(self, data):
        # We start by drawing a timezone, and an initial datetime.
        tz = data.draw(self.tz_strat)

        if tz is None:
            return self.draw_naive_datetime_and_combine(
                data, tz, self.min_value, self.max_value
            )

        # One-in-four times, we try a more complicated draw with the goal of finding
        # an ambiguous or imaginary time, or a time with a "leap smear"
        # See https://developers.google.com/time/smear for details on leap smear.
        try_to_be_nasty = data.draw_bits(2) == 1

        data.start_example(DRAW_NAIVE_DATETIME_PART)
        result = self.draw_naive_datetime_and_combine(
            data,
            tz,
            self.min_value.replace(tzinfo=tz),
            self.max_value.replace(tzinfo=tz),
        )
        lo, hi = get_nasty_bounds(
            self.min_value.replace(tzinfo=tz), result, self.max_value.replace(tzinfo=tz)
        )

        if (not try_to_be_nasty) or datetime_is_nasty(result) or lo is None:
            # Either (a) we're not being nasty, (b) we got very lucky, or
            # (c) we're replaying or mutating based on the loop below.
            data.stop_example()
        else:
            data.stop_example(discard=True)
            # OK: here we binary-search for a nasty
            while not datetime_is_nasty(result):
                data.start_example(DRAW_NAIVE_DATETIME_PART)
                result = self.draw_naive_datetime_and_combine(data, tz, lo, hi)
                data.stop_example(discard=True)
                lo, hi = get_nasty_bounds(lo, result, hi)
                assert lo <= hi
            data.start_example(DRAW_NAIVE_DATETIME_PART)
            result = self.draw_naive_datetime_and_combine(
                data,
                tz,
                self.min_value,
                self.max_value,
                forced={k: getattr(result, k) for k in DATENAMES + TIMENAMES},
            )
            data.stop_example()

        # If we happened to end up with a disallowed imaginary time, reject it.
        if (not self.allow_imaginary) and datetime_does_not_exist(result):
            data.mark_invalid()
        return result

    def draw_naive_datetime_and_combine(
        self, data, tz, min_value, max_value, forced=None
    ):
        result = draw_capped_multipart(data, min_value, max_value, forced=forced)
        try:
            return replace_tzinfo(dt.datetime(**result), timezone=tz)
        except (ValueError, OverflowError):
            msg = "Failed to draw a datetime between %r and %r with timezone from %r."
            data.note_event(msg % (min_value, max_value, self.tz_strat))
            data.mark_invalid()


@defines_strategy_with_reusable_values
@deprecated_posargs
def datetimes(
    min_value: dt.datetime = dt.datetime.min,
    max_value: dt.datetime = dt.datetime.max,
    *,
    timezones: SearchStrategy[Optional[dt.tzinfo]] = none(),
    allow_imaginary: bool = True
) -> SearchStrategy[dt.datetime]:
    """datetimes(min_value=datetime.datetime.min, max_value=datetime.datetime.max, *, timezones=none(), allow_imaginary=True)

    A strategy for generating datetimes, which may be timezone-aware.

    This strategy works by drawing a naive datetime between ``min_value``
    and ``max_value``, which must both be naive (have no timezone).

    ``timezones`` must be a strategy that generates either ``None``, for naive
    datetimes, or :class:`~python:datetime.tzinfo` objects for 'aware' datetimes.
    You can construct your own, though we recommend using the :pypi:`dateutil
    <python-dateutil>` package and :func:`hypothesis.extra.dateutil.timezones`
    strategy, and also provide :func:`hypothesis.extra.pytz.timezones`.

    You may pass ``allow_imaginary=False`` to filter out "imaginary" datetimes
    which did not (or will not) occur due to daylight savings, leap seconds,
    timezone and calendar adjustments, etc.  Imaginary datetimes are allowed
    by default, because malformed timestamps are a common source of bugs.
    Note that because :pypi:`pytz` predates :pep:`495`, this does not work
    correctly with timezones that use a negative DST offset (such as
    ``"Europe/Dublin"``).

    Examples from this strategy shrink towards midnight on January 1st 2000,
    local time.
    """
    # Why must bounds be naive?  In principle, we could also write a strategy
    # that took aware bounds, but the API and validation is much harder.
    # If you want to generate datetimes between two particular moments in
    # time I suggest (a) just filtering out-of-bounds values; (b) if bounds
    # are very close, draw a value and subtract its UTC offset, handling
    # overflows and nonexistent times; or (c) do something customised to
    # handle datetimes in e.g. a four-microsecond span which is not
    # representable in UTC.  Handling (d), all of the above, leads to a much
    # more complex API for all users and a useful feature for very few.
    check_type(bool, allow_imaginary, "allow_imaginary")
    check_type(dt.datetime, min_value, "min_value")
    check_type(dt.datetime, max_value, "max_value")
    if min_value.tzinfo is not None:
        raise InvalidArgument("min_value=%r must not have tzinfo" % (min_value,))
    if max_value.tzinfo is not None:
        raise InvalidArgument("max_value=%r must not have tzinfo" % (max_value,))
    check_valid_interval(min_value, max_value, "min_value", "max_value")
    if not isinstance(timezones, SearchStrategy):
        raise InvalidArgument(
            "timezones=%r must be a SearchStrategy that can provide tzinfo "
            "for datetimes (either None or dt.tzinfo objects)" % (timezones,)
        )
    return DatetimeStrategy(min_value, max_value, timezones, allow_imaginary)


class TimeStrategy(SearchStrategy):
    def __init__(self, min_value, max_value, timezones_strat):
        self.min_value = min_value
        self.max_value = max_value
        self.tz_strat = timezones_strat

    def do_draw(self, data):
        result = draw_capped_multipart(data, self.min_value, self.max_value)
        tz = data.draw(self.tz_strat)
        return dt.time(**result, tzinfo=tz)


@defines_strategy_with_reusable_values
@deprecated_posargs
def times(
    min_value: dt.time = dt.time.min,
    max_value: dt.time = dt.time.max,
    *,
    timezones: SearchStrategy[Optional[dt.tzinfo]] = none()
) -> SearchStrategy[dt.time]:
    """times(min_value=datetime.time.min, max_value=datetime.time.max, *, timezones=none())

    A strategy for times between ``min_value`` and ``max_value``.

    The ``timezones`` argument is handled as for :py:func:`datetimes`.

    Examples from this strategy shrink towards midnight, with the timezone
    component shrinking as for the strategy that provided it.
    """
    check_type(dt.time, min_value, "min_value")
    check_type(dt.time, max_value, "max_value")
    if min_value.tzinfo is not None:
        raise InvalidArgument("min_value=%r must not have tzinfo" % min_value)
    if max_value.tzinfo is not None:
        raise InvalidArgument("max_value=%r must not have tzinfo" % max_value)
    check_valid_interval(min_value, max_value, "min_value", "max_value")
    return TimeStrategy(min_value, max_value, timezones)


class DateStrategy(SearchStrategy):
    def __init__(self, min_value, max_value):
        assert isinstance(min_value, dt.date)
        assert isinstance(max_value, dt.date)
        assert min_value < max_value
        self.min_value = min_value
        self.max_value = max_value

    def do_draw(self, data):
        return dt.date(**draw_capped_multipart(data, self.min_value, self.max_value))


@defines_strategy_with_reusable_values
def dates(
    min_value: dt.date = dt.date.min, max_value: dt.date = dt.date.max
) -> SearchStrategy[dt.date]:
    """dates(min_value=datetime.date.min, max_value=datetime.date.max)

    A strategy for dates between ``min_value`` and ``max_value``.

    Examples from this strategy shrink towards January 1st 2000.
    """
    check_type(dt.date, min_value, "min_value")
    check_type(dt.date, max_value, "max_value")
    check_valid_interval(min_value, max_value, "min_value", "max_value")
    if min_value == max_value:
        return just(min_value)
    return DateStrategy(min_value, max_value)


class TimedeltaStrategy(SearchStrategy):
    def __init__(self, min_value, max_value):
        assert isinstance(min_value, dt.timedelta)
        assert isinstance(max_value, dt.timedelta)
        assert min_value < max_value
        self.min_value = min_value
        self.max_value = max_value

    def do_draw(self, data):
        result = {}
        low_bound = True
        high_bound = True
        for name in ("days", "seconds", "microseconds"):
            low = getattr(self.min_value if low_bound else dt.timedelta.min, name)
            high = getattr(self.max_value if high_bound else dt.timedelta.max, name)
            val = utils.integer_range(data, low, high, 0)
            result[name] = val
            low_bound = low_bound and val == low
            high_bound = high_bound and val == high
        return dt.timedelta(**result)


@defines_strategy_with_reusable_values
def timedeltas(
    min_value: dt.timedelta = dt.timedelta.min,
    max_value: dt.timedelta = dt.timedelta.max,
) -> SearchStrategy[dt.timedelta]:
    """timedeltas(min_value=datetime.timedelta.min, max_value=datetime.timedelta.max)

    A strategy for timedeltas between ``min_value`` and ``max_value``.

    Examples from this strategy shrink towards zero.
    """
    check_type(dt.timedelta, min_value, "min_value")
    check_type(dt.timedelta, max_value, "max_value")
    check_valid_interval(min_value, max_value, "min_value", "max_value")
    if min_value == max_value:
        return just(min_value)
    return TimedeltaStrategy(min_value=min_value, max_value=max_value)
