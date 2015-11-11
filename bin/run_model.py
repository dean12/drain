import yaml
import pandas as pd
from pandas.io.pytables import PerformanceWarning
from sklearn.externals import joblib
import os
import argparse
import inspect
from copy import deepcopy

from drain import model
from drain import util

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=PerformanceWarning)

from pprint import pformat
def pformat_indent(o, indent):
    return ' '*indent + pformat(o).replace('\n', '\n'+' '*indent)

parser = argparse.ArgumentParser(description='Use this script to run a single model.')
parser.add_argument('params', type=str, help='yaml params filename')
parser.add_argument('outputdir', type=str, help='output directory')
parser.add_argument('datadir', type=str, default=None, help='datadir filename')
args = parser.parse_args()

with open(args.params) as f:
    params = yaml.load(f)

data_name = params['data'].pop('name')
model_name = params['model'].pop('name')

print 'Reading ' + data_name + ' with parameters:' 
print pformat_indent(params['data'], 4)

data = util.get_attr(data_name)(**params['data'])
data.read(args.datadir)

print 'Tranforming with parameters:'
print pformat_indent(params['transform'], 4)

data.transform(**params['transform'])
train,test = data.cv

print 'Training ' + model_name
print '    with parameters ' + str(params['model'])
print '    on ' + str(train.sum()) + ' examples'
print '    with ' + str(len(data.X.columns)) + ' features'

estimator = util.get_attr(model_name)(**params['model'])

if 'sample_weight' in inspect.getargspec(estimator.fit) and hasattr(data, 'sample_weight'):
    estimator.fit(data.X[train],data.y[train], data.sample_weight[train])
else:
    estimator.fit(data.X[train],data.y[train])

print 'Validating model'
print '    on ' + str(test.sum()) + ' examples'

y_score = pd.Series(model.y_score(estimator, data.X[test]), index=data.X[test].index)

# create a single y dataframe with train and test and other masks
y = pd.DataFrame({'true': data.y})
y['score'] = y_score
if hasattr(data, 'masks'):
    y = y.join(data.masks)
y['train'] = train
y['test'] = test

run = {'data': data, 'estimator': estimator, 'y': y.reset_index()}

# print metrics
common_params = {'run': run} # stuff passed to every metric
for metric in params['metrics']:
    metric_name = metric.pop('name')
    metric_fn = util.get_attr(metric_name)

    # get the subset of args that this metric wants
    kwds = inspect.getargspec(metric_fn).args
    mp = {k:v for k,v in metric.iteritems() if k in kwds and v is not None}
    cp = {k:v for k,v in common_params.iteritems() if k in kwds}
    p = dict(mp, **cp)
    v = metric_fn(**dict(mp, **cp))

    mp = map(lambda k: str(k) + '=' + str(mp[k]),mp)
    print '    ' + metric_fn.__name__ + '(' + str.join(',', mp) + '): ' + str(v)

if not os.path.exists(args.outputdir):
    os.makedirs(args.outputdir)
    
joblib.dump(estimator, os.path.join(args.outputdir, 'estimator.pkl'))

# write output
y.to_hdf(os.path.join(args.outputdir, 'y.hdf'), 'y', mode='w')

model.feature_importance(estimator, data.X).to_csv(os.path.join(args.outputdir, 'features.csv'), index=False)