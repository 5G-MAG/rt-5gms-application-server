#!/usr/bin/python3
'''
General utility functions
'''

import subprocess

def find_executable_on_path(cmd):
    '''Find an executable command on the current $PATH

    Return the str path to the cmd or None if the command doesn't exist.
    '''
    result = subprocess.run(['which', cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8").strip()

