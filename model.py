import os
import sys
import datetime
import math
import logging
import inspect

import pandas as pd
import numpy as np

from sklearn.externals import joblib
from sklearn.base import _pprint

from . import util, metrics
from drain.util import merge_dicts
from drain.step import Step, Construct

class FitPredict(Step):
    def __init__(self, return_estimator=False, return_feature_importances=True, return_predictions=True, prefit=False, **kwargs):
        Step.__init__(self, return_estimator=return_estimator,
                return_feature_importances=return_feature_importances,
                return_predictions=return_predictions, prefit=prefit, **kwargs)

    def run(self, estimator, X, y, train=None, test=None, aux=None, sample_weight=None, **kwargs):
        if not self.prefit:
            if train is not None:
                X_train, y_train = X[train], y[train]
            else:
                X_train, y_train = X, y

            y_train = y_train.astype(bool)

            logging.info('Fitting with %s examples, %s features' % X_train.shape)
            if 'sample_weight' in inspect.getargspec(estimator.fit) and sample_weight is not None:
                logging.info('Using sample weight')
                estimator.fit(X_train, y_train, sample_weight=sample_weight)
            else:
                estimator.fit(X_train, y_train)

        result = {}

        if self.return_estimator:
            result['estimator'] = estimator
        if self.return_feature_importances:
            result['feature_importances'] = feature_importance(estimator, X)
        if self.return_predictions:
            if test is not None:
                X_test, y_test = X[test], y[test]
            else:
                X_test, y_test = X, y

            logging.info('Predicting %s examples' % len(X_test))
            y = pd.DataFrame({'true': y_test})
            y['score'] = y_score(estimator, X_test)
            if aux is not None:
                y = y.join(aux, how='left')

            result['y'] = y

        return result

    def dump(self):
        result = self.get_result()
        if self.return_estimator:
            filename = os.path.join(self._dump_dirname, 'estimator.pkl')
            joblib.dump(result['estimator'], filename)
        if self.return_feature_importances:
            filename = os.path.join(self._dump_dirname, 'feature_importances.hdf')
            result['feature_importances'].to_hdf(filename, 'df')
        if self.return_predictions:
            filename = os.path.join(self._dump_dirname, 'y.hdf')
            result['y'].to_hdf(filename, 'df')

    def load(self):
        result = {}
        if self.return_estimator:
            filename = os.path.join(self._dump_dirname, 'estimator.pkl')
            result['estimator'] = joblib.load(filename)
        if self.return_feature_importances:
            filename = os.path.join(self._dump_dirname, 'feature_importances.hdf')
            result['feature_importances'] = pd.read_hdf(filename, 'df')
        if self.return_predictions:
            filename = os.path.join(self._dump_dirname, 'y.hdf')
            result['y'] = pd.read_hdf(filename, 'df')

        self.set_result(result)

class Fit(FitPredict):
    def __init__(self, **kwargs):
        kwargs = merge_dicts(
                dict(prefit=False, return_predictions=False),
                kwargs)
        FitPredict.__init__(self, **kwargs)

class PredictProduct(Step):
    def run(self, **kwargs):
        keys = kwargs.keys()
        ys = [kwargs[k]['y'] for k in keys]
        y = ys[0].copy()
        y.rename(columns={'score':'score_%s' % keys[0]}, inplace=True)
        y['score_%s' % keys[1]] = ys[1].score
        y['score'] = ys[0].score * ys[1].score

        return {'y':y}

class Predict(FitPredict):
    def __init__(self, **kwargs):
        kwargs = merge_dicts(dict(return_feature_importances=False, 
                return_predictions=True, prefit=True), kwargs)
        FitPredict.__init__(self, **kwargs)
       
def y_score(estimator, X):
    if hasattr(estimator, 'decision_function'):
        return estimator.decision_function(X)
    else:
        y = estimator.predict_proba(X)
        return y[:,1]

def feature_importance(estimator, X):
    if hasattr(estimator, 'coef_'):
        i = estimator.coef_[0]
    elif hasattr(estimator, 'feature_importances_'):
        i = estimator.feature_importances_
    else:
        i = [np.nan]*X.shape[1]

    features = X.columns if hasattr(X, 'columns') else range(X.shape[1])

    return pd.DataFrame({'feature': features, 'importance': i}).sort_values('importance', ascending=False)

class LogisticRegression(object):
    def __init__(self):
        pass

    def fit(self, X, y, **kwargs):
        from statsmodels.discrete.discrete_model import Logit
        self.model = Logit(y, X)
        self.result = self.model.fit()
    
    def predict_proba(self, X):
        return self.result.predict(X)

from sklearn.externals.joblib import Parallel, delayed
from sklearn.ensemble.forest import _parallel_helper

def _proximity_parallel_helper(train_nodes, t, k):
    d = (train_nodes == t).sum(axis=1)
    n = d.argsort()[::-1][:k]
    
    return d[n], n #distance, neighbors

def _proximity_helper(train_nodes, test_nodes, k):
    results = Parallel(n_jobs=16, backend='threading')(delayed(_proximity_parallel_helper)(train_nodes, t, k) for t in test_nodes)
    distance, neighbors = zip(*results)
    return np.array(distance), np.array(neighbors)

# store nodes in run
def apply_forest(run):
    run['nodes'] = pd.DataFrame(run.estimator.apply(run['data'].X), index=run['data'].X.index)
    
# look for nodes in training set proximal to the given nodes
def proximity(run, ix, k):
    if 'nodes' not in run:
        apply_forest(run)
    distance, neighbors = _proximity_helper(run['nodes'][run.y.train].values, run['nodes'].loc[ix].values, k)
    neighbors = run['nodes'][run.y.train].irow(neighbors.flatten()).index
    neighbors = [neighbors[k*i:k*(i+1)] for i in range(len(ix))]
    return distance, neighbors

# subset a model "y" dataframe
# dropna means drop missing outcomes
# return top k (count) or p (proportion) if specified
# p_of specifies what the proportion is relative to:
# p_of='notnull' means proportion is relative to labeled count
# p_of='true' means proportion is relative to positive count
# p_of='all' means proportion is relative to total count

def y_subset(y, query=None, dropna=False, outcome='true',
        k=None, p=None, ascending=False, score='score', p_of='notnull'):

    if query is not None:
        y = y.query(query)

    if dropna:
        y = y.dropna(subset=[outcome])

    if k is not None and p is not None:
        raise ValueError("Cannot specify both k and p")
    elif k is not None:
        k = k
    elif p is not None:
        if p_of == 'notnull':
            k = int(p*y[outcome].notnull().sum())
        elif p_of == 'true':
            k = int(p*y[outcome].sum())
        elif p_of == 'all':
            k = int(p*len(y))
        else:
            raise ValueError('Invalid value for p_of: %s' % p_of)
    else:
        k = None

    if k is not None:
        y = y.sort_values(score, ascending=ascending).head(k)

    return y

# list of arguments to y_subset() for Metric above
Y_SUBSET_ARGS = inspect.getargspec(y_subset).args 

def true_score(y, outcome='true', score='score', **subset_args):
    y = y_subset(y, outcome=outcome, score=score, **subset_args) 
    return util.to_float(y[outcome], y[score])

def make_metric(function):
    def metric(predict_step, **kwargs):
        y = predict_step.get_result()['y']
        subset_args = [k for k in Y_SUBSET_ARGS if k in kwargs]
        kwargs_subset = {k:kwargs[k] for k in subset_args}
        y_true,y_score = true_score(y, **kwargs_subset)

        kwargs_metric = {k:kwargs[k] for k in kwargs if k not in Y_SUBSET_ARGS}
        r = function(y_true, y_score, **kwargs_metric)
        return r

    return metric

metrics = [o for o in inspect.getmembers(metrics) if inspect.isfunction(o[1]) and not o[0].startswith('_')]

for name,function in metrics:
    function = make_metric(function)
    function.__name__ = name
    setattr(sys.modules[__name__], name, function)

class PrintMetrics(Step):
    def __init__(self, metrics, **kwargs):
        Step.__init__(self, metrics=metrics, **kwargs)

    def run(self, *args, **kwargs):
        for metric in self.metrics:
            kwargs = dict(metric)
            metric_name = kwargs.pop('metric')
            metric_fn = getattr(sys.modules[__name__], metric_name) # TODO allow external metrics

            r = metric_fn(self.inputs[0], **kwargs)
            print('%s(%s): %s' % (metric_name, _pprint(kwargs, offset=len(metric_name)), r))

def perturb(estimator, X, bins, columns=None):
    """
    Predict on peturbations of a feature vector
    estimator: a fitted sklearn estimator
    index: the index of the example to perturb
    bins: a dictionary of column:bins arrays
    columns: list of columns if bins doesn't cover all columns
    TODO make this work when index is multiple rows
    """
    if columns is None:
        if len(bins) != X.shape[1]:
            raise ValueError("Must specify columns when not perturbing all columns")
        else:
            columns = X.columns

    n = np.concatenate(([0],np.cumsum([len(b) for b in bins])))
    
    X_test = np.empty((n[-1]*X.shape[0], X.shape[1]))
    r = pd.DataFrame(columns=['value', 'feature', 'index'], index=np.arange(n[-1]*X.shape[0]))
    for j,index in enumerate(X.index):
        X_test[j*n[-1]:(j+1)*n[-1], :] = X.values[j,:]
        for i,c in enumerate(columns):
            s = slice(j*n[-1] + n[i], j*n[-1] + n[i+1])
            r['value'].values[s] = bins[i]
            r['feature'].values[s] = c
            r['index'].values[s] = [index]*(n[i+1]-n[i])
            X_test[s, (X.columns==c).argmax()] = bins[i]
            
    y = estimator.predict_proba(X_test)[:,1]
    r['y'] = y
    return r

def forests(**kwargs):
    steps = []
    d = dict(criterion=['entropy', 'gini'], max_features=['sqrt', 'log2'], n_jobs=[-1], **kwargs)
    for estimator_args in util.dict_product(d):
        steps.append(Construct(name='estimator', 
                 __class_name__='sklearn.ensemble.RandomForestClassifier',
                **estimator_args))

    return steps

def logits(**kwargs):
    steps = []
    for estimator_args in util.dict_product(dict(
            penalty=['l1','l2'], C=[.001,.01,.1,1], **kwargs)):
        steps.append(Construct(name='estimator', 
                __class_name__='sklearn.linear_model.LogisticRegression',
                **estimator_args))

    return steps
    
def svms(**kwargs):
    steps = []
    for estimator_args in util.dict_product(dict(penalty=['l2'], 
            dual=[True, False], C=[.001,.01,.1,1])) + \
            util.dict_product(dict(
                    penalty=['l1'], dual=[False], C=[.001,.01,.1,1])):
        steps.append(Construct(name='estimator',
                __class_name__='sklearn.svm.LinearSVC', 
                **estimator_args))

    return steps
