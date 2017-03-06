#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sh, os, os.path
from os.path import exists

if not exists('ca.key'):
    sh.openssl('genrsa', '-out', 'ca.key', '2048',
    )

    
if not exists('ca.pem'):
    sh.openssl.req('-x509', '-key', 'ca.key',
                   '-subj','/CN=Root CA',
                   '-days', '400',
                   '-extensions', 'v3_ca',
                   '-out', 'ca.pem')

def host_cert(hostname):
    if not (exists (hostname + '.key') and exists(hostname + '.pem')):
        sh.openssl.genrsa('-out', '{}.key'.format(hostname),
                          '2048')
        sh.openssl.x509(
            sh.openssl( 'req',
                        '-new', '-subj', '/CN={}'.format(hostname),
                        '-key', '{}.key'.format(hostname),
            ),
            '-CAkey', 'ca.key',
                        '-CA', 'ca.pem',
                        '-CAcreateserial',
                        '-out', '{}.pem'.format(hostname),
                        '-days', '400',
                        '-extensions', 'usr_cert',
            '-req')

host_cert('host1')
host_cert('host2')

