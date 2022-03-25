import os
from datetime import datetime, timezone, timedelta
from typing import Union
import json

import numpy as np
import pandas as pd

from common.utils import *
from common.feature_generation_rolling_agg import *

"""
Feature/label generation.
These features are computed using explict transformations.
(True) labels are features computed from future data but stored as properties of the current row (in contrast to normal features which are computed from past data).
(Currently) feature/label generation is not based on (explicit) models - all parameters are hard-coded.
Also, no parameter training is performed.
"""


def generate_features(df,use_differences, base_window, windows, area_windows, last_rows: int = 0):
    """
    Generate derived features by adding them as new columns to the data frame.
    It is important that the same parameters are used for both training and prediction.

    Most features compute rolling aggregation. However, instead of absolute values, the difference
    of this rolling aggregation to the (longer) base rolling aggregation is computed.

    The window sizes are used for encoding feature/column names and might look like 'close_120'
    for average close price for the last 120 minutes (relative to the average base price).
    The column names are needed when preparing data for training or prediction.
    The easiest way to get them is to return from this function and copy and the
    corresponding config attribute.
    """
    features = []
    to_drop = []

    if use_differences:
        df['close'] = to_diff(df['close'])
        df['volume'] = to_diff(df['volume'])
        df['trades'] = to_diff(df['trades'])

    # close rolling mean. format: 'close_<window>'
    weight_column_name = 'volume'  # None: no weighting; 'volume': volume average
    to_drop += add_past_weighted_aggregations(df, 'close', weight_column_name, np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
    features += add_past_weighted_aggregations(df, 'close', weight_column_name, np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # close rolling std. format: 'close_std_<window>'
    to_drop += add_past_aggregations(df, 'close', np.nanstd, base_window, last_rows=last_rows)  # Base column
    features += add_past_aggregations(df, 'close', np.nanstd, windows, '_std', to_drop[-1], 100.0, last_rows=last_rows)

    # volume rolling mean. format: 'volume_<window>'
    to_drop += add_past_aggregations(df, 'volume', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
    features += add_past_aggregations(df, 'volume', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # Span: high-low difference. format: 'span_<window>'
    df['span'] = df['high'] - df['low']
    to_drop.append('span')
    to_drop += add_past_aggregations(df, 'span', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
    features += add_past_aggregations(df, 'span', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # Number of trades format: 'trades_<window>'
    to_drop += add_past_aggregations(df, 'trades', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
    features += add_past_aggregations(df, 'trades', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # tb_base_av / volume varies around 0.5 in base currency. format: 'tb_base_<window>>'
    df['tb_base'] = df['tb_base_av'] / df['volume']
    to_drop.append('tb_base')
    to_drop += add_past_aggregations(df, 'tb_base', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
    features += add_past_aggregations(df, 'tb_base', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # tb_quote_av / quote_av varies around 0.5 in quote currency. format: 'tb_quote_<window>>'
    df['tb_quote'] = df['tb_quote_av'] / df['quote_av']
    to_drop.append('tb_quote')
    to_drop += add_past_aggregations(df, 'tb_quote', np.nanmean, base_window, suffix='', last_rows=last_rows)  # Base column
    features += add_past_aggregations(df, 'tb_quote', np.nanmean, windows, '', to_drop[-1], 100.0, last_rows=last_rows)

    # Area over and under latest close price
    features += add_area_ratio(df, is_future=False, column_name="close", windows=area_windows, suffix = "_area", last_rows=last_rows)

    # Linear trend
    features += add_linear_trends(df, is_future=False, column_name="close", windows=windows, suffix="_trend", last_rows=last_rows)

    df.drop(columns=to_drop, inplace=True)

    return features


def generate_features_futur(df, use_differences=False):
    """
    Generate derived features for futures.
    """
    # Parameters of moving averages
    windows = [1, 2, 5, 20, 60, 180]
    base_window = 360

    features = []
    to_drop = []

    if use_differences:
        df['f_close'] = to_diff(df['f_close'])
        df['f_volume'] = to_diff(df['f_volume'])
        df['f_trades'] = to_diff(df['f_trades'])

    # close mean
    weight_column_name = 'f_volume'  # None: no weighting; 'volume': volume average
    to_drop += add_past_weighted_aggregations(df, 'f_close', weight_column_name, np.nanmean, base_window, suffix='')  # Base column
    features += add_past_weighted_aggregations(df, 'f_close', weight_column_name, np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['f_close_1', f_close_2', 'f_close_5', 'f_close_10', 'f_close_20']

    # close std
    to_drop += add_past_aggregations(df, 'f_close', np.nanstd, base_window)  # Base column
    features += add_past_aggregations(df, 'f_close', np.nanstd, windows[1:], '_std', to_drop[-1], 100.0)  # window 1 excluded
    # ['f_close_std_1', f_close_std_2', 'f_close_std_5', 'f_close_std_10', 'f_close_std_20']

    # volume mean
    to_drop += add_past_aggregations(df, 'f_volume', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'f_volume', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['f_volume_1', 'f_volume_2', 'f_volume_5', 'f_volume_10', 'f_volume_20']

    # Span: high-low difference
    df['f_span'] = df['f_high'] - df['f_low']
    to_drop.append('f_span')
    to_drop += add_past_aggregations(df, 'f_span', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'f_span', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['f_span_1', 'f_span_2', 'f_span_5', 'f_span_10', 'f_span_20']

    # Number of trades
    to_drop += add_past_aggregations(df, 'f_trades', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'f_trades', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['f_trades_1', 'f_trades_2', 'f_trades_5', 'f_trades_10', 'f_trades_20']

    # tb_base_av / volume varies around 0.5 in base currency
    #df['f_tb_base'] = df['f_tb_base_av'] / df['f_volume']
    #to_drop.append('f_tb_base')
    #to_drop += add_past_aggregations(df, 'f_tb_base', np.nanmean, base_window, suffix='')  # Base column
    #features += add_past_aggregations(df, 'f_tb_base', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['f_tb_base_1', 'f_tb_base_2', 'f_tb_base_5', 'f_tb_base_10', 'f_tb_base_20']

    # tb_quote_av / quote_av varies around 0.5 in quote currency
    #df['f_tb_quote'] = df['f_tb_quote_av'] / df['f_quote_av']
    #to_drop.append('f_tb_quote')
    #to_drop += add_past_aggregations(df, 'f_tb_quote', np.nanmean, base_window, suffix='')  # Base column
    #features += add_past_aggregations(df, 'f_tb_quote', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['f_tb_quote_1', 'f_tb_quote_2', 'f_tb_quote_5', 'f_tb_quote_10', 'f_tb_quote_20']

    # Area over and under latest close price
    features += add_area_ratio(df, is_future=False, column_name="f_close", windows=[20, 60, 120, 180], suffix = "_area")

    # Linear trend
    features += add_linear_trends(df, is_future=False, column_name="f_close", windows=windows[1:], suffix="_trend")  # window 1 excluded

    df.drop(columns=to_drop, inplace=True)

    return features


def generate_features_depth(df, use_differences=False):
    """
    Generate derived features from depth data.
    Original features:
    - gap, price,
    - bids_1,asks_1,
    - bids_2,asks_2,
    - bids_5,asks_5,
    - bids_10,asks_10,
    - bids_20,asks_20

    Features (33):
    gap_2,gap_5,gap_10,
    bids_1_2,bids_1_5,bids_1_10, asks_1_2,asks_1_5,asks_1_10,
    bids_2_2,bids_2_5,bids_2_10, asks_2_2,asks_2_5,asks_2_10,
    bids_5_2,bids_5_5,bids_5_10, asks_5_2,asks_5_5,asks_5_10,
    bids_10_2,bids_10_5,bids_10_10, asks_10_2,asks_10_5,asks_10_10,
    bids_20_2,bids_20_5,bids_20_10, asks_20_2,asks_20_5,asks_20_10,
    """
    # Parameters of moving averages
    windows = [2, 5, 10]
    base_window = 30

    features = []
    to_drop = []

    # gap mean
    to_drop += add_past_aggregations(df, 'gap', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'gap', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['gap_2', 'gap_5', 'gap_10']


    # bids_1 mean
    to_drop += add_past_aggregations(df, 'bids_1', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_1', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_1_2', 'bids_1_5', 'bids_1_10']
    # asks_1 mean
    to_drop += add_past_aggregations(df, 'asks_1', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_1', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_1_2', 'asks_1_5', 'asks_1_10']


    # bids_2 mean
    to_drop += add_past_aggregations(df, 'bids_2', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_2', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_2_2', 'bids_2_5', 'bids_2_10']
    # asks_2 mean
    to_drop += add_past_aggregations(df, 'asks_2', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_2', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_2_2', 'asks_2_5', 'asks_2_10']


    # bids_5 mean
    to_drop += add_past_aggregations(df, 'bids_5', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_5', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_5_2', 'bids_5_5', 'bids_5_10']
    # asks_5 mean
    to_drop += add_past_aggregations(df, 'asks_5', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_5', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_5_2', 'asks_5_5', 'asks_5_10']


    # bids_10 mean
    to_drop += add_past_aggregations(df, 'bids_10', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_10', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_10_2', 'bids_10_5', 'bids_10_10']
    # asks_10 mean
    to_drop += add_past_aggregations(df, 'asks_10', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_10', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_10_2', 'asks_10_5', 'asks_10_10']


    # bids_20 mean
    to_drop += add_past_aggregations(df, 'bids_20', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'bids_20', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['bids_20_2', 'bids_20_5', 'bids_20_10']
    # asks_20 mean
    to_drop += add_past_aggregations(df, 'asks_20', np.nanmean, base_window, suffix='')  # Base column
    features += add_past_aggregations(df, 'asks_20', np.nanmean, windows, '', to_drop[-1], 100.0)
    # ['asks_20_2', 'asks_20_5', 'asks_20_10']


    df.drop(columns=to_drop, inplace=True)

    return features


def add_threshold_feature(df, column_name: str, thresholds: list, out_names: list):
    """

    :param df:
    :param column_name: Column with values to compare with the thresholds
    :param thresholds: List of thresholds. For each of them an output column will be generated
    :param out_names: List of output column names (same length as thresholds)
    :return: List of output column names
    """

    for i, threshold in enumerate(thresholds):
        out_name = out_names[i]
        if threshold > 0.0:  # Max high
            if abs(threshold) >= 0.75:  # Large threshold
                df[out_name] = df[column_name] >= threshold  # At least one high is greater than the threshold
            else:  # Small threshold
                df[out_name] = df[column_name] <= threshold  # All highs are less than the threshold
        else:  # Min low
            if abs(threshold) >= 0.75:  # Large negative threshold
                df[out_name] = df[column_name] <= threshold  # At least one low is less than the (negative) threshold
            else:  # Small threshold
                df[out_name] = df[column_name] >= threshold  # All lows are greater than the (negative) threshold

    return out_names


def depth_to_df(depth: list):
    """
    Input is a list of json objects each representing current market depth with a list bids and asks
    The method computes features from the market depth and returns a data frame with the corresponding columns.

    NOTE:
    - Important: "timestamp" is real time of the depth data which corresponds to "close_time" in klines
      "timestamp" in klines is 1m before current time
      It has to be taken into account when matching/joining records, e.g., by shifting columns (if we match "timestamp" then the reslt will be wrong)
    - data frame index is continuous and may contain gaps. its start is first line and end is last line

    # TODO Questions:
    # !!! - what is zone for our timestamps - ensure that it is the same as Binance server
    # - is it possible to create a data frame with a column containing json object or string?
    # - how to match json/string values with data frame index?
    """
    bin_size = 1.0  # In USDT
    windows = [1, 2, 5, 10, 20]  # No of price bins for aggregate/smoothing

    #
    # Generate a table with feature records
    #
    table = []
    for entry in depth:
        record = depth_to_features(entry, windows, bin_size)
        table.append(record)

    #
    # Convert json table to data frame
    #
    df = pd.DataFrame.from_dict(table)
    # Alternatively, from_records() or json_normalize()

    # Timestamp is an index
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit='ms')
    df = df.set_index("timestamp")
    df = df.sort_index()

    #
    # Find start and end dates
    #
    # NOTE: timestamp is request time (in our implementation) and hence it is end of 1m interval while kline id is start of 1m inteval
    #  It is important for matching, so maybe align this difference here by shifting data
    start_line = depth[0]
    end_line = depth[-1]
    start_ts = start_line.get("timestamp")
    #start_ts -= 60_000  # To ensure that we do not lose any data
    end_ts = end_line.get("timestamp")
    #end_ts += 60_000  # To ensure that we do not lose any data

    #
    # Create index for this interval of timestamps
    #
    # NOTE: Add utc=True to get tz-aware object (with tz="UTC" instead of tz-unaware object with tz=None), so it seems that no tz means UTC
    start = pd.to_datetime(start_ts, unit='ms')
    end = pd.to_datetime(end_ts, unit='ms')

    # Alternatively:
    # If tz is not specified then 1 hour difference will be added so it seems that no tz means locale tz
    #datetime.fromtimestamp(float(start_ts) / 1e3, tz=pytz.UTC)

    # Create DatetimeIndex
    # NOTE: if tz is not specified then the index is tz-naive
    #   closed can be specified (which side to include/exclude: left, right or both). it influences if we want ot include/exclude start or end of the interval
    index = pd.date_range(start, end, freq="T")
    df_out = pd.DataFrame(index=index)

    #
    # Join data with this empty index (to ensure continuous range of timestamps)
    #
    df_out = df_out.join(df)

    return df_out


def depth_to_features(entry: list, windows: list, bin_size: float):
    """Convert one record of market depth to a dict of features"""

    bids = entry.get("bids")
    asks = entry.get("asks")

    timestamp = entry.get("timestamp")

    # Gap feature
    gap = asks[0][0] - bids[0][0]

    if gap < 0: gap = 0

    # Price feature
    price = bids[0][0] + (gap / 2)

    # Densities for bids and asks (volume per price unit)
    densities = mean_volumes(depth=entry, windows=windows, bin_size=bin_size)

    record = {"timestamp": timestamp, "gap": gap, "price": price}
    record.update(densities)

    return record


if __name__ == "__main__":
    pass
