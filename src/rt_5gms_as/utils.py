#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: utils.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS common utility functions.
#
# Functions:
# find_executable_on_path(cmd) - Find cmd and return the absolute path
#
'''
General utility functions
'''

import subprocess

def find_executable_on_path(cmd):
    '''Find an executable command on the current $PATH

    Return the str path to the cmd or None if the command doesn't exist.
    '''
    # Uses the external `which` command.
    # TODO: this will need enhancing to work on non-unix based systems
    result = subprocess.run(['which', cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8").strip()

