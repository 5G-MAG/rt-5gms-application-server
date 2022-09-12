#!/usr/bin/python3
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
      rt-5gsm-as <config-file>

    Options:
      -h    Show the help text
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument('config', metavar='CHC-JSON-FILE', help='The ContentHostingConfiguration JSON file')
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

    context = Context(args.config)
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
