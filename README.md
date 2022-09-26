# 5G-MAG Reference Tools: 5GMS Application Server

This repository holds the 5GMS Application Server implementation for the
5G-MAG Reference Tools.

## Introduction

The 5GMS application server (AS) is a Network Function that forms part of the
5G Media Services framework as defined in ETSI TS 126.501.

This implementation is comprised of a small Python daemon process which
implements the 5GMS M3 interface as a simple configuration file that is shared
with the Reference Tools [5GMS AF](https://github.com/5G-MAG/rt-5gms-application-function).

The web server or reverse proxy functionality is provided by an external daemon.
This 5GMS AS manages the external daemon by dynamically writing its
configuration files and managing the daemon process lifecycle. At present the
only daemon that can be controlled by the AS is nginx ([website](https://nginx.org/)).

## Specifications

* [ETSI TS 126 501](https://portal.etsi.org/webapp/workprogram/Report_WorkItem.asp?WKI_ID=66447) - 5G Media Streaming (5GMS): General description and architecture (3GPP TS 26.501 version 17.2.0 Release 17)
* [ETSI TS 126 512](https://portal.etsi.org/webapp/workprogram/Report_WorkItem.asp?WKI_ID=66919) - 5G Media Streaming (5GMS): Protocols (3GPP TS 26.512 version 17.1.2 Release 17)

## Downloading

_TODO_
```
git clone --recurse-submodules https://github.com/5G-MAG/rt-5gms-application-server.git
```

## Building

_TODO_
```
cd rt-5gms-application-server
python3 -m build --sdist
```

## Installing

_TODO_
```
cd rt-5gms-application-server
pip install -e .
```

## Running

_TODO_
```
5gms-application-server <ContentHostingConfiguration-JSON-file>
```

## Development

Please see the [docs/README.md](docs/README.md) file for project development
and testing information.
