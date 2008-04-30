#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#


class hashabledict(dict):
    def __hash__(self):
        return hash(tuple(sorted(self.items())))
