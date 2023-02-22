# 5G-MAG Reference Tools: 5GMS Application Server M3 Examples

This directory contains examples of configuration files that can be used with
the `m3_client_cli.py` tool.

## `Certificates.json`

This file contains a mapping from certificate ID to certificate filename. The
certificate IDs in this file are used to find the matching certificate file
(containing a public certificate, private key and any intermediate CA
certificates) when referenced from a ContentHostingConfiguration file.

The `external/rt-common-shared/5gms/scripts/make_self_signed_certs.py` script
can be used, passing a ContentHostingConfiguration and this `Certificates.json`
file as parameters, to create suitable self-signed certificate files for
testing purposes.

For example:
```bash
cd ~/rt-5gms-application-server
external/rt-common-shared/5gms/scripts/make_self_signed_certs.py tests/examples/ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest_https.json tests/examples/Certificates.json
```

## `ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest.json`

This file can be used with `m3_client_cli.py` to configuration the
rt-5gms-application-server to reverse proxy the Big Buck Bunny stream via HTTP distribution.

It contains a ContentHostingConfiguration, based on 3GPP TS 26.512 Release
17.3.0, which points to a media origin host, suitable for use with pull-ingest,
which holds the Big Buck Bunny short animated film.

The distribution side of the configurations tells the rt-5gms-application-server
to present an M4d interface, which will use a URL path prefix of
`/m4d/provisioning-session-d54a1fcc-d411-4e32-807b-2c60dbaeaf5f/`, on its
localhost (127.0.0.1) loopback interface without SSL/TLS.

When the rt-5gms-application-server is configured with this file, the media
should then be accessible, via the M4d interface, using
{http://localhost/m4d/provisioning-session-d54a1fcc-d411-4e32-807b-2c60dbaeaf5f/BigBuckBunny_4s_onDemand_2014_05_09.mpd} as the media entry URL.

To activate this configuration, with an active Application Server, use the
commands:
```bash
cd ~/rt-5gms-application-server
tests/m3_client.cli.py -H localhost:7777 add d54a1fcc-d411-4e32-807b-2c60dbaeaf5f tests/examples/ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest.json
```

## `ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest_http_and_https.json`

This file is an alternative to
`ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest.json` (see above) and
can be used along with the `Certificates.json` file to configure a
rt-5gms-application-server instance which will provide both HTTP and HTTPS
distribution points.

To activate this configuration, with an active Application Server, use the
commands:
```bash
cd ~/rt-5gms-application-server
external/rt-common-shared/5gms/scripts/make_self_signed_certs.py tests/examples/ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest_http_and_https.json tests/examples/Certificates.json
tests/m3_client.cli.py -c localhost:7777 add testcert1 tests/examples/certificate-1.pem
tests/m3_client.cli.py -H localhost:7777 add d54a1fcc-d411-4e32-807b-2c60dbaeaf5f tests/examples/ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest_http_and_https.json
```

## `ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest_https.json`

This file is an alternative to
`ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest.json` (see above) and
can be used along with the `Certificates.json` file to configure a 
rt-5gms-application-server instance which will provide a HTTPS distribution
point instead of an HTTP distribution point.

To activate this configuration, with an active Application Server, use the
commands:
```bash
cd ~/rt-5gms-application-server
external/rt-common-shared/5gms/scripts/make_self_signed_certs.py tests/examples/ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest_https.json tests/examples/Certificates.json
tests/m3_client.cli.py -c localhost:7777 add testcert1 tests/examples/certificate-1.pem
tests/m3_client.cli.py -H localhost:7777 add d54a1fcc-d411-4e32-807b-2c60dbaeaf5f tests/examples/ContentHostingConfiguration_Big-Buck-Bunny_pull-ingest_https.json
```
