import dateparser
import pytz
from datetime import datetime, timezone, timedelta
from typing import Union
import json
from decimal import *

import numpy as np
import pandas as pd

from binance.helpers import date_to_milliseconds, interval_to_milliseconds


#
# Decimals
#

def to_decimal(value):
    """Convert to a decimal with the required precision. The value can be string, float or decimal."""
    # Possible cases: string, 4.1-e7, float like 0.1999999999999 (=0.2), Decimal('4.1E-7')

    # App.config["trade"]["symbol_info"]["baseAssetPrecision"]

    n = 8
    rr = Decimal(1) / (Decimal(10) ** n)  # Result: 0.00000001
    ret = Decimal(str(value)).quantize(rr, rounding=ROUND_DOWN)
    return ret


def round_str(value, digits):
    rr = Decimal(1) / (Decimal(10) ** digits)  # Result for 8 digits: 0.00000001
    ret = Decimal(str(value)).quantize(rr, rounding=ROUND_HALF_UP)
    return f"{ret:.{digits}f}"


def round_down_str(value, digits):
    rr = Decimal(1) / (Decimal(10) ** digits)  # Result for 8 digits: 0.00000001
    ret = Decimal(str(value)).quantize(rr, rounding=ROUND_DOWN)
    return f"{ret:.{digits}f}"


#
# Date and time
#

def get_interval(freq: str, timestamp: int=None):
    """
    Return a triple of interval start (including), end (excluding) in milliseconds for the specified timestamp or now

    INFO:
    https://github.com/sammchardy/python-binance/blob/master/binance/helpers.py
        interval_to_milliseconds(interval) - binance freq string (like 1m) to millis

    :return: tuple of start (inclusive) and end (exclusive) of the interval in millis
    :rtype: (int, int)
    """
    if not timestamp:
        timestamp = datetime.utcnow()  # datetime.now(timezone.utc)
    elif isinstance(timestamp, int):
        timestamp = pd.to_datetime(timestamp, unit='ms').to_pydatetime()

    # Although in 3.6 (at least), datetime.timestamp() assumes a timezone naive (tzinfo=None) datetime is in UTC
    timestamp = timestamp.replace(microsecond=0, tzinfo=timezone.utc)

    if freq == "1s":
        start = timestamp.timestamp()
        end = timestamp + timedelta(seconds=1)
        end = end.timestamp()
    elif freq == "5s":
        reference_timestamp = timestamp.replace(second=0)
        now_duration = timestamp - reference_timestamp

        freq_duration = timedelta(seconds=5)

        full_intervals_no = now_duration.total_seconds() // freq_duration.total_seconds()

        start = reference_timestamp + freq_duration * full_intervals_no
        end = start + freq_duration

        start = start.timestamp()
        end = end.timestamp()
    elif freq == "1m":
        timestamp = timestamp.replace(second=0)
        start = timestamp.timestamp()
        end = timestamp + timedelta(minutes=1)
        end = end.timestamp()
    elif freq == "5m":
        # Here we need to find 1 h border (or 1 day border) by removing minutes
        # Then divide (now-1hourstart) by 5 min interval length by finding 5 min border for now
        print(f"Frequency 5m not implemented.")
    elif freq == "1h":
        timestamp = timestamp.replace(minute=0, second=0)
        start = timestamp.timestamp()
        end = timestamp + timedelta(hours=1)
        end = end.timestamp()
    else:
        print(f"Unknown frequency.")

    return int(start * 1000), int(end * 1000)


def now_timestamp():
    """
    INFO:
    https://github.com/sammchardy/python-binance/blob/master/binance/helpers.py
    date_to_milliseconds(date_str) - UTC date string to millis

    :return: timestamp in millis
    :rtype: int
    """
    return int(datetime.utcnow().replace(tzinfo=timezone.utc).timestamp() * 1000)


def find_index(df: pd.DataFrame, date_str: str, column_name: str= "timestamp"):
    """
    Return index of the record with the specified datetime string.

    :return: row id in the input data frame which can be then used in iloc function
    :rtype: int
    """
    d = dateparser.parse(date_str)
    try:
        res = df[df[column_name] == d]
    except TypeError:  # "Cannot compare tz-naive and tz-aware datetime-like objects"
        # Change timezone (set UTC timezone or reset timezone)
        if d.tzinfo is None or d.tzinfo.utcoffset(d) is None:
            d = d.replace(tzinfo=pytz.utc)
        else:
            d = d.replace(tzinfo=None)

        # Repeat
        res = df[df[column_name] == d]

    id = res.index[0]

    return id


#
# Depth processing
#

def price_to_volume(side, depth, price_limit):
    """
    Given limit, compute the available volume from the depth data on the specified side.
    The limit is inclusive.
    Bids (buyers) are on the left of X and asks (sellers) are on the right of X.

    :return: volume if limit is in the book and None otherwise
    :rtype: float
    """
    if side == "buy":
        orders = depth.get("asks", [])  # Sellers. Prices increase
        orders = [o for o in orders if o[0] <= price_limit]  # Select low prices
    elif side == "sell":
        orders = depth.get("bids", [])  # Buyers. Prices decrease
        orders = [o for o in orders if o[0] >= price_limit]  # Select high prices
    else:
        return None

    return orders[-1][1]  # Last element contains cumulative volume


def volume_to_price(side, depth, volume_limit):
    """
    Given volume, compute the corresponding limit from the depth data on the specified side.

    :return: limit if volume is available in book and None otherwise
    :rtype: float
    """
    if side == "buy":
        orders = depth.get("asks", [])  # Sellers. Prices increase
    elif side == "sell":
        orders = depth.get("bids", [])  # Buyers. Prices decrease
    else:
        return None

    orders = [o for o in orders if o[1] <= volume_limit]
    return orders[-1][0]  # Last element contains cumulative volume


def depth_accumulate(depth: list, start, end):
    """
    Convert a list of bid/ask volumes into an accumulated (monotonically increasing) volume curve.
    The result is the same list but each volume value in the pair is the sum of all previous volumes.
    For the very first bid/ask, the volume is that same.
    """
    prev_value = 0.0
    for point in depth:
        point[1] += prev_value
        prev_value = point[1]

    return depth


def discretize(side: str, depth: list, bin_size: float, start: float):
    """
    Main problem: current point can contribute to this bin (till bin end) and next bin (from bin end till next point)
    Iterate over bins. For each iteration, initial function value must be provided which works till first point or end
      With each bin iteration, iterate over points (global pointer).
      If point within this bin, the set current volume instead of initial, and compute contribution of the previous value
      If point in next bin, then still use current volume for the next bin, compute contribution till end only. Do not iterate point (it is needed when starting next bin)
        When we start next bin, compute contribution

    :param side:
    :param depth:
    :param bin_size:
    :param start:
    :return:
    """
    if side.startswith("ask") or side.startswith("sell"):
        price_increase = True
    elif side in ["bid", "buy"]:
        price_increase = False
    else:
        print("Wrong use. Side is either bid or ask.")

    # Start is either explict or first point
    if start is None:
        start = depth[0][0]  # First point

    # End covers the last point
    bin_count = int(abs(depth[-1][0] - start) // bin_size) + 1
    all_bins_length = bin_count * bin_size
    end = start + all_bins_length if price_increase else start - all_bins_length

    bin_volumes = []
    for b in range(bin_count):
        bin_start = start + b*bin_size if price_increase else start - b*bin_size
        bin_end = bin_start + bin_size if price_increase else bin_start - bin_size

        # Find point ids within this bin
        if price_increase:
            bin_point_ids = [i for i, x in enumerate(depth) if bin_start <= x[0] < bin_end]
        else:
            bin_point_ids = [i for i, x in enumerate(depth) if bin_end < x[0] <= bin_start]

        if bin_point_ids:
            first_point_id = min(bin_point_ids)
            last_point_id = max(bin_point_ids)
            prev_point = depth[first_point_id-1] if first_point_id >= 1 else None
        else:
            first_point_id = None
            last_point_id = None

        #
        # Iterate over points in this bin by collecting their contribution using previous interval
        #
        prev_price = bin_start
        prev_volume = prev_point[1] if prev_point else 0.0
        bin_volume = 0.0

        if first_point_id is None:  # Bin is empty
            # Update current bin volume
            price = bin_end
            price_delta = abs(price - prev_price)
            price_coeff = price_delta / bin_size  # Portion of this interval in bin
            bin_volume += prev_volume * price_coeff  # Each point in the bin contributes to this bin final value

            # Store current bin as finished
            bin_volumes.append(bin_volume)

            continue

        # Bin is not empty
        for point_id in range(first_point_id, last_point_id+1):
            point = depth[point_id]

            # Update current bin volume
            price = point[0]
            price_delta = abs(price - prev_price)
            price_coeff = price_delta / bin_size  # Portion of this interval in bin
            bin_volume += prev_volume * price_coeff  # Each point in the bin contributes to this bin final value

            # Iterate
            prev_price = point[0]
            prev_volume = point[1]
            prev_point = point
        #
        # Last point contributes till the end of this bin
        #
        # Update current bin volume
        price = bin_end
        price_delta = abs(price - prev_price)
        price_coeff = price_delta / bin_size  # Portion of this interval in bin
        bin_volume += prev_volume * price_coeff  # Each point in the bin contributes to this bin final value

        # Store current bin as finished
        bin_volumes.append(bin_volume)

    return bin_volumes


# OBSOLETE: Because works only for increasing prices (ask). Use general version instead.
def discretize_ask(depth: list, bin_size: float, start: float):
    """
    Find (volume) area between the specified interval (of prices) given the step function volume(price).

    The step-function is represented as list of points (price,volume) ordered by price.
    Volume is the function value for the next step (next price delta - not previous one). A point specifies volume till the next point.

    One bin has coefficient 1 and then all sub-intervals within one bin are coefficients to volume

    Criterion: whole volume area computed for the input data and output data (for the same price interval) must be the same

    side: "ask" (prices in depth list increase) or "bid" (prices in depth list decrease)

    TODO: It works only for increasing prices (asks). It is necessary to make it work also for decreasing prices.
    TODO: it does not work if start is after first point (only if before or equal/none)
    """
    if start is None:
        start = depth[0][0]  # First point

    prev_point = [start, 0.0]

    bin_start = start
    bin_end = bin_start + bin_size
    bin_volume = 0.0

    bin_volumes = []
    for i, point in enumerate(depth):
        if point[0] <= bin_start:  # Point belongs to previous bin (when start is in the middle of series)
            prev_point = point
            continue

        if point[0] >= bin_end:  # Point in the next bin
            price = bin_end
        else:  # Point within bin
            price = point[0]

        # Update current bin volume
        price_delta = abs(price - prev_point[0])
        price_coeff = price_delta / bin_size  # Portion of this interval in bin
        bin_volume += prev_point[1] * price_coeff  # Each point in the bin contributes to this bin final value

        # Iterate bin (if current is finished)
        if point[0] >= bin_end:  # Point in the next bin
            # Store current bin as finished
            bin_volumes.append(bin_volume)
            # Iterate to next bin
            bin_start = bin_end
            bin_end = bin_start + bin_size
            bin_volume = 0.0

            price = point[0]

            # Initialize bin volume with the rest of current point
            price_delta = abs(price - bin_start)
            price_coeff = price_delta / bin_size  # Portion of this interval in bin
            bin_volume += prev_point[1] * price_coeff  # Each point in the bin contributes to this bin final value

        # Iterate point
        prev_point = point

    #
    # Finalize by closing last bin which does not have enough points
    #
    price = bin_end

    # Update current bin volume
    price_delta = abs(price - prev_point[0])
    price_coeff = price_delta / bin_size  # Portion of this interval in bin
    bin_volume += prev_point[1] * price_coeff  # Each point in the bin contributes to this bin final value

    # Store current bin as finished
    bin_volumes.append(bin_volume)

    return bin_volumes


def mean_volumes(depth: list, windows: list, bin_size: 1.0):
    """
    Density. Mean volume per price unit (bin) computed using the specified number of price bins.
    First, we discreteize and then find average value for the first element (all if length is not specified).
    Return a list of values each value being a mean volume for one aggregation window (number of bins)
    """

    bid_volumes = discretize(side="bid", depth=depth.get("bids"), bin_size=bin_size, start=None)
    ask_volumes = discretize(side="ask", depth=depth.get("asks"), bin_size=bin_size, start=None)

    ret = {}
    for length in windows:
        density = np.nanmean(bid_volumes[0:min(length, len(bid_volumes))])
        feature_name = f"bids_{length}"
        ret[feature_name] = density

        density = np.nanmean(ask_volumes[0:min(length, len(ask_volumes))])
        feature_name = f"asks_{length}"
        ret[feature_name] = density

    return ret


#
# Klnes processing
#

def klines_to_df(klines: list):
    """
    Convert a list of klines to a data frame.
    """
    columns = [
        'timestamp',
        'open', 'high', 'low', 'close', 'volume',
        'close_time',
        'quote_av', 'trades', 'tb_base_av', 'tb_quote_av',
        'ignore'
    ]

    df = pd.DataFrame(klines, columns=columns)

    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')

    df["open"] = pd.to_numeric(df["open"])
    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])
    df["close"] = pd.to_numeric(df["close"])
    df["volume"] = pd.to_numeric(df["volume"])

    df["quote_av"] = pd.to_numeric(df["quote_av"])
    df["trades"] = pd.to_numeric(df["trades"])
    df["tb_base_av"] = pd.to_numeric(df["tb_base_av"])
    df["tb_quote_av"] = pd.to_numeric(df["tb_quote_av"])

    df.set_index('timestamp', inplace=True)

    return df
