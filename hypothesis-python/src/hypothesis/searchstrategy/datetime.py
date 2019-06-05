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
import enum

from hypothesis.internal.conjecture import utils
from hypothesis.searchstrategy.strategies import SearchStrategy

try:
    import dateutil
except ImportError:
    dateutil = None

__all__ = ["DateStrategy", "DatetimeStrategy", "TimedeltaStrategy"]


def is_pytz_timezone(tz):
    if not isinstance(tz, dt.tzinfo):
        return False
    module = type(tz).__module__
    return module == "pytz" or module.startswith("pytz.")


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

    def _attempt_one_draw(self, data):
        result = dict()
        cap_low, cap_high = True, True
        for name in ("year", "month", "day", "hour", "minute", "second", "microsecond"):
            low = getattr(self.min_dt if cap_low else dt.datetime.min, name)
            high = getattr(self.max_dt if cap_high else dt.datetime.max, name)
            if name == "year":
                val = utils.integer_range(data, low, high, 2000)
            else:
                val = utils.integer_range(data, low, high)
            result[name] = val
            cap_low = cap_low and val == low
            cap_high = cap_high and val == high
        tz = data.draw(self.tz_strat)
        try:
            result = dt.datetime(**result)
            if is_pytz_timezone(tz):
                # Can't just construct; see http://pytz.sourceforge.net
                return tz.normalize(tz.localize(result))
            return result.replace(tzinfo=tz)
        except (ValueError, OverflowError):
            return None

    def do_draw(self, data):
        for _ in range(3):
            result = self._attempt_one_draw(data)
            if result is not None:
                return result
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
        days = utils.integer_range(data, 0, self.days_apart, center=self.center)
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
            val = utils.integer_range(data, low, high, 0)
            result[name] = val
            low_bound = low_bound and val == low
            high_bound = high_bound and val == high
        return dt.timedelta(**result)


def transition_between(start, end):
    assert isinstance(start, dt.datetime)
    assert isinstance(end, dt.datetime)
    assert start <= end
    return (
        start.utcoffset() != end.utcoffset()
        or start.tzname() != end.tzname()
        or start.dst() != end.dst()
    )


HOUR = dt.timedelta(hours=1)
MINUTE = dt.timedelta(minutes=1)
SECOND = dt.timedelta(seconds=1)


class Weirdness(enum.Flag):
    nothing = 0

    ambiguous = enum.auto()
    imaginary = enum.auto()
    leap_second = enum.auto()

    tzname_not_len_three = enum.auto()
    tzname_not_alpha = enum.auto()
    tzname_not_ascii = enum.auto()

    utcoffset_non_integer_hours = enum.auto()
    utcoffset_non_integer_minutes = enum.auto()
    utcoffset_non_integer_seconds = enum.auto()

    dst_non_integer_hours = enum.auto()
    dst_non_integer_minutes = enum.auto()
    dst_non_integer_seconds = enum.auto()

    @classmethod
    def from_datetime(cls, instance):
        assert isinstance(instance, dt.datetime)
        self = cls.nothing

        if dateutil is not None and dateutil.tz.datetime_ambiguous(instance):
            self |= cls.ambiguous
        if dateutil is not None and not dateutil.tz.datetime_exists(instance):
            self |= cls.imaginary
        if instance.second >= 60:
            self |= cls.leap_second

        tzname = instance.tzname()
        if tzname is not None:
            if len(tzname) != 3:
                self |= cls.tzname_not_len_three
            if not tzname.isalpha():
                self |= cls.tzname_not_alpha
            if not tzname.isascii():
                self |= cls.tzname_not_ascii

        utcoffset = instance.utcoffset()
        if (utcoffset or HOUR) % HOUR:
            self |= cls.utcoffset_non_integer_hours
            if utcoffset % MINUTE:
                self |= cls.utcoffset_non_integer_minutes
                if utcoffset % SECOND:
                    self |= cls.utcoffset_non_integer_seconds

        dst = instance.dst()
        if (dst or HOUR) % HOUR:
            self |= cls.dst_non_integer_hours
            if dst % MINUTE:
                self |= cls.dst_non_integer_minutes
                if dst % SECOND:
                    self |= cls.dst_non_integer_seconds

        return self
