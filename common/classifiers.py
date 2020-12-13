import sys
import os

import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn import metrics
from sklearn.model_selection import ParameterGrid
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_score
from sklearn.metrics import recall_score
from sklearn.metrics import f1_score

from sklearn.linear_model import LogisticRegression, SGDClassifier

import lightgbm as lgbm

import tensorflow as tf

from keras.models import Sequential
from keras.layers import Dense, Dropout
from keras.optimizers import *
from keras.regularizers import *
from keras.callbacks import *

#
# GB
#

def train_predict_gb(df_X, df_y, df_X_test, params: dict):
    """
    Train model with the specified hyper-parameters and return its predictions for the test data.
    """
    models = train_gb(df_X, df_y, params)
    y_test_hat = predict_gb(models, df_X_test)
    return y_test_hat

def train_gb(df_X, df_y, params: dict):
    """
    Train model with the specified hyper-parameters and return its predictions for new data.
    """
    is_scale = params.get("is_scale", False)

    #
    # Prepare data
    #
    if is_scale:
        scaler = StandardScaler()
        scaler.fit(df_X)
        X_train = scaler.transform(df_X)
    else:
        scaler = None
        X_train = df_X.values

    y_train = df_y.values

    #
    # Create model
    #

    objective = params.get("objective")

    max_depth = params.get("max_depth")
    learning_rate = params.get("learning_rate")
    num_boost_round = params.get("num_boost_round")

    lambda_l1 = params.get("lambda_l1")
    lambda_l2 = params.get("lambda_l2")

    lgbm_params = {
        'learning_rate': learning_rate,
        'max_depth': max_depth,  # Can be -1
        #"n_estimators": 10000,

        #"min_split_gain": params['min_split_gain'],
        "min_data_in_leaf": int(0.01*len(df_X)),  # Best: ~0.02 * len() - 2% of size
        #'subsample': 0.8,
        #'colsample_bytree': 0.8,
        'num_leaves': 32,  # or (2 * 2**max_depth)
        #"bagging_freq": 5,
        #"bagging_fraction": 0.4,
        #"feature_fraction": 0.05,

        # gamma=0.1 ???
        "lambda_l1": lambda_l1,
        "lambda_l2": lambda_l2,

        'is_unbalance': 'true',
        # 'scale_pos_weight': scale_pos_weight,  # is_unbalance must be false

        'boosting_type': 'gbdt',  # dart (slow but best, worse than gbdt), goss, gbdt

        'objective': objective, # binary cross_entropy cross_entropy_lambda

        'metric': {'cross_entropy'},  # auc auc_mu map (mean_average_precision) cross_entropy binary_logloss cross_entropy_lambda binary_error

        'verbose': 0,
    }

    model = lgbm.train(
        lgbm_params,
        train_set=lgbm.Dataset(X_train, y_train),
        num_boost_round=num_boost_round,
        #valid_sets=[lgbm.Dataset(X_validate, y_validate)],
        #early_stopping_rounds=int(num_boost_round / 5),
        verbose_eval=100,
    )

    return (model, scaler)

def predict_gb(models: tuple, df_X_test):
    """
    Use the model(s) to make predictions for the test data.
    The first model is a prediction model and the second model (optional) is a scaler.
    """
    scaler = models[1]
    is_scale = scaler is not None

    input_index = df_X_test.index
    if is_scale:
        df_X_test = scaler.transform(df_X_test)
        df_X_test = pd.DataFrame(data=df_X_test, index=input_index)
    else:
        df_X_test = df_X_test

    df_X_test_nonans = df_X_test.dropna()  # Drop nans, create gaps in index
    nonans_index = df_X_test_nonans.index

    y_test_hat_nonans = models[0].predict(df_X_test_nonans.values)
    y_test_hat_nonans = pd.Series(data=y_test_hat_nonans, index=nonans_index)  # Attach indexes with gaps

    df_ret = pd.DataFrame(index=input_index)  # Create empty dataframe with original index
    df_ret["y_hat"] = y_test_hat_nonans  # Join using indexes
    sr_ret = df_ret["y_hat"]  # This series has all original input indexes but NaNs where input is NaN

    return sr_ret

#
# NN
#

def train_predict_nn(df_X, df_y, df_X_test, params: dict):
    """
    Train model with the specified hyper-parameters and return its predictions for the test data.
    """
    models = train_nn(df_X, df_y, params)
    y_test_hat = predict_nn(models, df_X_test)
    return y_test_hat

def train_nn(df_X, df_y, params: dict):
    """
    Train model with the specified hyper-parameters and return its predictions for the test data.
    """
    is_scale = params.get("is_scale", True)

    #
    # Prepare data
    #
    if is_scale:
        scaler = StandardScaler()
        scaler.fit(df_X)
        X_train = scaler.transform(df_X)
    else:
        scaler = None
        X_train = df_X.values

    y_train = df_y.values

    #
    # Create model
    #
    n_features = X_train.shape[1]
    layers = params.get("layers")  # List of ints
    learning_rate = params.get("learning_rate")
    n_epochs = params.get("n_epochs")
    batch_size = params.get("bs")

    # Topology
    model = Sequential()
    # sigmoid, relu, tanh, selu, elu, exponential
    # kernel_regularizer=l2(0.001)

    reg_l2 = 0.001

    model.add(
        Dense(n_features, activation='sigmoid', input_dim=n_features, kernel_regularizer=l2(reg_l2))
    )

    #model.add(Dense(layers[0], activation='sigmoid', input_dim=n_features, kernel_regularizer=l2(reg_l2)))
    #if len(layers) > 1:
    #    model.add(Dense(layers[1], activation='sigmoid', kernel_regularizer=l2(reg_l2)))
    #if len(layers) > 2:
    #    model.add(Dense(layers[2], activation='sigmoid', kernel_regularizer=l2(reg_l2)))

    model.add(
        Dense(1, activation='sigmoid')
    )

    # Compile model
    optimizer = Adam(lr=learning_rate)
    model.compile(
        loss='binary_crossentropy',
        optimizer=optimizer,
        metrics=[
            tf.keras.metrics.AUC(name="auc"),
            tf.keras.metrics.Precision(name="precision"),
            tf.keras.metrics.Recall(name="recall"),
        ],
    )

    es = EarlyStopping(
        monitor="loss",  # val_loss loss
        min_delta=0.0001,  # Minimum change qualified as improvement
        patience=0,  # Number of epochs with no improvements
        verbose=0,
        mode='auto',
    )

    #
    # Train
    #
    model.fit(
        X_train,
        y_train,
        batch_size=batch_size,
        epochs=n_epochs,
        #validation_data=(X_validate, y_validate),
        #class_weight={0: 1, 1: 20},
        callbacks=[es],
        verbose=0,
    )

    return (model, scaler)

def predict_nn(models: tuple, df_X_test):
    """
    Use the model(s) to make predictions for the test data.
    The first model is a prediction model and the second model (optional) is a scaler.
    """
    scaler = models[1]
    is_scale = scaler is not None

    input_index = df_X_test.index
    if is_scale:
        df_X_test = scaler.transform(df_X_test)
        df_X_test = pd.DataFrame(data=df_X_test, index=input_index)
    else:
        df_X_test = df_X_test

    df_X_test_nonans = df_X_test.dropna()  # Drop nans, create gaps in index
    nonans_index = df_X_test_nonans.index

    y_test_hat_nonans = models[0].predict(df_X_test_nonans.values)  # NN returns matrix with one column as prediction
    y_test_hat_nonans = y_test_hat_nonans[:, 0]  # Or y_test_hat.flatten()
    y_test_hat_nonans = pd.Series(data=y_test_hat_nonans, index=nonans_index)  # Attach indexes with gaps

    df_ret = pd.DataFrame(index=input_index)  # Create empty dataframe with original index
    df_ret["y_hat"] = y_test_hat_nonans  # Join using indexes
    sr_ret = df_ret["y_hat"]  # This series has all original input indexes but NaNs where input is NaN

    return sr_ret

#
# LC - Linear Classifier
#

def train_predict_lc(df_X, df_y, df_X_test, params: dict):
    """
    Train model with the specified hyper-parameters and return its predictions for the test data.
    """
    models = train_lc(df_X, df_y, params)
    y_test_hat = predict_lc(models, df_X_test)
    return y_test_hat

def train_lc(df_X, df_y, params: dict):
    """
    Train model with the specified hyper-parameters and return its predictions for the test data.
    """
    is_scale = params.get("is_scale", True)

    #
    # Prepare data
    #
    if is_scale:
        scaler = StandardScaler()
        scaler.fit(df_X)
        X_train = scaler.transform(df_X)
    else:
        scaler = None
        X_train = df_X.values

    y_train = df_y.values

    #
    # Create model
    #
    args = params.copy()
    del args["is_scale"]
    args["n_jobs"] = 1
    model = LogisticRegression(**args)

    #
    # Train
    #
    model.fit(X_train, y_train)

    return (model, scaler)

def predict_lc(models: tuple, df_X_test):
    """
    Use the model(s) to make predictions for the test data.
    The first model is a prediction model and the second model (optional) is a scaler.
    """
    scaler = models[1]
    is_scale = scaler is not None

    input_index = df_X_test.index
    if is_scale:
        df_X_test = scaler.transform(df_X_test)
        df_X_test = pd.DataFrame(data=df_X_test, index=input_index)
    else:
        df_X_test = df_X_test

    df_X_test_nonans = df_X_test.dropna()  # Drop nans, create gaps in index
    nonans_index = df_X_test_nonans.index

    y_test_hat_nonans = models[0].predict_proba(df_X_test_nonans.values)  # It returns pairs or probas for 0 and 1
    y_test_hat_nonans = y_test_hat_nonans[:, 1]  # Or y_test_hat.flatten()
    y_test_hat_nonans = pd.Series(data=y_test_hat_nonans, index=nonans_index)  # Attach indexes with gaps

    df_ret = pd.DataFrame(index=input_index)  # Create empty dataframe with original index
    df_ret["y_hat"] = y_test_hat_nonans  # Join using indexes
    sr_ret = df_ret["y_hat"]  # This series has all original input indexes but NaNs where input is NaN

    return sr_ret


if __name__ == '__main__':
    pass