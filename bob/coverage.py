#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Module containing coverage processing and reports.
'''

import cPickle
import logging
import os
import time

log = logging.getLogger('bob.coverage')


def process(cover_data):
    '''
    Process the given coverage data and produce a report of percent
    coverage on each covered module, as well as a grand total.
    
    Returns a tuple of:
     * A dictionary which maps filename to (num statements, num covered)
     * A grand total tuple of (total statements, total covered)
    '''

    total_statements = total_executed = 0

    covered = {}
    for morf, (statements, missing) in cover_data.iteritems():
        num_statements = len(statements)
        num_missing = len(missing)
        num_executed = num_statements - num_missing

        covered[morf] = (num_statements, num_executed)
        total_statements += num_statements
        total_executed += num_executed

    return covered, (total_statements, total_executed)


def simple_report((covered, (total_statements, total_executed)),
  fileobj=None):
    '''
    Print a simple coverage report from data produced by process_coverage.
    '''

    # Print out a report
    max_name = max([5,] + map(len, covered.keys()))
    fmt_name = "%%- %ds  " % max_name
    header = fmt_name % "Name" + " Stmts   Exec    Cover"
    fmt_coverage = fmt_name + "% 6d % 6d % 7s%%"

    print >>fileobj, header
    for morf in sorted(covered.keys()):
        num_statements, num_executed = covered[morf]
        if num_statements > 0:
            percent = 100.0 * num_executed / num_statements
        else:
            percent = 100.0
        str_percent = '%-4.2f' % percent

        print >>fileobj, fmt_coverage % (morf, num_statements,
            num_executed, str_percent)

    if total_statements > 0:
        total_percent = 100.0 * total_executed / total_statements

        print >>fileobj, '-' * len(header)
        print >>fileobj, fmt_coverage % ('TOTAL', total_statements, total_executed,
                              '%-4.2f' % total_percent)


def wiki_summary((covered, (total_statements, total_executed)), cfg):
    '''
    Write a slice of a table to a template on a mediawiki summarizing the
    coverage on this product. Requires that a "wiki" section be configured
    in the build plan.
    '''

    if not cfg.root or not cfg.subdir or not cfg.page:
        return
    if not cfg.product:
        cfg['product'] = cfg.page

    # If the wiki root does not exist then most likely the wikipediafs
    # has not been mounted, so bail out.
    if not os.path.isdir(cfg.root):
        log.warning('Wiki root %s does not exist; skipping wiki output.',
            cfg.root)
        return

    # Create the subdir if needed
    subdir = os.path.join(cfg.root, cfg.subdir)
    if not os.path.isdir(subdir):
        os.makedirs(subdir)

    # Write report
    cfg['page'] = cfg.page[0].upper() + cfg.page[1:] # wikicase
    path = os.path.join(subdir, cfg.page + '.mw')
    wiki_path = os.path.join(cfg.subdir, cfg.page)

    if total_statements > 0:
        percent = 100.0 * total_executed / total_statements
    else:
        percent = 100.0

    page = open(path, 'w')
    print >>page, '|-'
    print >>page, '| %s || %d || %d || %.02f%% || %s' % (
        cfg.product, total_statements, total_executed,
        percent, time.strftime('%m/%d'))
    page.close()

    log.info('Coverage summary written to mediawiki at %s under %s',
        cfg.root, wiki_path)


def dump(cover_data, fileobj):
    '''
    Write coverage data to a file as a pickle.
    '''
    assert isinstance(cover_data, dict)

    cPickle.dump(cover_data, fileobj, protocol=2)

def clover_report((covered, (total_statements, total_executed)),
  fileobj=None):
    '''
    Print a clover coverage report from data produced by process_coverage.
    '''
    print >>fileobj, 'Hello Clover!'

def load(cover_data, fileobj):
    '''
    Add the coverage data from one coverage blob to a "grand total"
    dictionary.
    '''

    this_coverage = cPickle.load(fileobj)
    for morf, (statements, missing) in this_coverage.iteritems():
        if not cover_data.has_key(morf):
            cover_data[morf] = [statements, set(missing)]
        else:
            cover_data[morf][1] &= set(missing)

def merge(main, other):
    '''
    Merge coverage from I{other} into I{main}
    '''

    for morf, (statements, missing) in other.iteritems():
        if morf in main:
            main[morf][1] &= missing
        else:
            main[morf] = [statements, missing]
