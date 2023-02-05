from __future__ import annotations  # Eliminates problem with type annotations like list[int]
import os
from datetime import datetime, timezone, timedelta
from typing import Union, List
import json

import numpy as np
import pandas as pd


"""
Signal generation is based on processing a relatively small number of highly informative 
(point-wise) scores generated by ML algorithms. The goal is to apply some rules to these
scores and generate the final signal buy, sell or do nothing. Such rules are described via
a number of parameters. These parameters are chosen to optimize the final trade performance
(and not precision in ML algorithms). Thus we have two sets of functions: 1) computing rules with
given parameters, and 2) finding optimal parameters of rules (currently via grid search).
"""


def aggregate_scores(df, model, score_column_out: str, score_columns: Union[List[str], str]):
    """
    Add two signal numeric (buy and sell) columns by processing a list of buy and sell point-wise predictions.

    The following operations are applied:
        - find average among all buy and sell columns, respectively
        - find moving average along each individual buy/sell column or/and the two final columns according to window(s)
        - apply threshold to source buy/sell column(s) according to threshold parameter(s) by producing a boolean column

    Notes:
        - Input point-wise scores in buy and sell columns are always positive
    """
    if not model:
        raise ValueError(f"Configuration must specify 'score_aggregation' parameters")

    point_threshold = model.get("point_threshold")
    window = model.get("window")

    if isinstance(score_columns, str):
        score_columns = [score_columns]

    #
    # Average all buy and sell columns
    #
    score_column = df[score_columns].mean(skipna=True, axis=1)

    #
    # Apply thresholds (if specified) and binarize the score
    #
    if point_threshold:
        score_column = score_column >= point_threshold

    #
    # Moving average
    #
    if isinstance(window, int):
        score_column = score_column.rolling(window, min_periods=window // 2).mean()
    elif isinstance(window, float):
        score_column = score_column.ewm(span=window, min_periods=window // 2, adjust=False).mean()

    df[score_column_out] = score_column

    return score_column


def combine_scores(df, model, buy_score_column, sell_score_column):
    """
    Mutually adjust two independent scores with opposite semantics.
    The result is stored in the same columns by overwriting old values.
    """
    if model.get("combine") == "relative":
        combine_scores_relative(df, buy_score_column, sell_score_column, buy_score_column, sell_score_column)
    elif model.get("combine") == "difference":
        combine_scores_difference(df, buy_score_column, sell_score_column, buy_score_column, sell_score_column)


def combine_scores_relative(df, buy_column, sell_column, buy_column_out, sell_column_out):
    """
    Mutually adjust input buy and sell scores by producing two output scores.
    The idea is that if both scores (buy and sell) are equally high then in the output
    they both will be 0. The output score describe if this score is higher relative to the other.
    The two output scores are in [-1, +1] but have opposite values.
    """

    # compute proportion in the sum
    buy_plus_sell = df[buy_column] + df[sell_column]
    buy_sell_score = ((df[buy_column] / buy_plus_sell) * 2) - 1.0  # in [-1, +1]

    df[buy_column_out] = buy_sell_score  # High values mean buy signal
    #df[buy_column_out] = df[df[buy_column_out] < 0] = 0  # Set negative values to 0

    df[sell_column_out] = -buy_sell_score  # High values mean sell signal
    #df[sell_column_out] = df[df[sell_column_out] < 0] = 0  # Set negative values to 0

    # Final score: abs difference between high and low (scaled to [-1,+1] maybe)
    #in_df["score"] = in_df["high"] - in_df["low"]
    #from sklearn.preprocessing import StandardScaler
    #in_df["score"] = StandardScaler().fit_transform(in_df["score"])

    return buy_sell_score


def combine_scores_difference(df, buy_column, sell_column, buy_column_out, sell_column_out):
    """
    This transformation represents how much buy score higher than sell score.
    If they are equal then the output is 0. The output scores have opposite signs.
    """

    # difference
    buy_minus_sell = df[buy_column] - df[sell_column]

    df[buy_column_out] = buy_minus_sell  # High values mean buy signal
    #df[buy_column_out] = df[df[buy_column_out] < 0] = 0  # Set negative values to 0

    df[sell_column_out] = -buy_minus_sell  # High values mean sell signal
    #df[sell_column_out] = df[df[sell_column_out] < 0] = 0  # Set negative values to 0

    return buy_minus_sell


def apply_rule_with_score_thresholds(df, model, buy_score_column, sell_score_column):
    """
    Apply rules based on thresholds and generate trade signal buy, sell or do nothing.

    Returns signals in two pre-defined columns: 'buy_signal_column' and 'sell_signal_column'
    """
    df['buy_signal_column'] = \
        ((df[buy_score_column] - df[sell_score_column]) > 0.0) & \
        (df[buy_score_column] >= model.get("buy_signal_threshold"))
    df['sell_signal_column'] = \
        ((df[sell_score_column] - df[buy_score_column]) > 0.0) & \
        (df[sell_score_column] >= model.get("sell_signal_threshold"))


def apply_rule_with_score_thresholds_one_row(row, model, buy_score_column, sell_score_column):
    """
    Same as above but applied to one row. It is used for online predictions.

    Returns signals a a tuple with two values: buy_signal and sell_signal
    """
    buy_score = row[buy_score_column]
    sell_score = row[sell_score_column]

    buy_signal = \
        (buy_score - sell_score > 0) and \
        (buy_score >= model.get("buy_signal_threshold"))
    sell_signal = \
        (sell_score - buy_score > 0) and \
        (sell_score >= model.get("sell_signal_threshold"))

    return buy_signal, sell_signal


def compute_score_slope(df, model, buy_score_columns_in, sell_score_columns_in):
    """
    Experimental. Currently not used.
    Compute slope of the numeric score over model.get("buy_window") and model.get("sell_window")
    """

    from scipy import stats
    from sklearn import linear_model
    def linear_regr_fn(X):
        """
        Given a Series, fit a linear regression model and return its slope interpreted as a trend.
        The sequence of values in X must correspond to increasing time in order for the trend to make sense.
        """
        X_array = np.asarray(range(len(X)))
        y_array = X
        if np.isnan(y_array).any():
            nans = ~np.isnan(y_array)
            X_array = X_array[nans]
            y_array = y_array[nans]

        # X_array = X_array.reshape(-1, 1)  # Make matrix
        # model = linear_model.LinearRegression()
        # model.fit(X_array, y_array)
        # slope = model.coef_[0]

        slope, intercept, r, p, se = stats.linregress(X_array, y_array)

        return slope

    # if 'buy_score_slope' not in df.columns:
    #    w = 10  #model.get("buy_window")
    #    df['buy_score_slope'] = df['buy_score_column'].rolling(window=w, min_periods=max(1, w // 2)).apply(linear_regr_fn, raw=True)
    #    w = 10  #model.get("sell_window")
    #    df['sell_score_slope'] = df['sell_score_column'].rolling(window=w, min_periods=max(1, w // 2)).apply(linear_regr_fn, raw=True)


def apply_rule_with_slope_thresholds(df, model, buy_score_column, sell_score_column):
    """
    Experimental. Currently not used.
    This rule type evaluates the score itself and also its slope.
    """
    # df['buy_signal_column'] = (df['buy_score_column'] >= model.get("buy_signal_threshold")) & (df['buy_score_slope'].abs() <= model.get("buy_slope_threshold"))
    # df['sell_signal_column'] = (df['sell_score_column'] >= model.get("sell_signal_threshold")) & (df['sell_score_slope'].abs() <= model.get("sell_slope_threshold"))


#
# Trade performance calculation
#

def simulated_trade_performance(df, sell_signal_column, buy_signal_column, price_column):
    """
    The function simulates trades over the time by buying and selling the asset
    according to the specified buy/sell signals and price. Essentially, it assumes
    the existence of some initial amount, then it moves forward in time by finding
    next buy/sell signal and accordingly buying/selling the asset using the current
    price. At the end, it finds how much it earned by comparing with the initial amount.

    It returns short and long performance as a number of metrics collected during
    one simulation pass.
    """
    is_buy_mode = True

    long_profit = 0
    long_profit_percent = 0
    long_transactions = 0
    long_profitable = 0
    longs = list()  # Where we buy

    short_profit = 0
    short_profit_percent = 0
    short_transactions = 0
    short_profitable = 0
    shorts = list()  # Where we sell

    # The order of columns is important for itertuples
    df = df[[sell_signal_column, buy_signal_column, price_column]]
    for (index, sell_signal, buy_signal, price) in df.itertuples(name=None):
        if not price or pd.isnull(price):
            continue
        if is_buy_mode:
            # Check if minimum price
            if buy_signal:
                previous_price = shorts[-1][2] if len(shorts) > 0 else 0.0
                profit = (previous_price - price) if previous_price > 0 else 0.0
                profit_percent = 100.0 * profit / previous_price if previous_price > 0 else 0.0
                short_profit += profit
                short_profit_percent += profit_percent
                short_transactions += 1
                if profit > 0:
                    short_profitable += 1
                longs.append((index, is_buy_mode, price, profit, profit_percent))  # Bought
                is_buy_mode = False
        else:
            # Check if maximum price
            if sell_signal:
                previous_price = longs[-1][2] if len(longs) > 0 else 0.0
                profit = (price - previous_price) if previous_price > 0 else 0.0
                profit_percent = 100.0 * profit / previous_price if previous_price > 0 else 0.0
                long_profit += profit
                long_profit_percent += profit_percent
                long_transactions += 1
                if profit > 0:
                    long_profitable += 1
                shorts.append((index, is_buy_mode, price, profit, profit_percent))  # Sold
                is_buy_mode = True

    long_performance = dict(  # Performance of buy at low price and sell at high price
        profit=long_profit,
        profit_percent=long_profit_percent,
        transaction_no=long_transactions,
        profitable=long_profitable / long_transactions if long_transactions else 0.0,
        transactions=longs,  # Buy signal list
    )
    short_performance = dict(  # Performance of sell at high price and buy at low price
        profit=short_profit,
        profit_percent=short_profit_percent,
        transaction_no=short_transactions,
        profitable=short_profitable / short_transactions if short_transactions else 0.0,
        transactions=shorts,  # Sell signal list
    )

    profit = long_profit + short_profit
    profit_percent = long_profit_percent + short_profit_percent
    transaction_no = long_transactions + short_transactions
    profitable = (long_profitable + short_profitable) / transaction_no if transaction_no else 0.0
    #minutes_in_month = 1440 * 30.5
    performance = dict(
        profit=profit,
        profit_percent=profit_percent,
        transaction_no=transaction_no,
        profitable=profitable,

        profit_per_transaction=profit / transaction_no if transaction_no else 0.0,
        profitable_percent=100.0 * profitable / transaction_no if transaction_no else 0.0,
        #transactions=transactions,
        #profit=profit,
        #profit_per_month=profit / (len(df) / minutes_in_month),
        #transactions_per_month=transaction_no / (len(df) / minutes_in_month),
    )

    return performance, long_performance, short_performance


#
# Helper and exploration functions
#

def find_interval_precision(df: pd.DataFrame, label_column: str, score_column: str, threshold: float):
    """
    Convert point-wise score/label pairs to interval-wise score/label.

    We assume that for each point there is a score and a boolean label. The score can be a future
    prediction while boolean label is whether this forecast is true. Or the score can be a prediction
    that this is a top/bottom while the label is whether it is indeed so.
    Importantly, the labels are supposed to represent contiguous intervals because the algorithm
    will output results for them by aggregating scores within these intervals.

    The output is a data frame with one row per contiguous interval. The intervals are interleaving
    like true, false, true, false etc. Accordingly, there is one label column which takes these
    values true, false etc. The score column for each interval is computed by using these rules:
    - for true interval: true (positive) if there is at least one point with score higher than the threshold
    - for true interval: false (positive) if all points are lower than the threshold
    - for false interval: true (negative) if all points are lower than the threshold
    - for false interval: false (negative) if there is at least one (wrong) points with the score higher than the thresond
    Essentially, we need only one boolean "all lower" function

    The input point-wise score is typically aggregated by applying some kind of rolling aggregation
    but it is performed separately.

    The function is supposed to be used for scoring during hyper-parameter search.
    We can search in level, tolerance, threshold, aggregation hyper-paraemters (no forecasting parameters).
    Or we can also search through various ML forecasting hyper-parameters like horizon etc.
    In any case, after we selected hyper-parameters, we apply interval selection, score aggregation,
    then apply this function, and finally computing the interval-wise score.

    Input data frame is supposed to be sorted (important for the algorithm of finding contiguous intervals).
    """

    #
    # Count all intervals by finding them as groups of points. Input is a boolean column with interleaving true-false
    # Mark true intervals (extremum) and false intervals (non-extremum)
    #

    # Find indexes with transfer from 0 to 1 (+1) and from 1 to 0 (-1)
    out = df[label_column].diff()
    out.iloc[0] = False  # Assume no change
    out = out.astype(int)

    # Find groups (intervals, starts-stops) and assign true-false label to them
    interval_no_column = 'interval_no'
    df[interval_no_column] = out.cumsum()

    #
    # For each group (with true-false label), compute their interval-wise score (using all or none principle)
    #

    # First, compute "score lower" (it will be used during interval-based aggregation)
    df[score_column + '_greater_than_threshold'] = (df[score_column] >= threshold)

    # Interval objects
    by_interval = df.groupby(interval_no_column)

    # Find interval label
    # Either 0 (all false) or 1 (at least one true - but must be all true)
    interval_label = by_interval[label_column].max()

    # Apply "all lower" function to each interval scores.
    # Either 0 (all lower) or 1 (at least one higher)
    interval_score = by_interval[score_column + '_greater_than_threshold'].max()
    interval_score.name = score_column

    # Compute into output
    interval_df = pd.concat([interval_label, interval_score], axis=1)
    interval_df = interval_df.reset_index(drop=False)

    return interval_df


def generate_score_high_low(df, feature_sets):
    """
    Add a score column which aggregates different types of scores generated by various algorithms with different options.
    The score is added as a new column and is supposed to be used by the signal generator as the final feature.

    :param df:
    :feature_sets: list of "kline", "futur" etc.
    :return:

    TODO: Refactor by replacing new more generation score aggregation functions which work for any type of label: high-low, top-bot etc.
    """

    if "kline" in feature_sets:
        # high kline: 3 algorithms for all 3 levels
        df["high_k"] = \
            df["high_10_k_gb"] + df["high_10_k_nn"] + df["high_10_k_lc"] + \
            df["high_15_k_gb"] + df["high_15_k_nn"] + df["high_15_k_lc"] + \
            df["high_20_k_gb"] + df["high_20_k_nn"] + df["high_20_k_lc"]
        df["high_k"] /= 9

        # low kline: 3 algorithms for all 3 levels
        df["low_k"] = \
            df["low_10_k_gb"] + df["low_10_k_nn"] + df["low_10_k_lc"] + \
            df["low_15_k_gb"] + df["low_15_k_nn"] + df["low_15_k_lc"] + \
            df["low_20_k_gb"] + df["low_20_k_nn"] + df["low_20_k_lc"]
        df["low_k"] /= 9

        # By algorithm type
        df["high_k_nn"] = (df["high_10_k_nn"] + df["high_15_k_nn"] + df["high_20_k_nn"]) / 3
        df["low_k_nn"] = (df["low_10_k_nn"] + df["low_15_k_nn"] + df["low_20_k_nn"]) / 3

    if "futur" in feature_sets:
        # high futur: 3 algorithms for all 3 levels
        df["high_f"] = \
            df["high_10_f_gb"] + df["high_10_f_nn"] + df["high_10_f_lc"] + \
            df["high_15_f_gb"] + df["high_15_f_nn"] + df["high_15_f_lc"] + \
            df["high_20_f_gb"] + df["high_20_f_nn"] + df["high_20_f_lc"]
        df["high_f"] /= 9

        # low kline: 3 algorithms for all 3 levels
        df["low_f"] = \
            df["low_10_f_gb"] + df["low_10_f_nn"] + df["low_10_f_lc"] + \
            df["low_15_f_gb"] + df["low_15_f_nn"] + df["low_15_f_lc"] + \
            df["low_20_f_gb"] + df["low_20_f_nn"] + df["low_20_f_lc"]
        df["low_f"] /= 9

        # By algorithm type
        df["high_f_nn"] = (df["high_10_f_nn"] + df["high_15_f_nn"] + df["high_20_f_nn"]) / 3
        df["low_f_nn"] = (df["low_10_f_nn"] + df["low_15_f_nn"] + df["low_20_f_nn"]) / 3

    # High and low
    # Both k and f
    #in_df["high"] = (in_df["high_k"] + in_df["high_f"]) / 2
    #in_df["low"] = (in_df["low_k"] + in_df["low_f"]) / 2

    # Only k and all algorithms
    df["high"] = (df["high_k"])
    df["low"] = (df["low_k"])

    # Using one NN algorithm only
    #in_df["high"] = (in_df["high_k_nn"])
    #in_df["low"] = (in_df["low_k_nn"])

    # Final score: proportion to the sum
    high_and_low = df["high"] + df["low"]
    df["score"] = ((df["high"] / high_and_low) * 2) - 1.0  # in [-1, +1]

    # Final score: abs difference betwee high and low (scaled to [-1,+1] maybe)
    #in_df["score"] = in_df["high"] - in_df["low"]
    from sklearn.preprocessing import StandardScaler
    #in_df["score"] = StandardScaler().fit_transform(in_df["score"])

    #in_df["score"] = in_df["score"].rolling(window=10, min_periods=1).apply(np.nanmean)

    return df


# NOT USED
def generate_signals(df, models: dict):
    """
    Use predicted labels in the data frame to decide whether to buy or sell.
    Use rule-based approach by comparing the predicted scores with some thresholds.
    The decision is made for the last row only but we can use also previous data.

    TODO: In future, values could be functions which return signal 1 or 0 when applied to a row

    :param df: data frame with features which will be used to generate signals
    :param models: dict where key is a signal name which is also an output column name and value a dict of parameters of the model
    :return: A number of binary columns will be added each corresponding to one signal and having same name
    """

    # Define one function for each signal type.
    # A function applies a predicates by using the provided parameters and qualifies this row as true or false
    # TODO: Access to model parameters and row has to be rubust and use default values (use get instead of [])

    def all_higher_fn(row, model):
        keys = model.keys()
        for field, value in model.items():
            if row.get(field) >= value:
                continue
            else:
                return 0
        return 1

    def all_lower_fn(row, model):
        keys = model.keys()
        for field, value in model.items():
            if row.get(field) <= value:
                continue
            else:
                return 0
        return 1

    for signal, model in models.items():
        # Choose function which implements (knows how to generate) this signal
        fn = None
        if signal == "buy":
            fn = all_higher_fn
        elif signal == "sell":
            fn = all_lower_fn
        else:
            print("ERROR: Wrong use. Unexpected signal name.")

        # Model will be passed as the second argument (the first one is the row)
        df[signal] = df.apply(fn, axis=1, args=[model])

    return models.keys()


if __name__ == '__main__':
    pass
