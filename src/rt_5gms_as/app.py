#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: app.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS main entry module.
# This file handles the command line parsing, creates the application context
# from the configuration file and manages the web server at a high level.
#
'''
Reference Tools: 5GMS Application Server
========================================

This NF provides the configuration interface for an external web proxy daemon.
'''
import argparse
import logging
import sys

from .proxy_factory import WebProxy, list_registered_web_proxies
from .context import Context

def get_arg_parser():
    '''
    Create the ArgumentParser object for this application

    Syntax:
      rt-5gsm-as -h
      rt-5gsm-as [-c <app-config-file>] <content-config-file>

    Options:
      -h         --help           Show the help text
      -c CONFIG  --config CONFIG  The application configuration file

    Parameters:
      content-config-file  This the file name of a file containing a
                           ContentHostingConfiguration in JSON format.
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', nargs=1, required=False, metavar='CONFIG', help='The application configuration file')
    parser.add_argument('contentconfig', nargs=1, metavar='CHC-JSON-FILE', help='The ContentHostingConfiguration JSON file')
    return parser

def list_join(l, sep1, sep2=None):
    '''
    Join a list to form a string using a choice of separators

    The str representations of the list l are joined together using sep1 to
    separate the items in the list except for the last two items which are
    separated by sep2. If sep2 is not provided then all items are separated by
    sep1.

    Examples
    list_join([1,2,3,4], ', ', ' or ') => '1, 2, 3 or 4'
    list_join([], ', ', ' or ') => ''
    list_join([1], ', ', ' or ') => '1'
    list_join([1,2], ', ', ' or ') => '1 or 2'
    list_join([1,2,3], ', ', ' or ') => '1, 2 or 3'
    list_join([1,2,3], ', ') => '1, 2, 3'
    '''
    if sep2 is None:
        sep2 = sep1
    lstr = [str(v) for v in l]
    return sep1.join(lstr[:-2]+[sep2.join(lstr[-2:])])

def main():
    '''
    Application entry point
    '''
    logging.basicConfig(level=logging.INFO)

    parser = get_arg_parser()
    args = parser.parse_args()
    log = logging.getLogger("rt-5gms-as")

    if WebProxy is None:
        log.error("Please install at least one of: %s", list_join([p.name() for p in list_registered_web_proxies()], ', ', ' or '))
        return 1

    config = args.config
    if config is not None:
        config = config[0]
    contentconfig = args.contentconfig
    if contentconfig is not None:
        contentconfig = contentconfig[0]
    context = Context(config, contentconfig)
    proxy = WebProxy(context)

    if not proxy.writeConfiguration():
        log.error("Unable to write out configurations for %s web proxy", proxy.name())
        return 1

    if not proxy.startDaemon():
        log.error("Unable to start %s web proxy", proxy.name())
        return 1

    if not proxy.wait():
        log.error("%s web proxy is not running", proxy.name())
        return 1

    if not proxy.tidyConfiguration():
        log.warning("Unable to tidy up after %s web proxy", proxy.name())
        return 2

    return 0

if __name__ == "__main__":
    sys.exit(main())
