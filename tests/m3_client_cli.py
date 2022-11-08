#!/usr/bin/python3
#
# 5G-MAG Reference Tools: M3 client testing app
# =============================================
#
# File: m3_client_cli.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# The provides a command which will exercise the M3 interface as a client.
#
'''
5G-MAG Reference Tools: M3 client testing app
=============================================

This application will connect to a server which provides an M3 interface, issue
a client request and display the response.

Usage:
    m3_client_cli.py -h | --help
    m3_client_cli.py -c | --certificate <connect>
    m3_client_cli.py -c | --certificate <connect> (add|update) <provisioning-session-id> <certificate-id> <pem-file>
    m3_client_cli.py -c | --certificate <connect> delete <provisioning-session-id> <certificate-id>
    m3_client_cli.py -H | --content-hosting-configuration <connect>
    m3_client_cli.py -H | --content-hosting-configuration <connect> (add|update) <provisioning-session-id> <content-hosting-configuration-json-file>
    m3_client_cli.py -H | --content-hosting-configuration <connect> delete <provisioning-session-id>

Parameters:
    connect                  Hostname:Port of the server providing M3.
    provisioning-session-id  Provisioning Session Identifier.
    certificate-id           Certificate Identifier.
    pem-file                 Server PEM format X.509 public certificate, private key and intermediate CA certificates.
    content-hosting-configuration-json-file
                             Filename of a ContentHostingConfiguration in JSON format.

Options:
    -h --help                Display the command help
    -v --version             Display command version
    -c --certificate         List known certificates or perform a certificate operation.
    -H --content-hosting-configuration
                             List known ContentHostingConfigurations or perform an operation on ContentHostingConfigurations.
'''

from docopt import docopt
import os.path
import os
import sys

from test_m3_client import M3Client, M3ClientException, M3ServerException

def bool_result(result: bool) -> int:
    if result:
        print("Success!")
    else:
        print("No change")
    return 0

def certificates_list_result(result: list) -> int:
    print('Known certificates:\n   '+'\n   '.join(result))
    return 0

def chc_list_result(result: list) -> int:
    print('Known content hosting configurations:\n   '+'\n   '.join(result))
    return 0

def main():
    args = docopt(__doc__, version='1.0.0')

    (server_host, server_port) = args['<connect>'].split(':')
    host_address = (server_host, int(server_port))

    m3_client = M3Client(host_address)

    operations = [
        {'flags': ['--certificate','add'], 'fn': m3_client.addCertificateFromPemFile, 'args': ['<provisioning-session-id>', '<certificate-id>', '<pem-file>'], 'format': bool_result},
        {'flags': ['--certificate','update'], 'fn': m3_client.updateCertificateFromPemFile, 'args': ['<provisioning-session-id>', '<certificate-id>', '<pem-file>'], 'format': bool_result},
        {'flags': ['--certificate','delete'], 'fn': m3_client.deleteCertificate, 'args': ['<provisioning-session-id>', '<certificate-id>'], 'format': bool_result},
        {'flags': ['--certificate'], 'fn': m3_client.listCertificates, 'args': [], 'format': certificates_list_result},
        {'flags': ['--content-hosting-configuration','add'], 'fn': m3_client.addContentHostingConfigurationFromJsonFile, 'args': ['<provisioning-session-id>', '<content-hosting-configuration-json-file>'], 'format': bool_result},
        {'flags': ['--content-hosting-configuration','update'], 'fn': m3_client.updateContentHostingConfigurationFromJsonFile, 'args': ['<provisioning-session-id>', '<content-hosting-configuration-json-file>'], 'format': bool_result},
        {'flags': ['--content-hosting-configuration','delete'], 'fn': m3_client.deleteContentHostingConfiguration, 'args': ['<provisioning-session-id>'], 'format': bool_result},
        {'flags': ['--content-hosting-configuration'], 'fn': m3_client.listContentHostingConfigurations, 'args': [], 'format': chc_list_result},
        ]

    for op in operations:
        for flag in op['flags']:
            if not args[flag]:
                break
        else:
            op_args = [args[a] for a in op['args']]
            try:
                return op['format'](op['fn'](*op_args))
            except M3ClientException as err:
                print("There was a problem with the request: %s"%str(err))
                return 1
            except M3ServerException as err:
                print("There was a problem with the server: %s"%str(err))
                return 2
    else:
        print("Unknown operation")
    
    return 1

if __name__ == "__main__":
    sys.exit(main())
