import os
import sys
import argparse
import importlib

from drain import step, util, drake
import drain.yaml

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Use this script to generate a Drakefile for grid search')
    
    parser.add_argument('--drakeoutput', type=str, help='internally used temp file for drake workflow')
    parser.add_argument('--drakeargsfile', type=str, help='internally used temp file for drake arguments')
    parser.add_argument('-D', '--Drakeinput', type=str, default=None, help='dependent drakefile')
    parser.add_argument('-d', '--debug', action='store_true', help='run python -m pdb')
    parser.add_argument('-P', '--preview', action='store_true', help='Preview Drakefile')
    parser.add_argument('--basedir', type=str, help='output base directory')
    
    parser.add_argument('steps', type=str, help='yaml file or reference to python collection of drain.Step objects or reference to python function returning same. can specify multiple using semi-colon separator.')

    #parser.add_argument('drakeargs', nargs='?', type=str, default=None, help='parameters to pass to drake via --drakeargsfile')
    args, drake_args = parser.parse_known_args()

    if args.drakeoutput is None or args.drakeargsfile is None:
        args.preview = True

    step.BASEDIR = os.path.abspath(args.basedir)
    drain.yaml.configure()

    steps = []
    for s in args.steps.split(';'):
        if s.endswith('.yaml'):
            steps +=  drain.yaml.load(s)
        else:
            print s
            modulename, fname = s.split('::')
            mod = importlib.import_module(modulename)
            s = getattr(mod, fname)
            # if s is callable, it should return a collection of Steps
            # otherwise assume it is a collection of Steps
            s = util.make_list(s() if hasattr(s, '__call__') else s)
            steps += s

    if args.Drakeinput is None and os.path.exists('Drakefile'):
        args.Drakeinput = 'Drakefile'

    workflow = drake.to_drakefile(steps, preview=args.preview, debug=args.debug)

    if not args.preview:
        with open(args.drakeoutput, 'w') as drakefile:
            drakefile.write(workflow)
    else:
        sys.stdout.write(workflow)

    # need PYTHONUNBUFFERED for pdb interactivity
    if args.debug:
        drake_args = list(drake_args) if drake_args is not None else []
        drake_args.insert(0, '-v PYTHONUNBUFFERED=Y')
   
    if args.drakeargsfile is not None and not args.preview:
        with open(args.drakeargsfile, 'w') as drakeargsfile:
            drakeargsfile.write(str.join(' ', drake_args))
