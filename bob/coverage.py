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
import sys

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
    dirName, fileName=None):
    '''
    Print a simple coverage report from data produced by process_coverage.
    '''

    if fileName == None:
        fileobj = sys.stdout
    else:
        fullPath = dirName + os.path.sep + fileName
        fileobj = open(fullPath, 'w')

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


def dump(cover_data, dirName, fileName='pickle'):
    '''
    Write coverage data to a file as a pickle.
    '''
    assert isinstance(cover_data, dict)
    
    fullPath = dirName + os.path.sep + fileName
    fileobj = open(fullPath, 'w')

    cPickle.dump(cover_data, fileobj, protocol=2)
    
def generate_reports(dirName, oldSchoolCoverageData, pickleDict):
    """
    Generate all the coverage reports
    @param dirName: the directory to create the reports in
    @param oldSchoolCoverageData: the old-school coverage data as returned by
        coverage.process
    @param pickleDict: a dict for pickle to dump.  THis should eventually go
        away.
    """
    
    # get the coverage data object
    coverageData = CoverageData.parseCoverageData(oldSchoolCoverageData)
    
    # create the dir
    os.makedirs(dirName)
    
    # pickle dump
    # TODO: dump the coverage data object instead of dict?
    dump(pickleDict, dirName)
    
    # simple reports
    # TODO: change to subclass of CoverageReport and use coverage data object
    simple_report(oldSchoolCoverageData, dirName)
    simple_report(oldSchoolCoverageData, dirName, 'simple.txt')
    
    # clover report
    CoverageReportClover(dirName, 'clover.xml', coverageData).writeReport()

def clover_report((covered, (total_statements, total_executed)),
  fileobj=None):
    '''
    Print a clover coverage report from data produced by process_coverage.
    '''

    report = CoverageReportClover('/tmp/clover.xml', covData)
    report.writeReport()    
    
def testCloverReport():
    cov = dict()
    cov['raa-plugins/configure/__init__.py'] = (12,12)
    cov['raa-plugins/configure/foo.py'] = (120,12)
    cov['raa-plugins/configure/srv/entitlements.py'] = (65, 46)
    cov['raa/db/database.py'] = (225, 192)
    cov['raa/lib/url.py'] = (68,68)
    cov['raa/web/__init__.py'] = (254,194)
    cov['raa/web/web.py'] = (25,19)
    
    covData =  CoverageData.parseCoverageData((cov, (150, 105)))
    CoverageReportClover('/tmp/clover.xml', covData).writeReport()
    
def testGenReports():
    cov = dict()
    cov['raa-plugins/configure/__init__.py'] = (12,12)
    cov['raa-plugins/configure/foo.py'] = (120,12)
    cov['raa-plugins/configure/srv/entitlements.py'] = (65, 46)
    cov['raa/db/database.py'] = (225, 192)
    cov['raa/lib/url.py'] = (68,68)
    cov['raa/web/__init__.py'] = (254,194)
    cov['raa/web/web.py'] = (25,19)
    
    generate_reports('/tmp/reports', (cov, (150, 105)), dict(data="foo"))
    
def testSimpleReport():
    cov = dict()
    cov['raa-plugins/configure/__init__.py'] = (12,12)
    cov['raa-plugins/configure/foo.py'] = (120,12)
    cov['raa-plugins/configure/srv/entitlements.py'] = (65, 46)
    cov['raa/db/database.py'] = (225, 192)
    cov['raa/lib/url.py'] = (68,68)
    cov['raa/web/__init__.py'] = (254,194)
    cov['raa/web/web.py'] = (25,19)
    
    covData =  CoverageData.parseCoverageData((cov, (150, 105)))
    CoverageReportSimple('/tmp/simple.txt', covData).writeReport()
    
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
            
def testCoverageData():
    cov = dict()
    cov['raa-plugins/configure/__init__.py'] = (12,12)
    cov['raa-plugins/configure/foo.py'] = (120,12)
    cov['raa-plugins/configure/srv/entitlements.py'] = (65, 46)
    cov['raa/db/database.py'] = (225, 192)
    cov['raa/lib/url.py'] = (68,68)
    cov['raa/web/__init__.py'] = (254,194)
    cov['raa/web/web.py'] = (25,19)
    
    total = (150, 105)
    
    coverageData = CoverageData.parseCoverageData((cov, total))
    
class CoverageTotalsData:
    """
    A class for storing/manipulating coverage totals
    """
    
    def __init__(self):
        self.totalFiles = 0
        self.totalStatements = 0
        self.totalCoveredStatements = 0
    
    @staticmethod
    def parseCoverageTotalsData(totalsData):
        """
        Parse the old-style coverage totals data which consist of a tuple
        of (total statements, total covered)
        @return: CoverageTotalsData
        """
        td = CoverageTotalsData()
        if totalsData and len(totalsData) == 2:
            td.totalStatements = totalsData[0]
            td.totalCoveredStatements = totalsData[1]
        return td
        
    def display(self):
        """
        Display coverage totals data in human readable form
        """
        print "\ttotal files=%d, total statements=%d, " \
              "total covered statements=%d" % \
               (self.totalFiles, self.totalStatements,
                self.totalCoveredStatements)
                            
class CoverageFileData:
    """
    A class that maps files to coverage data
    """
    
    def __init__(self):
        self.fileName = None
        self.coverageTotalsData = CoverageTotalsData()
    
    @staticmethod
    def parseCoverageFileData(file, fileData):
        """
        Parse the old-style coverage file data which consist of a tuple of
        (num statements, num covered)
        @return: a CoverageFileData object
        """
        cfd = CoverageFileData()
        if file:
            cfd.fileName = file
            
        if fileData and len(fileData) == 2:
            ctd = CoverageTotalsData()
            ctd.totalFiles = 1
            ctd.totalStatements = fileData[0]
            ctd.totalCoveredStatements = fileData[1]
            cfd.coverageTotalsData = ctd
            
        return cfd
        
    def display(self):
        """
        Display coverage file data in human readable form
        """
        fileTotals = self.getCoverageTotalsData()
        print "%s: statements=%d, statements covered=%d" % \
            (self.fileName, fileTotals.totalStatements, 
             fileTotals.totalCoveredStatements)
        
class CoveragePackageData:
    """
    A class for storing/manipulating coverage data for packages
    """
        
    def __init__(self):
        self.packageName = None
        self.coverageFileData = []
        self.coverageTotalsData = CoverageTotalsData()
        self.maxFileNameLen = 0
    
    @staticmethod
    def parseCoveragePackageData(packageName, filesData):
        """
        Parse the old-style coverage files data which consist of a list of
        dictionaries which map filenames to (num statements, num covered).
        @return: a CoveragePackageData object
        """
        
        cpd = CoveragePackageData()
        if packageName:
            cpd.packageName = packageName
            
        pkgNumFiles = 0
        pkgNumStatements = 0
        pkgNumCoveredStatements = 0
        for fileData in filesData:
            pkgNumFiles += 1
            file = fileData.keys()[0]
            fileLen = len(file)
            if fileLen > cpd.maxFileNameLen:
                cpd.maxFileNameLen = fileLen
            cfd = CoverageFileData.parseCoverageFileData(file, fileData[file])
            cpd.addCoverageFileData(cfd)
            fileTotals = cfd.coverageTotalsData
            pkgNumStatements += fileTotals.totalStatements
            pkgNumCoveredStatements += fileTotals.totalCoveredStatements
            
        ctd = CoverageTotalsData()
        ctd.totalFiles = pkgNumFiles
        ctd.totalStatements = pkgNumStatements
        ctd.totalCoveredStatements = pkgNumCoveredStatements
        cpd.coverageTotalsData  = ctd
            
        return cpd
        
    def addCoverageFileData(self, coverageFileData):
        """
        Add to the coverage file data
        @param coverageFileData: a CoverageFileData object
        """
        if not isinstance(self.coverageFileData, list):
            self.coverageFileData = []
        self.coverageFileData.append(coverageFileData)
        
    def display(self):
        """
        Display the package coverage data in human readable form
        """
        print "Package: %s" % (self.packageName())
        if self.coverageFileData:
            for data in self.coverageFileData:
                print "%s: statements=%d, statements covered=%d" % \
                    (data.fileName, data.numberOfStatements, 
                     data.numberOfCoveredStatements)
        else:
            print "No coverage data exists"
            
        if self.coverageTotalsData:
            print "total statements=%d, total statements covered=%d" % \
                (self.coverageTotalsData.totalStatements,
                 self.coverageTotalsData.totalCoveredStatements)
            
class CoverageData:
    """
    A class for storing/manipulating coverage data
    """
    
    def __init__(self):
        self.coveragePackageData = []
        self.coverageTotalsData = CoverageTotalsData()
    
    @staticmethod
    def parseCoverageData(data):
        """
        Parses the old-style coverage data which contains a tuple of:
            * A dictionary which maps filename to (num statements, num covered)
            * A grand total tuple of (total statements, total covered)
        The data will be arranged by files and there data based on the package.
        """
        
        cd = CoverageData()
        
        # set the package data
        fileData = data[0]
        lastPackage = None
        pkgFileData = []
        totalStmts = 0
        totalCoveredStmts = 0
        totalFiles = 0
        for file in sorted(fileData.keys()):
            
            # get the name of the package
            fileDir = os.path.split(file)[0]
            curPackage = fileDir.lstrip(os.path.sep).replace(os.path.sep, '.')
            
            if curPackage != lastPackage:
                if lastPackage is not None:       
                    # create/add the package
                    cpd = CoveragePackageData.parseCoveragePackageData(
                              lastPackage, pkgFileData)
                    cd.addCoveragePackageData(cpd)
                    totalFiles += cpd.coverageTotalsData.totalFiles
                    pkgFileData = []
                lastPackage = curPackage
            
            # add the file data to the package container
            pkgFileData.append({file: fileData[file]})
            
        # set the totals data
        ctd = CoverageTotalsData.parseCoverageTotalsData(data[1])
        ctd.totalFiles = totalFiles
        cd.coverageTotalsData = ctd
            
        return cd
        
    def addCoveragePackageData(self, coveragePackageData):
        """
        Add to the coverage package data
        @param coveragePackageData: a CoveragePackageData object
        """
        if not isinstance(self.coveragePackageData, list):
            self.coveragePackageData = []
        self.coveragePackageData.append(coveragePackageData)
            
    def display(self):
        """
        Display the coverage data in human readable form
        """
        if self.coveragePackageData:
            for pkgData in self.coveragePackageData:
                print pkgData.display()
        else:
            print "No coverage data exists"
            
        if self.coverageTotalsData:
            print "Totals: statements=%d, covered=%d" % \
                (self.coverageTotalsData.totalStatements,
                 self.coverageTotalsData.totalCoveredStatements)
            
class CoverageReport:
    """
    A generic coverage report class useful for subclassing
    """
    
    def __init__(self, dirName = None, fileName = None, coverageData = None):
        """
        Initialize the coverage report object
        @param dirName: the directory to generate the report in
        @param fileName: the name of the report file to generate
        @param coverageData: a CoverageData object containg the coverage data
        """
        self.dirName = dirName
        self.fileName = fileName
        self.coverageData = coverageData
        
    def openFile(self):
        """
        Open the file for writing
        """
        full_path = self.dirName + os.path.sep + self.fileName
        return open(full_path, 'w')
    
    def printReport(self):
        """
        Must be implemented by the subclass
        """
        raise NotImplementedError(
            "printReport is not implemented by this object")
        
    def writeReport(self, fileobj = None):
        """
        Must be implemented by the subclass
        """
        raise NotImplementedError(
            "writeReport is not implemented by this object")
        
class CoverageReportClover(CoverageReport):
    """
    A class for generating clover coverage reports
    """
    
    def printReport(self, fileobj = sys.stdout):
        """
        Generate the clover report and print to console
        """
        self.writeReport(sys.stdout)
    
    def writeReport(self, fileobj = None):
        """
        Generate the clover report and write to file
        """
        
        if fileobj == None:
            fileobj = self.openFile()
        
        theTime = int(time.time())
        
        # project data
        projTotals = self.coverageData.coverageTotalsData
        projFiles = projTotals.totalFiles
        projStmts = projTotals.totalStatements
        projCovStmts = projTotals.totalCoveredStatements
        
        # package data
        pkgData = self.coverageData.coveragePackageData
        numPackages = len(pkgData) 
        
        print >>fileobj, '<coverage generated="%d" clover="1.0">' % theTime
        
        print >>fileobj, '\t<project timestamp="%d">' % theTime
        print >> fileobj, \
            '\t\t<metrics packages="%d" files="%d" classes="%d" '\
                         'ncloc="%d" loc="%d" '\
                         'conditionals="%d" coveredconditionals="%d" '\
                         'methods="%d" coveredmethods="%d" '\
                         'elements="%d" coveredelements="%d" '\
                         'statements="%d" coveredstatements="%d" '\
                 '/>' % \
                 (numPackages, projFiles, projFiles,
                  projStmts, projStmts,
                  projStmts, projCovStmts,
                  projStmts, projCovStmts,
                  projStmts, projCovStmts,
                  projStmts, projCovStmts)
        
        for pkg in pkgData:
            
            pkgTotals = pkg.coverageTotalsData
            pkgFiles = pkgTotals.totalFiles
            pkgStmts = pkgTotals.totalStatements
            pkgCovStmts = pkgTotals.totalCoveredStatements
            
            # write out package metrics
            print >>fileobj, '\t\t<package name="%s">' % pkg.packageName
            print >>fileobj, \
                '\t\t\t<metrics loc="%d" statements="%d" '\
                               'coveredstatements="%d" files="%d" '\
                       '/>' % \
                       (pkgStmts, pkgStmts, pkgCovStmts, pkgFiles)
            
            filesData = pkg.coverageFileData
            for fileData in filesData:
                fileName = fileData.fileName
                fileTotals = fileData.coverageTotalsData
                fileStmts = fileTotals.totalStatements
                fileCovStmts = fileTotals.totalCoveredStatements
    
                # print file metrics
                print >>fileobj, '\t\t\t<file name="%s">' % fileName
                
                print >>fileobj, \
                    '\t\t\t\t<metrics loc="%d" statements="%d" '\
                                     'coveredstatements="%d" '\
                             '/>' % \
                             (fileStmts, fileStmts, fileCovStmts) 
                print >>fileobj, '\t\t\t</file>'
        
            print >>fileobj, '\t\t</package>'
        
        print >>fileobj, '\t</project>'
        
        print >>fileobj, '</coverage>'
