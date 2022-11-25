# 5G-MAG Reference Tools: 5GMS Application Server

This repository holds the 5GMS Application Server implementation for the 5G-MAG Reference Tools.

## Introduction

The 5GMS application server (AS) is a Network Function that forms part of the 5G Media Services framework as defined in
ETSI TS 126.501.

This implementation is comprised of a small Python daemon process which implements the 5GMS M3 interface as a simple
configuration file that is shared with the Reference
Tools [5GMS AF](https://github.com/5G-MAG/rt-5gms-application-function).

The web server or reverse proxy functionality is provided by an external daemon. This 5GMS AS manages the external
daemon by dynamically writing its configuration files and managing the daemon process lifecycle. At present the only
daemon that can be controlled by the AS is nginx
([website](https://nginx.org/)).

## Specifications

* [ETSI TS 126 501](https://portal.etsi.org/webapp/workprogram/Report_WorkItem.asp?WKI_ID=66447) - 5G Media Streaming (
  5GMS): General description and architecture (3GPP TS 26.501 version 17.2.0 Release 17)
* [ETSI TS 126 512](https://portal.etsi.org/webapp/workprogram/Report_WorkItem.asp?WKI_ID=66919) - 5G Media Streaming (
  5GMS): Protocols (3GPP TS 26.512 version 17.1.2 Release 17)

## Install dependencies

```
sudo apt install git python3-pip python3-venv
python3 -m pip install build
```

## Downloading

Release sdist tar files can be downloaded from _TBC_.

The source can be obtained by cloning the github repository.

```
cd ~
git clone --recurse-submodules https://github.com/5G-MAG/rt-5gms-application-server.git
```

## Building a Python distribution

To build a Python sdist distribution tar do the following.

```
cd ~/rt-5gms-application-server
python3 -m build --sdist
```

The distribution sdist tar file can then be found in the `dist` subdirectory.

## Installing

This application can be installed using pip with a distribution sdist tar file:

```
python3 -m pip install rt-5gms-application-server-<version>.tar.gz
```

Alternatively, to installing the 5GMS Application Server from the source can be done using these commands:

```
cd ~/rt-5gms-application-server
python3 -m pip install .
```

## Running

Once [installed](#Installing), the application server can be run using the following command syntax:

```
Syntax: 5gms-application-server [-c <configuration-file>] <ContentHostingConfiguration-JSON-file>
```

Command line help can be obtained using the -h flag:

```
5gms-application-server -h
```

Please note that the default configuration will require the application server to be run as the root user as it uses the privileged port 80 and stores logs and caches in root owned directories. If you wish to run this as an unprivileged user you will need to follow the instructions for creating and using an alternative configuration file. These instructions can be found in the [development documentation](docs/README.md#running-the-example-without-building).

## Development

This project follows
the [Gitflow workflow](https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow). The `development`
branch of this project serves as an integration branch for new features. Consequently, please make sure to switch to the `development`
branch before starting the implementation of a new feature. Please check the [docs/README.md](docs/README.md) file for
further project development and testing information.
