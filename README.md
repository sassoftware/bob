Bob the Constructinator version 4.2 (revision 1ac0a029bc31)
==============================================================

About
-----

**Bob the Constructinator** a.k.a. *bob* is a command line tool written in python to automate building packages from a git repo into a conary package consumable for Conary enabled systems. bob can leverage WMS and git to enable continuous integration builds.

Overview
---------

**bob** uses a *bob-plan* to build a conary recipe from a scm checkout. bob supports git, WMS, and hg (mercurial). *bob* can build a specific tag or version of an scm repo through command line options.

How it works
-------------

### High Level

**bob** reads plan, initializes build environment, downloads or checks out the appropriate version of the repo, creates an rmake job for the package, kicks off the job and monitors the output, upon completion **bob** will commit the built binary to the conary repo.

### Lower Level

**bob** uses a configuration to create an rmake job capable of building a conary package from a scm repository. **bob** needs to know the location (uri) of the conary recipe, this can be in an scm repo or other place. **bob** will read the plan and use it to create a archive of the correct version of the scm source. Next **bob** will load the recipe to test it before committing it to the conary targetLabel. Next **bob** will create an rmake config using information from the **bob** plan and start an rmake job to build the newly committed conary source. In most cases the **bob** plan references a common config and a platform config that help in the creation of the rmake job.

### Procedure

* Create a git repo for source
* Create a conary recipe to build source
* Create a bob-plan for git repo
* Run bob passing the location of the plan file




Usage
------

`Copyright (c) SAS Institute, Inc.`
`All rights reserved.`

`Usage: bob <plan file or URI> [options]`

`Options:`

`    -h, --help                  show this help message and exit`

`    --set-tag=SET_TAG           tree=revision`

`    --set-version=SET_VERSIO    package=version`

`    --debug`
