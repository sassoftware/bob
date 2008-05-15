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
    
    theTime = int(time.time())
    
     # get the data needed by clover
    cloverData, projNumStmt, projNumCov, numFiles = gatherCloverData(covered)
    numPackages = len(cloverData)
    
    print >>fileobj, '<coverage generated="%d" clover="1.3.13">' % theTime
    
    print >>fileobj, '\t<project timestamp="%d">' % theTime
    print >> fileobj, '\t\t<metrics loc="%d" statements="%d" coveredstatements="%d" packages="%d" files="%d" />' % (projNumStmt, projNumStmt, projNumCov, numPackages, numFiles)
    
    for pkgData in cloverData:
        pkgName = pkgData['package']
        pkgNumStmts = pkgData['total'][0]
        pkgNumCov = pkgData['total'][1]
        
        # write out package metrics
        print >>fileobj, '\t\t<package name="%s">' % pkgName
        print >>fileobj, '\t\t\t<metrics loc="%d" statements="%d" coveredstatements="%d"/>' % (projNumStmt, projNumStmt, pkgNumCov)
        
        filesData = pkgData['files']
        for fileData in filesData:
            fileName = fileData[0]
            fileNumStmts = fileData[1]
            fileNumCov = fileData[2]

            # print file metrics
            print >>fileobj, '\t\t\t<file name="%s">' % fileName
            
            print >>fileobj, '\t\t\t\t<metrics loc="%d" statements="%d" coveredstatements="%d"/>' % (fileNumStmts, fileNumStmts, fileNumCov) 
            print >>fileobj, '\t\t\t</file>'
    
        print >>fileobj, '\t\t</package>'
    
    print >>fileobj, '\t</project>'
    
    print >>fileobj, '</coverage>'
    
def testCloverReport():
    cov = dict()
    cov['raa-plugins/configure/__init__.py'] = (12,12)
    cov['raa-plugins/configure/foo.py'] = (120,12)
    cov['raa-plugins/configure/srv/entitlements.py'] = (65, 46)
    cov['raa/db/database.py'] = (225, 192)
    cov['raa/lib/url.py'] = (68,68)
    cov['raa/web/__init__.py'] = (254,194)
    cov['raa/web/web.py'] = (25,19)
    
    clover_report((cov, (0, 0)), open('/tmp/clover.xml', 'w'))
    
def testGatherCloverData():
    cov = dict()
    cov['raa-plugins/configure/__init__.py'] = (12,12)
    cov['raa-plugins/configure/foo.py'] = (120,12)
    cov['raa-plugins/configure/srv/entitlements.py'] = (65, 46)
    cov['raa/db/database.py'] = (225, 192)
    cov['raa/lib/url.py'] = (68,68)
    cov['raa/web/__init__.py'] = (254,194)
    cov['raa/web/web.py'] = (25,19)
    
    data, projStmt, projCov, numFiles = gatherCloverData(cov)
    
    for d in data:
        print "%s: total stmt %s, total cov %s" % (d['package'], d['total'][0], d['total'][1])
        for fileData in d['files']:
            print "\t%s: %d, %d" % (fileData[0], fileData[1], fileData[2])
    
    return data

    
def gatherCloverData(covered):
    '''
    Get the clover coverage data from data produced by process_coverage.
    '''
    cloverData = []
    
    if not covered:
        return cloverData
    
    files = covered.keys()
    
    projNumStmts = 0
    projNumCov = 0
    projNumFiles = len(files)
    
    lastPackage = None
    packageData = dict()
    packageFiles = []
    pkgNumStmts = 0
    pkgNumCov = 0
    for file in sorted(files):
        # gather the data for clover
        fileDir = os.path.split(file)[0]
        curPackage = fileDir.lstrip(os.path.sep).replace(os.path.sep, '.')
        
        if curPackage != lastPackage:
            if packageData:
                packageData['files'] = packageFiles
                packageData['total'] = (pkgNumStmts, pkgNumCov)
                projNumStmts += pkgNumStmts
                projNumCov += pkgNumCov
                cloverData.append(packageData)
                packageData = dict()
                packageFiles = []
                pkgNumStmts = 0
                pkgNumCov = 0
            packageData['package'] = curPackage
            lastPackage = curPackage
            
        fileNumStmts, fileNumCov = covered[file]
        pkgNumStmts += fileNumStmts
        pkgNumCov += fileNumCov
        packageFiles.append((file, fileNumStmts, fileNumCov))
        
    if packageData:
        # add the last one
        packageData['files'] = packageFiles
        packageData['total'] = (pkgNumStmts, pkgNumCov)
        cloverData.append(packageData)
    
    return cloverData, projNumStmts, projNumCov, projNumFiles

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
