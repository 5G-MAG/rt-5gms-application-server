<h1 align="center">5GMS Application Server</h1>
<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/Status-Under_Development-yellow" alt="Under Development"></a>
  <a href="https://github.com/5G-MAG/rt-5gms-application-server/releases/latest"><img src="https://img.shields.io/github/v/release/5G-MAG/rt-5gms-application-server?label=Version" alt="Version"></a>
  <a href="https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view"><img src="https://img.shields.io/badge/License-5G--MAG%20Public%20License%20(v1.0)-blue" alt="License"></a>
</p>

## Introduction

The 5GMS Application Server (AS) is a Network Function that forms part of the 5G Media Streaming framework as defined
in ETSI TS 126.501.

Additional information can be found at: https://5g-mag.github.io/Getting-Started/pages/5g-media-streaming/

### 5GMSd AS

A downlink 5GMS Application Server (5GMSd AS), which can be deployed in the Trusted Data Network or in an External Data
Network, provides downlink 5G Media Streaming services to 5GMSd Clients. This logical function embodies the data plane
aspects of the 5GMSd System that deals with proxying media content (similar to a Content Delivery Network). The content
is ingested from 5GMSd Application Providers at reference point M2d. Both push- and pull-based ingest methods are
supported, based on HTTP. Ingested content is distributed to 5GMSd clients at reference point M4d (after possible
manipulation by the 5GMSd AS). Standard pull-based content retrieval protocols (e.g. DASH) are supported at this
reference point.

### About the implementation

This implementation is comprised of a small Python daemon process which implements the 5GMS AS configuration service at
reference poont M3,
and which also manages an external HTTP(S) Web Server/Proxy daemon subprocess to ingest content (pull ingest only) at
reference point M2d
and serve it to 5GMSd Clients at reference point M4d.

The 5GMSd AS is configured via a RESTful network API at reference point M3, therefore you will need an appropriate M3
client to configure the 5GMSd AS. Such a client is the
[5GMS AF](https://github.com/5G-MAG/rt-5gms-application-function) (release v1.1.0 and above).

The web server or reverse proxy functionality is provided by an external daemon process. This 5GMSd AS manages the
external daemon by dynamically writing its configuration files and managing the daemon process lifecycle. At present,
the only daemon that can be controlled by the AS is Openresty (based on nginx) ([website](https://openresty.org/)).

## Docker setup

A setup comprising the 5GMSd AF and 5GMSd AS based on Docker Compose can be found in the
[rt-5gms-examples](https://github.com/5G-MAG/rt-5gms-examples/tree/development/5gms-docker-setup) project.

## Install dependencies

```
sudo apt install git python3-pip python3-venv
sudo python3 -m pip install --upgrade pip build setuptools
```

The Application Server requires a suitable web proxy server to be installed. At present the only web proxy server that
the Application Server can use is Openresty. This means you should install the Openresty package on your distribution.
Instructions on how to do so can be found on the [Openresty website](https://openresty.org/en/download.html)
for [linux distributions](https://openresty.org/en/linux-packages.html)
and [Microsoft Windows](https://openresty.org/en/download.html#windows). The Openresty version of nginx should also be
the first version on the system path:

```bash
PATH="/usr/local/openresty/nginx/sbin:$PATH" export PATH
```

## Downloading

Release sdist tar files can be downloaded from
the [releases](https://github.com/5G-MAG/rt-5gms-application-server/releases) page.

The source can be obtained by cloning the github repository.

```
cd ~
git clone --recurse-submodules https://github.com/5G-MAG/rt-5gms-application-server.git
```

## Building a Python distribution

### Prerequisites for building

You will additionally need `wget` and `java` to build a distribution.

```
sudo apt install wget default-jdk
```

### Building a distribution tar

To build a Python sdist distribution tar do the following:

```
cd ~/rt-5gms-application-server
python3 -m build --sdist
```

The distribution sdist tar file can then be found in the `dist` subdirectory.

## Installing

### Install from sdist

This application can be installed using pip with a distribution sdist tar file:

```
sudo python3 -m pip install rt-5gms-application-server-<version>.tar.gz
```

If installing as a unprivileged user, the installed files will be added to a local installation place in your home
directory. A warning is shown indicating that the directory where the application is installed should be added to your
path with a command like `PATH="${PATH}:${HOME}/.local/bin" export PATH`.

### Install direct from source

Alternatively, to install the 5GMS Application Server directly from the source you will first need the build
prerequisites, `wget` and `java`, indicated above in the [Prerequisites for building](#prerequisites-for-building)
section. After installing these the application can bi install directly using these commands:

```
cd ~/rt-5gms-application-server
sudo python3 -m pip install .
```

### Installing in a virtual Python environment

If you are testing out this project then you may wish to install in a Python virtual environment instead so that you do
not disturb you present system packages.

You will need the `wget` and `java` prerequisites, see the [Prerequisites for building](#prerequisites-for-building)
section above for details.

After installing the prerequisites, you can install the 5GMS Application Server using the commands:

```
cd ~/rt-5gms-application-server
python3 -m venv venv
venv/bin/python3 -m pip install .
```

When using the virtual environment approach, then you can run the application directly using
`venv/bin/5gms-application-server` instead of just `5gms-application-server` in the following instructions, or you can
activate the virtual environment using `source venv/bin/activate` to automatically add the `venv/bin` directory early in
the executable search path and just use the `5gms-application-server` command.

## Running

Once [installed](#installing), the application server can be run using the following command syntax:

```
Syntax: 5gms-application-server [-c <configuration-file>]
```

Most distributions will install the Nginx service to start on boot and some will even immediately start the Nginx
service daemon when nginx/openresty is installed. A running default configuration of nginx will interfere with the
operation of the application server by claiming TCP port 80 to listen on, thus denying the use of the TCP port to the
application server. To avoid this it is best to disable and stop the nginx and openresty services, for example:

```bash
systemctl disable --now nginx.service openresty.service
```

(If either of these services are not present then an error will be displayed, which is safe to ignore)

Command line help can be obtained using the -h flag:

```
5gms-application-server -h
```

Please note that the default configuration will require the application server to be run as the root user as it uses the
privileged port 80 and stores logs and caches in root owned directories. If you wish to run this as an unprivileged user
you will need to follow the instructions for creating and using an alternative configuration file. These instructions
can be found in
the [development documentation](https://5g-mag.github.io/Getting-Started/pages/5g-media-streaming/usage/application-server/testing-AS.html#running-the-example-without-building).

Once running you will need an M3 client, such as the [5GMS AF](https://github.com/5G-MAG/rt-5gms-application-function),
to manage the running AS. For standalone configuration for testing, see the "Testing without the Application Function"
section of
the [development documentation](https://5g-mag.github.io/Getting-Started/pages/5g-media-streaming/usage/application-server/testing-AS.html#testing-without-the-application-function).

## Development

This project follows
the [Gitflow workflow](https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow). The `development`
branch of this project serves as an integration branch for new features. Consequently, please make sure to switch to the
`development` branch before starting the implementation of a new feature. Please check this
[page](https://5g-mag.github.io/Getting-Started/pages/5g-media-streaming/usage/application-server/testing-AS.html) for
further project development and testing information.
