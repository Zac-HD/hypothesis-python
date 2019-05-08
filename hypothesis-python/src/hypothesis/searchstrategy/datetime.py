# coding=utf-8
#
# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Most of this work is copyright (C) 2013-2019 David R. MacIver
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

from __future__ import absolute_import, division, print_function

import datetime as dt
from calendar import monthrange

import hypothesis.internal.conjecture.utils as cu
from hypothesis.searchstrategy.strategies import SearchStrategy

__all__ = ["DateStrategy", "DatetimeStrategy", "TimedeltaStrategy"]

MULTIPLE_DATETIME_LABEL = cu.calc_label_from_name("trying to draw a weird datetime")
SINGLE_DATETIME_LABEL = cu.calc_label_from_name("drawing a single naive datetime")
MICROSECOND = dt.timedelta(microseconds=1)


def add_tz(value, tz):
    assert isinstance(value, dt.datetime)
    if tz is None:
        return value
    assert isinstance(tz, dt.tzinfo)
    if type(tz).__module__.split(".")[0] == "pytz":
        # Can't just construct; see http://pytz.sourceforge.net
        return tz.normalize(tz.localize(value))
    return value.replace(tzinfo=tz)


def interval_contains_interesting_datetimes(d1, d2):
    # TODO: magic by Paul
    return False


def datetime_is_interesting(value):
    assert isinstance(value, dt.datetime)
    assert value.tzinfo is not None
    # TODO: more magic by Paul
    return False


class DatetimeStrategy(SearchStrategy):
    def __init__(self, min_value, max_value, timezones_strat):
        assert isinstance(min_value, dt.datetime)
        assert isinstance(max_value, dt.datetime)
        assert min_value.tzinfo is None
        assert max_value.tzinfo is None
        assert min_value <= max_value
        assert isinstance(timezones_strat, SearchStrategy)
        self.min_dt = min_value
        self.max_dt = max_value
        self.tz_strat = timezones_strat

    @staticmethod
    def _draw_one_naive(data, min_value, max_value):
        assert isinstance(min_value, dt.datetime)
        assert isinstance(max_value, dt.datetime)
        result = dict()
        cap_low, cap_high = True, True
        data.start_example(SINGLE_DATETIME_LABEL)
        for name in ("year", "month", "day", "hour", "minute", "second", "microsecond"):
            low = getattr(min_value if cap_low else dt.datetime.min, name)
            high = getattr(max_value if cap_high else dt.datetime.max, name)
            if name == "year":
                val = cu.integer_range(data, low, high, 2000)
            elif name == "day":
                _, days = monthrange(**result)
                val = cu.integer_range(data, low, max(high, days))
            else:
                val = cu.integer_range(data, low, high)
            result[name] = val
            cap_low = cap_low and val == low
            cap_high = cap_high and val == high
        data.stop_example()
        return dt.datetime(**result)

    def _attempt_interesting_draw(self, data):
        tz = data.draw(self.tz_strat)
        lo = self._draw_one_naive(data, self.min_dt, self.max_dt)
        if tz is None or not cu.boolean(data):
            return lo
        hi = self._draw_one_naive(data, self.min_dt, self.max_dt)
        lo, hi = sorted([add_tz(lo, tz), add_tz(hi, tz)])
        for _ in range(100):
            # We *could* claim that a while-loop here is limited if we run out of
            # microseconds in a 9999-year interval - and the expected log(n) runtime
            # of a partition search - but we cap it at 100 anyway.
            if datetime_is_interesting(lo):
                return lo
            if datetime_is_interesting(hi) or lo > hi:
                return hi
            # We deliberately avoid binary search as this has better best-case
            # performance and we'll get that while shrinking!
            mid = add_tz(self._draw_one_naive(data, lo, hi), tz)
            if interval_contains_interesting_datetimes(lo, mid):
                hi = mid
                lo += MICROSECOND
            else:
                assert interval_contains_interesting_datetimes(mid, hi)
                lo = mid
                hi -= MICROSECOND
        # OK, time to give up and return whatever we have.
        return lo

    def do_draw(self, data):
        for _ in range(3):
            try:
                data.start_example(MULTIPLE_DATETIME_LABEL)
                value = self._attempt_interesting_draw(data)
                data.stop_example(discard=False)
                return value
            except (ValueError, OverflowError):
                data.stop_example(discard=True)
        data.note_event(
            "3 attempts to create a datetime between %r and %r "
            "with timezone from %r failed." % (self.min_dt, self.max_dt, self.tz_strat)
        )
        data.mark_invalid()


class DateStrategy(SearchStrategy):
    def __init__(self, min_value, max_value):
        assert isinstance(min_value, dt.date)
        assert isinstance(max_value, dt.date)
        assert min_value < max_value
        self.min_value = min_value
        self.days_apart = (max_value - min_value).days
        self.center = (dt.date(2000, 1, 1) - min_value).days

    def do_draw(self, data):
        days = cu.integer_range(data, 0, self.days_apart, center=self.center)
        return self.min_value + dt.timedelta(days=days)


class TimedeltaStrategy(SearchStrategy):
    def __init__(self, min_value, max_value):
        assert isinstance(min_value, dt.timedelta)
        assert isinstance(max_value, dt.timedelta)
        assert min_value < max_value
        self.min_value = min_value
        self.max_value = max_value

    def do_draw(self, data):
        result = dict()
        low_bound = True
        high_bound = True
        for name in ("days", "seconds", "microseconds"):
            low = getattr(self.min_value if low_bound else dt.timedelta.min, name)
            high = getattr(self.max_value if high_bound else dt.timedelta.max, name)
            val = cu.integer_range(data, low, high, 0)
            result[name] = val
            low_bound = low_bound and val == low
            high_bound = high_bound and val == high
        return dt.timedelta(**result)
