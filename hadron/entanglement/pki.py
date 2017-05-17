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

def gen_site_ca(pki_dir):
    ca_key = os.path.join(pki_dir, 'ca.key')
    ca_pem = os.path.join(pki_dir, 'ca.pem')
    os.makedirs(pki_dir, exist_ok = True)
    if not exists(ca_key):
        sh.openssl('genrsa', '-out', ca_key, '2048',
    )

    
    if not exists(ca_pem):
        sh.openssl.req('-x509', '-key', ca_key,
                   '-subj','/CN=Root CA',
                   '-days', '400',
                   '-extensions', 'v3_ca',
                   '-out', ca_pem)

def host_cert(pki_dir, hostname):
    ca_key = os.path.join(pki_dir, 'ca.key')
    ca_pem = os.path.join(pki_dir, 'ca.pem')
    gen_site_ca(pki_dir)
    hostfile = os.path.join(pki_dir, hostname)
    
    if not (exists (hostfile + '.key') and exists(hostfile + '.pem')):
        sh.openssl.genrsa('-out', '{}.key'.format(hostfile),
                          '2048')
        sh.openssl.x509(
            sh.openssl( 'req',
                        '-new', '-subj', '/CN={}'.format(hostname),
                        '-key', '{}.key'.format(hostfile),
            ),
            '-CAkey', ca_key,
            '-CA', ca_pem,
            '-CAcreateserial',
            '-out', '{}.pem'.format(hostfile),
            '-days', '400',
            '-extensions', 'usr_cert',
            '-req')

