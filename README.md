<h1 align="center">5GMS Application Server</h1>
<p align="center">
  <img src="https://img.shields.io/github/v/tag/5G-MAG/rt-5gms-application-server?label=version" alt="Version">
  <img src="https://img.shields.io/badge/Status-Under_Development-yellow" alt="Under Development">
  <img src="https://img.shields.io/badge/License-5G--MAG%20Public%20License%20(v1.0)-blue" alt="License">
</p>

## Introduction

The 5GMS Application Server (AS) is a Network Function that forms part of the 5G Media Services framework as defined in ETSI TS 126.501.

Additional information can be found at: https://5g-mag.github.io/Getting-Started/pages/5g-media-streaming/

### 5GMS Downlink Application Server
A 5GMS Downlink Application Server (5GMSd AS), which can be deployed in the 5G Core Network or in an External Data Network, provides 5G Downlink Media Streaming services to 5GMSd Clients. This logical function embodies the data plane aspects of the 5GMSd System that deals with proxying media content (similar to a Content Delivery Network). The content is ingested from 5GMSd Application Providers at reference point M2d. Both push- and pull-based ingest methods are supported, based on HTTP. Ingested content is distributed to 5GMSd clients at reference point M4d (after possible manipulation by the 5GMSd AS). Standard pull-based content retrieval protocols (e.g. DASH) are supported at this reference point.

#### Specifications

A list of specifications related to 5G Downlink Media Streaming is available in the [Standards Wiki](https://github.com/5G-MAG/Standards/wiki/5G-Downlink-Media-Streaming-Architecture-(5GMSd):-Relevant-Specifications).

#### About the implementation

This implementation is comprised of a small Python daemon process which implements the 5GMS AS configuration service at interface M3,
and which also manages an external HTTP(S) Web Server/Proxy daemon subprocess to ingest content (pull ingest only) at interface M2d
and serve it to 5GMSd Clients at interface M4d.

The 5GMSd AS is configured via the M3 interface, therefore you will need an appropriate M3 client to configure the 5GMSd AS. Such a client is
the [5GMS AF](https://github.com/5G-MAG/rt-5gms-application-function) (release v1.1.0 and above).

The web server or reverse proxy functionality is provided by an external daemon. This 5GMSd AS manages the external
daemon by dynamically writing its configuration files and managing the daemon process lifecycle. At present the only
daemon that can be controlled by the AS is Openresty (based on nginx) ([website](https://openresty.org/)).

## Install dependencies

```
sudo apt install git python3-pip python3-venv
sudo python3 -m pip install --upgrade pip build setuptools
```

## Downloading

Release sdist tar files can be downloaded from the [releases](https://github.com/5G-MAG/rt-5gms-application-server/releases) page.

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

To build a Python sdist distribution tar do the following.

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

If installing as a unprivileged user, the installed files will be added to a local installation place in your home directory. A warning is shown indicating that the directory where the application is installed should be added to your path with a command like `PATH="${PATH}:${HOME}/.local/bin" export PATH`.

### Install direct from source

Alternatively, to install the 5GMS Application Server directly from the source you will first need the build prerequisites, `wget` and `java`, indicated above in the [Prerequisites for building](#prerequisites-for-building) section. After installing these the application can bi install directly using these commands:

```
cd ~/rt-5gms-application-server
sudo python3 -m pip install .
```

### Installing in a virtual Python environment

If you are testing out this project then you may wish to install in a Python virtual environment instead so that you do not disturb you present system packages.

You will need the `wget` and `java` prerequisites, see the [Prerequisites for building](#prerequisites-for-building) section above for details.

After installing the prerequisites, you can install the 5GMS Application Server using the commands:

```
cd ~/rt-5gms-application-server
python3 -m venv venv
venv/bin/python3 -m pip install .
```

When using the virtual environment approach, then you can run the application directly using `venv/bin/5gms-application-server` instead of just `5gms-application-server` in the following instructions, or you can activate the virtual environment using `source venv/bin/activate` to automatically add the `venv/bin` directory early in the executable search path and just use the `5gms-application-server` command.

## Running

Once [installed](#installing), the application server can be run using the following command syntax:

```
Syntax: 5gms-application-server [-c <configuration-file>]
```

Please note that the application server requires a suitable web proxy server to be installed. At present the only web proxy server that the application server can use is Openresty. This means you should install the openresty package on your distribution, instruction to do so can be found on the [Openresty website](https://openresty.org/en/download.html) for [linux distributions](https://openresty.org/en/linux-packages.html) and [Microsoft Windows](https://openresty.org/en/download.html#windows). The Openresty version of nginx should also be the first version on the system path.

```bash
PATH="/usr/local/openresty/nginx/sbin:$PATH" export PATH
```

Most distributions will install the Nginx service to start on boot and some will even immediately start the Nginx service daemon when nginx/openresty is installed. A running default configuration of nginx will interfere with the operation of the application server by claiming TCP port 80 to listen on, thus denying the use of the TCP port to the application server. To avoid this it is best to disable and stop the nginx and openresty services, for example:

```bash
systemctl disable --now nginx.service openresty.service
```
(If either of these services are not present then an error will be displayed, which is safe to ignore)

Command line help can be obtained using the -h flag:

```
5gms-application-server -h
```

Please note that the default configuration will require the application server to be run as the root user as it uses the privileged port 80 and stores logs and caches in root owned directories. If you wish to run this as an unprivileged user you will need to follow the instructions for creating and using an alternative configuration file. These instructions can be found in the [development documentation](docs/README.md#running-the-example-without-building).

Once running you will need an M3 client, such as the [5GMS AF](https://github.com/5G-MAG/rt-5gms-application-function), to manage the running AS. For standalone configuration for testing, see the "Testing without the Application Function" section of the [development documentation](docs/README.md#testing-without-the-application-function).

## Development

This project follows
the [Gitflow workflow](https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow). The `development`
branch of this project serves as an integration branch for new features. Consequently, please make sure to switch to the `development`
branch before starting the implementation of a new feature. Please check the [docs/README.md](docs/README.md) file for
further project development and testing information.
